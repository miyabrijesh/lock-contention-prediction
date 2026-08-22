from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Booking,
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)


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