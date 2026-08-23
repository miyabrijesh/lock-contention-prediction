from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    AuditEvent,
    Seat,
    Show,
)
from backend.app.models.enums import ExperimentStatus


def _admin_headers() -> dict[str, str]:
    """Return valid local administrator authentication."""

    return {
        "X-Admin-Token": settings.admin_api_token,
    }


def _experiment_payload(
    session: Session,
    *,
    name: str,
) -> dict[str, object]:
    """Build a valid experiment using seeded test data."""

    experiment_show = session.scalar(
        select(Show).where(
            Show.is_experiment_show.is_(True)
        )
    )

    assert experiment_show is not None

    hotspot_seat_ids = list(
        session.scalars(
            select(Seat.id)
            .where(
                Seat.show_id == experiment_show.id
            )
            .order_by(Seat.id)
            .limit(5)
        )
    )

    assert len(hotspot_seat_ids) == 5

    return {
        "name": name,
        "show_id": experiment_show.id,
        "user_count": 100,
        "spawn_rate_per_sec": "20.00",
        "duration_sec": 300,
        "hotspot_ratio": "0.8000",
        "hotspot_seat_ids": hotspot_seat_ids,
        "sample_interval_sec": 2,
        "simulated_work_ms": 200,
        "booking_mode": "EXPERIMENT_RECYCLE",
        "random_seed": 42,
        "code_version": "test-version",
        "created_by": "pytest-admin",
    }


def test_experiment_endpoints_require_admin_token(
    client: TestClient,
) -> None:
    """Reject requests without the administrator token."""

    response = client.get(
        "/api/v1/experiments"
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == (
        "INVALID_ADMIN_TOKEN"
    )


def test_create_experiment_and_audit_event(
    client: TestClient,
    db_session: Session,
) -> None:
    """Create a draft experiment and its audit record."""

    response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="Create experiment test",
        ),
    )

    assert response.status_code == 201

    response_body = response.json()

    assert response_body["name"] == (
        "Create experiment test"
    )
    assert response_body["status"] == "DRAFT"
    assert response_body["started_at"] is None
    assert response_body["ended_at"] is None

    experiment_run_id = UUID(
        response_body["id"]
    )

    audit_events = list(
        db_session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.experiment_run_id
                == experiment_run_id
            )
            .order_by(AuditEvent.created_at)
        )
    )

    assert len(audit_events) == 1
    assert audit_events[0].action == (
        "experiment_created"
    )
    assert audit_events[0].actor == "pytest-admin"


def test_list_get_and_filter_experiments(
    client: TestClient,
    db_session: Session,
) -> None:
    """List, filter, and retrieve experiments."""

    create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="List experiment test",
        ),
    )

    assert create_response.status_code == 201

    experiment_run_id = create_response.json()["id"]

    list_response = client.get(
        "/api/v1/experiments",
        headers=_admin_headers(),
    )

    assert list_response.status_code == 200
    assert any(
        experiment["id"] == experiment_run_id
        for experiment in list_response.json()
    )

    filter_response = client.get(
        "/api/v1/experiments?status=DRAFT",
        headers=_admin_headers(),
    )

    assert filter_response.status_code == 200
    assert any(
        experiment["id"] == experiment_run_id
        for experiment in filter_response.json()
    )

    get_response = client.get(
        f"/api/v1/experiments/{experiment_run_id}",
        headers=_admin_headers(),
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == (
        experiment_run_id
    )


def test_normal_show_cannot_be_used_for_experiment(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject experiments targeting the normal show."""

    normal_show = db_session.scalar(
        select(Show).where(
            Show.is_experiment_show.is_(False)
        )
    )

    assert normal_show is not None

    normal_seat_ids = list(
        db_session.scalars(
            select(Seat.id)
            .where(Seat.show_id == normal_show.id)
            .order_by(Seat.id)
            .limit(5)
        )
    )

    payload = _experiment_payload(
        db_session,
        name="Invalid normal show experiment",
    )
    payload["show_id"] = normal_show.id
    payload["hotspot_seat_ids"] = normal_seat_ids

    response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "SHOW_NOT_EXPERIMENTAL"
    )


def test_start_rejected_when_experiment_mode_disabled(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent experiments when the safety flag is disabled."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        False,
    )

    create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="Disabled mode experiment",
        ),
    )

    assert create_response.status_code == 201

    experiment_run_id = create_response.json()["id"]

    start_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{experiment_run_id}/start"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Test disabled mode",
        },
    )

    assert start_response.status_code == 403
    assert start_response.json()["detail"]["code"] == (
        "EXPERIMENT_MODE_DISABLED"
    )


def test_complete_experiment_lifecycle(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create, start, and complete one experiment."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="Complete lifecycle test",
        ),
    )

    assert create_response.status_code == 201

    experiment_run_id = create_response.json()["id"]

    start_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{experiment_run_id}/start"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Begin test workload",
        },
    )

    assert start_response.status_code == 200
    assert start_response.json()["status"] == "RUNNING"
    assert start_response.json()["started_at"] is not None
    assert start_response.json()["ended_at"] is None

    complete_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{experiment_run_id}/complete"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Test workload completed",
        },
    )

    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == (
        "COMPLETED"
    )
    assert complete_response.json()["ended_at"] is not None

    audit_actions = list(
        db_session.scalars(
            select(AuditEvent.action)
            .where(
                AuditEvent.experiment_run_id
                == UUID(experiment_run_id)
            )
            .order_by(AuditEvent.created_at)
        )
    )

    assert audit_actions == [
        "experiment_created",
        "experiment_started",
        "experiment_completed",
    ]


def test_only_one_experiment_can_run(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent two simultaneous system-wide experiments."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    first_create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="First running experiment",
        ),
    )
    second_create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="Second running experiment",
        ),
    )

    assert first_create_response.status_code == 201
    assert second_create_response.status_code == 201

    first_experiment_id = (
        first_create_response.json()["id"]
    )
    second_experiment_id = (
        second_create_response.json()["id"]
    )

    first_start_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{first_experiment_id}/start"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "First experiment",
        },
    )

    assert first_start_response.status_code == 200
    assert first_start_response.json()["status"] == (
        "RUNNING"
    )

    second_start_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{second_experiment_id}/start"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Second experiment",
        },
    )

    assert second_start_response.status_code == 409
    assert second_start_response.json()["detail"]["code"] == (
        "EXPERIMENT_ALREADY_RUNNING"
    )


def test_draft_experiment_cannot_be_completed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject an invalid DRAFT to COMPLETED transition."""

    create_response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            name="Invalid transition test",
        ),
    )

    assert create_response.status_code == 201

    experiment_run_id = create_response.json()["id"]

    complete_response = client.post(
        (
            f"/api/v1/experiments/"
            f"{experiment_run_id}/complete"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Invalid completion attempt",
        },
    )

    assert complete_response.status_code == 409
    assert complete_response.json()["detail"]["code"] == (
        "INVALID_EXPERIMENT_TRANSITION"
    )