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
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import AttemptOutcome, BookingStatus


class Booking(Base):
    __tablename__ = "bookings"

    __table_args__ = (
        CheckConstraint(
            "(status = 'CONFIRMED' AND cancelled_at IS NULL) OR "
            "(status = 'CANCELLED' AND cancelled_at IS NOT NULL)",
            name="ck_bookings_cancellation_matches_status",
        ),
        Index(
            "uq_bookings_normal_confirmed_seat",
            "seat_id",
            unique=True,
            postgresql_where=text(
                "status = 'CONFIRMED' AND experiment_run_id IS NULL"
            ),
        ),
        Index(
            "uq_bookings_experiment_confirmed_seat",
            "experiment_run_id",
            "seat_id",
            unique=True,
            postgresql_where=text(
                "status = 'CONFIRMED' AND experiment_run_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_bookings_experiment_booked",
            "experiment_run_id",
            "booked_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    idempotency_key: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("synthetic_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    show_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seat_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("seats.id", ondelete="RESTRICT"),
        nullable=False,
    )
    experiment_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[BookingStatus] = mapped_column(
        SqlEnum(
            BookingStatus,
            name="booking_status",
        ),
        nullable=False,
        default=BookingStatus.CONFIRMED,
        server_default=BookingStatus.CONFIRMED.value,
    )
    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BookingAttempt(Base):
    __tablename__ = "booking_attempts"

    __table_args__ = (
        CheckConstraint(
            "latency_ms >= 0",
            name="ck_booking_attempts_latency_nonnegative",
        ),
        CheckConstraint(
            "http_status BETWEEN 100 AND 599",
            name="ck_booking_attempts_http_status",
        ),
        CheckConstraint(
            "completed_at >= requested_at",
            name="ck_booking_attempts_time_order",
        ),
        Index(
            "ix_booking_attempts_experiment_requested",
            "experiment_run_id",
            "requested_at",
        ),
        Index(
            "ix_booking_attempts_outcome_requested",
            "outcome",
            "requested_at",
        ),
        Index(
            "ix_booking_attempts_seat_requested",
            "seat_id",
            "requested_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    idempotency_key: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )
    experiment_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # These IDs intentionally do not use foreign keys. This allows the
    # system to record failed attempts containing invalid or missing IDs.
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    show_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    seat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    latency_ms: Mapped[Decimal] = mapped_column(
        Numeric(12, 3),
        nullable=False,
    )
    outcome: Mapped[AttemptOutcome] = mapped_column(
        SqlEnum(
            AttemptOutcome,
            name="attempt_outcome",
        ),
        nullable=False,
    )
    http_status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )