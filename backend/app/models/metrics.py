from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class MetricSample(Base):
    __tablename__ = "metric_samples"

    __table_args__ = (
        UniqueConstraint(
            "experiment_run_id",
            "sampled_at",
            name="uq_metric_samples_run_sampled_at",
        ),
        CheckConstraint(
            "active_transactions >= 0 "
            "AND blocked_sessions >= 0 "
            "AND waiting_locks >= 0 "
            "AND total_locks >= 0 "
            "AND total_connections >= 0 "
            "AND deadlock_delta >= 0 "
            "AND configured_users >= 0 "
            "AND active_users >= 0",
            name="ck_metric_samples_counts_nonnegative",
        ),
        CheckConstraint(
            "estimated_wait_age_ms >= 0 "
            "AND requests_per_sec >= 0 "
            "AND mean_latency_ms >= 0 "
            "AND p95_latency_ms >= 0",
            name="ck_metric_samples_measurements_nonnegative",
        ),
        CheckConstraint(
            "connection_utilization BETWEEN 0 AND 1 "
            "AND failure_rate BETWEEN 0 AND 1 "
            "AND hotspot_ratio BETWEEN 0 AND 1 "
            "AND booking_success_rate BETWEEN 0 AND 1 "
            "AND conflict_rate BETWEEN 0 AND 1 "
            "AND timeout_rate BETWEEN 0 AND 1",
            name="ck_metric_samples_rates",
        ),
        Index(
            "ix_metric_samples_run_sampled",
            "experiment_run_id",
            "sampled_at",
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
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # Database metrics can be null when collection is incomplete.
    active_transactions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    blocked_sessions: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    waiting_locks: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_locks: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_connections: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    connection_utilization: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    estimated_wait_age_ms: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )
    deadlock_delta: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Application and workload metrics can be null if Locust data
    # is unavailable during a collection interval.
    configured_users: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    active_users: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    requests_per_sec: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )
    mean_latency_ms: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )
    p95_latency_ms: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3),
        nullable=True,
    )
    failure_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    hotspot_ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
    )
    booking_success_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    conflict_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )
    timeout_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 6),
        nullable=True,
    )

    is_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    quality_notes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    collector_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )