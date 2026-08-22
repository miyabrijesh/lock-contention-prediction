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
def get_another_available_seat(
    session: Session,
    *,
    show_id: int,
    excluded_seat_id: int,
) -> Seat:
    """Return another available seat belonging to the same show."""

    seat = session.scalar(
        select(Seat)
        .where(
            Seat.show_id == show_id,
            Seat.id != excluded_seat_id,
            Seat.state == SeatState.AVAILABLE,
        )
        .order_by(Seat.id)
        .limit(1)
    )

    assert seat is not None

    return seat


def test_identical_idempotent_replay_returns_existing_booking(
    client: TestClient,
    db_session: Session,
) -> None:
    """Repeating an identical request must not create another booking."""

    synthetic_user, show, seat = get_normal_booking_data(
        db_session
    )
    original_seat_version = seat.version
    idempotency_key = uuid4()

    request_headers = booking_headers(
        str(idempotency_key)
    )
    request_body = {
        "user_id": synthetic_user.id,
        "show_id": show.id,
        "seat_id": seat.id,
        "experiment_run_id": None,
    }

    first_response = client.post(
        "/api/v1/bookings",
        headers=request_headers,
        json=request_body,
    )
    second_response = client.post(
        "/api/v1/bookings",
        headers=request_headers,
        json=request_body,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 200

    assert (
        second_response.headers["Idempotent-Replayed"]
        == "true"
    )
    assert (
        first_response.json()["id"]
        == second_response.json()["id"]
    )

    db_session.expire_all()

    bookings = db_session.scalars(
        select(Booking).where(
            Booking.idempotency_key == idempotency_key
        )
    ).all()
    attempts = db_session.scalars(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == idempotency_key
        )
    ).all()
    updated_seat = db_session.get(Seat, seat.id)

    assert len(bookings) == 1
    assert len(attempts) == 2

    assert sorted(
        attempt.http_status
        for attempt in attempts
    ) == [200, 201]

    assert all(
        attempt.outcome == AttemptOutcome.SUCCESS
        for attempt in attempts
    )
    assert all(
        attempt.error_code is None
        for attempt in attempts
    )

    assert updated_seat is not None
    assert updated_seat.state == SeatState.BOOKED
    assert updated_seat.version == original_seat_version + 1


def test_reused_idempotency_key_with_new_payload_conflicts(
    client: TestClient,
    db_session: Session,
) -> None:
    """One idempotency key cannot represent two different requests."""

    synthetic_user, show, first_seat = (
        get_normal_booking_data(db_session)
    )
    second_seat = get_another_available_seat(
        db_session,
        show_id=show.id,
        excluded_seat_id=first_seat.id,
    )
    idempotency_key = uuid4()
    request_headers = booking_headers(
        str(idempotency_key)
    )

    first_response = client.post(
        "/api/v1/bookings",
        headers=request_headers,
        json={
            "user_id": synthetic_user.id,
            "show_id": show.id,
            "seat_id": first_seat.id,
            "experiment_run_id": None,
        },
    )
    conflicting_response = client.post(
        "/api/v1/bookings",
        headers=request_headers,
        json={
            "user_id": synthetic_user.id,
            "show_id": show.id,
            "seat_id": second_seat.id,
            "experiment_run_id": None,
        },
    )

    assert first_response.status_code == 201
    assert conflicting_response.status_code == 409
    assert (
        conflicting_response.json()["detail"]["code"]
        == "IDEMPOTENCY_KEY_REUSED"
    )

    db_session.expire_all()

    bookings = db_session.scalars(
        select(Booking).where(
            Booking.idempotency_key == idempotency_key
        )
    ).all()
    attempts = db_session.scalars(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == idempotency_key
        )
    ).all()
    unchanged_second_seat = db_session.get(
        Seat,
        second_seat.id,
    )

    assert len(bookings) == 1
    assert bookings[0].seat_id == first_seat.id
    assert len(attempts) == 2

    error_codes = {
        attempt.error_code
        for attempt in attempts
    }

    assert error_codes == {
        None,
        "IDEMPOTENCY_KEY_REUSED",
    }

    assert unchanged_second_seat is not None
    assert (
        unchanged_second_seat.state
        == SeatState.AVAILABLE
    )


def test_second_booking_for_same_seat_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """A confirmed seat cannot receive a second normal booking."""

    first_user, show, seat = get_normal_booking_data(
        db_session
    )
    original_seat_version = seat.version

    second_user = db_session.scalar(
        select(SyntheticUser)
        .where(SyntheticUser.id != first_user.id)
        .order_by(SyntheticUser.id)
        .limit(1)
    )

    assert second_user is not None

    first_key = uuid4()
    second_key = uuid4()

    first_response = client.post(
        "/api/v1/bookings",
        headers=booking_headers(str(first_key)),
        json={
            "user_id": first_user.id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
        },
    )
    second_response = client.post(
        "/api/v1/bookings",
        headers=booking_headers(str(second_key)),
        json={
            "user_id": second_user.id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
        },
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert (
        second_response.json()["detail"]["code"]
        == "SEAT_UNAVAILABLE"
    )

    db_session.expire_all()

    bookings = db_session.scalars(
        select(Booking).where(
            Booking.seat_id == seat.id,
            Booking.status == BookingStatus.CONFIRMED,
        )
    ).all()
    rejected_attempt = db_session.scalar(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == second_key
        )
    )
    updated_seat = db_session.get(Seat, seat.id)

    assert len(bookings) == 1
    assert bookings[0].user_id == first_user.id

    assert rejected_attempt is not None
    assert (
        rejected_attempt.outcome
        == AttemptOutcome.SEAT_UNAVAILABLE
    )
    assert rejected_attempt.http_status == 409
    assert (
        rejected_attempt.error_code
        == "SEAT_UNAVAILABLE"
    )

    assert updated_seat is not None
    assert updated_seat.state == SeatState.BOOKED
    assert updated_seat.version == original_seat_version + 1


def test_missing_token_does_not_reach_booking_service(
    client: TestClient,
    db_session: Session,
) -> None:
    """Authentication failure must not create booking telemetry."""

    synthetic_user, show, seat = get_normal_booking_data(
        db_session
    )
    idempotency_key = uuid4()

    response = client.post(
        "/api/v1/bookings",
        headers={
            "Idempotency-Key": str(idempotency_key),
        },
        json={
            "user_id": synthetic_user.id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
        },
    )

    assert response.status_code == 401
    assert (
        response.json()["detail"]["code"]
        == "INVALID_INTERNAL_TOKEN"
    )

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
    assert attempt is None

    assert unchanged_seat is not None
    assert unchanged_seat.state == SeatState.AVAILABLE


def test_invalid_request_body_does_not_reach_booking_service(
    client: TestClient,
    db_session: Session,
) -> None:
    """Schema validation failure must happen before database work."""

    _, show, seat = get_normal_booking_data(
        db_session
    )
    idempotency_key = uuid4()

    response = client.post(
        "/api/v1/bookings",
        headers=booking_headers(
            str(idempotency_key)
        ),
        json={
            "user_id": 0,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": None,
            "unexpected_field": "not allowed",
        },
    )

    assert response.status_code == 422

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
    assert attempt is None

    assert unchanged_seat is not None
    assert unchanged_seat.state == SeatState.AVAILABLE