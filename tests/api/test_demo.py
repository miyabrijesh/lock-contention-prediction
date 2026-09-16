from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    DemoSession,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    DemoScenarioName,
    DemoSessionStatus,
    SeatState,
)
from datetime import UTC, datetime, timedelta

from backend.app.services.demo_session_service import (
    create_waiting_demo_session,
)



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

def _available_experiment_seats(
    session: Session,
    *,
    count: int,
) -> list[Seat]:
    """Return consecutive available experiment seats in one row."""

    show = _experiment_show(session)

    seats = list(
        session.scalars(
            select(Seat)
            .where(
                Seat.show_id == show.id,
                Seat.state == SeatState.AVAILABLE,
            )
            .order_by(
                Seat.seat_number,
                Seat.id,
            )
        ).all()
    )

    seats_by_row: dict[str, list[tuple[int, Seat]]] = {}

    for seat in seats:
        row = seat.seat_number.rstrip(
            "0123456789"
        )
        number_text = seat.seat_number[
            len(row):
        ]

        if not row or not number_text.isdigit():
            continue

        seats_by_row.setdefault(
            row,
            [],
        ).append(
            (
                int(number_text),
                seat,
            )
        )

    for row_seats in seats_by_row.values():
        row_seats.sort(
            key=lambda item: item[0]
        )

        for start_index in range(
            len(row_seats) - count + 1
        ):
            candidate = row_seats[
                start_index:
                start_index + count
            ]
            numbers = [
                number
                for number, _ in candidate
            ]

            if numbers == list(
                range(
                    numbers[0],
                    numbers[0] + count,
                )
            ):
                return [
                    seat
                    for _, seat in candidate
                ]

    raise AssertionError(
        f"Could not find {count} consecutive "
        "available experiment seats."
    )

def _experiment_seats_by_number(
    session: Session,
    *seat_numbers: str,
) -> list[Seat]:
    """Return experiment seats in the requested order."""

    show = _experiment_show(session)

    found_seats = list(
        session.scalars(
            select(Seat)
            .where(
                Seat.show_id == show.id,
                Seat.seat_number.in_(
                    seat_numbers
                ),
            )
        ).all()
    )

    seats_by_number = {
        seat.seat_number: seat
        for seat in found_seats
    }

    assert set(seats_by_number) == set(
        seat_numbers
    )

    return [
        seats_by_number[seat_number]
        for seat_number in seat_numbers
    ]


def _waiting_demo_session(
    session: Session,
) -> DemoSession:
    """Create one waiting browser-selection session."""

    show = _experiment_show(session)

    return create_waiting_demo_session(
        session,
        show_id=show.id,
        scenario=DemoScenarioName.BASELINE,
    )


def test_demo_session_contract_without_open_session(
    client: TestClient,
    db_session: Session,
) -> None:
    """Return an unavailable session when no runner is waiting."""

    show = _experiment_show(db_session)

    response = client.get(
        "/api/v1/demo/session",
        params={
            "show_id": show.id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "sessionId": None,
        "showId": None,
        "status": None,
        "scenario": None,
        "selectedSeatIds": [],
        "experimentRunId": None,
        "expiresAt": None,
        "selectionSubmittedAt": None,
    }


def test_demo_session_exposes_waiting_runner(
    client: TestClient,
    db_session: Session,
) -> None:
    """Expose a terminal-created waiting session."""

    demo_session = _waiting_demo_session(
        db_session
    )

    response = client.get(
        "/api/v1/demo/session",
        params={
            "show_id": demo_session.show_id,
        },
    )

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is True
    assert body["sessionId"] == str(
        demo_session.id
    )
    assert body["showId"] == demo_session.show_id
    assert body["status"] == (
        "WAITING_FOR_SELECTION"
    )
    assert body["scenario"] == "BASELINE"
    assert body["selectedSeatIds"] == []
    assert body["experimentRunId"] is None
    assert body["expiresAt"] is not None
    assert body["selectionSubmittedAt"] is None


def test_demo_selection_accepts_consecutive_seats(
    client: TestClient,
    db_session: Session,
) -> None:
    """Accept between one and five same-row consecutive seats."""

    demo_session = _waiting_demo_session(
        db_session
    )
    seats = _available_experiment_seats(
        db_session,
        count=3,
    )
    seat_ids = [
        seat.id
        for seat in seats
    ]

    response = client.post(
        (
            "/api/v1/demo/sessions/"
            f"{demo_session.id}/selection"
        ),
        json={
            "seatIds": seat_ids,
        },
    )

    assert response.status_code == 200, response.json()

    body = response.json()

    assert body["available"] is True
    assert body["sessionId"] == str(
        demo_session.id
    )
    assert body["status"] == "READY"
    assert body["selectedSeatIds"] == seat_ids
    assert (
        body["selectionSubmittedAt"]
        is not None
    )

    db_session.expire_all()

    updated_session = db_session.get(
        DemoSession,
        demo_session.id,
    )

    assert updated_session is not None
    assert updated_session.status == (
        DemoSessionStatus.READY
    )
    assert updated_session.selected_seat_ids == (
        seat_ids
    )


def test_demo_selection_rejects_nonconsecutive_seats(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject a gap inside one selected row."""

    demo_session = _waiting_demo_session(
        db_session
    )
    first_seat, third_seat = (
        _experiment_seats_by_number(
            db_session,
            "A1",
            "A3",
        )
    )

    response = client.post(
        (
            "/api/v1/demo/sessions/"
            f"{demo_session.id}/selection"
        ),
        json={
            "seatIds": [
                first_seat.id,
                third_seat.id,
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "DEMO_SEATS_NOT_CONSECUTIVE"
    )


def test_demo_selection_rejects_cross_row_seats(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject seats selected from different rows."""

    demo_session = _waiting_demo_session(
        db_session
    )
    first_row_seat, second_row_seat = (
        _experiment_seats_by_number(
            db_session,
            "A1",
            "B1",
        )
    )

    response = client.post(
        (
            "/api/v1/demo/sessions/"
            f"{demo_session.id}/selection"
        ),
        json={
            "seatIds": [
                first_row_seat.id,
                second_row_seat.id,
            ],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "DEMO_SEATS_NOT_SAME_ROW"
    )


def test_demo_selection_rejects_unavailable_seat(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject a selected seat that is already booked."""

    demo_session = _waiting_demo_session(
        db_session
    )
    seat = _available_experiment_seats(
        db_session,
        count=1,
    )[0]

    seat.state = SeatState.BOOKED
    db_session.flush()

    response = client.post(
        (
            "/api/v1/demo/sessions/"
            f"{demo_session.id}/selection"
        ),
        json={
            "seatIds": [seat.id],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "DEMO_SEAT_UNAVAILABLE"
    )


def test_demo_selection_rejects_more_than_five_seats(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject a browser selection larger than five seats."""

    demo_session = _waiting_demo_session(
        db_session
    )
    seats = _available_experiment_seats(
        db_session,
        count=6,
    )

    response = client.post(
        (
            "/api/v1/demo/sessions/"
            f"{demo_session.id}/selection"
        ),
        json={
            "seatIds": [
                seat.id
                for seat in seats
            ],
        },
    )

    assert response.status_code == 422


def test_expired_demo_session_is_hidden(
    client: TestClient,
    db_session: Session,
) -> None:
    """Cancel and hide an expired waiting session."""

    demo_session = _waiting_demo_session(
        db_session
    )
    demo_session.expires_at = (
        datetime.now(UTC)
        - timedelta(seconds=1)
    )
    db_session.commit()

    response = client.get(
        "/api/v1/demo/session",
        params={
            "show_id": demo_session.show_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["available"] is False

    db_session.expire_all()

    updated_session = db_session.get(
        DemoSession,
        demo_session.id,
    )

    assert updated_session is not None
    assert updated_session.status == (
        DemoSessionStatus.CANCELLED
    )