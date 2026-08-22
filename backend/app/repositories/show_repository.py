from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Seat, Show
from backend.app.models.enums import SeatState


def list_shows(session: Session) -> list[Show]:
    """Return all shows in chronological order."""

    statement = select(Show).order_by(
        Show.show_time,
        Show.id,
    )

    return list(session.scalars(statement).all())


def get_show_by_id(
    session: Session,
    show_id: int,
) -> Show | None:
    """Return one show or None when it does not exist."""

    return session.get(Show, show_id)


def list_seats_for_show(
    session: Session,
    show_id: int,
    seat_state: SeatState | None = None,
) -> list[Seat]:
    """Return seats belonging to one show."""

    statement = select(Seat).where(
        Seat.show_id == show_id
    )

    if seat_state is not None:
        statement = statement.where(
            Seat.state == seat_state
        )

    statement = statement.order_by(Seat.id)

    return list(session.scalars(statement).all())