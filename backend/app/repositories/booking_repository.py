from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Booking,
    BookingAttempt,
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import AttemptOutcome


def get_booking_by_idempotency_key(
    session: Session,
    idempotency_key: UUID,
) -> Booking | None:
    """Find a booking using its unique idempotency key."""

    statement = select(Booking).where(
        Booking.idempotency_key == idempotency_key
    )

    return session.scalar(statement)


def get_synthetic_user(
    session: Session,
    user_id: int,
) -> SyntheticUser | None:
    """Find a synthetic user by primary key."""

    return session.get(SyntheticUser, user_id)


def get_show(
    session: Session,
    show_id: int,
) -> Show | None:
    """Find a show by primary key."""

    return session.get(Show, show_id)


def get_experiment_run(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun | None:
    """Find an experiment run by primary key."""

    return session.get(
        ExperimentRun,
        experiment_run_id,
    )


def get_seat_for_update(
    session: Session,
    seat_id: int,
) -> Seat | None:
    """Lock and return one seat for a booking transaction."""

    statement = (
        select(Seat)
        .where(Seat.id == seat_id)
        .with_for_update()
    )

    return session.scalar(statement)



def get_seats_for_update(
    session: Session,
    seat_ids: list[int],
) -> list[Seat]:
    """Lock selected seats in deterministic database-ID order."""

    if not seat_ids:
        return []

    statement = (
        select(Seat)
        .where(
            Seat.id.in_(seat_ids)
        )
        .order_by(Seat.id)
        .with_for_update()
    )

    return list(
        session.scalars(statement).all()
    )


def add_booking_attempt(
    session: Session,
    *,
    idempotency_key: UUID,
    experiment_run_id: UUID | None,
    user_id: int,
    show_id: int,
    seat_id: int,
    requested_at: datetime,
    completed_at: datetime,
    latency_ms: Decimal,
    outcome: AttemptOutcome,
    http_status: int,
    error_code: str | None,
) -> BookingAttempt:
    """Add one booking-attempt telemetry record to the session."""

    attempt = BookingAttempt(
        idempotency_key=idempotency_key,
        experiment_run_id=experiment_run_id,
        user_id=user_id,
        show_id=show_id,
        seat_id=seat_id,
        requested_at=requested_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        outcome=outcome,
        http_status=http_status,
        error_code=error_code,
    )

    session.add(attempt)

    return attempt
