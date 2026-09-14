import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final

import joblib
import numpy as np
from sklearn.metrics import fbeta_score

from model_training.train_baselines import (
    evaluate_probabilities,
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

DEFAULT_FINAL_REPORT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "final_test_evaluation_v1.json"
)

FINAL_REPORT_SCHEMA_VERSION: Final[str] = "1.0.0"
F_BETA: Final[float] = 2.0


class FinalEvaluationError(ValueError):
    """Raised when final evaluation cannot run safely."""


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
        raise FinalEvaluationError(
            f"{field_name} must be numeric."
        )

    numeric_value = float(value)

    if not np.isfinite(numeric_value):
        raise FinalEvaluationError(
            f"{field_name} must be finite."
        )

    return numeric_value


def _load_json_object(
    path: Path,
    *,
    description: str,
) -> dict[str, Any]:
    """Load one required JSON object."""

    if not path.is_file():
        raise FinalEvaluationError(
            f"{description} was not found: {path}"
        )

    try:
        loaded_value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise FinalEvaluationError(
            f"{description} is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise FinalEvaluationError(
            f"{description} must contain a JSON object."
        )

    result: dict[str, Any] = loaded_value

    return result


def load_frozen_candidate(
    *,
    model_file: Path = DEFAULT_MODEL_FILE,
    selection_report_file: Path = (
        DEFAULT_SELECTION_REPORT
    ),
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    str,
]:
    """Load and verify the frozen candidate artifacts."""

    if not model_file.is_file():
        raise FinalEvaluationError(
            f"Candidate model was not found: {model_file}"
        )

    selection_report = _load_json_object(
        selection_report_file,
        description="Candidate selection report",
    )

    if selection_report.get("checkpoint") != "33C":
        raise FinalEvaluationError(
            "Expected a Checkpoint 33C "
            "candidate selection report."
        )

    selection = selection_report.get("selection")

    if not isinstance(selection, dict):
        raise FinalEvaluationError(
            "Candidate selection metadata is invalid."
        )

    selected_model_name = selection.get(
        "model_name"
    )

    if (
        not isinstance(selected_model_name, str)
        or not selected_model_name
    ):
        raise FinalEvaluationError(
            "The selected model name is invalid."
        )

    test_metadata = selection_report.get("test")

    if not isinstance(test_metadata, dict):
        raise FinalEvaluationError(
            "Candidate test metadata is invalid."
        )

    if test_metadata.get("usage") != "NOT_EVALUATED":
        raise FinalEvaluationError(
            "Candidate report indicates that the "
            "test split was already evaluated."
        )

    artifact_metadata = selection_report.get(
        "artifact"
    )

    if not isinstance(artifact_metadata, dict):
        raise FinalEvaluationError(
            "Candidate artifact metadata is invalid."
        )

    expected_model_sha256 = artifact_metadata.get(
        "model_sha256"
    )

    if (
        not isinstance(expected_model_sha256, str)
        or len(expected_model_sha256) != 64
    ):
        raise FinalEvaluationError(
            "Candidate model checksum is invalid."
        )

    actual_model_sha256 = _sha256(model_file)

    if actual_model_sha256 != expected_model_sha256:
        raise FinalEvaluationError(
            "Candidate model checksum does not match "
            "the selection report."
        )

    loaded_artifact: Any = joblib.load(
        model_file
    )

    if not isinstance(loaded_artifact, dict):
        raise FinalEvaluationError(
            "Candidate model artifact is invalid."
        )

    artifact: dict[str, Any] = loaded_artifact

    if (
        artifact.get("model_name")
        != selected_model_name
    ):
        raise FinalEvaluationError(
            "Candidate artifact model does not match "
            "the selection report."
        )

    model = artifact.get("model")

    if model is None or not hasattr(
        model,
        "predict_proba",
    ):
        raise FinalEvaluationError(
            "Candidate artifact does not contain "
            "a probability classifier."
        )

    artifact_threshold = _numeric_value(
        artifact.get("classification_threshold"),
        field_name="classification_threshold",
    )

    report_threshold = _numeric_value(
        selection.get("selected_threshold"),
        field_name="selected_threshold",
    )

    if not np.isclose(
        artifact_threshold,
        report_threshold,
    ):
        raise FinalEvaluationError(
            "Candidate artifact and report use "
            "different thresholds."
        )

    artifact_features = artifact.get(
        "feature_columns"
    )

    report_feature_contract = (
        selection_report.get("feature_contract")
    )

    if not isinstance(
        report_feature_contract,
        dict,
    ):
        raise FinalEvaluationError(
            "Candidate feature contract is invalid."
        )

    report_features = report_feature_contract.get(
        "feature_columns"
    )

    if (
        not isinstance(artifact_features, list)
        or not all(
            isinstance(value, str)
            for value in artifact_features
        )
    ):
        raise FinalEvaluationError(
            "Candidate artifact feature list is invalid."
        )

    if artifact_features != report_features:
        raise FinalEvaluationError(
            "Candidate artifact and report use "
            "different feature columns."
        )

    selection_report_sha256 = _sha256(
        selection_report_file
    )

    return (
        artifact,
        selection_report,
        actual_model_sha256,
        selection_report_sha256,
    )


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
        raise FinalEvaluationError(
            "Candidate model does not support "
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
        raise FinalEvaluationError(
            "Candidate model returned an invalid "
            "probability matrix."
        )

    probabilities = probability_matrix[:, 1]

    if not np.isfinite(probabilities).all():
        raise FinalEvaluationError(
            "Candidate model returned non-finite "
            "probabilities."
        )

    if (
        (probabilities < 0).any()
        or (probabilities > 1).any()
    ):
        raise FinalEvaluationError(
            "Candidate model returned probabilities "
            "outside the range 0 to 1."
        )

    return probabilities


def evaluate_frozen_candidate(
    *,
    data: TrainingDataBundle,
    artifact: dict[str, Any],
    selection_report: dict[str, Any],
    model_sha256: str,
    selection_report_sha256: str,
) -> dict[str, Any]:
    """Evaluate the frozen candidate on the test split."""

    feature_columns = artifact.get(
        "feature_columns"
    )

    if feature_columns != list(
        data.feature_columns
    ):
        raise FinalEvaluationError(
            "Candidate model features do not match "
            "the current dataset feature contract."
        )

    selection_provenance = selection_report.get(
        "provenance"
    )

    if not isinstance(selection_provenance, dict):
        raise FinalEvaluationError(
            "Candidate provenance is invalid."
        )

    if (
        selection_provenance.get(
            "assignment_sha256"
        )
        != data.assignment_sha256
    ):
        raise FinalEvaluationError(
            "Candidate and test data use different "
            "split assignments."
        )

    model = artifact.get("model")

    if model is None:
        raise FinalEvaluationError(
            "Candidate model is missing."
        )

    threshold = _numeric_value(
        artifact.get("classification_threshold"),
        field_name="classification_threshold",
    )

    test_features = (
        data.test.features.to_numpy(
            dtype=np.float64
        )
    )

    test_target = (
        data.test.target.to_numpy(
            dtype=np.int8
        )
    )

    probabilities = _positive_probabilities(
        model,
        test_features,
    )

    metrics = evaluate_probabilities(
        true_labels=test_target,
        probabilities=probabilities,
        threshold=threshold,
    )

    predicted_labels = (
        probabilities >= threshold
    ).astype(np.int8)

    metrics["f2"] = _numeric_value(
        fbeta_score(
            test_target,
            predicted_labels,
            beta=F_BETA,
            zero_division=0,
        ),
        field_name="test_f2",
    )

    selection = selection_report.get("selection")

    if not isinstance(selection, dict):
        raise FinalEvaluationError(
            "Candidate selection metadata is invalid."
        )

    validation_metrics = selection.get(
        "selected_threshold_metrics"
    )

    if not isinstance(validation_metrics, dict):
        raise FinalEvaluationError(
            "Candidate validation metrics are invalid."
        )

    validation_f2 = _numeric_value(
        validation_metrics.get("f2"),
        field_name="validation_f2",
    )

    validation_precision = _numeric_value(
        validation_metrics.get("precision"),
        field_name="validation_precision",
    )

    validation_recall = _numeric_value(
        validation_metrics.get("recall"),
        field_name="validation_recall",
    )

    test_f2 = _numeric_value(
        metrics.get("f2"),
        field_name="test_f2",
    )

    test_precision = _numeric_value(
        metrics.get("precision"),
        field_name="test_precision",
    )

    test_recall = _numeric_value(
        metrics.get("recall"),
        field_name="test_recall",
    )

    negative_count = int(
        (test_target == 0).sum()
    )

    positive_count = int(
        (test_target == 1).sum()
    )

    return {
        "schema_version": (
            FINAL_REPORT_SCHEMA_VERSION
        ),
        "checkpoint": "33D",
        "evaluation_status": (
            "FINAL_TEST_EVALUATION_COMPLETED"
        ),
        "one_time_evaluation": True,
        "model": {
            "model_name": (
                artifact["model_name"]
            ),
            "classification_threshold": threshold,
            "model_sha256": model_sha256,
            "selection_report_sha256": (
                selection_report_sha256
            ),
        },
        "dataset": {
            "split": "test",
            "row_count": data.test.row_count,
            "experiment_count": (
                data.test.experiment_count
            ),
            "negative_label_count": negative_count,
            "positive_label_count": positive_count,
            "test_file_sha256": (
                data.test.file_sha256
            ),
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
        },
        "test_metrics": metrics,
        "validation_reference": {
            "f2": validation_f2,
            "precision": validation_precision,
            "recall": validation_recall,
        },
        "generalization_difference": {
            "test_minus_validation_f2": (
                test_f2 - validation_f2
            ),
            "test_minus_validation_precision": (
                test_precision
                - validation_precision
            ),
            "test_minus_validation_recall": (
                test_recall
                - validation_recall
            ),
        },
        "interpretation_boundary": {
            "test_split_used_for_model_selection": False,
            "test_split_used_for_threshold_selection": False,
            "model_retrained_during_test_evaluation": False,
            "results_describe_current_frozen_dataset": True,
        },
    }


def write_final_report_once(
    *,
    report: dict[str, Any],
    output_file: Path = DEFAULT_FINAL_REPORT,
) -> Path:
    """Write the final report without overwriting it."""

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with output_file.open(
            "x",
            encoding="utf-8",
        ) as file:
            json.dump(
                report,
                file,
                indent=2,
                sort_keys=True,
            )

            file.write("\n")
    except FileExistsError as error:
        raise FinalEvaluationError(
            "Final test evaluation already exists. "
            "The test split must not be evaluated again: "
            f"{output_file}"
        ) from error

    return output_file


def run_final_evaluation(
    *,
    model_file: Path = DEFAULT_MODEL_FILE,
    selection_report_file: Path = (
        DEFAULT_SELECTION_REPORT
    ),
    output_file: Path = DEFAULT_FINAL_REPORT,
    split_directory: Path = DEFAULT_SPLIT_DIRECTORY,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
) -> dict[str, Any]:
    """Run and save the one-time final evaluation."""

    if output_file.exists():
        raise FinalEvaluationError(
            "Final test evaluation already exists. "
            "The test split must not be evaluated again: "
            f"{output_file}"
        )

    data = load_training_data(
        split_directory=split_directory,
        manifest_file=manifest_file,
    )

    (
        artifact,
        selection_report,
        model_sha256,
        selection_report_sha256,
    ) = load_frozen_candidate(
        model_file=model_file,
        selection_report_file=(
            selection_report_file
        ),
    )

    report = evaluate_frozen_candidate(
        data=data,
        artifact=artifact,
        selection_report=selection_report,
        model_sha256=model_sha256,
        selection_report_sha256=(
            selection_report_sha256
        ),
    )

    write_final_report_once(
        report=report,
        output_file=output_file,
    )

    return report


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the frozen contention candidate "
            "exactly once on the test split."
        )
    )

    parser.add_argument(
        "--model-file",
        type=Path,
        default=DEFAULT_MODEL_FILE,
    )

    parser.add_argument(
        "--selection-report",
        type=Path,
        default=DEFAULT_SELECTION_REPORT,
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_FINAL_REPORT,
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
    """Run the final test evaluation command."""

    arguments = parse_arguments()

    try:
        report = run_final_evaluation(
            model_file=arguments.model_file,
            selection_report_file=(
                arguments.selection_report
            ),
            output_file=arguments.output_file,
            split_directory=(
                arguments.split_directory
            ),
            manifest_file=arguments.manifest_file,
        )

        metrics = report["test_metrics"]
        model = report["model"]
        dataset = report["dataset"]

        print(
            "Checkpoint 33D final test evaluation "
            "completed:"
        )

        print(
            f"  Model: {model['model_name']}"
        )

        print(
            "  Classification threshold: "
            f"{model['classification_threshold']:.2f}"
        )

        print(
            f"  Test rows: {dataset['row_count']}"
        )

        print(
            "  Test experiments: "
            f"{dataset['experiment_count']}"
        )

        print(
            f"  Test PR-AUC: "
            f"{metrics['pr_auc']:.4f}"
        )

        print(
            f"  Test ROC-AUC: "
            f"{metrics['roc_auc']:.4f}"
        )

        print(
            f"  Test F2: {metrics['f2']:.4f}"
        )

        print(
            "  Test precision: "
            f"{metrics['precision']:.4f}"
        )

        print(
            f"  Test recall: "
            f"{metrics['recall']:.4f}"
        )

        print(
            f"  Final report: "
            f"{arguments.output_file}"
        )

        print(
            "  Further automatic test evaluation: "
            "blocked"
        )

        return 0

    except (
        FinalEvaluationError,
        TrainingDataError,
        OSError,
    ) as error:
        print(
            f"Final evaluation error: {error}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())