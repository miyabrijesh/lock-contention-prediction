from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    Booking,
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    BookingStatus,
    ExperimentStatus,
    SeatState,
)
from load_testing.reset_interactive_demo import (
    InteractiveDemoResetError,
    reset_interactive_demo_seats,
)


def _create_reset_test_data(
    session: Session,
    *,
    seat_numbers: list[str],
    experiment_status: ExperimentStatus = (
        ExperimentStatus.COMPLETED
    ),
    create_confirmed_bookings: bool = True,
) -> tuple[int, list[int], list[UUID]]:
    """Create isolated experiment seats and bookings."""

    current_time = datetime.now(UTC)

    show = Show(
        name=f"Reset test {uuid4()}",
        venue="Reset Test Theatre",
        show_time=current_time + timedelta(days=1),
        is_experiment_show=True,
    )
    user = SyntheticUser(
        external_key=f"reset-test-{uuid4()}",
    )

    session.add_all([
        show,
        user,
    ])
    session.flush()

    seats = [
        Seat(
            show_id=show.id,
            seat_number=seat_number,
            state=SeatState.BOOKED,
        )
        for seat_number in seat_numbers
    ]

    session.add_all(seats)
    session.flush()

    experiment = ExperimentRun(
        name=f"Reset experiment {uuid4()}",
        show_id=show.id,
        status=experiment_status,
        user_count=2,
        spawn_rate_per_sec=Decimal("2.00"),
        duration_sec=60,
        traffic_pattern="STEADY",
        demand_profile="CUSTOM",
        hotspot_ratio=Decimal("1.0000"),
        hotspot_seat_ids=[
            seat.id
            for seat in seats
        ],
        sample_interval_sec=2,
        simulated_work_ms=600,
        booking_mode="NORMAL",
        random_seed=42,
        code_version=None,
        started_at=current_time,
        ended_at=(
            None
            if experiment_status
            == ExperimentStatus.RUNNING
            else current_time + timedelta(seconds=60)
        ),
        created_by="reset-test",
    )

    session.add(experiment)
    session.flush()

    bookings: list[Booking] = []

    if create_confirmed_bookings:
        bookings = [
            Booking(
                idempotency_key=uuid4(),
                user_id=user.id,
                show_id=show.id,
                seat_id=seat.id,
                experiment_run_id=experiment.id,
                status=BookingStatus.CONFIRMED,
            )
            for seat in seats
        ]

        session.add_all(bookings)
        session.flush()

    show_id = show.id
    seat_ids = [
        seat.id
        for seat in seats
    ]
    booking_ids = [
        booking.id
        for booking in bookings
    ]

    session.commit()

    return (
        show_id,
        seat_ids,
        booking_ids,
    )


def test_multi_seat_dry_run_changes_nothing(
    db_session: Session,
) -> None:
    """Keep all seat and booking records unchanged in dry-run mode."""

    show_id, seat_ids, booking_ids = (
        _create_reset_test_data(
            db_session,
            seat_numbers=["R1", "R2", "R3"],
        )
    )

    returned_seat_ids, cancelled_count = (
        reset_interactive_demo_seats(
            db_session,
            show_id=show_id,
            seat_numbers=["R1", "R2", "R3"],
            apply_changes=False,
        )
    )

    assert returned_seat_ids == seat_ids
    assert cancelled_count == 3

    for seat_id in seat_ids:
        seat = db_session.get(Seat, seat_id)

        assert seat is not None
        assert seat.state == SeatState.BOOKED

    for booking_id in booking_ids:
        booking = db_session.get(
            Booking,
            booking_id,
        )

        assert booking is not None
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.cancelled_at is None


def test_multi_seat_apply_resets_every_seat(
    db_session: Session,
) -> None:
    """Reset all seats and cancel their bookings atomically."""

    show_id, seat_ids, booking_ids = (
        _create_reset_test_data(
            db_session,
            seat_numbers=["S1", "S2", "S3"],
        )
    )

    returned_seat_ids, cancelled_count = (
        reset_interactive_demo_seats(
            db_session,
            show_id=show_id,
            seat_numbers=["S1", "S2", "S3"],
            apply_changes=True,
        )
    )

    assert returned_seat_ids == seat_ids
    assert cancelled_count == 3

    for seat_id in seat_ids:
        seat = db_session.get(Seat, seat_id)

        assert seat is not None
        assert seat.state == SeatState.AVAILABLE
        assert seat.hold_expires_at is None
        assert seat.version == 1

    for booking_id in booking_ids:
        booking = db_session.get(
            Booking,
            booking_id,
        )

        assert booking is not None
        assert booking.status == BookingStatus.CANCELLED
        assert booking.cancelled_at is not None


def test_missing_seat_prevents_every_change(
    db_session: Session,
) -> None:
    """Reject the whole batch when any requested seat is absent."""

    show_id, seat_ids, booking_ids = (
        _create_reset_test_data(
            db_session,
            seat_numbers=["T1", "T2"],
        )
    )

    with pytest.raises(
        InteractiveDemoResetError,
        match="MISSING",
    ):
        reset_interactive_demo_seats(
            db_session,
            show_id=show_id,
            seat_numbers=[
                "T1",
                "T2",
                "MISSING",
            ],
            apply_changes=True,
        )

    db_session.rollback()

    for seat_id in seat_ids:
        seat = db_session.get(Seat, seat_id)

        assert seat is not None
        assert seat.state == SeatState.BOOKED
        assert seat.version == 0

    for booking_id in booking_ids:
        booking = db_session.get(
            Booking,
            booking_id,
        )

        assert booking is not None
        assert booking.status == BookingStatus.CONFIRMED
        assert booking.cancelled_at is None


def test_duplicate_seat_numbers_are_rejected(
    db_session: Session,
) -> None:
    """Reject duplicate seat identifiers before changing data."""

    show_id, seat_ids, booking_ids = (
        _create_reset_test_data(
            db_session,
            seat_numbers=["U1"],
        )
    )

    with pytest.raises(
        InteractiveDemoResetError,
        match="Repeated: U1",
    ):
        reset_interactive_demo_seats(
            db_session,
            show_id=show_id,
            seat_numbers=["U1", "u1"],
            apply_changes=True,
        )

    db_session.rollback()

    seat = db_session.get(
        Seat,
        seat_ids[0],
    )
    booking = db_session.get(
        Booking,
        booking_ids[0],
    )

    assert seat is not None
    assert seat.state == SeatState.BOOKED
    assert booking is not None
    assert booking.status == BookingStatus.CONFIRMED


def test_running_experiment_prevents_reset(
    db_session: Session,
) -> None:
    """Prevent reset while an experiment is still running."""

    show_id, seat_ids, booking_ids = (
        _create_reset_test_data(
            db_session,
            seat_numbers=["V1", "V2"],
            experiment_status=ExperimentStatus.RUNNING,
        )
    )

    with pytest.raises(
        InteractiveDemoResetError,
        match="cannot be reset while experiment",
    ):
        reset_interactive_demo_seats(
            db_session,
            show_id=show_id,
            seat_numbers=["V1", "V2"],
            apply_changes=True,
        )

    db_session.rollback()

    for seat_id in seat_ids:
        seat = db_session.get(Seat, seat_id)

        assert seat is not None
        assert seat.state == SeatState.BOOKED

    for booking_id in booking_ids:
        booking = db_session.get(
            Booking,
            booking_id,
        )

        assert booking is not None
        assert booking.status == BookingStatus.CONFIRMED
