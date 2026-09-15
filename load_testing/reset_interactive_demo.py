import argparse
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    Booking,
    ExperimentRun,
    Seat,
    Show,
)
from backend.app.models.enums import (
    BookingStatus,
    ExperimentStatus,
    SeatState,
)


class InteractiveDemoResetError(Exception):
    """Expected failure while resetting an interactive demo seat."""


def _normalize_seat_number(value: str) -> str:
    """Normalize and validate a supplied seat number."""

    normalized_value = value.strip().upper()

    if not normalized_value:
        raise argparse.ArgumentTypeError(
            "The seat number must not be empty."
        )

    if len(normalized_value) > 10:
        raise argparse.ArgumentTypeError(
            "The seat number must contain at most 10 characters."
        )

    return normalized_value


def _load_experiment_show(
    session: Session,
    show_id: int,
) -> Show:
    """Load and validate the requested experiment show."""

    show = session.get(
        Show,
        show_id,
    )

    if show is None:
        raise InteractiveDemoResetError(
            f"Show {show_id} was not found."
        )

    if not show.is_experiment_show:
        raise InteractiveDemoResetError(
            f"Show {show_id} is not an experiment show."
        )

    return show


def _ensure_no_running_experiment(
    session: Session,
    show_id: int,
) -> None:
    """Prevent seat mutation during a running experiment."""

    statement = (
        select(ExperimentRun)
        .where(
            ExperimentRun.show_id == show_id,
            ExperimentRun.status
            == ExperimentStatus.RUNNING,
        )
        .order_by(
            ExperimentRun.started_at.desc(),
            ExperimentRun.id.desc(),
        )
        .limit(1)
        .with_for_update()
    )

    running_experiment = session.scalar(statement)

    if running_experiment is not None:
        raise InteractiveDemoResetError(
            "The seat cannot be reset while experiment "
            f"{running_experiment.id} is running."
        )


def _load_seat_for_update(
    session: Session,
    *,
    show_id: int,
    seat_number: str,
) -> Seat:
    """Load and lock the selected experiment seat."""

    statement = (
        select(Seat)
        .where(
            Seat.show_id == show_id,
            Seat.seat_number == seat_number,
        )
        .limit(1)
        .with_for_update()
    )

    seat = session.scalar(statement)

    if seat is None:
        raise InteractiveDemoResetError(
            f"Seat {seat_number} was not found "
            f"for show {show_id}."
        )

    return seat


def _load_confirmed_bookings_for_update(
    session: Session,
    *,
    show_id: int,
    seat_id: int,
) -> list[Booking]:
    """Load confirmed experiment bookings for the seat."""

    statement = (
        select(Booking)
        .where(
            Booking.show_id == show_id,
            Booking.seat_id == seat_id,
            Booking.experiment_run_id.is_not(None),
            Booking.status == BookingStatus.CONFIRMED,
        )
        .order_by(Booking.created_at)
        .with_for_update()
    )

    return list(
        session.scalars(statement).all()
    )


def reset_interactive_demo_seat(
    session: Session,
    *,
    show_id: int,
    seat_number: str,
    apply_changes: bool,
) -> tuple[int, int]:
    """Reset one experiment seat and cancel its confirmed bookings."""

    _load_experiment_show(
        session,
        show_id,
    )

    _ensure_no_running_experiment(
        session,
        show_id,
    )

    seat = _load_seat_for_update(
        session,
        show_id=show_id,
        seat_number=seat_number,
    )

    confirmed_bookings = (
        _load_confirmed_bookings_for_update(
            session,
            show_id=show_id,
            seat_id=seat.id,
        )
    )

    print("Interactive demo reset plan:")
    print(f"  Show: {show_id}")
    print(
        f"  Seat: {seat.seat_number} "
        f"(database ID {seat.id})"
    )
    print(f"  Current seat state: {seat.state.value}")
    print(
        "  Confirmed experiment bookings to cancel: "
        f"{len(confirmed_bookings)}"
    )

    if not apply_changes:
        session.rollback()

        print()
        print(
            "Dry run only. No database records were changed."
        )
        print(
            "Run the command again with --apply "
            "to perform this reset."
        )

        return seat.id, len(confirmed_bookings)

    reset_at = datetime.now(UTC)

    for booking in confirmed_bookings:
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = reset_at

    seat.state = SeatState.AVAILABLE
    seat.hold_expires_at = None
    seat.version += 1

    session.commit()

    print()
    print("Interactive demo seat reset completed.")
    print(
        f"  Cancelled bookings: "
        f"{len(confirmed_bookings)}"
    )
    print(f"  Seat state: {SeatState.AVAILABLE.value}")

    return seat.id, len(confirmed_bookings)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""

    parser = argparse.ArgumentParser(
        description=(
            "Safely reset one experiment-show seat after "
            "an interactive contention demonstration."
        )
    )

    parser.add_argument(
        "--seat-number",
        required=True,
        type=_normalize_seat_number,
        help=(
            "Experiment-show seat to reset, for example D9."
        ),
    )
    parser.add_argument(
        "--show-id",
        type=int,
        default=2,
        help="Experiment show ID. Default: 2.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the reset. Without this option, the "
            "command performs a dry run."
        ),
    )

    return parser


def main() -> int:
    """Run the interactive demo reset command."""

    parser = _build_argument_parser()
    arguments = parser.parse_args()

    if arguments.show_id < 1:
        parser.error("--show-id must be a positive integer.")

    session = SessionLocal()

    try:
        reset_interactive_demo_seat(
            session,
            show_id=arguments.show_id,
            seat_number=arguments.seat_number,
            apply_changes=arguments.apply,
        )
    except InteractiveDemoResetError as error:
        session.rollback()
        print(
            f"Interactive demo reset error: {error}",
            file=sys.stderr,
        )
        return 1
    except SQLAlchemyError:
        session.rollback()
        print(
            "Interactive demo reset error: "
            "an unexpected database error occurred.",
            file=sys.stderr,
        )
        return 1
    finally:
        session.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())