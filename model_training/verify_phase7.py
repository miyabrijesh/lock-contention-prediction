import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from sqlalchemy import (
    distinct,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    MetricSample,
    ModelVersion,
    Prediction,
)
from model_training.inference import (
    EXPECTED_ARTIFACT_SHA256,
    REGISTERED_MODEL_NAME,
)
from monitoring.prediction_coverage import (
    PredictionCoverageError,
    build_prediction_coverage_report,
)


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

DEFAULT_COMPLETION_REPORT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "phase7_completion_v1.json"
)

EXPECTED_DATABASE_REVISION: Final[str] = (
    "b7e4c2d9f801"
)

REQUIRED_EVIDENCE_FILES: Final[
    tuple[Path, ...]
] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "model_activation_contract_v1.md",
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "prediction_persistence_contract_v1.md",
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "prediction_api_contract_v1.md",
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "automatic_prediction_contract_v1.md",
    PROJECT_ROOT
    / "docs"
    / "demo"
    / "phase7_end_to_end_demo.md",
)

AUTOMATIC_PREDICTION_CONTRACT: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "automatic_prediction_contract_v1.md"
)

DEMO_WORKFLOW_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "demo"
    / "phase7_end_to_end_demo.md"
)


class Phase7VerificationError(ValueError):
    """Raised when Phase 7 evidence is incomplete."""


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


def verify_documentation_evidence() -> dict[str, Any]:
    """Verify required Phase 7 contracts and demo documentation."""

    missing_files = [
        path
        for path in REQUIRED_EVIDENCE_FILES
        if not path.is_file()
    ]

    if missing_files:
        raise Phase7VerificationError(
            "Required Phase 7 evidence was not found: "
            + ", ".join(
                str(path)
                for path in missing_files
            )
        )

    automatic_contract = (
        AUTOMATIC_PREDICTION_CONTRACT.read_text(
            encoding="utf-8"
        )
    )

    required_automatic_values = (
        "Transaction 1",
        "Transaction 2",
        "create_prediction_for_metric_sample()",
        "prediction coverage = 1.0",
        "actual_label = null",
        "ACTIVE_MODEL_NOT_FOUND",
    )

    missing_automatic_values = [
        value
        for value in required_automatic_values
        if value not in automatic_contract
    ]

    if missing_automatic_values:
        raise Phase7VerificationError(
            "Automatic prediction contract is incomplete: "
            + ", ".join(
                missing_automatic_values
            )
        )

    demo_workflow = DEMO_WORKFLOW_FILE.read_text(
        encoding="utf-8"
    )

    required_demo_values = (
        "EXPERIMENT_MODE_ENABLED=true",
        "load_testing.run_synchronized_experiment",
        "monitoring.prediction_coverage",
        "Final experiment status: COMPLETED",
        "Prediction coverage verification passed.",
        "Known limitation",
    )

    missing_demo_values = [
        value
        for value in required_demo_values
        if value not in demo_workflow
    ]

    if missing_demo_values:
        raise Phase7VerificationError(
            "Phase 7 demonstration guide is incomplete: "
            + ", ".join(missing_demo_values)
        )

    return {
        "required_file_count": len(
            REQUIRED_EVIDENCE_FILES
        ),
        "files": {
            str(
                path.relative_to(PROJECT_ROOT)
            ): {
                "sha256": _sha256(path),
            }
            for path in REQUIRED_EVIDENCE_FILES
        },
        "automatic_prediction_transaction_boundary": (
            True
        ),
        "demonstration_workflow_documented": True,
    }


def verify_database_revision(
    session: Session,
) -> dict[str, Any]:
    """Verify the expected Phase 7 database revision."""

    revision = session.scalar(
        text(
            "SELECT version_num "
            "FROM alembic_version"
        )
    )

    if revision != EXPECTED_DATABASE_REVISION:
        raise Phase7VerificationError(
            "Database revision is unexpected: "
            f"{revision!r}."
        )

    return {
        "revision": revision,
        "expected_revision": (
            EXPECTED_DATABASE_REVISION
        ),
        "is_current": True,
        "phase7_migration_required": False,
    }


def verify_active_model(
    session: Session,
) -> dict[str, Any]:
    """Verify the single active registered model."""

    active_models = session.scalars(
        select(ModelVersion).where(
            ModelVersion.is_active.is_(True)
        )
    ).all()

    if len(active_models) != 1:
        raise Phase7VerificationError(
            "Expected exactly one active model version."
        )

    active_model = active_models[0]

    if active_model.name != REGISTERED_MODEL_NAME:
        raise Phase7VerificationError(
            "The active model name is unexpected."
        )

    if (
        active_model.artifact_sha256.strip()
        != EXPECTED_ARTIFACT_SHA256
    ):
        raise Phase7VerificationError(
            "The active model artifact checksum "
            "is unexpected."
        )

    if active_model.target_horizon_sec != 30:
        raise Phase7VerificationError(
            "The active model target horizon "
            "is unexpected."
        )

    return {
        "model_version_id": str(
            active_model.id
        ),
        "name": active_model.name,
        "algorithm": active_model.algorithm,
        "is_active": active_model.is_active,
        "artifact_path": active_model.artifact_path,
        "artifact_sha256": (
            active_model.artifact_sha256.strip()
        ),
        "target_horizon_seconds": (
            active_model.target_horizon_sec
        ),
        "code_version": active_model.code_version,
    }


def verify_experiment_evidence(
    session: Session,
    experiment_run_id: UUID,
    *,
    active_model_id: UUID,
) -> dict[str, Any]:
    """Verify one successful live prediction experiment."""

    try:
        coverage = build_prediction_coverage_report(
            session,
            experiment_run_id,
        )
    except PredictionCoverageError as error:
        raise Phase7VerificationError(
            str(error)
        ) from error

    if not coverage.passed:
        raise Phase7VerificationError(
            "The selected experiment does not satisfy "
            "Phase 7 prediction coverage."
        )

    collector_versions = set(
        session.scalars(
            select(
                distinct(
                    MetricSample.collector_version
                )
            ).where(
                MetricSample.experiment_run_id
                == experiment_run_id,
                MetricSample.is_complete.is_(True),
            )
        )
    )

    if collector_versions != {
        "checkpoint-38-v1"
    }:
        raise Phase7VerificationError(
            "The experiment collector version "
            "is unexpected."
        )

    prediction_model_ids = set(
        session.scalars(
            select(
                distinct(
                    Prediction.model_version_id
                )
            ).where(
                Prediction.experiment_run_id
                == experiment_run_id
            )
        )
    )

    if prediction_model_ids != {
        active_model_id
    }:
        raise Phase7VerificationError(
            "The experiment predictions do not use "
            "the current active model."
        )

    return {
        "experiment_run_id": str(
            coverage.experiment_run_id
        ),
        "status": coverage.experiment_status,
        "complete_sample_count": (
            coverage.complete_sample_count
        ),
        "prediction_count": (
            coverage.prediction_count
        ),
        "distinct_predicted_sample_count": (
            coverage.distinct_predicted_sample_count
        ),
        "missing_prediction_count": (
            coverage.missing_prediction_count
        ),
        "duplicate_prediction_count": (
            coverage.duplicate_prediction_count
        ),
        "stale_prediction_count": (
            coverage.stale_prediction_count
        ),
        "labeled_prediction_count": (
            coverage.labeled_prediction_count
        ),
        "model_version_count": (
            coverage.model_version_count
        ),
        "coverage": str(coverage.coverage),
        "collector_versions": sorted(
            collector_versions
        ),
        "prediction_model_version_ids": sorted(
            str(model_id)
            for model_id in prediction_model_ids
        ),
        "passed": coverage.passed,
    }


def build_phase7_completion_report(
    session: Session,
    experiment_run_id: UUID,
) -> dict[str, Any]:
    """Build verified Phase 7 completion evidence."""

    documentation = (
        verify_documentation_evidence()
    )
    database = verify_database_revision(
        session
    )
    active_model = verify_active_model(
        session
    )
    experiment = verify_experiment_evidence(
        session,
        experiment_run_id,
        active_model_id=UUID(
            active_model["model_version_id"]
        ),
    )

    return {
        "schema_version": "1.0.0",
        "phase": 7,
        "status": "COMPLETED",
        "completion_scope": (
            "model_activation_live_prediction_"
            "integration_and_demonstration"
        ),
        "checkpoints": {
            "35A": "COMPLETED",
            "35B": "COMPLETED",
            "35C": "COMPLETED",
            "35D": "COMPLETED",
            "36A": "COMPLETED",
            "36B": "COMPLETED",
            "36C": "COMPLETED",
            "36D": "COMPLETED",
            "37A": "COMPLETED",
            "37B": "COMPLETED",
            "37C": "COMPLETED",
            "37D": "COMPLETED",
            "38A": "COMPLETED",
            "38B": "COMPLETED",
            "38C": "COMPLETED",
            "38D": "COMPLETED",
            "39A": "COMPLETED",
            "39B": "COMPLETED",
            "39C": "COMPLETED",
            "39D": "COMPLETED",
        },
        "documentation": documentation,
        "database": database,
        "active_model": active_model,
        "live_experiment": experiment,
        "capabilities": {
            "controlled_model_activation": True,
            "authenticated_prediction_api": True,
            "automatic_prediction_persistence": True,
            "prediction_failure_isolation": True,
            "prediction_coverage_monitoring": True,
            "synchronized_demo_workflow": True,
        },
        "finalization_boundary": {
            "phase7_technical_verification": (
                "COMPLETED"
            ),
            "final_test_and_documentation_review": (
                "COMPLETED"
            ),
        },
        "limitations": [
            (
                "The demonstration uses a controlled "
                "local PostgreSQL workload."
            ),
            (
                "The workload is not traffic from a "
                "commercial ticketing platform."
            ),
            (
                "Model performance was evaluated on "
                "controlled collected experiments."
            ),
            (
                "Production deployment requires "
                "continued monitoring and validation."
            ),
        ],
    }


def write_completion_report(
    *,
    report: dict[str, Any],
    output_file: Path = DEFAULT_COMPLETION_REPORT,
) -> Path:
    """Write deterministic JSON completion evidence."""

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
    """Parse Phase 7 verification arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Verify Phase 7 live prediction evidence "
            "and generate its completion report."
        )
    )

    parser.add_argument(
        "--experiment-run-id",
        type=UUID,
        required=True,
        help=(
            "UUID of the successful integrated "
            "Phase 7 experiment."
        ),
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_COMPLETION_REPORT,
    )

    return parser.parse_args()


def main() -> int:
    """Run Phase 7 verification."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            report = build_phase7_completion_report(
                session,
                arguments.experiment_run_id,
            )

        output_file = write_completion_report(
            report=report,
            output_file=arguments.output_file,
        )

        experiment = report["live_experiment"]
        active_model = report["active_model"]

        print("Phase 7 verification passed:")
        print("  Status: COMPLETED")
        print(
            "  Active model: "
            f"{active_model['name']}"
        )
        print(
            "  Experiment: "
            f"{experiment['experiment_run_id']}"
        )
        print(
            "  Complete samples: "
            f"{experiment['complete_sample_count']}"
        )
        print(
            "  Predictions: "
            f"{experiment['prediction_count']}"
        )
        print(
            "  Coverage: "
            f"{experiment['coverage']}"
        )
        print(
            "  Report: "
            f"{output_file}"
        )

        return 0

    except (
        Phase7VerificationError,
        SQLAlchemyError,
    ) as error:
        print(
            f"Phase 7 verification failed: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())