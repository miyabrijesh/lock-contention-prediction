from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
from backend.app.models.enums import ExperimentStatus


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    __table_args__ = (
        CheckConstraint(
            "user_count BETWEEN 1 AND 2000",
            name="ck_experiment_runs_user_count",
        ),
        CheckConstraint(
            "spawn_rate_per_sec > 0",
            name="ck_experiment_runs_spawn_rate",
        ),
        CheckConstraint(
            "duration_sec BETWEEN 30 AND 3600",
            name="ck_experiment_runs_duration",
        ),
        CheckConstraint(
            "hotspot_ratio BETWEEN 0 AND 1",
            name="ck_experiment_runs_hotspot_ratio",
        ),
        CheckConstraint(
            "jsonb_typeof(hotspot_seat_ids) = 'array' "
            "AND jsonb_array_length(hotspot_seat_ids) > 0",
            name="ck_experiment_runs_hotspot_seats",
        ),
        CheckConstraint(
            "sample_interval_sec BETWEEN 1 AND 10",
            name="ck_experiment_runs_sample_interval",
        ),
        CheckConstraint(
            "simulated_work_ms BETWEEN 0 AND 1000",
            name="ck_experiment_runs_simulated_work",
        ),
        CheckConstraint(
            "booking_mode IN ('NORMAL', 'EXPERIMENT_RECYCLE')",
            name="ck_experiment_runs_booking_mode",
        ),
        CheckConstraint(
            "traffic_pattern IN "
            "('STEADY', 'RAMP', 'BURST', "
            "'FLASH_SALE', 'COOLDOWN')",
            name="ck_experiment_runs_traffic_pattern",
        ),
        CheckConstraint(
            "demand_profile IN "
            "('CUSTOM', 'LOW', 'MEDIUM', 'HIGH')",
            name="ck_experiment_runs_demand_profile",
        ),
        Index(
            "ix_experiment_runs_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_experiment_runs_show_started",
            "show_id",
            "started_at",
        ),
        Index(
            "uq_experiment_runs_single_running",
            "status",
            unique=True,
            postgresql_where=text(
                "status = 'RUNNING'"
            ),
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
    show_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[ExperimentStatus] = mapped_column(
        SqlEnum(
            ExperimentStatus,
            name="experiment_status",
        ),
        nullable=False,
        default=ExperimentStatus.DRAFT,
        server_default=ExperimentStatus.DRAFT.value,
    )
    user_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    spawn_rate_per_sec: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        nullable=False,
    )
    duration_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    traffic_pattern: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="STEADY",
        server_default="STEADY",
    )
    demand_profile: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="CUSTOM",
        server_default="CUSTOM",
    )
    hotspot_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    hotspot_seat_ids: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
    )
    sample_interval_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default="2",
    )
    simulated_work_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
    )
    booking_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    random_seed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    code_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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