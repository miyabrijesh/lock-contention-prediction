"""Estimate frozen-test uncertainty with experiment-cluster bootstrap resampling.

Each bootstrap draw samples whole held-out experiments, with replacement,
instead of treating two-second telemetry rows as independent observations.
The model and its classification threshold remain frozen throughout.
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
    EXPERIMENT_ID_COLUMN,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_FILE: Final[Path] = (
    PROJECT_ROOT / "artifacts" / "models" / "contention_candidate_v1.joblib"
)
DEFAULT_OUTPUT_FILE: Final[Path] = (
    PROJECT_ROOT / "analysis_outputs" / "clustered_bootstrap_test_metrics_v1.json"
)
DEFAULT_BOOTSTRAP_SAMPLES: Final[int] = 10_000
DEFAULT_RANDOM_SEED: Final[int] = 20261102


class ClusterBootstrapError(ValueError):
    """Raised when cluster bootstrap analysis cannot run safely."""


def _load_artifact(model_file: Path) -> dict[str, Any]:
    """Load the frozen candidate and validate the required fields."""

    artifact = joblib.load(model_file)
    if not isinstance(artifact, dict) or artifact.get("model") is None:
        raise ClusterBootstrapError("Frozen model artifact is invalid.")
    threshold = artifact.get("classification_threshold")
    if not isinstance(threshold, (int, float)) or not 0 < float(threshold) < 1:
        raise ClusterBootstrapError("Frozen model threshold is invalid.")
    return artifact


def _probabilities(model: Any, features: np.ndarray) -> np.ndarray:
    """Return finite positive-class probabilities."""

    probability_matrix = np.asarray(model.predict_proba(features), dtype=np.float64)
    if probability_matrix.ndim != 2 or probability_matrix.shape[1] != 2:
        raise ClusterBootstrapError("Frozen model returned invalid probabilities.")
    values = probability_matrix[:, 1]
    if not np.isfinite(values).all() or (values < 0).any() or (values > 1).any():
        raise ClusterBootstrapError("Frozen model probabilities are invalid.")
    return values


def _metrics(labels: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict[str, float]:
    """Calculate the paper's four key metrics."""

    predictions = (probabilities >= threshold).astype(np.int8)
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "average_precision": float(average_precision_score(labels, probabilities)),
    }


def run_clustered_bootstrap(
    *,
    model_file: Path = DEFAULT_MODEL_FILE,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    split_directory: Path = DEFAULT_SPLIT_DIRECTORY,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
    n_bootstrap: int = DEFAULT_BOOTSTRAP_SAMPLES,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> dict[str, Any]:
    """Resample whole test experiments to quantify cluster-level uncertainty."""

    if n_bootstrap < 1_000:
        raise ClusterBootstrapError("Use at least 1,000 bootstrap samples.")

    data = load_training_data(
        split_directory=split_directory,
        manifest_file=manifest_file,
    )
    artifact = _load_artifact(model_file)
    if artifact.get("feature_columns") != list(data.feature_columns):
        raise ClusterBootstrapError("Model features do not match the data contract.")

    labels = data.test.target.to_numpy(dtype=np.int8)
    probabilities = _probabilities(
        artifact["model"], data.test.features.to_numpy(dtype=np.float64)
    )
    threshold = float(artifact["classification_threshold"])
    experiment_ids = data.test.frame[EXPERIMENT_ID_COLUMN].astype(str).to_numpy()
    unique_experiments = np.unique(experiment_ids)
    if len(unique_experiments) < 2:
        raise ClusterBootstrapError("At least two held-out experiments are required.")

    group_indices = {
        experiment_id: np.flatnonzero(experiment_ids == experiment_id)
        for experiment_id in unique_experiments
    }
    point_estimates = _metrics(labels, probabilities, threshold)
    values = {name: np.empty(n_bootstrap, dtype=np.float64) for name in point_estimates}
    rng = np.random.default_rng(random_seed)

    for iteration in range(n_bootstrap):
        selected_groups = rng.choice(
            unique_experiments, size=len(unique_experiments), replace=True
        )
        indices = np.concatenate([group_indices[group] for group in selected_groups])
        sampled_metrics = _metrics(labels[indices], probabilities[indices], threshold)
        for name, value in sampled_metrics.items():
            values[name][iteration] = value

    intervals = {
        name: {
            "lower": float(np.quantile(metric_values, 0.025)),
            "upper": float(np.quantile(metric_values, 0.975)),
        }
        for name, metric_values in values.items()
    }
    report = {
        "schema_version": "1.0.0",
        "analysis_name": "experiment_clustered_bootstrap_test_metrics",
        "purpose": (
            "Quantify frozen-test uncertainty while preserving within-experiment "
            "temporal dependence by resampling whole experiments."
        ),
        "model": {
            "model_name": artifact.get("model_name"),
            "classification_threshold": threshold,
        },
        "dataset": {
            "split": "test",
            "row_count": data.test.row_count,
            "experiment_count": data.test.experiment_count,
            "assignment_sha256": data.assignment_sha256,
        },
        "method": {
            "resampling_method": "nonparametric_experiment_cluster_bootstrap",
            "bootstrap_samples": n_bootstrap,
            "random_seed": random_seed,
            "confidence_level": 0.95,
            "interval_method": "percentile",
            "clusters": "experiment_run_id",
        },
        "point_estimates": point_estimates,
        "confidence_intervals_95": intervals,
        "interpretation_boundary": {
            "model_retrained": False,
            "threshold_retuned": False,
            "test_split_used_for_model_selection": False,
            "rows_within_each_resampled_experiment_remain_intact": True,
            "limited_number_of_test_experiments": data.test.experiment_count,
        },
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    """Run the cluster bootstrap command."""

    parser = argparse.ArgumentParser(
        description="Calculate experiment-clustered frozen-test bootstrap intervals."
    )
    parser.add_argument("--n-bootstrap", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES)
    parser.add_argument("--seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)
    arguments = parser.parse_args()

    try:
        report = run_clustered_bootstrap(
            n_bootstrap=arguments.n_bootstrap,
            random_seed=arguments.seed,
            output_file=arguments.output_file,
        )
    except ClusterBootstrapError as error:
        print(f"Cluster bootstrap failed: {error}")
        return 1

    print("Experiment-clustered bootstrap completed:")
    for name, point_estimate in report["point_estimates"].items():
        interval = report["confidence_intervals_95"][name]
        print(
            f"  {name}: {point_estimate:.4f} "
            f"(95% CI {interval['lower']:.4f}–{interval['upper']:.4f})"
        )
    print(f"  Saved: {arguments.output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
