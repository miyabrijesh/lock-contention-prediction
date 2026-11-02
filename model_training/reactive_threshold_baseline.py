"""Evaluate a validation-selected reactive lock-monitoring baseline.

The baseline emits an alert when the current number of waiting locks is at
least a threshold selected only on the validation split.  It is deliberately
simple: unlike the predictive model, it does not use a learned combination of
telemetry features or estimate a future probability.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Final

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    fbeta_score,
    precision_score,
    recall_score,
)

from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    TrainingDataBundle,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT / "analysis_outputs" / "reactive_threshold_baseline_v1.json"
)
FEATURE_NAME: Final[str] = "waiting_locks"
F_BETA: Final[float] = 2.0


class ReactiveBaselineError(ValueError):
    """Raised when the baseline cannot be evaluated safely."""


def _metrics(labels: np.ndarray, predictions: np.ndarray) -> dict[str, Any]:
    """Return classification metrics for one binary alert rule."""

    matrix = confusion_matrix(labels, predictions, labels=[0, 1])
    true_negative, false_positive = map(int, matrix[0])
    false_negative, true_positive = map(int, matrix[1])

    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "balanced_accuracy": float(
            balanced_accuracy_score(labels, predictions)
        ),
        "precision": float(
            precision_score(labels, predictions, zero_division=0)
        ),
        "recall": float(
            recall_score(labels, predictions, zero_division=0)
        ),
        "f2": float(
            fbeta_score(labels, predictions, beta=F_BETA, zero_division=0)
        ),
        "specificity": float(
            true_negative / (true_negative + false_positive)
        ),
        "confusion_matrix": {
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_positive": true_positive,
        },
    }


def _candidate_thresholds(values: np.ndarray) -> tuple[int, ...]:
    """Return positive integer waiting-lock thresholds from validation data."""

    positive_values = sorted({int(value) for value in values if value > 0})

    if not positive_values:
        raise ReactiveBaselineError(
            "Validation data contains no positive waiting-lock values."
        )

    return tuple(positive_values)


def _select_threshold(data: TrainingDataBundle) -> tuple[int, dict[str, Any]]:
    """Select the baseline threshold on validation F2 only."""

    values = data.validation.features[FEATURE_NAME].to_numpy(dtype=np.float64)
    labels = data.validation.target.to_numpy(dtype=np.int8)
    candidates: list[tuple[float, float, float, int, dict[str, Any]]] = []

    for threshold in _candidate_thresholds(values):
        predictions = (values >= threshold).astype(np.int8)
        metrics = _metrics(labels, predictions)
        candidates.append(
            (
                metrics["f2"],
                metrics["recall"],
                metrics["precision"],
                -threshold,
                metrics,
            )
        )

    _, _, _, negative_threshold, metrics = max(candidates)
    return -negative_threshold, metrics


def run_reactive_baseline(
    *,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    split_directory: Path = DEFAULT_SPLIT_DIRECTORY,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
) -> dict[str, Any]:
    """Select the rule on validation data and evaluate it once on test data."""

    data = load_training_data(
        split_directory=split_directory,
        manifest_file=manifest_file,
    )

    threshold, validation_metrics = _select_threshold(data)
    test_values = data.test.features[FEATURE_NAME].to_numpy(dtype=np.float64)
    test_labels = data.test.target.to_numpy(dtype=np.int8)
    test_predictions = (test_values >= threshold).astype(np.int8)
    test_metrics = _metrics(test_labels, test_predictions)

    report = {
        "schema_version": "1.0.0",
        "analysis_name": "reactive_waiting_lock_threshold_baseline",
        "purpose": (
            "Provide a simple threshold-based monitoring baseline for "
            "comparison with the learned future-risk predictor."
        ),
        "rule": {
            "feature": FEATURE_NAME,
            "alert_condition": f"{FEATURE_NAME} >= {threshold}",
            "threshold_selection_split": "validation",
            "threshold_selection_metric": "validation_f2",
            "tie_break_order": ["f2", "recall", "precision", "lower_threshold"],
        },
        "validation": {
            "row_count": data.validation.row_count,
            "experiment_count": data.validation.experiment_count,
            "metrics": validation_metrics,
        },
        "test": {
            "row_count": data.test.row_count,
            "experiment_count": data.test.experiment_count,
            "metrics": test_metrics,
        },
        "interpretation_boundary": {
            "test_split_used_for_threshold_selection": False,
            "baseline_uses_only_current_telemetry": True,
            "baseline_is_reactive_not_probabilistic": True,
            "test_observations_within_experiments_are_temporally_dependent": True,
        },
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    """Run the baseline command."""

    parser = argparse.ArgumentParser(
        description="Evaluate a reactive waiting-lock threshold baseline."
    )
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    arguments = parser.parse_args()

    try:
        report = run_reactive_baseline(output_file=arguments.output_file)
    except ReactiveBaselineError as error:
        print(f"Reactive baseline failed: {error}")
        return 1

    rule = report["rule"]
    metrics = report["test"]["metrics"]
    matrix = metrics["confusion_matrix"]
    print("Reactive threshold baseline completed:")
    print(f"  Rule: {rule['alert_condition']}")
    print(f"  Test accuracy: {metrics['accuracy']:.4f}")
    print(f"  Test precision: {metrics['precision']:.4f}")
    print(f"  Test recall: {metrics['recall']:.4f}")
    print(f"  Test F2: {metrics['f2']:.4f}")
    print(
        "  Test confusion matrix: "
        f"TN={matrix['true_negative']}, FP={matrix['false_positive']}, "
        f"FN={matrix['false_negative']}, TP={matrix['true_positive']}"
    )
    print(f"  Saved: {arguments.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
