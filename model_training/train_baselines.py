import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

import numpy as np
import sklearn
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from model_training.training_data import (
    TrainingDataBundle,
    TrainingDataError,
    load_training_data,
)


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

DEFAULT_REPORT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "baseline_results_v1.json"
)

BASELINE_SCHEMA_VERSION: Final[str] = "1.0.0"
BASELINE_RANDOM_SEED: Final[int] = 3301

DEFAULT_CLASSIFICATION_THRESHOLD: Final[float] = (
    0.5
)


class BaselineTrainingError(ValueError):
    """Raised when baseline training cannot run safely."""


def _metric_float(value: Any) -> float:
    """Convert a metric result to a JSON-safe float."""

    return float(value)


def build_baseline_models(
    *,
    random_seed: int = BASELINE_RANDOM_SEED,
) -> dict[str, Any]:
    """Create deterministic baseline classifiers."""

    return {
        "dummy_prior": DummyClassifier(
            strategy="prior",
            random_state=random_seed,
        ),
        "logistic_regression": Pipeline(
            steps=[
                (
                    "scaler",
                    StandardScaler(),
                ),
                (
                    "classifier",
                    LogisticRegression(
                        class_weight="balanced",
                        max_iter=2000,
                        random_state=random_seed,
                        solver="lbfgs",
                    ),
                ),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=300,
            max_depth=12,
            min_samples_leaf=5,
            class_weight="balanced_subsample",
            random_state=random_seed,
            n_jobs=-1,
        ),
    }


def _validate_training_inputs(
    data: TrainingDataBundle,
) -> None:
    """Validate the training and validation inputs."""

    if data.train.features.empty:
        raise BaselineTrainingError(
            "The training feature matrix is empty."
        )

    if data.validation.features.empty:
        raise BaselineTrainingError(
            "The validation feature matrix is empty."
        )

    if tuple(data.train.features.columns) != (
        data.feature_columns
    ):
        raise BaselineTrainingError(
            "Training features do not match "
            "the approved feature contract."
        )

    if tuple(data.validation.features.columns) != (
        data.feature_columns
    ):
        raise BaselineTrainingError(
            "Validation features do not match "
            "the approved feature contract."
        )

    training_labels = {
        int(value)
        for value in data.train.target.unique()
    }

    validation_labels = {
        int(value)
        for value in data.validation.target.unique()
    }

    if training_labels != {0, 1}:
        raise BaselineTrainingError(
            "Training data must contain both target labels."
        )

    if validation_labels != {0, 1}:
        raise BaselineTrainingError(
            "Validation data must contain both target labels."
        )

    if not data.train.experiment_ids.isdisjoint(
        data.validation.experiment_ids
    ):
        raise BaselineTrainingError(
            "Training and validation experiments overlap."
        )


def _positive_probabilities(
    model: Any,
    features: np.ndarray,
) -> np.ndarray:
    """Return positive-class probability estimates."""

    predict_proba = getattr(
        model,
        "predict_proba",
        None,
    )

    if predict_proba is None:
        raise BaselineTrainingError(
            "A baseline model does not support "
            "probability prediction."
        )

    probability_matrix = np.asarray(
        predict_proba(features),
        dtype=np.float64,
    )

    if (
        probability_matrix.ndim != 2
        or probability_matrix.shape[1] != 2
    ):
        raise BaselineTrainingError(
            "A baseline model returned an invalid "
            "probability matrix."
        )

    probabilities = probability_matrix[:, 1]

    if not np.isfinite(probabilities).all():
        raise BaselineTrainingError(
            "A baseline model returned non-finite "
            "probabilities."
        )

    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
    ):
        raise BaselineTrainingError(
            "A baseline model returned probabilities "
            "outside the range 0 to 1."
        )

    return probabilities


def evaluate_probabilities(
    *,
    true_labels: np.ndarray,
    probabilities: np.ndarray,
    threshold: float = DEFAULT_CLASSIFICATION_THRESHOLD,
) -> dict[str, Any]:
    """Calculate validation metrics at one threshold."""

    if threshold <= 0 or threshold >= 1:
        raise BaselineTrainingError(
            "Classification threshold must be "
            "between 0 and 1."
        )

    if len(true_labels) != len(probabilities):
        raise BaselineTrainingError(
            "Labels and probabilities have "
            "different lengths."
        )

    if len(true_labels) == 0:
        raise BaselineTrainingError(
            "Labels and probabilities must not be empty."
        )

    if not np.isfinite(probabilities).all():
        raise BaselineTrainingError(
            "Probabilities contain non-finite values."
        )

    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
    ):
        raise BaselineTrainingError(
            "Probabilities must be between 0 and 1."
        )

    label_values = {
        int(value)
        for value in np.unique(true_labels)
    }

    if label_values != {0, 1}:
        raise BaselineTrainingError(
            "Evaluation labels must contain both 0 and 1."
        )

    predicted_labels = (
        probabilities >= threshold
    ).astype(np.int8)

    matrix = confusion_matrix(
        true_labels,
        predicted_labels,
        labels=[0, 1],
    )

    true_negative = int(matrix[0, 0])
    false_positive = int(matrix[0, 1])
    false_negative = int(matrix[1, 0])
    true_positive = int(matrix[1, 1])

    return {
        "threshold": threshold,
        "roc_auc": _metric_float(
            roc_auc_score(
                true_labels,
                probabilities,
            )
        ),
        "pr_auc": _metric_float(
            average_precision_score(
                true_labels,
                probabilities,
            )
        ),
        "accuracy": _metric_float(
            accuracy_score(
                true_labels,
                predicted_labels,
            )
        ),
        "balanced_accuracy": _metric_float(
            balanced_accuracy_score(
                true_labels,
                predicted_labels,
            )
        ),
        "precision": _metric_float(
            precision_score(
                true_labels,
                predicted_labels,
                zero_division=0,
            )
        ),
        "recall": _metric_float(
            recall_score(
                true_labels,
                predicted_labels,
                zero_division=0,
            )
        ),
        "f1": _metric_float(
            f1_score(
                true_labels,
                predicted_labels,
                zero_division=0,
            )
        ),
        "brier_score": _metric_float(
            brier_score_loss(
                true_labels,
                probabilities,
            )
        ),
        "confusion_matrix": {
            "true_negative": true_negative,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_positive": true_positive,
        },
        "predicted_negative": int(
            (predicted_labels == 0).sum()
        ),
        "predicted_positive": int(
            (predicted_labels == 1).sum()
        ),
    }


def train_baseline_models(
    data: TrainingDataBundle,
    *,
    random_seed: int = BASELINE_RANDOM_SEED,
) -> dict[str, Any]:
    """Train models and evaluate on validation data."""

    _validate_training_inputs(data)

    training_features = (
        data.train.features.to_numpy(
            dtype=np.float64
        )
    )

    training_target = (
        data.train.target.to_numpy(
            dtype=np.int8
        )
    )

    validation_features = (
        data.validation.features.to_numpy(
            dtype=np.float64
        )
    )

    validation_target = (
        data.validation.target.to_numpy(
            dtype=np.int8
        )
    )

    models = build_baseline_models(
        random_seed=random_seed
    )

    model_results: dict[str, Any] = {}

    for model_name, model in models.items():
        model.fit(
            training_features,
            training_target,
        )

        probabilities = _positive_probabilities(
            model,
            validation_features,
        )

        model_results[model_name] = {
            "model_class": (
                f"{model.__class__.__module__}."
                f"{model.__class__.__name__}"
            ),
            "metrics": evaluate_probabilities(
                true_labels=validation_target,
                probabilities=probabilities,
            ),
        }

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "checkpoint": "33B",
        "random_seed": random_seed,
        "scikit_learn_version": (
            sklearn.__version__
        ),
        "dataset": {
            "assignment_sha256": (
                data.assignment_sha256
            ),
            "feature_count": len(
                data.feature_columns
            ),
            "feature_columns": list(
                data.feature_columns
            ),
            "target_column": (
                data.target_column
            ),
            "training": {
                "row_count": (
                    data.train.row_count
                ),
                "experiment_count": (
                    data.train.experiment_count
                ),
                "file_sha256": (
                    data.train.file_sha256
                ),
            },
            "validation": {
                "row_count": (
                    data.validation.row_count
                ),
                "experiment_count": (
                    data.validation.experiment_count
                ),
                "file_sha256": (
                    data.validation.file_sha256
                ),
            },
            "test": {
                "usage": "NOT_EVALUATED",
                "reason": (
                    "Reserved for one-time final "
                    "evaluation in Checkpoint 33D."
                ),
            },
        },
        "evaluation": {
            "split": "validation",
            "classification_threshold": (
                DEFAULT_CLASSIFICATION_THRESHOLD
            ),
            "model_selection_performed": False,
            "threshold_selection_performed": False,
        },
        "models": model_results,
    }


def write_baseline_report(
    report: dict[str, Any],
    output_file: Path = DEFAULT_REPORT_FILE,
) -> Path:
    """Write the deterministic baseline report."""

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return output_file


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Train deterministic baseline contention "
            "classifiers using the frozen training and "
            "validation experiment splits."
        )
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_REPORT_FILE,
        help=(
            "JSON report destination. Default: "
            "docs/model_training/"
            "baseline_results_v1.json"
        ),
    )

    return parser.parse_args()


def _report_models(
    report: dict[str, Any],
) -> dict[str, Any]:
    """Return validated model results from a report."""

    models = report.get("models")

    if not isinstance(models, dict):
        raise BaselineTrainingError(
            "Baseline report contains invalid "
            "model results."
        )

    return models

def _required_metric(
    metrics: dict[str, Any],
    metric_name: str,
) -> float:
    """Read one required numeric model metric."""

    value = metrics.get(metric_name)

    if not isinstance(value, (int, float)):
        raise BaselineTrainingError(
            f"Metric {metric_name} is missing "
            "or is not numeric."
        )

    return float(value)

def main() -> int:
    """Run Checkpoint 33B baseline training."""

    arguments = parse_arguments()

    try:
        data = load_training_data()

        report = train_baseline_models(data)

        output_file = write_baseline_report(
            report,
            arguments.output_file,
        )

        models = _report_models(report)

        print(
            "Checkpoint 33B baseline training "
            "completed:"
        )

        print(
            f"  Training rows: "
            f"{data.train.row_count}"
        )

        print(
            f"  Validation rows: "
            f"{data.validation.row_count}"
        )

        print("  Test split evaluated: no")

        for model_name, model_result in models.items():
            if not isinstance(model_result, dict):
                raise BaselineTrainingError(
                    f"Results for {model_name} "
                    "are invalid."
                )

            metrics = model_result.get("metrics")

            if not isinstance(metrics, dict):
                raise BaselineTrainingError(
                    f"Metrics for {model_name} "
                    "are invalid."
                )

            typed_metrics: dict[str, Any] = metrics

            pr_auc = _required_metric(
                typed_metrics,
                "pr_auc",
            )

            roc_auc = _required_metric(
                typed_metrics,
                "roc_auc",
            )

            f1 = _required_metric(
                typed_metrics,
                "f1",
            )

            recall = _required_metric(
                typed_metrics,
                "recall",
            )

            print(
                f"  {model_name}: "
                f"PR-AUC={pr_auc:.4f}, "
                f"ROC-AUC={roc_auc:.4f}, "
                f"F1={f1:.4f}, "
                f"Recall={recall:.4f}"
            )

        print(f"  Report: {output_file}")

        return 0

    except (
        TrainingDataError,
        BaselineTrainingError,
        OSError,
    ) as error:
        print(
            f"Baseline training error: {error}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())