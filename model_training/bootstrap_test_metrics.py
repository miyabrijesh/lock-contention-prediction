"""Bootstrap confidence intervals for frozen test-set metrics.

This script does not retrain the model or tune its threshold. It only
quantifies uncertainty around the already frozen test-set evaluation.
"""

import argparse
import json
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    precision_score,
    recall_score,
)

from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

DEFAULT_MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

DEFAULT_OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "analysis_outputs"
    / "bootstrap_test_metrics_v1.json"
)

DEFAULT_BOOTSTRAP_SAMPLES: Final[int] = 10_000
DEFAULT_RANDOM_SEED: Final[int] = 20260925


class BootstrapAnalysisError(ValueError):
    """Raised when bootstrap analysis cannot run safely."""


def _load_artifact(model_file: Path) -> dict[str, Any]:
    """Load and validate the frozen model artifact."""

    if not model_file.is_file():
        raise BootstrapAnalysisError(
            f"Frozen model artifact was not found: {model_file}"
        )

    artifact = joblib.load(model_file)

    if not isinstance(artifact, dict):
        raise BootstrapAnalysisError(
            "Frozen model artifact must be a dictionary."
        )

    if artifact.get("model") is None:
        raise BootstrapAnalysisError(
            "Frozen model artifact has no model."
        )

    threshold = artifact.get("classification_threshold")

    if not isinstance(threshold, (int, float)):
        raise BootstrapAnalysisError(
            "Frozen model artifact has no valid threshold."
        )

    if not 0 < float(threshold) < 1:
        raise BootstrapAnalysisError(
            "Classification threshold must be between 0 and 1."
        )

    return artifact


def _positive_probabilities(
    model: Any,
    features: np.ndarray,
) -> np.ndarray:
    """Return positive-class probabilities from the frozen model."""

    predict_proba = getattr(model, "predict_proba", None)

    if predict_proba is None:
        raise BootstrapAnalysisError(
            "Frozen model does not support probability prediction."
        )

    probabilities = np.asarray(
        predict_proba(features),
        dtype=np.float64,
    )

    if (
        probabilities.ndim != 2
        or probabilities.shape[0] != len(features)
        or probabilities.shape[1] != 2
    ):
        raise BootstrapAnalysisError(
            "Frozen model returned an invalid probability matrix."
        )

    positive_probabilities = probabilities[:, 1]

    if not np.isfinite(positive_probabilities).all():
        raise BootstrapAnalysisError(
            "Frozen model returned non-finite probabilities."
        )

    if (
        (positive_probabilities < 0).any()
        or (positive_probabilities > 1).any()
    ):
        raise BootstrapAnalysisError(
            "Frozen model returned probabilities outside 0 to 1."
        )

    return positive_probabilities


def _calculate_metrics(
    labels: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    """Calculate the four guide-requested metrics."""

    predictions = (probabilities >= threshold).astype(np.int8)

    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "recall": float(
            recall_score(labels, predictions, zero_division=0)
        ),
        "precision": float(
            precision_score(labels, predictions, zero_division=0)
        ),
        "average_precision": float(
            average_precision_score(labels, probabilities)
        ),
    }


def _percentile_interval(
    values: np.ndarray,
    confidence_level: float,
) -> dict[str, float]:
    """Return a two-sided percentile bootstrap interval."""

    alpha = 1.0 - confidence_level
    lower, upper = np.quantile(
        values,
        [alpha / 2, 1 - (alpha / 2)],
    )

    return {
        "lower": float(lower),
        "upper": float(upper),
    }


def run_bootstrap_analysis(
    *,
    model_file: Path = DEFAULT_MODEL_FILE,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    split_directory: Path = DEFAULT_SPLIT_DIRECTORY,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_SAMPLES,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    """Calculate stratified bootstrap CIs on the frozen test split."""

    if n_bootstrap < 1_000:
        raise BootstrapAnalysisError(
            "Use at least 1,000 bootstrap samples."
        )

    data = load_training_data(
        split_directory=split_directory,
        manifest_file=manifest_file,
    )

    artifact = _load_artifact(model_file)

    if artifact.get("feature_columns") != list(data.feature_columns):
        raise BootstrapAnalysisError(
            "Model features do not match the frozen dataset contract."
        )

    threshold = float(artifact["classification_threshold"])

    test_features = data.test.features.to_numpy(dtype=np.float64)
    test_labels = data.test.target.to_numpy(dtype=np.int8)

    probabilities = _positive_probabilities(
        artifact["model"],
        test_features,
    )

    point_estimates = _calculate_metrics(
        test_labels,
        probabilities,
        threshold,
    )

    negative_indices = np.flatnonzero(test_labels == 0)
    positive_indices = np.flatnonzero(test_labels == 1)

    if len(negative_indices) == 0 or len(positive_indices) == 0:
        raise BootstrapAnalysisError(
            "Frozen test split must contain both classes."
        )

    metric_names = list(point_estimates)
    bootstrap_values = {
        metric_name: np.empty(n_bootstrap, dtype=np.float64)
        for metric_name in metric_names
    }

    rng = np.random.default_rng(random_seed)

    for iteration in range(n_bootstrap):
        sampled_negative = rng.choice(
            negative_indices,
            size=len(negative_indices),
            replace=True,
        )
        sampled_positive = rng.choice(
            positive_indices,
            size=len(positive_indices),
            replace=True,
        )

        sampled_indices = np.concatenate(
            [sampled_negative, sampled_positive]
        )

        sampled_metrics = _calculate_metrics(
            test_labels[sampled_indices],
            probabilities[sampled_indices],
            threshold,
        )

        for metric_name, value in sampled_metrics.items():
            bootstrap_values[metric_name][iteration] = value

    confidence_intervals = {
        metric_name: _percentile_interval(
            values,
            confidence_level=0.95,
        )
        for metric_name, values in bootstrap_values.items()
    }

    report = {
        "schema_version": "1.0.0",
        "analysis_name": "stratified_bootstrap_test_metrics",
        "purpose": (
            "Quantify uncertainty around the already frozen test-set "
            "results without retraining, threshold tuning, or model "
            "selection."
        ),
        "model": {
            "model_name": artifact.get("model_name"),
            "model_file": str(model_file),
            "classification_threshold": threshold,
        },
        "dataset": {
            "split": "test",
            "row_count": data.test.row_count,
            "experiment_count": data.test.experiment_count,
            "negative_label_count": len(negative_indices),
            "positive_label_count": len(positive_indices),
            "assignment_sha256": data.assignment_sha256,
        },
        "method": {
            "resampling_method": "stratified_bootstrap",
            "bootstrap_samples": n_bootstrap,
            "random_seed": random_seed,
            "confidence_level": 0.95,
            "interval_method": "percentile",
        },
        "point_estimates": point_estimates,
        "confidence_intervals_95": confidence_intervals,
        "interpretation_boundary": {
            "model_retrained": False,
            "threshold_retuned": False,
            "test_split_used_for_model_selection": False,
            "intervals_describe_current_frozen_dataset": True,
        },
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with output_file.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")

    return report


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Calculate stratified bootstrap confidence intervals "
            "for frozen test metrics."
        )
    )

    parser.add_argument(
        "--n-bootstrap",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
    )

    return parser.parse_args()


def main() -> int:
    """Run bootstrap confidence-interval analysis."""

    arguments = parse_arguments()

    try:
        report = run_bootstrap_analysis(
            n_bootstrap=arguments.n_bootstrap,
            random_seed=arguments.seed,
            output_file=arguments.output_file,
        )
    except BootstrapAnalysisError as error:
        print(f"Bootstrap analysis failed: {error}")
        return 1

    print("Bootstrap confidence intervals completed:")
    print(
        f"  Test rows: {report['dataset']['row_count']} "
        f"from {report['dataset']['experiment_count']} experiments"
    )
    print(
        "  Fixed threshold: "
        f"{report['model']['classification_threshold']:.2f}"
    )

    for metric_name, estimate in report["point_estimates"].items():
        interval = report["confidence_intervals_95"][metric_name]
        print(
            f"  {metric_name}: {estimate:.4f} "
            f"(95% CI {interval['lower']:.4f}–"
            f"{interval['upper']:.4f})"
        )

    print(f"  Saved: {arguments.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())