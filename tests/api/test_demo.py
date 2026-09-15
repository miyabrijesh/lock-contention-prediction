from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import SeatState


def _normal_booking_data(
    session: Session,
) -> tuple[SyntheticUser, Show, Seat]:
    """Return seeded entities for one demo booking."""

    user = session.scalar(
        select(SyntheticUser)
        .order_by(SyntheticUser.id)
        .limit(1)
    )

    show = session.scalar(
        select(Show)
        .where(
            Show.is_experiment_show.is_(False)
        )
        .order_by(Show.id)
        .limit(1)
    )

    assert user is not None
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

    return user, show, seat


def _experiment_show(
    session: Session,
) -> Show:
    """Return the seeded experiment show."""

    show = session.scalar(
        select(Show)
        .where(
            Show.is_experiment_show.is_(True)
        )
        .order_by(Show.id)
        .limit(1)
    )

    assert show is not None

    return show


def test_demo_booking_uses_transactional_service(
    client: TestClient,
    db_session: Session,
) -> None:
    """Book a normal-show seat without an internal token."""

    user, show, seat = _normal_booking_data(
        db_session
    )
    original_version = seat.version

    response = client.post(
        "/api/v1/demo/bookings",
        headers={
            "Idempotency-Key": str(uuid4()),
        },
        json={
            "user_id": user.id,
            "show_id": show.id,
            "seat_id": seat.id,
        },
    )

    assert response.status_code == 201

    body = response.json()

    assert body["user_id"] == user.id
    assert body["show_id"] == show.id
    assert body["seat_id"] == seat.id
    assert body["status"] == "CONFIRMED"

    db_session.expire_all()

    updated_seat = db_session.get(
        Seat,
        seat.id,
    )

    assert updated_seat is not None
    assert updated_seat.state == SeatState.BOOKED
    assert updated_seat.version == (
        original_version + 1
    )


def test_demo_booking_rejects_experiment_identifier(
    client: TestClient,
    db_session: Session,
) -> None:
    """Keep public demo requests outside supplied experiment IDs."""

    user, show, seat = _normal_booking_data(
        db_session
    )

    response = client.post(
        "/api/v1/demo/bookings",
        headers={
            "Idempotency-Key": str(uuid4()),
        },
        json={
            "user_id": user.id,
            "show_id": show.id,
            "seat_id": seat.id,
            "experiment_run_id": str(uuid4()),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == (
        "DEMO_EXPERIMENT_NOT_ALLOWED"
    )


def test_demo_contention_status_contract(
    client: TestClient,
) -> None:
    """Expose the camel-case status contract for React."""

    response = client.get(
        "/api/v1/demo/contention-status"
    )

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {
        "available",
        "experimentRunId",
        "probability",
        "riskLevel",
        "predictedAt",
        "threshold",
        "isStale",
        "activeTransactions",
        "blockedSessions",
        "waitingLocks",
        "requestsPerSecond",
        "p95LatencyMs",
    }

    assert isinstance(body["available"], bool)

    if body["available"]:
        assert body["experimentRunId"] is not None
        assert body["probability"] is not None
        assert body["riskLevel"] in {
            "LOW",
            "MEDIUM",
            "HIGH",
        }


def test_demo_active_experiment_contract(
    client: TestClient,
    db_session: Session,
) -> None:
    """Expose running experiment state using camel-case fields."""

    show = _experiment_show(db_session)

    response = client.get(
        "/api/v1/demo/active-experiment",
        params={
            "show_id": show.id,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert set(body) == {
        "active",
        "experimentRunId",
        "showId",
        "status",
        "hotspotSeatIds",
        "startedAt",
        "durationSec",
    }

    assert isinstance(body["active"], bool)
    assert isinstance(body["hotspotSeatIds"], list)

    if body["active"]:
        assert body["experimentRunId"] is not None
        assert body["showId"] == show.id
        assert body["status"] == "RUNNING"
        assert body["hotspotSeatIds"]
        assert body["startedAt"] is not None
        assert isinstance(body["durationSec"], int)
        assert body["durationSec"] > 0
    else:
        assert body == {
            "active": False,
            "experimentRunId": None,
            "showId": None,
            "status": None,
            "hotspotSeatIds": [],
            "startedAt": None,
            "durationSec": None,
        }


def test_demo_active_experiment_rejects_invalid_show_id(
    client: TestClient,
) -> None:
    """Reject a nonpositive active-experiment show identifier."""

    response = client.get(
        "/api/v1/demo/active-experiment",
        params={
            "show_id": 0,
        },
    )

    assert response.status_code == 422