from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    AuditEvent,
    ModelVersion,
)
from backend.app.schemas.model import (
    ModelActivationRequest,
)
from backend.app.services.model_service import (
    ModelServiceError,
    activate_model_version,
    list_registered_models,
    read_active_model,
    read_registered_model,
)
from model_training.register_model import (
    build_registration_spec,
)


def _create_registered_model(
    session: Session,
    *,
    is_active: bool = False,
) -> ModelVersion:
    """Create one valid registered model in the test database."""

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
        is_active=is_active,
    )

    session.add(model_version)
    session.commit()
    session.refresh(model_version)

    return model_version


def _activation_request(
    *,
    reason: str | None = (
        "Checkpoint 35B test activation"
    ),
) -> ModelActivationRequest:
    """Return a valid activation request."""

    return ModelActivationRequest(
        actor="pytest-admin",
        reason=reason,
    )


def test_list_and_read_registered_models(
    db_session: Session,
) -> None:
    """List and retrieve a registered model."""

    registered_model = _create_registered_model(
        db_session
    )

    models = list_registered_models(
        db_session
    )

    assert len(models) == 1
    assert models[0].id == registered_model.id

    loaded_model = read_registered_model(
        db_session,
        registered_model.id,
    )

    assert loaded_model.id == registered_model.id
    assert loaded_model.is_active is False


def test_missing_registered_model_is_rejected(
    db_session: Session,
) -> None:
    """Return a structured error for an unknown model."""

    with pytest.raises(
        ModelServiceError,
        match="was not found",
    ) as captured_error:
        read_registered_model(
            db_session,
            uuid4(),
        )

    assert (
        captured_error.value.code
        == "MODEL_VERSION_NOT_FOUND"
    )

    assert captured_error.value.status_code == 404


def test_no_active_model_is_reported(
    db_session: Session,
) -> None:
    """Do not silently select an inactive model."""

    _create_registered_model(
        db_session
    )

    with pytest.raises(
        ModelServiceError,
        match="No active model",
    ) as captured_error:
        read_active_model(db_session)

    assert (
        captured_error.value.code
        == "ACTIVE_MODEL_NOT_FOUND"
    )

    assert captured_error.value.status_code == 404


def test_activate_registered_model_and_audit(
    db_session: Session,
) -> None:
    """Activate a verified model and store its audit event."""

    registered_model = _create_registered_model(
        db_session
    )

    activated_model = activate_model_version(
        db_session,
        registered_model.id,
        _activation_request(),
    )

    assert activated_model.is_active is True

    active_model = read_active_model(
        db_session
    )

    assert active_model.id == registered_model.id

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

    audit_events = list(
        db_session.scalars(
            audit_statement
        ).all()
    )

    assert len(audit_events) == 1

    audit_event = audit_events[0]

    assert audit_event.experiment_run_id is None
    assert audit_event.actor == "pytest-admin"

    assert audit_event.details["model_name"] == (
        "contention-logistic-v1"
    )

    assert (
        audit_event.details["already_active"]
        is False
    )

    assert audit_event.details["reason"] == (
        "Checkpoint 35B test activation"
    )


def test_repeat_activation_is_idempotent(
    db_session: Session,
) -> None:
    """Allow repeated activation without duplicating active models."""

    registered_model = _create_registered_model(
        db_session
    )

    first_result = activate_model_version(
        db_session,
        registered_model.id,
        _activation_request(
            reason="First activation"
        ),
    )

    second_result = activate_model_version(
        db_session,
        registered_model.id,
        _activation_request(
            reason="Repeated activation"
        ),
    )

    assert first_result.id == second_result.id
    assert second_result.is_active is True

    models = list_registered_models(
        db_session
    )

    active_models = [
        model
        for model in models
        if model.is_active
    ]

    assert len(active_models) == 1

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
        .order_by(AuditEvent.created_at)
    )

    audit_events = list(
        db_session.scalars(
            audit_statement
        ).all()
    )

    assert len(audit_events) == 2

    assert (
        audit_events[0].details[
            "already_active"
        ]
        is False
    )

    assert (
        audit_events[1].details[
            "already_active"
        ]
        is True
    )


def test_activation_switches_the_active_model(
    db_session: Session,
) -> None:
    """Deactivate the old model when activating another."""

    first_model = _create_registered_model(
        db_session
    )

    second_model = _create_registered_model(
        db_session
    )

    activate_model_version(
        db_session,
        first_model.id,
        _activation_request(
            reason="Activate first model"
        ),
    )

    switched_model = activate_model_version(
        db_session,
        second_model.id,
        _activation_request(
            reason="Switch active model"
        ),
    )

    db_session.refresh(first_model)
    db_session.refresh(second_model)

    assert switched_model.id == second_model.id
    assert first_model.is_active is False
    assert second_model.is_active is True

    active_model = read_active_model(
        db_session
    )

    assert active_model.id == second_model.id


def test_invalid_artifact_checksum_preserves_inactive_state(
    db_session: Session,
) -> None:
    """Reject a modified checksum without activating the model."""

    registered_model = _create_registered_model(
        db_session
    )

    registered_model.artifact_sha256 = "0" * 64
    db_session.commit()

    with pytest.raises(
        ModelServiceError,
        match="failed inference validation",
    ) as captured_error:
        activate_model_version(
            db_session,
            registered_model.id,
            _activation_request(
                reason="Invalid checksum test"
            ),
        )

    assert (
        captured_error.value.code
        == "MODEL_ARTIFACT_INVALID"
    )

    stored_model = db_session.get(
        ModelVersion,
        registered_model.id,
    )

    assert stored_model is not None
    assert stored_model.is_active is False

    audit_statement = (
        select(AuditEvent)
        .where(
            AuditEvent.resource_type
            == "model_version",
            AuditEvent.resource_id
            == str(registered_model.id),
        )
    )

    audit_events = list(
        db_session.scalars(
            audit_statement
        ).all()
    )

    assert audit_events == []


def test_invalid_provenance_preserves_inactive_state(
    db_session: Session,
) -> None:
    """Reject inconsistent metadata without activating the model."""

    registered_model = _create_registered_model(
        db_session
    )

    changed_feature_schema = dict(
        registered_model.feature_schema
    )

    changed_feature_schema[
        "target_horizon_sec"
    ] = 99

    changed_feature_schema[
        "ordered_features"
    ] = [
        "blocked_sessions",
    ]

    registered_model.feature_schema = (
        changed_feature_schema
    )

    db_session.commit()

    with pytest.raises(
        ModelServiceError,
        match="feature order",
    ) as captured_error:
        activate_model_version(
            db_session,
            registered_model.id,
            _activation_request(
                reason="Invalid provenance test"
            ),
        )

    assert (
        captured_error.value.code
        == "MODEL_PROVENANCE_INVALID"
    )

    stored_model = db_session.get(
        ModelVersion,
        registered_model.id,
    )

    assert stored_model is not None
    assert stored_model.is_active is False