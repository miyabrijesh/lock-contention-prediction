from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CHAR,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import RiskLevel


class ModelVersion(Base):
    __tablename__ = "model_versions"

    __table_args__ = (
        CheckConstraint(
            "target_horizon_sec > 0",
            name="ck_model_versions_target_horizon",
        ),
        CheckConstraint(
            "jsonb_typeof(feature_schema) = 'object'",
            name="ck_model_versions_feature_schema_object",
        ),
        CheckConstraint(
            "jsonb_typeof(training_run_ids) = 'array' "
            "AND jsonb_array_length(training_run_ids) > 0",
            name="ck_model_versions_training_runs",
        ),
        CheckConstraint(
            "jsonb_typeof(risk_thresholds) = 'object'",
            name="ck_model_versions_risk_thresholds_object",
        ),
        CheckConstraint(
            "jsonb_typeof(evaluation_metrics) = 'object'",
            name="ck_model_versions_evaluation_metrics_object",
        ),
        CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_model_versions_artifact_sha256",
        ),
        Index(
            "uq_model_versions_single_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        Index(
            "ix_model_versions_name_created",
            "name",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    algorithm: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    feature_schema: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    target_horizon_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    training_run_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    validation_strategy: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    risk_thresholds: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    evaluation_metrics: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
    )
    artifact_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    artifact_sha256: Mapped[str] = mapped_column(
        CHAR(64),
        nullable=False,
    )
    code_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


class Prediction(Base):
    __tablename__ = "predictions"

    __table_args__ = (
        CheckConstraint(
            "probability BETWEEN 0 AND 1",
            name="ck_predictions_probability",
        ),
        CheckConstraint(
            "threshold_used BETWEEN 0 AND 1",
            name="ck_predictions_threshold",
        ),
        CheckConstraint(
            "target_window_end > target_window_start",
            name="ck_predictions_target_window",
        ),
        CheckConstraint(
            "actual_label_evidence IS NULL "
            "OR jsonb_typeof(actual_label_evidence) = "
            "'object'",
            name=(
                "ck_predictions_label_evidence_object"
            ),
        ),
        CheckConstraint(
            "actual_label_contract_version IS NULL "
            "OR btrim(actual_label_contract_version) "
            "<> ''",
            name=(
                "ck_predictions_label_contract_nonblank"
            ),
        ),
        CheckConstraint(
            "actual_label_assigned_at IS NULL "
            "OR actual_label_assigned_at "
            ">= target_window_end",
            name=(
                "ck_predictions_label_assignment_time"
            ),
        ),
        CheckConstraint(
            "("
            "actual_label IS NULL "
            "AND actual_label_assigned_at IS NULL "
            "AND actual_label_contract_version IS NULL "
            "AND actual_label_evidence IS NULL"
            ") OR ("
            "actual_label IS NOT NULL "
            "AND actual_label_assigned_at IS NOT NULL "
            "AND actual_label_contract_version "
            "IS NOT NULL "
            "AND actual_label_evidence IS NOT NULL"
            ")",
            name=(
                "ck_predictions_label_provenance_complete"
            ),
        ),
        Index(
            "ix_predictions_experiment_predicted",
            "experiment_run_id",
            "predicted_at",
        ),
        Index(
            "ix_predictions_model_predicted",
            "model_version_id",
            "predicted_at",
        ),
        Index(
            "ix_predictions_unlabeled_target_window_end",
            "target_window_end",
            postgresql_where=text(
                "actual_label IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    experiment_run_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    metric_sample_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("metric_samples.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    model_version_id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("model_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    target_window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    target_window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    probability: Mapped[Decimal] = mapped_column(
        Numeric(7, 6),
        nullable=False,
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        SqlEnum(
            RiskLevel,
            name="risk_level",
        ),
        nullable=False,
    )
    threshold_used: Mapped[Decimal] = mapped_column(
        Numeric(7, 6),
        nullable=False,
    )
    is_stale: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    quality_flags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    actual_label: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    actual_label_assigned_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    actual_label_contract_version: Mapped[
        str | None
    ] = mapped_column(
        String(32),
        nullable=True,
    )

    actual_label_evidence: Mapped[
        dict[str, object] | None
    ] = mapped_column(
        JSONB(none_as_null=True),
        nullable=True,
    )
