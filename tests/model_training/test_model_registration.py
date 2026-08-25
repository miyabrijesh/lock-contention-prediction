from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

import model_training.register_model as registration
from backend.app.models import ModelVersion
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    FEATURE_COLUMNS,
)
from model_training.register_model import (
    ModelRegistrationError,
    ModelRegistrationSpec,
    build_registration_spec,
    register_model_version,
)


def _mock_session() -> tuple[Session, MagicMock]:
    """Return a typed mocked SQLAlchemy session."""

    mocked_session = MagicMock()
    session = cast(
        Session,
        mocked_session,
    )

    return session, mocked_session


def _existing_model(
    spec: ModelRegistrationSpec,
) -> ModelVersion:
    """Build an existing registration from a spec."""

    return ModelVersion(
        name=spec.name,
        algorithm=spec.algorithm,
        feature_schema=spec.feature_schema,
        target_horizon_sec=(
            spec.target_horizon_sec
        ),
        training_run_ids=list(
            spec.training_run_ids
        ),
        validation_strategy=(
            spec.validation_strategy
        ),
        risk_thresholds=dict(
            spec.risk_thresholds
        ),
        evaluation_metrics=dict(
            spec.evaluation_metrics
        ),
        artifact_path=spec.artifact_path,
        artifact_sha256=spec.artifact_sha256,
        code_version=spec.code_version,
        is_active=False,
    )


def test_build_frozen_registration_spec() -> None:
    """Build registration data from frozen evidence."""

    spec = build_registration_spec()

    assert spec.name == "contention-logistic-v1"

    assert spec.algorithm == (
        "StandardScaler + LogisticRegression"
    )

    assert spec.target_horizon_sec == 30
    assert len(spec.training_run_ids) == 42

    assert spec.artifact_path == (
        "artifacts/models/"
        "contention_candidate_v1.joblib"
    )

    assert spec.artifact_sha256 == (
        EXPECTED_ARTIFACT_SHA256
    )

    assert spec.feature_schema[
        "ordered_features"
    ] == list(FEATURE_COLUMNS)

    assert spec.feature_schema[
        "feature_count"
    ] == 18

    assert spec.risk_thresholds[
        "high_contention_probability"
    ] == pytest.approx(0.34)

    assert spec.evaluation_metrics[
        "pr_auc"
    ] == pytest.approx(
        0.9822,
        abs=0.0001,
    )

    assert spec.evaluation_metrics[
        "recall"
    ] == pytest.approx(
        0.9744,
        abs=0.0001,
    )

    assert len(spec.code_version) == 40


def test_register_new_model_as_inactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Create an inactive model registration."""

    spec = build_registration_spec()
    session, mocked_session = _mock_session()

    monkeypatch.setattr(
        registration,
        "validate_training_experiments",
        lambda session, training_run_ids: None,
    )

    mocked_session.scalars.return_value.all.return_value = []

    result = register_model_version(
        session,
        spec,
    )

    assert result.created is True

    assert result.model_version.name == (
        "contention-logistic-v1"
    )

    assert result.model_version.is_active is False

    assert result.model_version.artifact_sha256 == (
        EXPECTED_ARTIFACT_SHA256
    )

    mocked_session.add.assert_called_once()
    mocked_session.commit.assert_called_once()
    mocked_session.refresh.assert_called_once()
    mocked_session.rollback.assert_not_called()


def test_matching_registration_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return an existing identical registration."""

    spec = build_registration_spec()
    existing = _existing_model(spec)

    session, mocked_session = _mock_session()

    monkeypatch.setattr(
        registration,
        "validate_training_experiments",
        lambda session, training_run_ids: None,
    )

    mocked_session.scalars.return_value.all.return_value = [
        existing
    ]

    result = register_model_version(
        session,
        spec,
    )

    assert result.created is False
    assert result.model_version is existing

    mocked_session.add.assert_not_called()
    mocked_session.commit.assert_not_called()
    mocked_session.refresh.assert_not_called()
    mocked_session.rollback.assert_not_called()


def test_conflicting_registration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not overwrite a different registration."""

    spec = build_registration_spec()
    existing = _existing_model(spec)

    existing.artifact_path = (
        "artifacts/models/different.joblib"
    )

    session, mocked_session = _mock_session()

    monkeypatch.setattr(
        registration,
        "validate_training_experiments",
        lambda session, training_run_ids: None,
    )

    mocked_session.scalars.return_value.all.return_value = [
        existing
    ]

    with pytest.raises(
        ModelRegistrationError,
        match="different registration",
    ):
        register_model_version(
            session,
            spec,
        )

    mocked_session.add.assert_not_called()
    mocked_session.commit.assert_not_called()
    mocked_session.rollback.assert_called_once()


def test_modified_artifact_blocks_specification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject registration when the artifact changed."""

    source_file = Path(
        "artifacts/models/"
        "contention_candidate_v1.joblib"
    )

    modified_file = (
        tmp_path / "modified.joblib"
    )

    modified_file.write_bytes(
        source_file.read_bytes()
        + b"modified"
    )

    monkeypatch.setattr(
        registration,
        "MODEL_ARTIFACT_FILE",
        modified_file,
    )

    with pytest.raises(
        ModelRegistrationError,
        match="checksum",
    ):
        build_registration_spec()