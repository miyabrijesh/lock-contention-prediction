from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import Seat, SeatState, Show, SyntheticUser


SYNTHETIC_USER_COUNT = 200

NORMAL_SHOW_NAME = "Normal Booking Demonstration"
EXPERIMENT_SHOW_NAME = "Lock Contention Experiment"

VENUE_NAME = "College Demo Theatre"

NORMAL_SHOW_TIME = datetime(
    2030,
    1,
    15,
    18,
    0,
    tzinfo=timezone.utc,
)

EXPERIMENT_SHOW_TIME = datetime(
    2030,
    1,
    16,
    18,
    0,
    tzinfo=timezone.utc,
)


def seed_synthetic_users(
    session: Session,
    count: int = SYNTHETIC_USER_COUNT,
) -> None:
    """Create deterministic synthetic users without duplicates."""

    required_keys = {
        f"synthetic-user-{number:04d}"
        for number in range(1, count + 1)
    }

    existing_keys = set(
        session.scalars(
            select(SyntheticUser.external_key).where(
                SyntheticUser.external_key.in_(required_keys)
            )
        ).all()
    )

    missing_keys = sorted(required_keys - existing_keys)

    session.add_all(
        SyntheticUser(external_key=external_key)
        for external_key in missing_keys
    )


def get_or_create_show(
    session: Session,
    *,
    name: str,
    show_time: datetime,
    is_experiment_show: bool,
) -> Show:
    """Return an existing seeded show or create it."""

    existing_show = session.scalar(
        select(Show)
        .where(
            Show.name == name,
            Show.is_experiment_show.is_(is_experiment_show),
        )
        .limit(1)
    )

    if existing_show is not None:
        return existing_show

    show = Show(
        name=name,
        venue=VENUE_NAME,
        show_time=show_time,
        is_experiment_show=is_experiment_show,
    )

    session.add(show)
    session.flush()

    return show


def seed_seats(
    session: Session,
    show: Show,
) -> None:
    """Create seats A1 through J10 for one show."""

    required_seat_numbers = {
        f"{row}{number}"
        for row in "ABCDEFGHIJ"
        for number in range(1, 11)
    }

    existing_seat_numbers = set(
        session.scalars(
            select(Seat.seat_number).where(
                Seat.show_id == show.id
            )
        ).all()
    )

    missing_seat_numbers = sorted(
        required_seat_numbers - existing_seat_numbers
    )

    session.add_all(
        Seat(
            show_id=show.id,
            seat_number=seat_number,
            state=SeatState.AVAILABLE,
            version=0,
        )
        for seat_number in missing_seat_numbers
    )


def main() -> None:
    """Create all required initial demonstration data."""

    with SessionLocal.begin() as session:
        seed_synthetic_users(session)

        normal_show = get_or_create_show(
            session,
            name=NORMAL_SHOW_NAME,
            show_time=NORMAL_SHOW_TIME,
            is_experiment_show=False,
        )

        experiment_show = get_or_create_show(
            session,
            name=EXPERIMENT_SHOW_NAME,
            show_time=EXPERIMENT_SHOW_TIME,
            is_experiment_show=True,
        )

        seed_seats(session, normal_show)
        seed_seats(session, experiment_show)

        session.flush()

        synthetic_user_count = session.scalar(
            select(func.count()).select_from(SyntheticUser)
        )

        normal_seat_count = session.scalar(
            select(func.count())
            .select_from(Seat)
            .where(Seat.show_id == normal_show.id)
        )

        experiment_seat_count = session.scalar(
            select(func.count())
            .select_from(Seat)
            .where(Seat.show_id == experiment_show.id)
        )

        normal_show_id = normal_show.id
        experiment_show_id = experiment_show.id

    print("Seed completed successfully.")
    print(f"Synthetic users: {synthetic_user_count}")
    print(
        f"Normal show: id={normal_show_id}, "
        f"seats={normal_seat_count}"
    )
    print(
        f"Experiment show: id={experiment_show_id}, "
        f"seats={experiment_seat_count}"
    )


if __name__ == "__main__":
    main()