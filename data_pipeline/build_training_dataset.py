import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import UUID

from backend.app.db.session import SessionLocal
from data_pipeline.combine_exports import (
    CombinedDatasetError,
    CombinedDatasetResult,
    build_combined_pilot_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_EXECUTION_MANIFEST = Path(
    "data/raw/experiment_matrix/"
    "checkpoint_31_training_matrix_v1.manifest.json"
)

DEFAULT_OUTPUT_FILE = Path(
    "data/processed/"
    "contention_training_dataset_v1.csv"
)


class MatrixTrainingDatasetError(Exception):
    """Expected failure while building the matrix training dataset."""


def _resolve_project_path(path: Path) -> Path:
    """Resolve a relative path from the project root."""

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def _calculate_sha256(file_path: Path) -> str:
    """Calculate a SHA-256 checksum without loading the whole file."""

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        for block in iter(
            lambda: input_file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _load_json_object(
    file_path: Path,
    *,
    description: str,
) -> dict[str, object]:
    """Read and validate one JSON object."""

    try:
        value = json.loads(
            file_path.read_text(encoding="utf-8")
        )
    except OSError as error:
        raise MatrixTrainingDatasetError(
            f"Could not read {description} {file_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise MatrixTrainingDatasetError(
            f"{description.capitalize()} {file_path} "
            "is not valid JSON."
        ) from error

    if not isinstance(value, dict):
        raise MatrixTrainingDatasetError(
            f"The {description} root must be a JSON object."
        )

    return value


def _required_text(
    mapping: dict[str, object],
    field_name: str,
    *,
    description: str,
) -> str:
    """Read one required nonempty string."""

    value = mapping.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise MatrixTrainingDatasetError(
            f"{description} must contain a nonempty "
            f"{field_name!r} value."
        )

    return value.strip()


def _required_positive_integer(
    mapping: dict[str, object],
    field_name: str,
    *,
    description: str,
) -> int:
    """Read one required positive integer."""

    value = mapping.get(field_name)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise MatrixTrainingDatasetError(
            f"{description} must contain a positive integer "
            f"{field_name!r} value."
        )

    return value


def _completed_experiment_ids(
    execution_manifest: dict[str, object],
) -> tuple[list[UUID], dict[str, object]]:
    """Extract one completed experiment ID from every scenario."""

    matrix_name = _required_text(
        execution_manifest,
        "matrix_name",
        description="Execution manifest",
    )
    matrix_schema_version = _required_text(
        execution_manifest,
        "matrix_schema_version",
        description="Execution manifest",
    )
    expected_run_count = _required_positive_integer(
        execution_manifest,
        "expected_run_count",
        description="Execution manifest",
    )

    scenarios = execution_manifest.get("scenarios")

    if not isinstance(scenarios, dict):
        raise MatrixTrainingDatasetError(
            "Execution manifest must contain a scenarios object."
        )

    if len(scenarios) != expected_run_count:
        raise MatrixTrainingDatasetError(
            "Execution manifest scenario count does not match "
            f"expected_run_count. Expected {expected_run_count}, "
            f"found {len(scenarios)}."
        )

    incomplete_scenarios: list[str] = []
    experiment_ids: list[UUID] = []

    for scenario_id in sorted(scenarios):
        raw_entry = scenarios[scenario_id]

        if not isinstance(scenario_id, str):
            raise MatrixTrainingDatasetError(
                "Every scenario identifier must be a string."
            )

        if not isinstance(raw_entry, dict):
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} must be a JSON object."
            )

        if raw_entry.get("status") != "COMPLETED":
            incomplete_scenarios.append(scenario_id)
            continue

        attempts = raw_entry.get("attempts")

        if not isinstance(attempts, list) or not attempts:
            raise MatrixTrainingDatasetError(
                f"Completed scenario {scenario_id} does not "
                "contain an execution attempt."
            )

        latest_attempt = attempts[-1]

        if not isinstance(latest_attempt, dict):
            raise MatrixTrainingDatasetError(
                f"Latest attempt for {scenario_id} is invalid."
            )

        if latest_attempt.get("status") != "COMPLETED":
            raise MatrixTrainingDatasetError(
                f"Latest attempt for {scenario_id} is not COMPLETED."
            )

        raw_experiment_id = latest_attempt.get(
            "experiment_run_id"
        )

        if not isinstance(raw_experiment_id, str):
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} does not contain "
                "a valid experiment_run_id."
            )

        try:
            experiment_id = UUID(raw_experiment_id)
        except ValueError as error:
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} contains an invalid "
                "experiment UUID."
            ) from error

        export_value = latest_attempt.get("export")

        if not isinstance(export_value, dict):
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} does not contain "
                "a completed export."
            )

        export_experiment_id = export_value.get(
            "experiment_run_id"
        )

        if export_experiment_id != raw_experiment_id:
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} export belongs to "
                "a different experiment."
            )

        export_row_count = export_value.get("row_count")

        if (
            not isinstance(export_row_count, int)
            or isinstance(export_row_count, bool)
            or export_row_count <= 0
        ):
            raise MatrixTrainingDatasetError(
                f"Scenario {scenario_id} export has an invalid "
                "row_count."
            )

        experiment_ids.append(experiment_id)

    if incomplete_scenarios:
        preview = ", ".join(incomplete_scenarios[:5])

        raise MatrixTrainingDatasetError(
            "Cannot build the training dataset because "
            f"{len(incomplete_scenarios)} scenarios are incomplete. "
            f"First incomplete scenarios: {preview}."
        )

    if len(experiment_ids) != len(set(experiment_ids)):
        raise MatrixTrainingDatasetError(
            "The execution manifest contains duplicate "
            "experiment IDs."
        )

    metadata: dict[str, object] = {
        "matrix_name": matrix_name,
        "matrix_schema_version": matrix_schema_version,
        "expected_run_count": expected_run_count,
        "completed_scenario_count": len(experiment_ids),
    }

    return experiment_ids, metadata


def _write_json_atomically(
    file_path: Path,
    value: dict[str, object],
) -> None:
    """Write a JSON document using an atomic replacement."""

    temporary_file = file_path.with_suffix(
        f"{file_path.suffix}.tmp"
    )

    try:
        temporary_file.write_text(
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(file_path)
    except OSError as error:
        raise MatrixTrainingDatasetError(
            f"Could not update manifest {file_path}: {error}"
        ) from error


def _enrich_combined_manifest(
    *,
    combined_manifest_file: Path,
    execution_manifest_file: Path,
    execution_manifest_sha256: str,
    matrix_metadata: dict[str, object],
) -> None:
    """Add matrix provenance to the combined dataset manifest."""

    combined_manifest = _load_json_object(
        combined_manifest_file,
        description="combined dataset manifest",
    )

    combined_manifest["dataset_role"] = (
        "matrix_training_dataset"
    )
    combined_manifest["source_matrix"] = {
        "execution_manifest_file": str(
            execution_manifest_file
        ),
        "execution_manifest_sha256": (
            execution_manifest_sha256
        ),
        **matrix_metadata,
    }

    _write_json_atomically(
        combined_manifest_file,
        combined_manifest,
    )


def build_matrix_training_dataset(
    *,
    execution_manifest_file: Path = (
        DEFAULT_EXECUTION_MANIFEST
    ),
    output_file: Path = DEFAULT_OUTPUT_FILE,
    manifest_file: Path | None = None,
    created_by: str = "local-admin",
) -> CombinedDatasetResult:
    """Build one versioned dataset from a completed matrix run."""

    execution_manifest_path = _resolve_project_path(
        execution_manifest_file
    )
    output_path = _resolve_project_path(output_file)

    if manifest_file is None:
        combined_manifest_path = output_path.with_suffix(
            ".manifest.json"
        )
    else:
        combined_manifest_path = _resolve_project_path(
            manifest_file
        )

    normalized_created_by = created_by.strip()

    if not normalized_created_by:
        raise MatrixTrainingDatasetError(
            "created_by must not be empty."
        )

    if not execution_manifest_path.is_file():
        raise MatrixTrainingDatasetError(
            "Execution manifest was not found: "
            f"{execution_manifest_path}"
        )

    if output_path.exists():
        raise MatrixTrainingDatasetError(
            "Output dataset already exists. Use a new versioned "
            f"filename instead of overwriting it: {output_path}"
        )

    if combined_manifest_path.exists():
        raise MatrixTrainingDatasetError(
            "Output manifest already exists. Use a new versioned "
            "filename instead of overwriting it: "
            f"{combined_manifest_path}"
        )

    execution_manifest_sha256 = _calculate_sha256(
        execution_manifest_path
    )
    execution_manifest = _load_json_object(
        execution_manifest_path,
        description="execution manifest",
    )
    experiment_ids, matrix_metadata = (
        _completed_experiment_ids(execution_manifest)
    )

    with SessionLocal() as session:
        result = build_combined_pilot_dataset(
            session,
            experiment_ids,
            output_file=output_path,
            manifest_file=combined_manifest_path,
            created_by=normalized_created_by,
        )

    _enrich_combined_manifest(
        combined_manifest_file=combined_manifest_path,
        execution_manifest_file=execution_manifest_path,
        execution_manifest_sha256=(
            execution_manifest_sha256
        ),
        matrix_metadata=matrix_metadata,
    )

    return result


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Combine all completed Checkpoint 31 matrix exports "
            "into one versioned training dataset."
        )
    )

    parser.add_argument(
        "--execution-manifest",
        type=Path,
        default=DEFAULT_EXECUTION_MANIFEST,
        help=(
            "Completed matrix execution manifest. Default: "
            "data/raw/experiment_matrix/"
            "checkpoint_31_training_matrix_v1.manifest.json"
        ),
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=(
            "Versioned combined CSV output. Default: "
            "data/processed/"
            "contention_training_dataset_v1.csv"
        ),
    )
    parser.add_argument(
        "--manifest-file",
        type=Path,
        default=None,
        help=(
            "Optional combined dataset manifest path. By default, "
            "it is placed beside the output CSV."
        ),
    )
    parser.add_argument(
        "--created-by",
        default="local-admin",
        help="Actor recorded in the combined dataset manifest.",
    )

    return parser.parse_args()


def main() -> int:
    """Build the matrix training dataset."""

    arguments = parse_arguments()

    try:
        build_matrix_training_dataset(
            execution_manifest_file=(
                arguments.execution_manifest
            ),
            output_file=arguments.output_file,
            manifest_file=arguments.manifest_file,
            created_by=arguments.created_by,
        )

        manifest_path = _resolve_project_path(
            arguments.manifest_file
            if arguments.manifest_file is not None
            else arguments.output_file.with_suffix(
                ".manifest.json"
            )
        )
        manifest = _load_json_object(
            manifest_path,
            description="combined dataset manifest",
        )

        label_distribution = manifest.get(
            "label_distribution"
        )

        if not isinstance(label_distribution, dict):
            raise MatrixTrainingDatasetError(
                "Combined manifest label_distribution is invalid."
            )

        print("Matrix training dataset created:")
        print(f"  Dataset: {manifest.get('dataset_file')}")
        print(f"  Manifest: {manifest_path}")
        print(
            "  Experiments: "
            f"{manifest.get('experiment_count')}"
        )
        print(f"  Rows: {manifest.get('row_count')}")
        print(
            "  Negative labels: "
            f"{label_distribution.get('negative')}"
        )
        print(
            "  Positive labels: "
            f"{label_distribution.get('positive')}"
        )
        print(
            "  SHA-256: "
            f"{manifest.get('dataset_sha256')}"
        )

        return 0

    except (
        CombinedDatasetError,
        MatrixTrainingDatasetError,
    ) as error:
        print(
            f"Training dataset build error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())