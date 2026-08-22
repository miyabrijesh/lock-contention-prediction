from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    Booking,
    BookingAttempt,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    AttemptOutcome,
    BookingStatus,
    SeatState,
)


def get_normal_booking_data(
    session: Session,
) -> tuple[SyntheticUser, Show, Seat]:
    """Return valid seeded data for one normal booking."""

    synthetic_user = session.scalar(
        select(SyntheticUser)
        .order_by(SyntheticUser.id)
        .limit(1)
    )

    show = session.scalar(
        select(Show)
        .where(Show.is_experiment_show.is_(False))
        .order_by(Show.id)
        .limit(1)
    )

    assert synthetic_user is not None
    assert show is not None

    seat = session.scalar(
        select(Seat)
        .where(
            Seat.show_id == show.id,
            Seat.state == SeatState.AVAILABLE,
        )
        .order_by(Seat.id)
        .limit(1)
    )

    assert seat is not None

    return synthetic_user, show, seat


def booking_headers(
    idempotency_key: str,
) -> dict[str, str]:
    """Create authenticated booking request headers."""

    return {
        "X-Internal-Token": settings.internal_api_token,
        "Idempotency-Key": idempotency_key,
    }


def test_successful_booking_records_attempt(
    client: TestClient,
    db_session: Session,
) -> None:
    """A successful booking must create success telemetry."""

    synthetic_user, show, seat = get_normal_booking_data(
        db_session
    )
    original_seat_version = seat.version
    idempotency_key = uuid4()

    response = client.post(
        "/api/v1/bookings",
        headers=booking_headers(str(idempotency_key)),
        json={
            "user_id": synthetic_user.id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
        },
    )

    assert response.status_code == 201

    response_body = response.json()

    assert response_body["idempotency_key"] == str(
        idempotency_key
    )
    assert response_body["status"] == BookingStatus.CONFIRMED
    assert response_body["user_id"] == synthetic_user.id
    assert response_body["show_id"] == show.id
    assert response_body["seat_id"] == seat.id

    db_session.expire_all()

    booking = db_session.scalar(
        select(Booking).where(
            Booking.idempotency_key == idempotency_key
        )
    )
    attempt = db_session.scalar(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == idempotency_key
        )
    )
    updated_seat = db_session.get(Seat, seat.id)

    assert booking is not None
    assert booking.status == BookingStatus.CONFIRMED

    assert attempt is not None
    assert attempt.outcome == AttemptOutcome.SUCCESS
    assert attempt.http_status == 201
    assert attempt.error_code is None
    assert attempt.latency_ms >= Decimal("0")
    assert attempt.completed_at >= attempt.requested_at

    assert updated_seat is not None
    assert updated_seat.state == SeatState.BOOKED
    assert updated_seat.version == original_seat_version + 1


def test_failed_validation_records_attempt(
    client: TestClient,
    db_session: Session,
) -> None:
    """An invalid user must produce retained failure telemetry."""

    _, show, seat = get_normal_booking_data(
        db_session
    )
    idempotency_key = uuid4()
    invalid_user_id = 999999999

    response = client.post(
        "/api/v1/bookings",
        headers=booking_headers(str(idempotency_key)),
        json={
            "user_id": invalid_user_id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "USER_NOT_FOUND"

    db_session.expire_all()

    booking = db_session.scalar(
        select(Booking).where(
            Booking.idempotency_key == idempotency_key
        )
    )
    attempt = db_session.scalar(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == idempotency_key
        )
    )
    unchanged_seat = db_session.get(Seat, seat.id)

    assert booking is None

    assert attempt is not None
    assert attempt.user_id == invalid_user_id
    assert attempt.outcome == AttemptOutcome.VALIDATION_ERROR
    assert attempt.http_status == 404
    assert attempt.error_code == "USER_NOT_FOUND"
    assert attempt.latency_ms >= Decimal("0")
    assert attempt.completed_at >= attempt.requested_at

    assert unchanged_seat is not None
    assert unchanged_seat.state == SeatState.AVAILABLE