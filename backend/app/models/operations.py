from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class AuditEvent(Base):
    __tablename__ = "audit_events"

    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="ck_audit_events_details_object",
        ),
        Index(
            "ix_audit_events_experiment_created",
            "experiment_run_id",
            "created_at",
        ),
        Index(
            "ix_audit_events_action_created",
            "action",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    experiment_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DataExport(Base):
    __tablename__ = "data_exports"

    __table_args__ = (
        CheckConstraint(
            "row_count >= 0",
            name="ck_data_exports_row_count",
        ),
        CheckConstraint(
            "file_sha256 ~ '^[0-9a-fA-F]{64}$'",
            name="ck_data_exports_file_sha256",
        ),
        Index(
            "ix_data_exports_created_at",
            "created_at",
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
        unique=True,
    )
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    file_sha256: Mapped[str] = mapped_column(
        CHAR(64),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    row_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )