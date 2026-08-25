import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import ModelVersion
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    FEATURE_COLUMNS,
)
from model_training.verify_phase6 import (
    BASELINE_REPORT_FILE,
    DEFAULT_COMPLETION_REPORT,
    FINAL_EVALUATION_FILE,
    INFERENCE_CONTRACT_FILE,
    MODEL_FILE,
    SELECTION_REPORT_FILE,
    verify_database_registration,
    verify_dataset_evidence,
    verify_inference_evidence,
    verify_model_evidence,
    write_completion_report,
)


def _sha256(path: Path) -> str:
    """Calculate one file checksum."""

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _load_completion_report() -> dict[str, Any]:
    """Load the generated Phase 6 report."""

    loaded_value: Any = json.loads(
        DEFAULT_COMPLETION_REPORT.read_text(
            encoding="utf-8"
        )
    )

    assert isinstance(loaded_value, dict)

    report: dict[str, Any] = loaded_value

    return report


def test_phase6_completion_report_contract() -> None:
    """Require all Phase 6 checkpoints to be complete."""

    report = _load_completion_report()

    assert report["phase"] == 6
    assert report["status"] == "COMPLETED"

    checkpoints = report["checkpoints"]

    assert checkpoints == {
        "28": "COMPLETED",
        "29": "COMPLETED",
        "30": "COMPLETED",
        "31": "COMPLETED",
        "32": "COMPLETED",
        "33": "COMPLETED",
        "34A": "COMPLETED",
        "34B": "COMPLETED",
        "34C": "COMPLETED",
        "34D": "COMPLETED",
    }

    assert report["dataset"]["row_count"] == 2666

    assert report["dataset"][
        "experiment_count"
    ] == 60

    assert report["model"]["registered_name"] == (
        "contention-logistic-v1"
    )

    assert report["model"][
        "classification_threshold"
    ] == pytest.approx(0.34)

    assert report["registration"][
        "is_active"
    ] is False

    phase7_boundary = report[
        "phase7_boundary"
    ]

    assert phase7_boundary[
        "model_active"
    ] is False

    assert phase7_boundary[
        "live_predictions_enabled"
    ] is False

    assert phase7_boundary[
        "prediction_api_implemented"
    ] is False


def test_completion_report_matches_evidence_files() -> None:
    """Match completion evidence to tracked files."""

    report = _load_completion_report()

    assert report["model"][
        "artifact_sha256"
    ] == _sha256(MODEL_FILE)

    assert report["model"][
        "artifact_sha256"
    ] == EXPECTED_ARTIFACT_SHA256

    assert report["model"][
        "baseline_report_sha256"
    ] == _sha256(BASELINE_REPORT_FILE)

    assert report["model"][
        "selection_report_sha256"
    ] == _sha256(SELECTION_REPORT_FILE)

    assert report["model"][
        "final_report_sha256"
    ] == _sha256(FINAL_EVALUATION_FILE)

    assert report["inference"][
        "contract_sha256"
    ] == _sha256(INFERENCE_CONTRACT_FILE)


def test_static_phase6_verifiers_pass() -> None:
    """Recheck dataset, model, and inference evidence."""

    dataset = verify_dataset_evidence()
    model = verify_model_evidence()
    inference = verify_inference_evidence()

    assert dataset["row_count"] == 2666
    assert dataset["experiment_count"] == 60

    assert model["artifact_sha256"] == (
        EXPECTED_ARTIFACT_SHA256
    )

    assert model["test_metrics"][
        "pr_auc"
    ] == pytest.approx(
        0.9822,
        abs=0.0001,
    )

    assert inference[
        "deterministic_repeated_inference"
    ] is True

    assert inference["feature_count"] == 18


def test_database_registration_verifier() -> None:
    """Validate one mocked inactive registration."""

    model_version = ModelVersion(
        id=uuid4(),
        name="contention-logistic-v1",
        algorithm=(
            "StandardScaler + LogisticRegression"
        ),
        feature_schema={
            "schema_version": "1.0.0",
            "feature_count": 18,
            "ordered_features": list(
                FEATURE_COLUMNS
            ),
        },
        target_horizon_sec=30,
        training_run_ids=[
            str(
                UUID(
                    int=index + 1
                )
            )
            for index in range(42)
        ],
        validation_strategy=(
            "Grouped by experiment_run_id: "
            "42 train, 9 validation, 9 test"
        ),
        risk_thresholds={
            "high_contention_probability": 0.34
        },
        evaluation_metrics={
            "pr_auc": 0.9822,
            "recall": 0.9744,
        },
        artifact_path=(
            "artifacts/models/"
            "contention_candidate_v1.joblib"
        ),
        artifact_sha256=(
            EXPECTED_ARTIFACT_SHA256
        ),
        code_version="a" * 40,
        created_at=datetime.now(UTC),
        is_active=False,
    )

    mocked_session = MagicMock()

    mocked_session.scalars.return_value.all.return_value = [
        model_version
    ]

    session = cast(
        Session,
        mocked_session,
    )

    result = verify_database_registration(
        session
    )

    assert result["name"] == (
        "contention-logistic-v1"
    )

    assert result["is_active"] is False
    assert result["training_run_count"] == 42

    assert result["artifact_sha256"] == (
        EXPECTED_ARTIFACT_SHA256
    )


def test_write_completion_report(
    tmp_path: Path,
) -> None:
    """Write deterministic JSON completion evidence."""

    output_file = (
        tmp_path / "phase6.json"
    )

    report = {
        "schema_version": "1.0.0",
        "phase": 6,
        "status": "COMPLETED",
    }

    written_file = write_completion_report(
        report=report,
        output_file=output_file,
    )

    assert written_file == output_file

    assert json.loads(
        output_file.read_text(
            encoding="utf-8"
        )
    ) == report