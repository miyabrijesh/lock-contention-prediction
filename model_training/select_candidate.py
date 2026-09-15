import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
from sklearn.metrics import (
    balanced_accuracy_score,
    confusion_matrix,
    fbeta_score,
    precision_score,
    recall_score,
)

from model_training.train_baselines import (
    BASELINE_RANDOM_SEED,
    build_baseline_models,
)
from model_training.training_data import (
    DEFAULT_MANIFEST_FILE,
    DEFAULT_SPLIT_DIRECTORY,
    TrainingDataBundle,
    TrainingDataError,
    load_training_data,
)

PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

DEFAULT_BASELINE_REPORT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "baseline_results_v1.json"
)

DEFAULT_MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

DEFAULT_SELECTION_REPORT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "candidate_selection_v1.json"
)

CANDIDATE_SCHEMA_VERSION: Final[str] = "1.0.0"
THRESHOLD_MINIMUM: Final[float] = 0.05
THRESHOLD_MAXIMUM: Final[float] = 0.95
THRESHOLD_STEP: Final[float] = 0.01
F_BETA: Final[float] = 2.0


class CandidateSelectionError(ValueError):
    """Raised when candidate selection is invalid."""


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


def _numeric_value(
    value: Any,
    *,
    field_name: str,
) -> float:
    """Return one required finite numeric value."""

    if not isinstance(value, (int, float)):
        raise CandidateSelectionError(
            f"{field_name} must be numeric."
        )

    numeric_value = float(value)

    if not np.isfinite(numeric_value):
        raise CandidateSelectionError(
            f"{field_name} must be finite."
        )

    return numeric_value


def load_baseline_report(
    path: Path = DEFAULT_BASELINE_REPORT,
) -> dict[str, Any]:
    """Load and validate the Checkpoint 33B report."""

    if not path.is_file():
        raise CandidateSelectionError(
            f"Baseline report was not found: {path}"
        )

    try:
        loaded_value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise CandidateSelectionError(
            "Baseline report is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise CandidateSelectionError(
            "Baseline report must contain a JSON object."
        )

    report: dict[str, Any] = loaded_value

    if report.get("checkpoint") != "33B":
        raise CandidateSelectionError(
            "Expected a Checkpoint 33B baseline report."
        )

    dataset = report.get("dataset")

    if not isinstance(dataset, dict):
        raise CandidateSelectionError(
            "Baseline dataset metadata is invalid."
        )

    test_metadata = dataset.get("test")

    if not isinstance(test_metadata, dict):
        raise CandidateSelectionError(
            "Baseline test metadata is invalid."
        )

    if test_metadata.get("usage") != "NOT_EVALUATED":
        raise CandidateSelectionError(
            "The baseline report indicates that the "
            "test split was already evaluated."
        )

    models = report.get("models")

    if not isinstance(models, dict) or not models:
        raise CandidateSelectionError(
            "Baseline model results are missing."
        )

    return report


def select_model_by_validation_pr_auc(
    report: dict[str, Any],
) -> tuple[str, float]:
    """Select the model with the highest validation PR-AUC."""

    models = report.get("models")

    if not isinstance(models, dict) or not models:
        raise CandidateSelectionError(
            "Baseline model results are missing."
        )

    scored_models: list[tuple[float, str]] = []

    for model_name, model_result in models.items():
        if not isinstance(model_name, str):
            raise CandidateSelectionError(
                "A baseline model name is invalid."
            )

        if not isinstance(model_result, dict):
            raise CandidateSelectionError(
                f"Results for {model_name} are invalid."
            )

        metrics = model_result.get("metrics")

        if not isinstance(metrics, dict):
            raise CandidateSelectionError(
                f"Metrics for {model_name} are invalid."
            )

        pr_auc = _numeric_value(
            metrics.get("pr_auc"),
            field_name=f"{model_name}.pr_auc",
        )

        if pr_auc < 0 or pr_auc > 1:
            raise CandidateSelectionError(
                f"{model_name}.pr_auc must be "
                "between 0 and 1."
            )

        scored_models.append(
            (pr_auc, model_name)
        )

    selected_pr_auc, selected_name = max(
        scored_models,
        key=lambda item: (
            item[0],
            item[1],
        ),
    )

    return selected_name, selected_pr_auc


def _positive_probabilities(
    model: Any,
    features: np.ndarray,
) -> np.ndarray:
    """Return positive-class probabilities."""

    predict_proba = getattr(
        model,
        "predict_proba",
        None,
    )

    if predict_proba is None:
        raise CandidateSelectionError(
            "Selected model does not support "
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
        raise CandidateSelectionError(
            "Selected model returned an invalid "
            "probability matrix."
        )

    probabilities = probability_matrix[:, 1]

    if not np.isfinite(probabilities).all():
        raise CandidateSelectionError(
            "Selected model returned non-finite "
            "probabilities."
        )

    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
    ):
        raise CandidateSelectionError(
            "Selected model returned probabilities "
            "outside the range 0 to 1."
        )

    return probabilities


def _threshold_candidates() -> tuple[float, ...]:
    """Return the deterministic threshold search grid."""

    threshold_count = round(
        (
            THRESHOLD_MAXIMUM
            - THRESHOLD_MINIMUM
        )
        / THRESHOLD_STEP
    ) + 1

    return tuple(
        round(
            THRESHOLD_MINIMUM
            + index * THRESHOLD_STEP,
            2,
        )
        for index in range(threshold_count)
    )


def _threshold_metrics(
    *,
    true_labels: np.ndarray,
    probabilities: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    """Calculate classification metrics for one threshold."""

    predicted_labels = (
        probabilities >= threshold
    ).astype(np.int8)

    matrix = confusion_matrix(
        true_labels,
        predicted_labels,
        labels=[0, 1],
    )

    return {
        "threshold": threshold,
        "f2": _numeric_value(
            fbeta_score(
                true_labels,
                predicted_labels,
                beta=F_BETA,
                zero_division=0,
            ),
            field_name="f2",
        ),
        "recall": _numeric_value(
            recall_score(
                true_labels,
                predicted_labels,
                zero_division=0,
            ),
            field_name="recall",
        ),
        "precision": _numeric_value(
            precision_score(
                true_labels,
                predicted_labels,
                zero_division=0,
            ),
            field_name="precision",
        ),
        "balanced_accuracy": _numeric_value(
            balanced_accuracy_score(
                true_labels,
                predicted_labels,
            ),
            field_name="balanced_accuracy",
        ),
        "confusion_matrix": {
            "true_negative": int(matrix[0, 0]),
            "false_positive": int(matrix[0, 1]),
            "false_negative": int(matrix[1, 0]),
            "true_positive": int(matrix[1, 1]),
        },
        "predicted_negative": int(
            (predicted_labels == 0).sum()
        ),
        "predicted_positive": int(
            (predicted_labels == 1).sum()
        ),
    }


def optimize_validation_threshold(
    *,
    true_labels: np.ndarray,
    probabilities: np.ndarray,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select the threshold with the highest validation F2."""

    if len(true_labels) == 0:
        raise CandidateSelectionError(
            "Validation labels must not be empty."
        )

    if len(true_labels) != len(probabilities):
        raise CandidateSelectionError(
            "Validation labels and probabilities "
            "have different lengths."
        )

    label_values = {
        int(value)
        for value in np.unique(true_labels)
    }

    if label_values != {0, 1}:
        raise CandidateSelectionError(
            "Validation data must contain both labels."
        )

    if not np.isfinite(probabilities).all():
        raise CandidateSelectionError(
            "Validation probabilities contain "
            "non-finite values."
        )

    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
    ):
        raise CandidateSelectionError(
            "Validation probabilities must be "
            "between 0 and 1."
        )

    search_results = [
        _threshold_metrics(
            true_labels=true_labels,
            probabilities=probabilities,
            threshold=threshold,
        )
        for threshold in _threshold_candidates()
    ]

    def ranking_key(
        result: dict[str, Any],
    ) -> tuple[float, float, float, float, float]:
        return (
            _numeric_value(
                result.get("f2"),
                field_name="f2",
            ),
            _numeric_value(
                result.get("recall"),
                field_name="recall",
            ),
            _numeric_value(
                result.get("precision"),
                field_name="precision",
            ),
            _numeric_value(
                result.get("balanced_accuracy"),
                field_name="balanced_accuracy",
            ),
            _numeric_value(
                result.get("threshold"),
                field_name="threshold",
            ),
        )

    selected_result = max(
        search_results,
        key=ranking_key,
    )

    return selected_result, search_results


def build_candidate(
    *,
    data: TrainingDataBundle,
    baseline_report: dict[str, Any],
    baseline_report_file: Path = (
        DEFAULT_BASELINE_REPORT
    ),
) -> tuple[Any, dict[str, Any]]:
    """Fit and select the candidate without evaluating test data."""

    baseline_dataset = baseline_report.get(
        "dataset"
    )

    if not isinstance(baseline_dataset, dict):
        raise CandidateSelectionError(
            "Baseline dataset metadata is invalid."
        )

    if (
        baseline_dataset.get("assignment_sha256")
        != data.assignment_sha256
    ):
        raise CandidateSelectionError(
            "Baseline report and current dataset "
            "use different split assignments."
        )

    selected_model_name, selected_pr_auc = (
        select_model_by_validation_pr_auc(
            baseline_report
        )
    )

    available_models = build_baseline_models(
        random_seed=BASELINE_RANDOM_SEED
    )

    if selected_model_name not in available_models:
        raise CandidateSelectionError(
            "Selected model cannot be reconstructed."
        )

    selected_model = available_models[
        selected_model_name
    ]

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

    selected_model.fit(
        training_features,
        training_target,
    )

    probabilities = _positive_probabilities(
        selected_model,
        validation_features,
    )

    selected_threshold, threshold_search = (
        optimize_validation_threshold(
            true_labels=validation_target,
            probabilities=probabilities,
        )
    )

    report: dict[str, Any] = {
        "schema_version": (
            CANDIDATE_SCHEMA_VERSION
        ),
        "checkpoint": "33C",
        "selection": {
            "model_name": selected_model_name,
            "model_selection_metric": (
                "validation_pr_auc"
            ),
            "model_selection_value": (
                selected_pr_auc
            ),
            "threshold_selection_metric": (
                "validation_f2"
            ),
            "f_beta": F_BETA,
            "selected_threshold": (
                selected_threshold["threshold"]
            ),
            "selected_threshold_metrics": (
                selected_threshold
            ),
            "tie_break_order": [
                "f2",
                "recall",
                "precision",
                "balanced_accuracy",
                "threshold",
            ],
        },
        "training": {
            "fit_split": "train",
            "row_count": data.train.row_count,
            "experiment_count": (
                data.train.experiment_count
            ),
            "random_seed": (
                BASELINE_RANDOM_SEED
            ),
        },
        "validation": {
            "usage": (
                "MODEL_AND_THRESHOLD_SELECTION"
            ),
            "row_count": (
                data.validation.row_count
            ),
            "experiment_count": (
                data.validation.experiment_count
            ),
            "threshold_candidates_evaluated": (
                len(threshold_search)
            ),
            "threshold_search": threshold_search,
        },
        "test": {
            "usage": "NOT_EVALUATED",
            "reason": (
                "Reserved for one-time final "
                "evaluation in Checkpoint 33D."
            ),
        },
        "feature_contract": {
            "feature_count": len(
                data.feature_columns
            ),
            "feature_columns": list(
                data.feature_columns
            ),
            "target_column": (
                data.target_column
            ),
        },
        "provenance": {
            "assignment_sha256": (
                data.assignment_sha256
            ),
            "training_file_sha256": (
                data.train.file_sha256
            ),
            "validation_file_sha256": (
                data.validation.file_sha256
            ),
            "baseline_report_sha256": (
                _sha256(
                    baseline_report_file
                )
            ),
        },
    }

    return selected_model, report

def _recorded_path(path: Path) -> str:
    """Return a project-relative or absolute path."""

    resolved_path = path.resolve()
    resolved_project_root = PROJECT_ROOT.resolve()

    try:
        relative_path = resolved_path.relative_to(
            resolved_project_root
        )
    except ValueError:
        return str(resolved_path)

    return str(relative_path)

def save_candidate_artifacts(
    *,
    model: Any,
    report: dict[str, Any],
    model_file: Path = DEFAULT_MODEL_FILE,
    report_file: Path = DEFAULT_SELECTION_REPORT,
) -> tuple[Path, Path]:
    """Save the candidate model and selection report."""

    selection = report.get("selection")

    if not isinstance(selection, dict):
        raise CandidateSelectionError(
            "Candidate selection metadata is invalid."
        )

    feature_contract = report.get(
        "feature_contract"
    )

    if not isinstance(feature_contract, dict):
        raise CandidateSelectionError(
            "Candidate feature contract is invalid."
        )

    artifact = {
        "schema_version": (
            CANDIDATE_SCHEMA_VERSION
        ),
        "model_name": (
            selection["model_name"]
        ),
        "classification_threshold": (
            selection["selected_threshold"]
        ),
        "feature_columns": (
            feature_contract["feature_columns"]
        ),
        "target_column": (
            feature_contract["target_column"]
        ),
        "model": model,
    }

    model_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    joblib.dump(
        artifact,
        model_file,
        compress=0,
    )

    model_sha256 = _sha256(model_file)

    report["artifact"] = {
        "model_file": _recorded_path(
            model_file
        ),
        "model_sha256": model_sha256,
        "serialization": "joblib",
    }

    report_file.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return model_file, report_file


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Select and save the Checkpoint 33C "
            "contention model candidate."
        )
    )

    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=DEFAULT_BASELINE_REPORT,
    )

    parser.add_argument(
        "--model-file",
        type=Path,
        default=DEFAULT_MODEL_FILE,
    )

    parser.add_argument(
        "--report-file",
        type=Path,
        default=DEFAULT_SELECTION_REPORT,
    )

    parser.add_argument(
        "--split-directory",
        type=Path,
        default=DEFAULT_SPLIT_DIRECTORY,
        help=(
            "Directory containing train.csv, "
            "validation.csv, and test.csv."
        ),
    )

    parser.add_argument(
        "--manifest-file",
        type=Path,
        default=DEFAULT_MANIFEST_FILE,
        help="Frozen split manifest JSON file.",
    )

    return parser.parse_args()


def main() -> int:
    """Run candidate model and threshold selection."""

    arguments = parse_arguments()

    try:
        data = load_training_data(
            split_directory=(
                arguments.split_directory
            ),
            manifest_file=arguments.manifest_file,
        )

        baseline_report = load_baseline_report(
            arguments.baseline_report
        )

        model, report = build_candidate(
            data=data,
            baseline_report=baseline_report,
            baseline_report_file=(
                arguments.baseline_report
            ),
        )

        model_file, report_file = (
            save_candidate_artifacts(
                model=model,
                report=report,
                model_file=arguments.model_file,
                report_file=arguments.report_file,
            )
        )

        selection = report["selection"]

        print(
            "Checkpoint 33C candidate selection "
            "completed:"
        )

        print(
            "  Selected model: "
            f"{selection['model_name']}"
        )

        print(
            "  Validation PR-AUC: "
            f"{selection['model_selection_value']:.4f}"
        )

        print(
            "  Selected threshold: "
            f"{selection['selected_threshold']:.2f}"
        )

        metrics = selection[
            "selected_threshold_metrics"
        ]

        print(
            "  Validation F2: "
            f"{metrics['f2']:.4f}"
        )

        print(
            "  Validation precision: "
            f"{metrics['precision']:.4f}"
        )

        print(
            "  Validation recall: "
            f"{metrics['recall']:.4f}"
        )

        print("  Test split evaluated: no")
        print(f"  Model artifact: {model_file}")
        print(f"  Selection report: {report_file}")

        return 0

    except (
        CandidateSelectionError,
        TrainingDataError,
        OSError,
    ) as error:
        print(
            f"Candidate selection error: {error}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())