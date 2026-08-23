from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    Booking,
    BookingAttempt,
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    AttemptOutcome,
    BookingStatus,
    ExperimentStatus,
    SeatState,
)


def _create_running_recycle_experiment(
    session: Session,
    *,
    simulated_work_ms: int,
) -> tuple[ExperimentRun, Seat, SyntheticUser]:
    """Create one running recycling experiment using seeded data."""

    experiment_show = session.scalar(
        select(Show).where(
            Show.is_experiment_show.is_(True)
        )
    )

    assert experiment_show is not None

    seat = session.scalar(
        select(Seat)
        .where(
            Seat.show_id == experiment_show.id,
            Seat.state == SeatState.AVAILABLE,
        )
        .order_by(Seat.id)
    )

    assert seat is not None

    synthetic_user = session.scalar(
        select(SyntheticUser).order_by(
            SyntheticUser.id
        )
    )

    assert synthetic_user is not None

    experiment = ExperimentRun(
        name=f"Recycle test {uuid4()}",
        show_id=experiment_show.id,
        status=ExperimentStatus.RUNNING,
        user_count=10,
        spawn_rate_per_sec=Decimal("5.00"),
        duration_sec=60,
        hotspot_ratio=Decimal("1.0000"),
        hotspot_seat_ids=[seat.id],
        sample_interval_sec=2,
        simulated_work_ms=simulated_work_ms,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=42,
        code_version="pytest",
        started_at=datetime.now(UTC),
        created_by="pytest",
    )

    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    return experiment, seat, synthetic_user


def _booking_headers(
    idempotency_key: UUID,
) -> dict[str, str]:
    """Return valid internal booking headers."""

    return {
        "X-Internal-Token": settings.internal_api_token,
        "Idempotency-Key": str(idempotency_key),
    }


def test_recycle_booking_returns_seat_to_available(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recycle a seat while preserving telemetry and traceability."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    experiment, seat, synthetic_user = (
        _create_running_recycle_experiment(
            db_session,
            simulated_work_ms=120,
        )
    )

    initial_version = seat.version
    idempotency_key = uuid4()

    started_at = perf_counter()

    response = client.post(
        "/api/v1/bookings",
        headers=_booking_headers(idempotency_key),
        json={
            "user_id": synthetic_user.id,
            "show_id": experiment.show_id,
            "seat_id": seat.id,
            "experiment_run_id": str(experiment.id),
        },
    )

    elapsed_seconds = perf_counter() - started_at

    assert response.status_code == 201
    assert response.json()["status"] == "CANCELLED"
    assert response.json()["cancelled_at"] is not None
    assert elapsed_seconds >= 0.09

    db_session.refresh(seat)

    assert seat.state == SeatState.AVAILABLE
    assert seat.hold_expires_at is None
    assert seat.version == initial_version + 1

    booking = db_session.scalar(
        select(Booking).where(
            Booking.idempotency_key
            == idempotency_key
        )
    )

    assert booking is not None
    assert booking.status == BookingStatus.CANCELLED
    assert booking.experiment_run_id == experiment.id

    attempt = db_session.scalar(
        select(BookingAttempt).where(
            BookingAttempt.idempotency_key
            == idempotency_key
        )
    )

    assert attempt is not None
    assert attempt.outcome == AttemptOutcome.SUCCESS
    assert attempt.http_status == 201
    assert attempt.error_code is None
    assert attempt.experiment_run_id == experiment.id
    assert attempt.latency_ms >= Decimal("90.000")


def test_recycle_seat_can_be_used_repeatedly(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow unique requests to reuse one experiment seat."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    experiment, seat, synthetic_user = (
        _create_running_recycle_experiment(
            db_session,
            simulated_work_ms=20,
        )
    )

    initial_version = seat.version

    first_key = uuid4()
    second_key = uuid4()

    request_body = {
        "user_id": synthetic_user.id,
        "show_id": experiment.show_id,
        "seat_id": seat.id,
        "experiment_run_id": str(experiment.id),
    }

    first_response = client.post(
        "/api/v1/bookings",
        headers=_booking_headers(first_key),
        json=request_body,
    )

    second_response = client.post(
        "/api/v1/bookings",
        headers=_booking_headers(second_key),
        json=request_body,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    assert first_response.json()["status"] == (
        "CANCELLED"
    )
    assert second_response.json()["status"] == (
        "CANCELLED"
    )

    assert first_response.json()["id"] != (
        second_response.json()["id"]
    )

    db_session.refresh(seat)

    assert seat.state == SeatState.AVAILABLE
    assert seat.version == initial_version + 2

    bookings = list(
        db_session.scalars(
            select(Booking).where(
                Booking.experiment_run_id
                == experiment.id
            )
        )
    )

    attempts = list(
        db_session.scalars(
            select(BookingAttempt).where(
                BookingAttempt.experiment_run_id
                == experiment.id
            )
        )
    )

    assert len(bookings) == 2
    assert len(attempts) == 2
    assert all(
        booking.status == BookingStatus.CANCELLED
        for booking in bookings
    )
    assert all(
        attempt.outcome == AttemptOutcome.SUCCESS
        for attempt in attempts
    )


def test_recycle_idempotent_replay_does_not_repeat_delay(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay a request without repeating simulated lock work."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    experiment, seat, synthetic_user = (
        _create_running_recycle_experiment(
            db_session,
            simulated_work_ms=150,
        )
    )

    initial_version = seat.version
    idempotency_key = uuid4()

    request_body = {
        "user_id": synthetic_user.id,
        "show_id": experiment.show_id,
        "seat_id": seat.id,
        "experiment_run_id": str(experiment.id),
    }

    first_response = client.post(
        "/api/v1/bookings",
        headers=_booking_headers(idempotency_key),
        json=request_body,
    )

    second_response = client.post(
        "/api/v1/bookings",
        headers=_booking_headers(idempotency_key),
        json=request_body,
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 200
    assert second_response.headers[
        "Idempotent-Replayed"
    ] == "true"

    assert first_response.json()["id"] == (
        second_response.json()["id"]
    )

    db_session.refresh(seat)

    # The replay must not increment the seat twice.
    assert seat.version == initial_version + 1
    assert seat.state == SeatState.AVAILABLE

    attempts = list(
        db_session.scalars(
            select(BookingAttempt)
            .where(
                BookingAttempt.idempotency_key
                == idempotency_key
            )
            .order_by(BookingAttempt.requested_at)
        )
    )

    assert len(attempts) == 2
    assert attempts[0].latency_ms >= Decimal("120.000")
    assert attempts[1].latency_ms < attempts[0].latency_ms