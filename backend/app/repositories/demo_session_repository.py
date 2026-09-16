from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    DemoSession,
    Seat,
    Show,
)
from backend.app.models.enums import (
    DemoSessionStatus,
)


OPEN_DEMO_SESSION_STATUSES = (
    DemoSessionStatus.WAITING_FOR_SELECTION,
    DemoSessionStatus.READY,
    DemoSessionStatus.RUNNING,
)


def add_demo_session(
    session: Session,
    demo_session: DemoSession,
) -> DemoSession:
    """Add one demo session to the current transaction."""

    session.add(demo_session)

    return demo_session


def get_demo_session(
    session: Session,
    demo_session_id: UUID,
) -> DemoSession | None:
    """Return one demo session by primary key."""

    return session.get(
        DemoSession,
        demo_session_id,
    )


def get_demo_session_for_update(
    session: Session,
    demo_session_id: UUID,
) -> DemoSession | None:
    """Lock and return one demo session."""

    statement = (
        select(DemoSession)
        .where(
            DemoSession.id == demo_session_id
        )
        .with_for_update()
    )

    return session.scalar(statement)


def get_open_demo_session(
    session: Session,
    *,
    show_id: int,
    for_update: bool = False,
) -> DemoSession | None:
    """Return the newest open session for one show."""

    statement = (
        select(DemoSession)
        .where(
            DemoSession.show_id == show_id,
            DemoSession.status.in_(
                OPEN_DEMO_SESSION_STATUSES
            ),
        )
        .order_by(
            DemoSession.created_at.desc(),
            DemoSession.id.desc(),
        )
        .limit(1)
    )

    if for_update:
        statement = statement.with_for_update()

    return session.scalar(statement)


def get_demo_show(
    session: Session,
    show_id: int,
) -> Show | None:
    """Return one show by primary key."""

    return session.get(
        Show,
        show_id,
    )


def get_demo_seats_for_update(
    session: Session,
    seat_ids: list[int],
) -> list[Seat]:
    """Lock and return selected seats in ID order."""

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


def get_expired_open_demo_sessions(
    session: Session,
    *,
    current_time: datetime,
) -> list[DemoSession]:
    """Lock and return expired open sessions."""

    statement = (
        select(DemoSession)
        .where(
            DemoSession.status.in_(
                OPEN_DEMO_SESSION_STATUSES
            ),
            DemoSession.expires_at <= current_time,
        )
        .order_by(
            DemoSession.created_at,
            DemoSession.id,
        )
        .with_for_update()
    )

    return list(
        session.scalars(statement).all()
    )