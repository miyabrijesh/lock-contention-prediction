from pathlib import Path

from backend.app.schemas.prediction import (
    PredictionRead,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "prediction_api_contract_v1.md"
)


def test_prediction_api_contract_content() -> None:
    """Require authentication and leakage boundaries."""

    contract_text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_content = (
        "# Prediction API Contract v1",
        "X-Internal-Token",
        "X-Admin-Token",
        "METRIC_SAMPLE_NOT_FOUND",
        "ACTIVE_MODEL_NOT_FOUND",
        "PREDICTION_NOT_FOUND",
        "METRIC_SAMPLE_INCOMPLETE",
        "MODEL_ARTIFACT_INVALID",
        "MODEL_PROVENANCE_INVALID",
        "PREDICTION_INPUT_INVALID",
        "PREDICTION_CONFLICT",
        "STALE_TARGET_WINDOW",
        "Data-leakage boundary",
        "Idempotency",
        "git diff --check",
    )

    missing_content = [
        value
        for value in required_content
        if value not in contract_text
    ]

    assert not missing_content, (
        "The prediction API contract is missing: "
        f"{missing_content}"
    )


def test_prediction_api_contract_defines_routes() -> None:
    """Require all version-one prediction endpoints."""

    contract_text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_routes = (
        (
            "POST",
            (
                "/api/v1/predictions/"
                "metric-samples/{metric_sample_id}"
            ),
        ),
        (
            "GET",
            (
                "/api/v1/predictions/"
                "metric-samples/{metric_sample_id}"
            ),
        ),
        (
            "GET",
            (
                "/api/v1/predictions/"
                "{prediction_id}"
            ),
        ),
    )

    for method, path in required_routes:
        assert method in contract_text
        assert path in contract_text


def test_prediction_response_schema_is_complete() -> None:
    """Require persisted prediction provenance in responses."""

    actual_fields = set(
        PredictionRead.model_fields
    )

    expected_fields = {
        "id",
        "experiment_run_id",
        "metric_sample_id",
        "model_version_id",
        "predicted_at",
        "target_window_start",
        "target_window_end",
        "probability",
        "risk_level",
        "threshold_used",
        "is_stale",
        "quality_flags",
        "actual_label",
    }

    assert actual_fields == expected_fields