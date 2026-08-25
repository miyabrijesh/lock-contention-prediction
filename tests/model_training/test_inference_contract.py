import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import pytest

from model_training.training_data import (
    FEATURE_COLUMNS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "inference_contract_v1.md"
)

MODEL_FILE = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

SELECTION_REPORT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "candidate_selection_v1.json"
)

FINAL_REPORT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "final_test_evaluation_v1.json"
)

EXPECTED_MODEL_SHA256 = (
    "e255db6ee5d3a59c075b6249fc7d355e"
    "5107dd56b2ed7ddab695bb029db24704"
)


def _sha256(path: Path) -> str:
    """Calculate a file checksum."""

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def test_contract_contains_required_sections() -> None:
    """Require the complete inference contract."""

    text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_sections = [
        "# Contention Model Inference Contract",
        "## Frozen model artifact",
        "## Required feature order",
        "## Input validation",
        "## Output contract",
        "## Risk-level mapping",
        "## Prediction database mapping",
        "## Model-version registration mapping",
        "## Frozen test evaluation",
        "## Determinism requirements",
        "## Security requirements",
        "## Scientific limitations",
        "## Checkpoint 34A completion condition",
    ]

    missing_sections = [
        section
        for section in required_sections
        if section not in text
    ]

    assert not missing_sections, (
        f"Missing contract sections: "
        f"{missing_sections}"
    )


def test_contract_feature_order_matches_artifact() -> None:
    """Match the contract, loader, and artifact features."""

    text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    artifact: Any = joblib.load(MODEL_FILE)

    assert artifact["feature_columns"] == list(
        FEATURE_COLUMNS
    )

    previous_position = -1

    for feature_name in FEATURE_COLUMNS:
        marker = f"`{feature_name}`"
        position = text.find(
            marker,
            previous_position + 1,
        )

        assert position > previous_position, (
            f"Missing or incorrectly ordered feature: "
            f"{feature_name}"
        )

        previous_position = position


def test_contract_matches_frozen_candidate() -> None:
    """Verify model checksum and threshold."""

    text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    artifact: Any = joblib.load(MODEL_FILE)

    selection_report = json.loads(
        SELECTION_REPORT_FILE.read_text(
            encoding="utf-8"
        )
    )

    assert _sha256(MODEL_FILE) == (
        EXPECTED_MODEL_SHA256
    )

    assert EXPECTED_MODEL_SHA256 in text

    assert artifact["model_name"] == (
        "logistic_regression"
    )

    assert artifact[
        "classification_threshold"
    ] == pytest.approx(0.34)

    assert selection_report["selection"][
        "selected_threshold"
    ] == pytest.approx(0.34)

    assert "`probability < 0.34`" in text
    assert "`probability >= 0.34`" in text


def test_contract_matches_final_test_report() -> None:
    """Match documented and frozen test results."""

    text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    final_report = json.loads(
        FINAL_REPORT_FILE.read_text(
            encoding="utf-8"
        )
    )

    metrics = final_report["test_metrics"]

    assert metrics["pr_auc"] == pytest.approx(
        0.9822,
        abs=0.0001,
    )

    assert metrics["roc_auc"] == pytest.approx(
        0.9708,
        abs=0.0001,
    )

    assert metrics["f2"] == pytest.approx(
        0.9367,
        abs=0.0001,
    )

    assert metrics["precision"] == pytest.approx(
        0.8114,
        abs=0.0001,
    )

    assert metrics["recall"] == pytest.approx(
        0.9744,
        abs=0.0001,
    )

    for expected_text in (
        "0.9822",
        "0.9708",
        "0.9367",
        "0.8114",
        "0.9744",
        "False positives | 53",
        "False negatives | 6",
    ):
        assert expected_text in text