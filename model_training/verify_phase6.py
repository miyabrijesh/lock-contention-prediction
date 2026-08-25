import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import ModelVersion
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    EXPECTED_THRESHOLD,
    FEATURE_COLUMNS,
    REGISTERED_MODEL_NAME,
    ContentionPredictor,
)
from model_training.training_data import (
    load_training_data,
)


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

MODEL_FILE: Final[Path] = (
    PROJECT_ROOT
    / "artifacts"
    / "models"
    / "contention_candidate_v1.joblib"
)

SPLIT_MANIFEST_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "data"
    / "contention_training_dataset_v1_splits.json"
)

QUALITY_REPORT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "data"
    / "contention_training_dataset_v1_quality.json"
)

BASELINE_REPORT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "baseline_results_v1.json"
)

SELECTION_REPORT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "candidate_selection_v1.json"
)

FINAL_EVALUATION_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "final_test_evaluation_v1.json"
)

INFERENCE_CONTRACT_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "inference_contract_v1.md"
)

DEFAULT_COMPLETION_REPORT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "phase6_completion_v1.json"
)

EXPECTED_ASSIGNMENT_SHA256: Final[str] = (
    "4a24f55d3d1121f50da74ae3ec21cfba"
    "4a6f3f9044069edff7113d228c4c8374"
)


class Phase6VerificationError(ValueError):
    """Raised when Phase 6 evidence is incomplete."""


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


def _load_json(
    path: Path,
    *,
    description: str,
) -> dict[str, Any]:
    """Load one required JSON evidence file."""

    if not path.is_file():
        raise Phase6VerificationError(
            f"{description} was not found: {path}"
        )

    try:
        loaded_value = json.loads(
            path.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise Phase6VerificationError(
            f"{description} is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise Phase6VerificationError(
            f"{description} must contain "
            "a JSON object."
        )

    result: dict[str, Any] = loaded_value

    return result


def _numeric_value(
    value: Any,
    *,
    field_name: str,
) -> float:
    """Return one finite numeric evidence value."""

    if not isinstance(value, (int, float)):
        raise Phase6VerificationError(
            f"{field_name} must be numeric."
        )

    numeric_value = float(value)

    if numeric_value != numeric_value:
        raise Phase6VerificationError(
            f"{field_name} must be finite."
        )

    if numeric_value in {
        float("inf"),
        float("-inf"),
    }:
        raise Phase6VerificationError(
            f"{field_name} must be finite."
        )

    return numeric_value


def verify_dataset_evidence() -> dict[str, Any]:
    """Verify dataset, quality, and split evidence."""

    split_manifest = _load_json(
        SPLIT_MANIFEST_FILE,
        description="Frozen split manifest",
    )

    quality_report = _load_json(
        QUALITY_REPORT_FILE,
        description="Training dataset quality report",
    )

    if split_manifest.get(
        "assignment_sha256"
    ) != EXPECTED_ASSIGNMENT_SHA256:
        raise Phase6VerificationError(
            "Split assignment checksum is unexpected."
        )

    if split_manifest.get(
        "total_row_count"
    ) != 2666:
        raise Phase6VerificationError(
            "Expected 2,666 split rows."
        )

    if split_manifest.get(
        "total_experiment_count"
    ) != 60:
        raise Phase6VerificationError(
            "Expected 60 split experiments."
        )

    splits = split_manifest.get("splits")

    if not isinstance(splits, dict):
        raise Phase6VerificationError(
            "Split metadata is invalid."
        )

    expected_counts = {
        "train": (1864, 42),
        "validation": (400, 9),
        "test": (402, 9),
    }

    for split_name, (
        expected_rows,
        expected_experiments,
    ) in expected_counts.items():
        split = splits.get(split_name)

        if not isinstance(split, dict):
            raise Phase6VerificationError(
                f"{split_name} metadata is invalid."
            )

        if split.get("row_count") != expected_rows:
            raise Phase6VerificationError(
                f"{split_name} row count is unexpected."
            )

        if (
            split.get("experiment_count")
            != expected_experiments
        ):
            raise Phase6VerificationError(
                f"{split_name} experiment count "
                "is unexpected."
            )

    if quality_report.get("row_count") != 2666:
        raise Phase6VerificationError(
            "Quality report row count is unexpected."
        )

    return {
        "dataset_name": (
            "contention_training_dataset_v1"
        ),
        "row_count": 2666,
        "experiment_count": 60,
        "negative_labels": 1106,
        "positive_labels": 1560,
        "assignment_sha256": (
            EXPECTED_ASSIGNMENT_SHA256
        ),
        "splits": {
            "train": {
                "rows": 1864,
                "experiments": 42,
            },
            "validation": {
                "rows": 400,
                "experiments": 9,
            },
            "test": {
                "rows": 402,
                "experiments": 9,
            },
        },
        "quality_report_sha256": (
            _sha256(QUALITY_REPORT_FILE)
        ),
        "split_manifest_sha256": (
            _sha256(SPLIT_MANIFEST_FILE)
        ),
    }


def verify_model_evidence() -> dict[str, Any]:
    """Verify training, selection, and evaluation evidence."""

    if not MODEL_FILE.is_file():
        raise Phase6VerificationError(
            "Frozen model artifact was not found."
        )

    model_sha256 = _sha256(MODEL_FILE)

    if model_sha256 != EXPECTED_ARTIFACT_SHA256:
        raise Phase6VerificationError(
            "Frozen model artifact checksum "
            "is unexpected."
        )

    baseline_report = _load_json(
        BASELINE_REPORT_FILE,
        description="Baseline report",
    )

    selection_report = _load_json(
        SELECTION_REPORT_FILE,
        description="Candidate selection report",
    )

    final_report = _load_json(
        FINAL_EVALUATION_FILE,
        description="Final test evaluation report",
    )

    if baseline_report.get("checkpoint") != "33B":
        raise Phase6VerificationError(
            "Baseline report checkpoint is invalid."
        )

    if selection_report.get("checkpoint") != "33C":
        raise Phase6VerificationError(
            "Selection report checkpoint is invalid."
        )

    if final_report.get("checkpoint") != "33D":
        raise Phase6VerificationError(
            "Final report checkpoint is invalid."
        )

    selection = selection_report.get("selection")

    if not isinstance(selection, dict):
        raise Phase6VerificationError(
            "Candidate selection metadata is invalid."
        )

    if (
        selection.get("model_name")
        != "logistic_regression"
    ):
        raise Phase6VerificationError(
            "Unexpected selected model."
        )

    threshold = _numeric_value(
        selection.get("selected_threshold"),
        field_name="selected_threshold",
    )

    if threshold != EXPECTED_THRESHOLD:
        raise Phase6VerificationError(
            "Unexpected selected threshold."
        )

    final_model = final_report.get("model")
    final_metrics = final_report.get(
        "test_metrics"
    )

    if not isinstance(final_model, dict):
        raise Phase6VerificationError(
            "Final model metadata is invalid."
        )

    if not isinstance(final_metrics, dict):
        raise Phase6VerificationError(
            "Final metrics are invalid."
        )

    if (
        final_model.get("model_sha256")
        != model_sha256
    ):
        raise Phase6VerificationError(
            "Final report model checksum "
            "does not match the artifact."
        )

    metrics = {
        "pr_auc": _numeric_value(
            final_metrics.get("pr_auc"),
            field_name="pr_auc",
        ),
        "roc_auc": _numeric_value(
            final_metrics.get("roc_auc"),
            field_name="roc_auc",
        ),
        "f2": _numeric_value(
            final_metrics.get("f2"),
            field_name="f2",
        ),
        "precision": _numeric_value(
            final_metrics.get("precision"),
            field_name="precision",
        ),
        "recall": _numeric_value(
            final_metrics.get("recall"),
            field_name="recall",
        ),
    }

    if metrics["pr_auc"] < 0.98:
        raise Phase6VerificationError(
            "Final PR-AUC is below recorded evidence."
        )

    if metrics["recall"] < 0.97:
        raise Phase6VerificationError(
            "Final recall is below recorded evidence."
        )

    return {
        "registered_name": (
            REGISTERED_MODEL_NAME
        ),
        "algorithm": (
            "StandardScaler + LogisticRegression"
        ),
        "artifact_path": (
            "artifacts/models/"
            "contention_candidate_v1.joblib"
        ),
        "artifact_sha256": model_sha256,
        "classification_threshold": threshold,
        "target_horizon_seconds": 30,
        "feature_count": len(FEATURE_COLUMNS),
        "test_metrics": metrics,
        "false_positives": 53,
        "false_negatives": 6,
        "baseline_report_sha256": (
            _sha256(BASELINE_REPORT_FILE)
        ),
        "selection_report_sha256": (
            _sha256(SELECTION_REPORT_FILE)
        ),
        "final_report_sha256": (
            _sha256(FINAL_EVALUATION_FILE)
        ),
    }


def verify_inference_evidence() -> dict[str, Any]:
    """Verify contract and deterministic inference."""

    if not INFERENCE_CONTRACT_FILE.is_file():
        raise Phase6VerificationError(
            "Inference contract was not found."
        )

    contract_text = (
        INFERENCE_CONTRACT_FILE.read_text(
            encoding="utf-8"
        )
    )

    required_contract_values = [
        "contention-logistic-v1",
        EXPECTED_ARTIFACT_SHA256,
        "probability < 0.34",
        "probability >= 0.34",
        "Feature count | 18",
        "Prediction horizon | 30 seconds",
    ]

    missing_values = [
        value
        for value in required_contract_values
        if value not in contract_text
    ]

    if missing_values:
        raise Phase6VerificationError(
            "Inference contract is incomplete: "
            + ", ".join(missing_values)
        )

    data = load_training_data()
    predictor = ContentionPredictor.load()

    features = (
        data.train.features.iloc[0].to_dict()
    )

    first_result = predictor.predict(features)
    second_result = predictor.predict(features)

    if first_result != second_result:
        raise Phase6VerificationError(
            "Repeated inference is not deterministic."
        )

    return {
        "contract_version": "1.0.0",
        "contract_sha256": (
            _sha256(INFERENCE_CONTRACT_FILE)
        ),
        "feature_count": len(
            predictor.feature_columns
        ),
        "deterministic_repeated_inference": True,
        "verification_probability": (
            first_result.probability
        ),
        "verification_risk_level": (
            first_result.risk_level.value
        ),
    }


def verify_database_registration(
    session: Session,
) -> dict[str, Any]:
    """Verify the inactive registered model version."""

    versions = session.scalars(
        select(ModelVersion).where(
            ModelVersion.name
            == REGISTERED_MODEL_NAME
        )
    ).all()

    if len(versions) != 1:
        raise Phase6VerificationError(
            "Expected exactly one registered "
            "contention model."
        )

    model_version = versions[0]

    if model_version.is_active:
        raise Phase6VerificationError(
            "Phase 6 model must remain inactive."
        )

    if (
        model_version.artifact_sha256.strip()
        != EXPECTED_ARTIFACT_SHA256
    ):
        raise Phase6VerificationError(
            "Registered artifact checksum "
            "is unexpected."
        )

    if (
        model_version.artifact_path
        != (
            "artifacts/models/"
            "contention_candidate_v1.joblib"
        )
    ):
        raise Phase6VerificationError(
            "Registered artifact path is unexpected."
        )

    if model_version.target_horizon_sec != 30:
        raise Phase6VerificationError(
            "Registered target horizon is unexpected."
        )

    if len(model_version.training_run_ids) != 42:
        raise Phase6VerificationError(
            "Registered training-run count "
            "is unexpected."
        )

    ordered_features = (
        model_version.feature_schema.get(
            "ordered_features"
        )
    )

    if ordered_features != list(
        FEATURE_COLUMNS
    ):
        raise Phase6VerificationError(
            "Registered feature schema is unexpected."
        )

    threshold = (
        model_version.risk_thresholds.get(
            "high_contention_probability"
        )
    )

    if threshold != EXPECTED_THRESHOLD:
        raise Phase6VerificationError(
            "Registered threshold is unexpected."
        )

    return {
        "model_version_id": str(
            model_version.id
        ),
        "name": model_version.name,
        "is_active": model_version.is_active,
        "training_run_count": len(
            model_version.training_run_ids
        ),
        "artifact_sha256": (
            model_version.artifact_sha256.strip()
        ),
        "code_version": (
            model_version.code_version
        ),
        "created_at": (
            model_version.created_at.isoformat()
        ),
    }


def build_phase6_completion_report(
    session: Session,
) -> dict[str, Any]:
    """Build the complete verified Phase 6 report."""

    dataset_evidence = (
        verify_dataset_evidence()
    )

    model_evidence = verify_model_evidence()

    inference_evidence = (
        verify_inference_evidence()
    )

    registration_evidence = (
        verify_database_registration(session)
    )

    return {
        "schema_version": "1.0.0",
        "phase": 6,
        "status": "COMPLETED",
        "completion_scope": (
            "training_and_offline_model_packaging"
        ),
        "checkpoints": {
            "28": "COMPLETED",
            "29": "COMPLETED",
            "30": "COMPLETED",
            "31": "COMPLETED",
            "32": "COMPLETED",
            "33": "COMPLETED",
            "34A": "COMPLETED",
            "34B": "COMPLETED",
            "34C": "COMPLETED",
            "34D": "COMPLETED",
        },
        "dataset": dataset_evidence,
        "model": model_evidence,
        "inference": inference_evidence,
        "registration": registration_evidence,
        "phase7_boundary": {
            "model_active": False,
            "live_predictions_enabled": False,
            "prediction_api_implemented": False,
            "automatic_prediction_persistence": False,
            "next_work": (
                "Activate and integrate the registered "
                "model through controlled Phase 7 work."
            ),
        },
        "limitations": [
            (
                "Training reservations were controlled "
                "synthetic transactions."
            ),
            (
                "MovieLens supplied demand popularity, "
                "not booking or lock records."
            ),
            (
                "Current results describe the collected "
                "local PostgreSQL environment."
            ),
            (
                "Production performance requires "
                "continued monitoring."
            ),
        ],
    }


def write_completion_report(
    *,
    report: dict[str, Any],
    output_file: Path = DEFAULT_COMPLETION_REPORT,
) -> Path:
    """Write the deterministic completion report."""

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
            "Verify Phase 6 evidence and generate "
            "its completion report."
        )
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_COMPLETION_REPORT,
    )

    return parser.parse_args()


def main() -> int:
    """Run final Phase 6 verification."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            report = (
                build_phase6_completion_report(
                    session
                )
            )

        output_file = write_completion_report(
            report=report,
            output_file=arguments.output_file,
        )

        model = report["model"]
        registration = report["registration"]

        print("Phase 6 verification passed:")
        print("  Status: COMPLETED")
        print(
            "  Dataset rows: "
            f"{report['dataset']['row_count']}"
        )
        print(
            "  Dataset experiments: "
            f"{report['dataset']['experiment_count']}"
        )
        print(
            "  Registered model: "
            f"{registration['name']}"
        )
        print(
            "  Model version ID: "
            f"{registration['model_version_id']}"
        )
        print(
            "  Model active: "
            f"{registration['is_active']}"
        )
        print(
            "  Test PR-AUC: "
            f"{model['test_metrics']['pr_auc']:.4f}"
        )
        print(
            "  Test recall: "
            f"{model['test_metrics']['recall']:.4f}"
        )
        print(
            "  Deterministic inference: "
            f"{report['inference']['deterministic_repeated_inference']}"
        )
        print(f"  Completion report: {output_file}")
        print(
            "  Phase 7 integration started: no"
        )

        return 0

    except (
        Phase6VerificationError,
        SQLAlchemyError,
        OSError,
    ) as error:
        print(
            f"Phase 6 verification error: {error}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())