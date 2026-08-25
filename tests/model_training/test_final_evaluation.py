import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from model_training.evaluate_candidate import (
    DEFAULT_FINAL_REPORT,
    DEFAULT_MODEL_FILE,
    FinalEvaluationError,
    load_frozen_candidate,
    run_final_evaluation,
    write_final_report_once,
)


def _sha256(path: Path) -> str:
    """Calculate a file SHA-256 checksum."""

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _load_final_report() -> dict[str, Any]:
    """Load the frozen Checkpoint 33D report."""

    loaded_value: Any = json.loads(
        DEFAULT_FINAL_REPORT.read_text(
            encoding="utf-8"
        )
    )

    assert isinstance(loaded_value, dict)

    report: dict[str, Any] = loaded_value

    return report


def test_frozen_candidate_checksum_is_valid() -> None:
    """Verify the exact model selected in Checkpoint 33C."""

    (
        artifact,
        selection_report,
        model_sha256,
        selection_report_sha256,
    ) = load_frozen_candidate()

    assert artifact["model_name"] == (
        "logistic_regression"
    )

    assert artifact[
        "classification_threshold"
    ] == pytest.approx(0.34)

    assert len(artifact["feature_columns"]) == 18

    assert model_sha256 == _sha256(
        DEFAULT_MODEL_FILE
    )

    assert len(selection_report_sha256) == 64

    assert selection_report["test"]["usage"] == (
        "NOT_EVALUATED"
    )


def test_final_report_contains_frozen_results() -> None:
    """Validate the one-time final evaluation report."""

    report = _load_final_report()

    assert report["checkpoint"] == "33D"

    assert report["evaluation_status"] == (
        "FINAL_TEST_EVALUATION_COMPLETED"
    )

    assert report["one_time_evaluation"] is True

    assert report["model"]["model_name"] == (
        "logistic_regression"
    )

    assert report["model"][
        "classification_threshold"
    ] == pytest.approx(0.34)

    assert report["dataset"]["split"] == "test"
    assert report["dataset"]["row_count"] == 402

    assert report["dataset"][
        "experiment_count"
    ] == 9

    assert report["dataset"][
        "negative_label_count"
    ] == 168

    assert report["dataset"][
        "positive_label_count"
    ] == 234

    metrics = report["test_metrics"]

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

    confusion = metrics["confusion_matrix"]

    assert sum(confusion.values()) == 402

    boundary = report[
        "interpretation_boundary"
    ]

    assert boundary[
        "test_split_used_for_model_selection"
    ] is False

    assert boundary[
        "test_split_used_for_threshold_selection"
    ] is False

    assert boundary[
        "model_retrained_during_test_evaluation"
    ] is False


def test_final_report_matches_model_checksum() -> None:
    """Link the final report to the frozen model file."""

    report = _load_final_report()

    assert report["model"]["model_sha256"] == (
        _sha256(DEFAULT_MODEL_FILE)
    )

    assert report["dataset"][
        "test_file_sha256"
    ]

    assert len(
        report["dataset"]["test_file_sha256"]
    ) == 64

    assert len(
        report["dataset"]["assignment_sha256"]
    ) == 64


def test_final_report_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    """Refuse to overwrite a completed evaluation."""

    output_file = (
        tmp_path / "final_report.json"
    )

    first_report = {
        "checkpoint": "33D",
        "evaluation_status": (
            "FINAL_TEST_EVALUATION_COMPLETED"
        ),
    }

    write_final_report_once(
        report=first_report,
        output_file=output_file,
    )

    original_contents = output_file.read_text(
        encoding="utf-8"
    )

    with pytest.raises(
        FinalEvaluationError,
        match="already exists",
    ):
        write_final_report_once(
            report={"changed": True},
            output_file=output_file,
        )

    assert output_file.read_text(
        encoding="utf-8"
    ) == original_contents


def test_existing_report_blocks_evaluation_early(
    tmp_path: Path,
) -> None:
    """Stop before loading or evaluating the test split."""

    output_file = (
        tmp_path / "existing_report.json"
    )

    output_file.write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        FinalEvaluationError,
        match="already exists",
    ):
        run_final_evaluation(
            model_file=(
                tmp_path / "missing-model.joblib"
            ),
            selection_report_file=(
                tmp_path / "missing-selection.json"
            ),
            output_file=output_file,
        )