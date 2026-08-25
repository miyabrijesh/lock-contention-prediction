import json
from pathlib import Path

import numpy as np
import pytest

from model_training.train_baselines import (
    BASELINE_RANDOM_SEED,
    BaselineTrainingError,
    build_baseline_models,
    evaluate_probabilities,
    train_baseline_models,
    write_baseline_report,
)
from model_training.training_data import (
    load_training_data,
)


def test_build_expected_baseline_models() -> None:
    """Provide the three planned baseline classifiers."""

    models = build_baseline_models()

    assert set(models) == {
        "dummy_prior",
        "logistic_regression",
        "random_forest",
    }


def test_probability_metrics_are_calculated() -> None:
    """Calculate correct metrics for perfect predictions."""

    true_labels = np.array(
        [0, 0, 1, 1],
        dtype=np.int8,
    )

    probabilities = np.array(
        [0.1, 0.2, 0.8, 0.9],
        dtype=np.float64,
    )

    metrics = evaluate_probabilities(
        true_labels=true_labels,
        probabilities=probabilities,
    )

    assert metrics["roc_auc"] == pytest.approx(1.0)
    assert metrics["pr_auc"] == pytest.approx(1.0)
    assert metrics["accuracy"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)

    assert metrics["confusion_matrix"] == {
        "true_negative": 2,
        "false_positive": 0,
        "false_negative": 0,
        "true_positive": 2,
    }


def test_invalid_threshold_is_rejected() -> None:
    """Reject thresholds outside the open zero-to-one range."""

    with pytest.raises(
        BaselineTrainingError,
        match="threshold",
    ):
        evaluate_probabilities(
            true_labels=np.array([0, 1]),
            probabilities=np.array([0.2, 0.8]),
            threshold=1.0,
        )


def test_train_baselines_without_using_test_split() -> None:
    """Train on train and evaluate only on validation."""

    data = load_training_data()

    report = train_baseline_models(data)

    assert report["checkpoint"] == "33B"
    assert report["random_seed"] == (
        BASELINE_RANDOM_SEED
    )

    dataset = report["dataset"]

    assert isinstance(dataset, dict)
    assert dataset["training"]["row_count"] == 1864
    assert dataset["validation"]["row_count"] == 400
    assert dataset["test"]["usage"] == "NOT_EVALUATED"

    models = report["models"]

    assert isinstance(models, dict)

    assert set(models) == {
        "dummy_prior",
        "logistic_regression",
        "random_forest",
    }

    for model_result in models.values():
        assert isinstance(model_result, dict)

        metrics = model_result["metrics"]

        assert isinstance(metrics, dict)

        for metric_name in (
            "roc_auc",
            "pr_auc",
            "accuracy",
            "balanced_accuracy",
            "precision",
            "recall",
            "f1",
            "brier_score",
        ):
            metric_value = metrics[metric_name]

            assert isinstance(metric_value, float)
            assert 0 <= metric_value <= 1

        confusion = metrics["confusion_matrix"]

        assert isinstance(confusion, dict)
        assert sum(confusion.values()) == 400


def test_baseline_training_is_deterministic() -> None:
    """Repeated training must produce identical results."""

    data = load_training_data()

    first_report = train_baseline_models(data)
    second_report = train_baseline_models(data)

    assert first_report == second_report


def test_write_baseline_report(
    tmp_path: Path,
) -> None:
    """Write a readable JSON baseline report."""

    data = load_training_data()
    report = train_baseline_models(data)

    output_file = (
        tmp_path / "baseline_results.json"
    )

    written_file = write_baseline_report(
        report,
        output_file,
    )

    assert written_file == output_file
    assert output_file.is_file()

    loaded_report = json.loads(
        output_file.read_text(encoding="utf-8")
    )

    assert loaded_report == report