import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pytest

from model_training.select_candidate import (
    DEFAULT_BASELINE_REPORT,
    build_candidate,
    load_baseline_report,
    optimize_validation_threshold,
    save_candidate_artifacts,
    select_model_by_validation_pr_auc,
)
from model_training.training_data import (
    FEATURE_COLUMNS,
    load_training_data,
)


def _sha256(path: Path) -> str:
    """Calculate a test file checksum."""

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def test_select_logistic_regression_by_pr_auc() -> None:
    """Select the strongest validation PR-AUC model."""

    report = load_baseline_report(
        DEFAULT_BASELINE_REPORT
    )

    model_name, pr_auc = (
        select_model_by_validation_pr_auc(
            report
        )
    )

    assert model_name == "logistic_regression"
    assert pr_auc == pytest.approx(0.9712, abs=0.0001)


def test_threshold_selection_is_deterministic() -> None:
    """Resolve equal metric results deterministically."""

    true_labels = np.array(
        [0, 0, 1, 1],
        dtype=np.int8,
    )

    probabilities = np.array(
        [0.10, 0.40, 0.35, 0.80],
        dtype=np.float64,
    )

    selected, search_results = (
        optimize_validation_threshold(
            true_labels=true_labels,
            probabilities=probabilities,
        )
    )

    assert len(search_results) == 91
    assert selected["threshold"] == pytest.approx(
        0.35
    )
    assert selected["recall"] == pytest.approx(1.0)


def test_build_candidate_without_test_evaluation() -> None:
    """Select the model without evaluating test data."""

    data = load_training_data()
    baseline_report = load_baseline_report()

    model, report = build_candidate(
        data=data,
        baseline_report=baseline_report,
    )

    assert model is not None
    assert report["checkpoint"] == "33C"

    selection = report["selection"]

    assert selection["model_name"] == (
        "logistic_regression"
    )

    assert selection["selected_threshold"] == (
        pytest.approx(0.34)
    )

    assert selection[
        "model_selection_metric"
    ] == "validation_pr_auc"

    assert selection[
        "threshold_selection_metric"
    ] == "validation_f2"

    assert report["training"]["fit_split"] == "train"

    assert report["validation"]["usage"] == (
        "MODEL_AND_THRESHOLD_SELECTION"
    )

    assert report["test"]["usage"] == "NOT_EVALUATED"

    assert report[
        "feature_contract"
    ]["feature_columns"] == list(FEATURE_COLUMNS)


def test_save_and_reload_candidate_artifact(
    tmp_path: Path,
) -> None:
    """Save a loadable model with matching provenance."""

    data = load_training_data()
    baseline_report = load_baseline_report()

    model, report = build_candidate(
        data=data,
        baseline_report=baseline_report,
    )

    model_file = (
        tmp_path / "candidate.joblib"
    )

    report_file = (
        tmp_path / "selection.json"
    )

    saved_model_file, saved_report_file = (
        save_candidate_artifacts(
            model=model,
            report=report,
            model_file=model_file,
            report_file=report_file,
        )
    )

    assert saved_model_file == model_file
    assert saved_report_file == report_file
    assert model_file.is_file()
    assert report_file.is_file()

    loaded_artifact: Any = joblib.load(
        model_file
    )

    assert loaded_artifact["model_name"] == (
        "logistic_regression"
    )

    assert loaded_artifact[
        "classification_threshold"
    ] == pytest.approx(0.34)

    assert loaded_artifact[
        "feature_columns"
    ] == list(FEATURE_COLUMNS)

    assert hasattr(
        loaded_artifact["model"],
        "predict_proba",
    )

    loaded_report = json.loads(
        report_file.read_text(encoding="utf-8")
    )

    assert loaded_report["artifact"][
        "model_sha256"
    ] == _sha256(model_file)

    assert loaded_report["test"]["usage"] == (
        "NOT_EVALUATED"
    )