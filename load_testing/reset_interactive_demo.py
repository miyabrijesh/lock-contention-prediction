import argparse
import sys
from collections import Counter
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
    """Expected failure while resetting interactive demo seats."""


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
            "Seats cannot be reset while experiment "
            f"{running_experiment.id} is running."
        )


def _load_seats_for_update(
    session: Session,
    *,
    show_id: int,
    seat_numbers: list[str],
) -> list[Seat]:
    """Load and lock all requested seats in deterministic order."""

    statement = (
        select(Seat)
        .where(
            Seat.show_id == show_id,
            Seat.seat_number.in_(seat_numbers),
        )
        .order_by(Seat.id)
        .with_for_update()
    )

    seats = list(
        session.scalars(statement).all()
    )

    found_seat_numbers = {
        seat.seat_number
        for seat in seats
    }
    missing_seat_numbers = [
        seat_number
        for seat_number in seat_numbers
        if seat_number not in found_seat_numbers
    ]

    if missing_seat_numbers:
        missing_text = ", ".join(
            missing_seat_numbers
        )
        raise InteractiveDemoResetError(
            f"Seats {missing_text} were not found "
            f"for show {show_id}."
        )

    return seats


def _load_confirmed_bookings_for_update(
    session: Session,
    *,
    show_id: int,
    seat_ids: list[int],
) -> list[Booking]:
    """Lock confirmed experiment bookings for all seats."""

    statement = (
        select(Booking)
        .where(
            Booking.show_id == show_id,
            Booking.seat_id.in_(seat_ids),
            Booking.experiment_run_id.is_not(None),
            Booking.status == BookingStatus.CONFIRMED,
        )
        .order_by(
            Booking.seat_id,
            Booking.created_at,
        )
        .with_for_update()
    )

    return list(
        session.scalars(statement).all()
    )


def reset_interactive_demo_seats(
    session: Session,
    *,
    show_id: int,
    seat_numbers: list[str],
    apply_changes: bool,
) -> tuple[list[int], int]:
    """Atomically reset multiple experiment seats."""

    if not seat_numbers:
        raise InteractiveDemoResetError(
            "At least one seat number is required."
        )

    normalized_seat_numbers = [
        _normalize_seat_number(seat_number)
        for seat_number in seat_numbers
    ]

    duplicate_seat_numbers = sorted(
        seat_number
        for seat_number, count
        in Counter(normalized_seat_numbers).items()
        if count > 1
    )

    if duplicate_seat_numbers:
        duplicate_text = ", ".join(
            duplicate_seat_numbers
        )
        raise InteractiveDemoResetError(
            "Seat numbers must be unique. "
            f"Repeated: {duplicate_text}."
        )

    _load_experiment_show(
        session,
        show_id,
    )

    _ensure_no_running_experiment(
        session,
        show_id,
    )

    seats = _load_seats_for_update(
        session,
        show_id=show_id,
        seat_numbers=normalized_seat_numbers,
    )

    seats_by_number = {
        seat.seat_number: seat
        for seat in seats
    }
    ordered_seats = [
        seats_by_number[seat_number]
        for seat_number in normalized_seat_numbers
    ]

    confirmed_bookings = (
        _load_confirmed_bookings_for_update(
            session,
            show_id=show_id,
            seat_ids=[
                seat.id
                for seat in seats
            ],
        )
    )

    booking_counts = Counter(
        booking.seat_id
        for booking in confirmed_bookings
    )

    print("Interactive demo reset plan:")
    print(f"  Show: {show_id}")
    print(f"  Seats: {len(ordered_seats)}")

    for seat in ordered_seats:
        print(
            f"  - {seat.seat_number} "
            f"(database ID {seat.id})"
        )
        print(
            f"    Current state: {seat.state.value}"
        )
        print(
            "    Confirmed experiment bookings "
            f"to cancel: {booking_counts[seat.id]}"
        )

    print(
        "  Total confirmed experiment bookings "
        f"to cancel: {len(confirmed_bookings)}"
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

        return (
            [
                seat.id
                for seat in ordered_seats
            ],
            len(confirmed_bookings),
        )

    reset_at = datetime.now(UTC)

    for booking in confirmed_bookings:
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = reset_at

    for seat in ordered_seats:
        seat.state = SeatState.AVAILABLE
        seat.hold_expires_at = None
        seat.version += 1

    session.commit()

    print()
    print("Interactive demo seat reset completed.")
    print(
        f"  Seats reset: {len(ordered_seats)}"
    )
    print(
        "  Cancelled bookings: "
        f"{len(confirmed_bookings)}"
    )
    print(f"  Seat state: {SeatState.AVAILABLE.value}")

    return (
        [
            seat.id
            for seat in ordered_seats
        ],
        len(confirmed_bookings),
    )


def reset_interactive_demo_seat(
    session: Session,
    *,
    show_id: int,
    seat_number: str,
    apply_changes: bool,
) -> tuple[int, int]:
    """Reset one seat while preserving the original interface."""

    seat_ids, cancelled_booking_count = (
        reset_interactive_demo_seats(
            session,
            show_id=show_id,
            seat_numbers=[seat_number],
            apply_changes=apply_changes,
        )
    )

    return (
        seat_ids[0],
        cancelled_booking_count,
    )


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""

    parser = argparse.ArgumentParser(
        description=(
            "Safely and atomically reset one or more "
            "experiment-show seats after an interactive "
            "contention demonstration."
        )
    )

    parser.add_argument(
        "--seat-number",
        required=True,
        type=_normalize_seat_number,
        action="append",
        help=(
            "Experiment-show seat to reset. Repeat this "
            "option for multiple seats, for example: "
            "--seat-number D7 --seat-number D8."
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

    if len(arguments.seat_number) != len(
        set(arguments.seat_number)
    ):
        parser.error(
            "--seat-number values must be unique."
        )

    session = SessionLocal()

    try:
        reset_interactive_demo_seats(
            session,
            show_id=arguments.show_id,
            seat_numbers=arguments.seat_number,
            apply_changes=arguments.apply,
        )
    except (
        InteractiveDemoResetError,
        argparse.ArgumentTypeError,
    ) as error:
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
