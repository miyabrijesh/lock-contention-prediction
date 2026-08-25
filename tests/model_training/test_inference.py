from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.models.enums import RiskLevel
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    EXPECTED_THRESHOLD,
    FEATURE_COLUMNS,
    REGISTERED_MODEL_NAME,
    ContentionPredictor,
    InferenceError,
    InferenceResult,
    features_from_metric_sample,
    risk_level_for_probability,
    validate_feature_mapping,
)
from model_training.training_data import (
    load_training_data,
)


def _valid_features() -> dict[str, object]:
    """Return one real training feature row."""

    data = load_training_data()

    values = (
        data.train.features.iloc[0].to_dict()
    )

    return {
        feature_name: values[feature_name]
        for feature_name in FEATURE_COLUMNS
    }


def test_load_checksum_verified_predictor() -> None:
    """Load the exact frozen candidate artifact."""

    predictor = ContentionPredictor.load()

    assert predictor.threshold == pytest.approx(
        EXPECTED_THRESHOLD
    )

    assert predictor.artifact_sha256 == (
        EXPECTED_ARTIFACT_SHA256
    )

    assert predictor.feature_columns == (
        FEATURE_COLUMNS
    )


def test_repeated_inference_is_deterministic() -> None:
    """Identical inputs must produce identical results."""

    predictor = ContentionPredictor.load()
    features = _valid_features()

    first_result = predictor.predict(features)
    second_result = predictor.predict(features)

    assert first_result == second_result

    assert 0 <= first_result.probability <= 1

    assert first_result.risk_level in {
        RiskLevel.LOW,
        RiskLevel.HIGH,
    }

    assert first_result.threshold_used == (
        pytest.approx(0.34)
    )

    assert first_result.model_name == (
        REGISTERED_MODEL_NAME
    )


def test_risk_threshold_boundary() -> None:
    """The exact threshold must produce HIGH risk."""

    assert risk_level_for_probability(
        0.339999
    ) == RiskLevel.LOW

    assert risk_level_for_probability(
        0.34
    ) == RiskLevel.HIGH

    assert risk_level_for_probability(
        0.90
    ) == RiskLevel.HIGH


def test_missing_feature_is_rejected() -> None:
    """Reject incomplete feature mappings."""

    features = _valid_features()
    features.pop("blocked_sessions")

    with pytest.raises(
        InferenceError,
        match="Missing required features",
    ):
        validate_feature_mapping(features)


def test_unexpected_feature_is_rejected() -> None:
    """Reject unknown model inputs."""

    features = _valid_features()
    features["future_unknown_value"] = 1

    with pytest.raises(
        InferenceError,
        match="Unexpected features",
    ):
        validate_feature_mapping(features)


@pytest.mark.parametrize(
    ("feature_name", "invalid_value", "message"),
    [
        (
            "blocked_sessions",
            -1,
            "must not be negative",
        ),
        (
            "failure_rate",
            1.1,
            "between 0 and 1",
        ),
        (
            "hotspot_ratio",
            float("nan"),
            "must be finite",
        ),
        (
            "timeout_rate",
            float("inf"),
            "must be finite",
        ),
        (
            "active_users",
            True,
            "not boolean",
        ),
        (
            "requests_per_sec",
            "12.5",
            "must be numeric",
        ),
        (
            "connection_utilization",
            None,
            "must be numeric",
        ),
    ],
)
def test_invalid_feature_value_is_rejected(
    feature_name: str,
    invalid_value: object,
    message: str,
) -> None:
    """Reject unsafe model input values."""

    features = _valid_features()
    features[feature_name] = invalid_value

    with pytest.raises(
        InferenceError,
        match=message,
    ):
        validate_feature_mapping(features)


def test_modified_artifact_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject an artifact with the wrong checksum."""

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

    with pytest.raises(
        InferenceError,
        match="checksum verification failed",
    ):
        ContentionPredictor.load(
            artifact_path=modified_file,
        )


def test_extract_features_from_complete_sample() -> None:
    """Extract exactly the approved model inputs."""

    features = _valid_features()

    sample = SimpleNamespace(
        is_complete=True,
        **features,
    )

    extracted = features_from_metric_sample(
        sample
    )

    assert tuple(extracted) == FEATURE_COLUMNS
    assert extracted == features


def test_incomplete_metric_sample_is_rejected() -> None:
    """Do not predict from incomplete telemetry."""

    features = _valid_features()

    sample = SimpleNamespace(
        is_complete=False,
        **features,
    )

    with pytest.raises(
        InferenceError,
        match="must be complete",
    ):
        features_from_metric_sample(sample)


def test_prediction_result_is_json_compatible() -> None:
    """Return the documented inference output."""

    result = InferenceResult(
        probability=0.75,
        risk_level=RiskLevel.HIGH,
        threshold_used=0.34,
        model_name=REGISTERED_MODEL_NAME,
        model_artifact_sha256=(
            EXPECTED_ARTIFACT_SHA256
        ),
        feature_schema_version="1.0.0",
        target_horizon_sec=30,
    )

    assert result.as_dictionary() == {
        "probability": 0.75,
        "risk_level": "HIGH",
        "threshold_used": 0.34,
        "model_name": "contention-logistic-v1",
        "model_artifact_sha256": (
            EXPECTED_ARTIFACT_SHA256
        ),
        "feature_schema_version": "1.0.0",
        "target_horizon_sec": 30,
    }