from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    AuditEvent,
    ModelVersion,
)
from model_training.register_model import (
    build_registration_spec,
)


def _admin_headers() -> dict[str, str]:
    """Return valid local administrator authentication."""

    return {
        "X-Admin-Token": settings.admin_api_token,
    }


def _create_registered_model(
    session: Session,
) -> ModelVersion:
    """Create one valid inactive model in the test database."""

    specification = build_registration_spec()

    model_version = ModelVersion(
        id=uuid4(),
        name=specification.name,
        algorithm=specification.algorithm,
        feature_schema=dict(
            specification.feature_schema
        ),
        target_horizon_sec=(
            specification.target_horizon_sec
        ),
        training_run_ids=list(
            specification.training_run_ids
        ),
        validation_strategy=(
            specification.validation_strategy
        ),
        risk_thresholds=dict(
            specification.risk_thresholds
        ),
        evaluation_metrics=dict(
            specification.evaluation_metrics
        ),
        artifact_path=specification.artifact_path,
        artifact_sha256=(
            specification.artifact_sha256
        ),
        code_version=specification.code_version,
        is_active=False,
    )

    session.add(model_version)
    session.commit()
    session.refresh(model_version)

    return model_version


def test_model_endpoints_require_admin_token(
    client: TestClient,
) -> None:
    """Reject model management without administrator access."""

    response = client.get(
        "/api/v1/models"
    )

    assert response.status_code == 401

    response_body = response.json()

    assert response_body["detail"]["code"] == (
        "INVALID_ADMIN_TOKEN"
    )


def test_list_and_read_registered_model(
    client: TestClient,
    db_session: Session,
) -> None:
    """List and retrieve a registered inactive model."""

    registered_model = _create_registered_model(
        db_session
    )

    list_response = client.get(
        "/api/v1/models",
        headers=_admin_headers(),
    )

    assert list_response.status_code == 200

    listed_models = list_response.json()

    assert len(listed_models) == 1
    assert listed_models[0]["id"] == str(
        registered_model.id
    )
    assert listed_models[0]["name"] == (
        "contention-logistic-v1"
    )
    assert listed_models[0]["is_active"] is False

    read_response = client.get(
        (
            "/api/v1/models/"
            f"{registered_model.id}"
        ),
        headers=_admin_headers(),
    )

    assert read_response.status_code == 200

    read_model = read_response.json()

    assert read_model["id"] == str(
        registered_model.id
    )
    assert read_model["artifact_sha256"] == (
        registered_model.artifact_sha256.strip()
    )


def test_read_missing_model_returns_structured_error(
    client: TestClient,
) -> None:
    """Return a machine-readable error for an unknown UUID."""

    response = client.get(
        f"/api/v1/models/{uuid4()}",
        headers=_admin_headers(),
    )

    assert response.status_code == 404

    response_body = response.json()

    assert response_body["detail"]["code"] == (
        "MODEL_VERSION_NOT_FOUND"
    )


def test_read_active_model_without_activation(
    client: TestClient,
    db_session: Session,
) -> None:
    """Do not silently select an inactive model."""

    _create_registered_model(
        db_session
    )

    response = client.get(
        "/api/v1/models/active",
        headers=_admin_headers(),
    )

    assert response.status_code == 404

    response_body = response.json()

    assert response_body["detail"]["code"] == (
        "ACTIVE_MODEL_NOT_FOUND"
    )


def test_activate_model_through_admin_api(
    client: TestClient,
    db_session: Session,
) -> None:
    """Activate a verified model through the secured API."""

    registered_model = _create_registered_model(
        db_session
    )

    response = client.post(
        (
            "/api/v1/models/"
            f"{registered_model.id}/activate"
        ),
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": (
                "Checkpoint 35C API activation"
            ),
        },
    )

    assert response.status_code == 200

    response_body = response.json()

    assert response_body["id"] == str(
        registered_model.id
    )
    assert response_body["is_active"] is True
    assert response_body["name"] == (
        "contention-logistic-v1"
    )

    active_response = client.get(
        "/api/v1/models/active",
        headers=_admin_headers(),
    )

    assert active_response.status_code == 200

    active_body = active_response.json()

    assert active_body["id"] == str(
        registered_model.id
    )

    audit_statement = (
        select(AuditEvent)
        .where(
            AuditEvent.resource_type
            == "model_version",
            AuditEvent.resource_id
            == str(registered_model.id),
            AuditEvent.action
            == "model_activated",
        )
    )

    audit_event = db_session.scalar(
        audit_statement
    )

    assert audit_event is not None
    assert audit_event.actor == "pytest-admin"
    assert audit_event.experiment_run_id is None


def test_repeat_api_activation_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    """Keep one active model after repeated API activation."""

    registered_model = _create_registered_model(
        db_session
    )

    activation_url = (
        "/api/v1/models/"
        f"{registered_model.id}/activate"
    )

    first_response = client.post(
        activation_url,
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "First API activation",
        },
    )

    second_response = client.post(
        activation_url,
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "reason": "Repeated API activation",
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    assert (
        first_response.json()["id"]
        == second_response.json()["id"]
    )

    active_statement = (
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
    )

    active_models = list(
        db_session.scalars(
            active_statement
        ).all()
    )

    assert len(active_models) == 1
    assert active_models[0].id == (
        registered_model.id
    )


def test_activation_request_validation(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject invalid actor and unexpected request fields."""

    registered_model = _create_registered_model(
        db_session
    )

    activation_url = (
        "/api/v1/models/"
        f"{registered_model.id}/activate"
    )

    blank_actor_response = client.post(
        activation_url,
        headers=_admin_headers(),
        json={
            "actor": "   ",
        },
    )

    assert blank_actor_response.status_code == 422

    extra_field_response = client.post(
        activation_url,
        headers=_admin_headers(),
        json={
            "actor": "pytest-admin",
            "unexpected_field": True,
        },
    )

    assert extra_field_response.status_code == 422

    db_session.refresh(registered_model)

    assert registered_model.is_active is False