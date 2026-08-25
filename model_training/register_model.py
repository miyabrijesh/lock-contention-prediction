import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    ExperimentRun,
    ModelVersion,
)
from backend.app.models.enums import (
    ExperimentStatus,
)
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    EXPECTED_THRESHOLD,
    FEATURE_COLUMNS,
    MODEL_CONTRACT_VERSION,
    REGISTERED_MODEL_NAME,
    TARGET_HORIZON_SECONDS,
)


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

MODEL_ARTIFACT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

MODEL_ARTIFACT_PATH: Final[str] = (
    "artifacts/models/"
    "contention_candidate_v1.joblib"
)

SPLIT_MANIFEST_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "data"
    / "contention_training_dataset_v1_splits.json"
)

FINAL_EVALUATION_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "final_test_evaluation_v1.json"
)

MODEL_ALGORITHM: Final[str] = (
    "StandardScaler + LogisticRegression"
)

VALIDATION_STRATEGY: Final[str] = (
    "Grouped by experiment_run_id: "
    "42 train, 9 validation, 9 test"
)


class ModelRegistrationError(ValueError):
    """Raised when model registration is unsafe."""


@dataclass(frozen=True)
class ModelRegistrationSpec:
    """Complete immutable model registration data."""

    name: str
    algorithm: str
    feature_schema: dict[str, object]
    target_horizon_sec: int
    training_run_ids: list[str]
    validation_strategy: str
    risk_thresholds: dict[str, object]
    evaluation_metrics: dict[str, object]
    artifact_path: str
    artifact_sha256: str
    code_version: str


@dataclass(frozen=True)
class ModelRegistrationResult:
    """Result of an idempotent registration attempt."""

    model_version: ModelVersion
    created: bool


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


def _load_json_object(
    path: Path,
    *,
    description: str,
) -> dict[str, Any]:
    """Load one required JSON object."""

    if not path.is_file():
        raise ModelRegistrationError(
            f"{description} was not found: {path}"
        )

    try:
        loaded_value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise ModelRegistrationError(
            f"{description} is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise ModelRegistrationError(
            f"{description} must contain "
            "a JSON object."
        )

    result: dict[str, Any] = loaded_value

    return result


def _current_git_commit() -> str:
    """Return the current tracked Git commit."""

    completed_process = subprocess.run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    if completed_process.returncode != 0:
        raise ModelRegistrationError(
            "Current Git commit could not be read."
        )

    commit = completed_process.stdout.strip()

    if (
        len(commit) != 40
        or any(
            character not in "0123456789abcdef"
            for character in commit.lower()
        )
    ):
        raise ModelRegistrationError(
            "Current Git commit is invalid."
        )

    return commit


def _training_run_ids(
    split_manifest: dict[str, Any],
) -> list[str]:
    """Return the 42 frozen training experiment IDs."""

    splits = split_manifest.get("splits")

    if not isinstance(splits, dict):
        raise ModelRegistrationError(
            "Split manifest metadata is invalid."
        )

    training = splits.get("train")

    if not isinstance(training, dict):
        raise ModelRegistrationError(
            "Training split metadata is invalid."
        )

    raw_ids = training.get("experiment_ids")

    if (
        not isinstance(raw_ids, list)
        or not all(
            isinstance(value, str)
            for value in raw_ids
        )
    ):
        raise ModelRegistrationError(
            "Training experiment IDs are invalid."
        )

    training_ids: list[str] = list(raw_ids)

    if len(training_ids) != 42:
        raise ModelRegistrationError(
            "Expected exactly 42 training "
            "experiment IDs."
        )

    if len(training_ids) != len(
        set(training_ids)
    ):
        raise ModelRegistrationError(
            "Training experiment IDs contain duplicates."
        )

    try:
        for value in training_ids:
            UUID(value)
    except ValueError as error:
        raise ModelRegistrationError(
            "A training experiment ID is not "
            "a valid UUID."
        ) from error

    return training_ids


def _evaluation_metrics(
    final_report: dict[str, Any],
) -> dict[str, object]:
    """Extract frozen final test metrics."""

    if final_report.get("checkpoint") != "33D":
        raise ModelRegistrationError(
            "Expected a Checkpoint 33D "
            "final evaluation report."
        )

    if final_report.get("evaluation_status") != (
        "FINAL_TEST_EVALUATION_COMPLETED"
    ):
        raise ModelRegistrationError(
            "Final evaluation is not completed."
        )

    model = final_report.get("model")
    dataset = final_report.get("dataset")
    metrics = final_report.get("test_metrics")

    if not isinstance(model, dict):
        raise ModelRegistrationError(
            "Final model metadata is invalid."
        )

    if not isinstance(dataset, dict):
        raise ModelRegistrationError(
            "Final dataset metadata is invalid."
        )

    if not isinstance(metrics, dict):
        raise ModelRegistrationError(
            "Final test metrics are invalid."
        )

    if (
        model.get("model_sha256")
        != EXPECTED_ARTIFACT_SHA256
    ):
        raise ModelRegistrationError(
            "Final report uses an unexpected "
            "model checksum."
        )

    required_metric_names = (
        "pr_auc",
        "roc_auc",
        "f2",
        "f1",
        "precision",
        "recall",
        "balanced_accuracy",
        "accuracy",
        "brier_score",
    )

    extracted_metrics: dict[str, object] = {}

    for metric_name in required_metric_names:
        value = metrics.get(metric_name)

        if not isinstance(value, (int, float)):
            raise ModelRegistrationError(
                f"Final metric {metric_name} "
                "is invalid."
            )

        extracted_metrics[metric_name] = float(
            value
        )

    confusion_matrix = metrics.get(
        "confusion_matrix"
    )

    if not isinstance(confusion_matrix, dict):
        raise ModelRegistrationError(
            "Final confusion matrix is invalid."
        )

    extracted_metrics["confusion_matrix"] = (
        confusion_matrix
    )

    extracted_metrics["evaluation_split"] = "test"
    extracted_metrics["test_row_count"] = (
        dataset.get("row_count")
    )
    extracted_metrics["test_experiment_count"] = (
        dataset.get("experiment_count")
    )
    extracted_metrics["report_sha256"] = (
        _sha256(FINAL_EVALUATION_FILE)
    )

    return extracted_metrics


def build_registration_spec() -> ModelRegistrationSpec:
    """Build and verify the frozen registration data."""

    if not MODEL_ARTIFACT_FILE.is_file():
        raise ModelRegistrationError(
            "Frozen model artifact was not found."
        )

    actual_artifact_sha256 = _sha256(
        MODEL_ARTIFACT_FILE
    )

    if (
        actual_artifact_sha256
        != EXPECTED_ARTIFACT_SHA256
    ):
        raise ModelRegistrationError(
            "Frozen model artifact checksum "
            "does not match the inference contract."
        )

    split_manifest = _load_json_object(
        SPLIT_MANIFEST_FILE,
        description="Frozen split manifest",
    )

    final_report = _load_json_object(
        FINAL_EVALUATION_FILE,
        description="Final evaluation report",
    )

    training_ids = _training_run_ids(
        split_manifest
    )

    feature_schema: dict[str, object] = {
        "schema_version": (
            MODEL_CONTRACT_VERSION
        ),
        "feature_count": len(
            FEATURE_COLUMNS
        ),
        "ordered_features": list(
            FEATURE_COLUMNS
        ),
        "input_type": "numeric",
        "missing_value_policy": "reject",
        "unexpected_feature_policy": "reject",
        "boolean_value_policy": "reject",
        "target_column": (
            "future_high_contention_30s"
        ),
    }

    risk_thresholds: dict[str, object] = {
        "schema_version": (
            MODEL_CONTRACT_VERSION
        ),
        "high_contention_probability": (
            EXPECTED_THRESHOLD
        ),
        "low_condition": (
            "probability < 0.34"
        ),
        "high_condition": (
            "probability >= 0.34"
        ),
        "medium_emitted": False,
        "unknown_emitted_on_success": False,
    }

    return ModelRegistrationSpec(
        name=REGISTERED_MODEL_NAME,
        algorithm=MODEL_ALGORITHM,
        feature_schema=feature_schema,
        target_horizon_sec=(
            TARGET_HORIZON_SECONDS
        ),
        training_run_ids=training_ids,
        validation_strategy=(
            VALIDATION_STRATEGY
        ),
        risk_thresholds=risk_thresholds,
        evaluation_metrics=(
            _evaluation_metrics(final_report)
        ),
        artifact_path=MODEL_ARTIFACT_PATH,
        artifact_sha256=(
            actual_artifact_sha256
        ),
        code_version=_current_git_commit(),
    )


def validate_training_experiments(
    session: Session,
    training_run_ids: list[str],
) -> None:
    """Require all training experiments to be completed."""

    uuid_values = [
        UUID(value)
        for value in training_run_ids
    ]

    experiments = session.scalars(
        select(ExperimentRun).where(
            ExperimentRun.id.in_(
                uuid_values
            )
        )
    ).all()

    found_ids = {
        experiment.id
        for experiment in experiments
    }

    expected_ids = set(uuid_values)

    missing_ids = expected_ids - found_ids

    if missing_ids:
        raise ModelRegistrationError(
            "One or more training experiments "
            "were not found."
        )

    incomplete_ids = {
        experiment.id
        for experiment in experiments
        if experiment.status
        != ExperimentStatus.COMPLETED
    }

    if incomplete_ids:
        raise ModelRegistrationError(
            "Every training experiment must "
            "have COMPLETED status."
        )


def _registration_matches(
    existing: ModelVersion,
    spec: ModelRegistrationSpec,
) -> bool:
    """Check whether an existing registration is identical."""

    return all([
        existing.name == spec.name,
        existing.algorithm == spec.algorithm,
        existing.feature_schema
        == spec.feature_schema,
        existing.target_horizon_sec
        == spec.target_horizon_sec,
        existing.training_run_ids
        == spec.training_run_ids,
        existing.validation_strategy
        == spec.validation_strategy,
        existing.risk_thresholds
        == spec.risk_thresholds,
        existing.evaluation_metrics
        == spec.evaluation_metrics,
        existing.artifact_path
        == spec.artifact_path,
        existing.artifact_sha256.strip()
        == spec.artifact_sha256,
    ])


def register_model_version(
    session: Session,
    spec: ModelRegistrationSpec,
) -> ModelRegistrationResult:
    """Register the frozen model without overwriting it."""

    try:
        validate_training_experiments(
            session,
            spec.training_run_ids,
        )

        existing_versions = session.scalars(
            select(ModelVersion).where(
                ModelVersion.name == spec.name
            )
        ).all()

        if len(existing_versions) > 1:
            raise ModelRegistrationError(
                "Multiple registrations already use "
                "the frozen model name."
            )

        if existing_versions:
            existing = existing_versions[0]

            if not _registration_matches(
                existing,
                spec,
            ):
                raise ModelRegistrationError(
                    "A different registration already "
                    "uses this model name."
                )

            return ModelRegistrationResult(
                model_version=existing,
                created=False,
            )

        model_version = ModelVersion(
            name=spec.name,
            algorithm=spec.algorithm,
            feature_schema=spec.feature_schema,
            target_horizon_sec=(
                spec.target_horizon_sec
            ),
            training_run_ids=(
                spec.training_run_ids
            ),
            validation_strategy=(
                spec.validation_strategy
            ),
            risk_thresholds=(
                spec.risk_thresholds
            ),
            evaluation_metrics=(
                spec.evaluation_metrics
            ),
            artifact_path=spec.artifact_path,
            artifact_sha256=(
                spec.artifact_sha256
            ),
            code_version=spec.code_version,
            is_active=False,
        )

        session.add(model_version)
        session.commit()
        session.refresh(model_version)

        return ModelRegistrationResult(
            model_version=model_version,
            created=True,
        )

    except ModelRegistrationError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()

        raise ModelRegistrationError(
            "Database model registration failed."
        ) from error


def _print_spec(
    spec: ModelRegistrationSpec,
) -> None:
    """Print safe registration metadata."""

    print("Model registration specification:")
    print(f"  Name: {spec.name}")
    print(f"  Algorithm: {spec.algorithm}")
    print(
        "  Feature count: "
        f"{spec.feature_schema['feature_count']}"
    )
    print(
        "  Target horizon: "
        f"{spec.target_horizon_sec} seconds"
    )
    print(
        "  Training experiments: "
        f"{len(spec.training_run_ids)}"
    )
    print(
        "  High-risk threshold: "
        f"{spec.risk_thresholds['high_contention_probability']}"
    )
    print(
        f"  Artifact path: "
        f"{spec.artifact_path}"
    )
    print(
        f"  Artifact SHA-256: "
        f"{spec.artifact_sha256}"
    )
    print(
        f"  Code version: "
        f"{spec.code_version}"
    )
    print("  Initial active status: false")


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Build or register the frozen contention "
            "model version."
        )
    )

    action = parser.add_mutually_exclusive_group(
        required=True
    )

    action.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate and display registration data "
            "without changing the database."
        ),
    )

    action.add_argument(
        "--register",
        action="store_true",
        help=(
            "Create the inactive model registration."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run dry-run or controlled registration."""

    arguments = parse_arguments()

    try:
        spec = build_registration_spec()
        _print_spec(spec)

        if arguments.dry_run:
            with SessionLocal() as session:
                validate_training_experiments(
                    session,
                    spec.training_run_ids,
                )

            print(
                "Dry run passed. "
                "Database was not changed."
            )

            return 0

        with SessionLocal() as session:
            result = register_model_version(
                session,
                spec,
            )

        if result.created:
            print(
                "Model version registered successfully."
            )
        else:
            print(
                "Matching model version was "
                "already registered."
            )

        print(
            f"  Model version ID: "
            f"{result.model_version.id}"
        )

        print(
            f"  Active: "
            f"{result.model_version.is_active}"
        )

        return 0

    except ModelRegistrationError as error:
        print(
            f"Model registration error: {error}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())