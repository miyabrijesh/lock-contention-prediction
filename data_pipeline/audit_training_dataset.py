import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from data_pipeline.export_experiment import (
    COUNT_FEATURE_NAMES,
    CSV_FIELD_NAMES,
    DECIMAL_FEATURE_NAMES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_FILE = Path(
    "data/processed/contention_training_dataset_v1.csv"
)
DEFAULT_MANIFEST_FILE = Path(
    "data/processed/contention_training_dataset_v1.manifest.json"
)
DEFAULT_REPORT_FILE = Path(
    "docs/data/contention_training_dataset_v1_quality.json"
)

FUTURE_INTEGER_FIELDS = (
    "future_max_blocked_sessions_30s",
    "future_max_waiting_locks_30s",
    "future_deadlock_delta_sum_30s",
)
FUTURE_DECIMAL_FIELDS = (
    "future_max_timeout_rate_30s",
    "future_max_p95_latency_ms_30s",
)
BOUNDED_RATE_FIELDS = {
    "connection_utilization",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
    "future_max_timeout_rate_30s",
}
CONSISTENT_FIELDS = (
    "schema_version",
    "experiment_name",
    "code_version",
    "booking_mode",
    "random_seed",
    "sample_interval_sec",
    "simulated_work_ms",
)


class TrainingDatasetAuditError(Exception):
    """Expected training-dataset quality failure."""


@dataclass(frozen=True)
class TrainingDatasetAuditResult:
    """Summary returned after all quality checks pass."""

    dataset_file: Path
    manifest_file: Path
    report_file: Path
    dataset_sha256: str
    row_count: int
    experiment_count: int
    negative_label_count: int
    positive_label_count: int
    negative_label_fraction: Decimal
    positive_label_fraction: Decimal
    minimum_rows_per_experiment: int
    maximum_rows_per_experiment: int


def _resolve(path: Path) -> Path:
    """Resolve a relative path from the project root."""

    return path if path.is_absolute() else PROJECT_ROOT / path


def _display_path(path: Path) -> str:
    """Prefer a portable project-relative report path."""

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _sha256(file_path: Path) -> str:
    """Calculate a file SHA-256 checksum incrementally."""

    digest = hashlib.sha256()
    with file_path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(file_path: Path, description: str) -> dict[str, object]:
    """Load one JSON object."""

    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise TrainingDatasetAuditError(
            f"Could not read {description} {file_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise TrainingDatasetAuditError(
            f"{description.capitalize()} is not valid JSON: {file_path}"
        ) from error

    if not isinstance(value, dict):
        raise TrainingDatasetAuditError(
            f"The {description} root must be a JSON object."
        )
    return value


def _required(row: dict[str, str], field: str, row_number: int) -> str:
    """Return one required nonempty CSV value."""

    value = row.get(field, "").strip()
    if not value:
        raise TrainingDatasetAuditError(
            f"Row {row_number} has an empty {field!r} value."
        )
    return value


def _integer(
    row: dict[str, str],
    field: str,
    row_number: int,
    *,
    minimum: int | None = None,
) -> int:
    """Parse one integer and apply an optional lower bound."""

    try:
        value = int(_required(row, field, row_number))
    except ValueError as error:
        raise TrainingDatasetAuditError(
            f"Row {row_number} field {field!r} must be an integer."
        ) from error
    if minimum is not None and value < minimum:
        raise TrainingDatasetAuditError(
            f"Row {row_number} field {field!r} must be at least {minimum}."
        )
    return value


def _decimal(
    row: dict[str, str],
    field: str,
    row_number: int,
    *,
    bounded_rate: bool = False,
) -> Decimal:
    """Parse one finite nonnegative decimal."""

    try:
        value = Decimal(_required(row, field, row_number))
    except InvalidOperation as error:
        raise TrainingDatasetAuditError(
            f"Row {row_number} field {field!r} must be a decimal."
        ) from error
    if not value.is_finite() or value < 0:
        raise TrainingDatasetAuditError(
            f"Row {row_number} field {field!r} must be finite and nonnegative."
        )
    if bounded_rate and value > 1:
        raise TrainingDatasetAuditError(
            f"Row {row_number} field {field!r} must not exceed 1."
        )
    return value


def _validate_row(
    row: dict[str, str], row_number: int
) -> tuple[str, datetime, int]:
    """Validate one row and independently recalculate its label."""

    experiment_id = _required(row, "experiment_run_id", row_number)
    try:
        UUID(experiment_id)
    except ValueError as error:
        raise TrainingDatasetAuditError(
            f"Row {row_number} contains an invalid experiment UUID."
        ) from error

    sampled_at_text = _required(row, "sampled_at", row_number)
    try:
        sampled_at = datetime.fromisoformat(sampled_at_text)
    except ValueError as error:
        raise TrainingDatasetAuditError(
            f"Row {row_number} sampled_at is not ISO-8601."
        ) from error
    if sampled_at.tzinfo is None or sampled_at.utcoffset() is None:
        raise TrainingDatasetAuditError(
            f"Row {row_number} sampled_at must include a timezone."
        )

    for field in (
        "schema_version",
        "experiment_name",
        "collector_version",
        "code_version",
        "booking_mode",
    ):
        _required(row, field, row_number)

    if _required(row, "is_complete", row_number).lower() != "true":
        raise TrainingDatasetAuditError(
            f"Row {row_number} is not marked complete."
        )

    try:
        quality_notes = json.loads(_required(row, "quality_notes", row_number))
    except json.JSONDecodeError as error:
        raise TrainingDatasetAuditError(
            f"Row {row_number} quality_notes is not valid JSON."
        ) from error
    if not isinstance(quality_notes, (dict, list)):
        raise TrainingDatasetAuditError(
            f"Row {row_number} quality_notes must be an object or array."
        )

    _integer(row, "random_seed", row_number)
    _integer(row, "sample_interval_sec", row_number, minimum=1)
    _integer(row, "simulated_work_ms", row_number, minimum=0)
    for field in COUNT_FEATURE_NAMES:
        _integer(row, field, row_number, minimum=0)
    for field in DECIMAL_FEATURE_NAMES:
        _decimal(
            row,
            field,
            row_number,
            bounded_rate=field in BOUNDED_RATE_FIELDS,
        )

    future_integers = {
        field: _integer(row, field, row_number, minimum=0)
        for field in FUTURE_INTEGER_FIELDS
    }
    future_decimals = {
        field: _decimal(
            row,
            field,
            row_number,
            bounded_rate=field in BOUNDED_RATE_FIELDS,
        )
        for field in FUTURE_DECIMAL_FIELDS
    }
    label = _integer(
        row,
        "future_high_contention_30s",
        row_number,
        minimum=0,
    )
    if label not in {0, 1}:
        raise TrainingDatasetAuditError(
            f"Row {row_number} target label must be 0 or 1."
        )

    expected_label = int(
        future_integers["future_max_blocked_sessions_30s"] >= 2
        or future_integers["future_max_waiting_locks_30s"] >= 2
        or future_integers["future_deadlock_delta_sum_30s"] >= 1
        or future_decimals["future_max_timeout_rate_30s"] > 0
    )
    if label != expected_label:
        raise TrainingDatasetAuditError(
            f"Row {row_number} has a label inconsistent with future evidence."
        )
    return experiment_id, sampled_at, label


def _validate_manifest(
    manifest: dict[str, object],
    *,
    dataset_file: Path,
    dataset_sha256: str,
    rows_by_experiment: Counter[str],
    labels: Counter[int],
    row_count: int,
) -> None:
    """Cross-check dataset values and source provenance."""

    if manifest.get("dataset_role") != "matrix_training_dataset":
        raise TrainingDatasetAuditError(
            "Manifest dataset_role is not matrix_training_dataset."
        )
    stored_file = manifest.get("dataset_file")
    if not isinstance(stored_file, str) or Path(stored_file).resolve() != dataset_file:
        raise TrainingDatasetAuditError(
            "Manifest dataset_file does not identify the audited CSV."
        )
    if manifest.get("dataset_sha256") != dataset_sha256:
        raise TrainingDatasetAuditError(
            "Dataset SHA-256 does not match the manifest."
        )
    if manifest.get("row_count") != row_count:
        raise TrainingDatasetAuditError(
            "Dataset row count does not match the manifest."
        )
    if manifest.get("experiment_count") != len(rows_by_experiment):
        raise TrainingDatasetAuditError(
            "Experiment count does not match the manifest."
        )

    distribution = manifest.get("label_distribution")
    if not isinstance(distribution, dict):
        raise TrainingDatasetAuditError(
            "Manifest label_distribution is invalid."
        )
    if distribution.get("negative") != labels[0]:
        raise TrainingDatasetAuditError(
            "Negative-label count does not match the manifest."
        )
    if distribution.get("positive") != labels[1]:
        raise TrainingDatasetAuditError(
            "Positive-label count does not match the manifest."
        )

    source_matrix = manifest.get("source_matrix")
    if not isinstance(source_matrix, dict):
        raise TrainingDatasetAuditError("Manifest source_matrix is invalid.")
    expected_count = source_matrix.get("expected_run_count")
    completed_count = source_matrix.get("completed_scenario_count")
    if expected_count != len(rows_by_experiment) or completed_count != expected_count:
        raise TrainingDatasetAuditError(
            "Source matrix coverage does not match the dataset."
        )

    execution_file = source_matrix.get("execution_manifest_file")
    execution_hash = source_matrix.get("execution_manifest_sha256")
    if not isinstance(execution_file, str) or not isinstance(execution_hash, str):
        raise TrainingDatasetAuditError(
            "Source matrix checksum metadata is invalid."
        )
    execution_path = Path(execution_file).resolve()
    if not execution_path.is_file() or _sha256(execution_path) != execution_hash:
        raise TrainingDatasetAuditError(
            "Source execution manifest is missing or its checksum changed."
        )

    source_exports = manifest.get("source_exports")
    if not isinstance(source_exports, list) or len(source_exports) != expected_count:
        raise TrainingDatasetAuditError(
            "Source-export count does not match the matrix."
        )
    source_ids: set[str] = set()
    for source in source_exports:
        if not isinstance(source, dict):
            raise TrainingDatasetAuditError("A source export entry is invalid.")
        experiment_id = source.get("experiment_run_id")
        source_file = source.get("file_path")
        source_hash = source.get("file_sha256")
        if not isinstance(experiment_id, str) or experiment_id in source_ids:
            raise TrainingDatasetAuditError(
                "A source export has a missing or duplicate experiment ID."
            )
        source_ids.add(experiment_id)
        if source.get("row_count") != rows_by_experiment.get(experiment_id):
            raise TrainingDatasetAuditError(
                f"Source row count is incorrect for {experiment_id}."
            )
        if not isinstance(source_file, str) or not isinstance(source_hash, str):
            raise TrainingDatasetAuditError(
                f"Source metadata is invalid for {experiment_id}."
            )
        source_path = Path(source_file).resolve()
        if not source_path.is_file() or _sha256(source_path) != source_hash:
            raise TrainingDatasetAuditError(
                f"Source file is missing or changed for {experiment_id}."
            )
    if source_ids != set(rows_by_experiment):
        raise TrainingDatasetAuditError(
            "Source experiment IDs do not match the combined CSV."
        )


def _write_report(
    report_file: Path,
    result: TrainingDatasetAuditResult,
) -> None:
    """Write a compact, non-sensitive audit report atomically."""

    report = {
        "audit_schema_version": "1.0.0",
        "status": "PASS",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_file": _display_path(result.dataset_file),
        "manifest_file": _display_path(result.manifest_file),
        "dataset_sha256": result.dataset_sha256,
        "row_count": result.row_count,
        "experiment_count": result.experiment_count,
        "label_distribution": {
            "negative": result.negative_label_count,
            "positive": result.positive_label_count,
            "negative_fraction": str(result.negative_label_fraction),
            "positive_fraction": str(result.positive_label_fraction),
        },
        "rows_per_experiment": {
            "minimum": result.minimum_rows_per_experiment,
            "maximum": result.maximum_rows_per_experiment,
        },
        "checks": {
            "csv_schema": "PASS",
            "required_values": "PASS",
            "numeric_ranges": "PASS",
            "unique_sample_keys": "PASS",
            "experiment_metadata_consistency": "PASS",
            "future_label_consistency": "PASS",
            "class_balance": "PASS",
            "dataset_checksum": "PASS",
            "source_export_checksums": "PASS",
            "execution_manifest_checksum": "PASS",
        },
    }
    report_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = report_file.with_suffix(f"{report_file.suffix}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(report_file)
    except OSError as error:
        raise TrainingDatasetAuditError(
            f"Could not write quality report {report_file}: {error}"
        ) from error


def audit_training_dataset(
    *,
    dataset_file: Path = DEFAULT_DATASET_FILE,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
    report_file: Path = DEFAULT_REPORT_FILE,
    minimum_class_fraction: Decimal = Decimal("0.20"),
) -> TrainingDatasetAuditResult:
    """Run all integrity and modelling-readiness checks."""

    if minimum_class_fraction <= 0 or minimum_class_fraction > Decimal("0.5"):
        raise TrainingDatasetAuditError(
            "minimum_class_fraction must be greater than 0 and no more than 0.5."
        )
    dataset_path = _resolve(dataset_file).resolve()
    manifest_path = _resolve(manifest_file).resolve()
    report_path = _resolve(report_file).resolve()
    if not dataset_path.is_file():
        raise TrainingDatasetAuditError(
            f"Training dataset was not found: {dataset_path}"
        )
    if not manifest_path.is_file():
        raise TrainingDatasetAuditError(
            f"Dataset manifest was not found: {manifest_path}"
        )

    dataset_hash = _sha256(dataset_path)
    manifest = _load_json(manifest_path, "dataset manifest")
    rows_by_experiment: Counter[str] = Counter()
    labels: Counter[int] = Counter()
    keys: set[tuple[str, datetime]] = set()
    metadata: dict[str, tuple[str, ...]] = {}

    try:
        with dataset_path.open("r", encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames != list(CSV_FIELD_NAMES):
                raise TrainingDatasetAuditError(
                    "CSV header does not match the versioned export schema."
                )
            for row_number, raw_row in enumerate(reader, start=2):
                if None in raw_row or any(value is None for value in raw_row.values()):
                    raise TrainingDatasetAuditError(
                        f"Row {row_number} contains missing or extra CSV values."
                    )
                row = {str(key): str(value) for key, value in raw_row.items()}
                experiment_id, sampled_at, label = _validate_row(row, row_number)
                key = (experiment_id, sampled_at)
                if key in keys:
                    raise TrainingDatasetAuditError(
                        "Duplicate experiment/sample timestamp found: "
                        f"{experiment_id} at {sampled_at.isoformat()}."
                    )
                keys.add(key)
                rows_by_experiment[experiment_id] += 1
                labels[label] += 1
                row_metadata = tuple(row[field] for field in CONSISTENT_FIELDS)
                if experiment_id in metadata and metadata[experiment_id] != row_metadata:
                    raise TrainingDatasetAuditError(
                        f"Experiment metadata changes within {experiment_id}."
                    )
                metadata[experiment_id] = row_metadata
    except OSError as error:
        raise TrainingDatasetAuditError(
            f"Could not read training dataset {dataset_path}: {error}"
        ) from error

    row_count = sum(rows_by_experiment.values())
    if row_count == 0:
        raise TrainingDatasetAuditError("Training dataset is empty.")
    if set(labels) != {0, 1}:
        raise TrainingDatasetAuditError(
            "Training dataset must contain both target classes."
        )
    negative_fraction = Decimal(labels[0]) / Decimal(row_count)
    positive_fraction = Decimal(labels[1]) / Decimal(row_count)
    if min(negative_fraction, positive_fraction) < minimum_class_fraction:
        raise TrainingDatasetAuditError(
            "The minority target class is below the required fraction."
        )

    _validate_manifest(
        manifest,
        dataset_file=dataset_path,
        dataset_sha256=dataset_hash,
        rows_by_experiment=rows_by_experiment,
        labels=labels,
        row_count=row_count,
    )
    row_counts = list(rows_by_experiment.values())
    result = TrainingDatasetAuditResult(
        dataset_file=dataset_path,
        manifest_file=manifest_path,
        report_file=report_path,
        dataset_sha256=dataset_hash,
        row_count=row_count,
        experiment_count=len(rows_by_experiment),
        negative_label_count=labels[0],
        positive_label_count=labels[1],
        negative_label_fraction=negative_fraction,
        positive_label_fraction=positive_fraction,
        minimum_rows_per_experiment=min(row_counts),
        maximum_rows_per_experiment=max(row_counts),
    )
    _write_report(report_path, result)
    return result


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Audit the combined contention training dataset."
    )
    parser.add_argument("--dataset-file", type=Path, default=DEFAULT_DATASET_FILE)
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST_FILE)
    parser.add_argument("--report-file", type=Path, default=DEFAULT_REPORT_FILE)
    parser.add_argument(
        "--minimum-class-fraction",
        type=Decimal,
        default=Decimal("0.20"),
    )
    return parser.parse_args()


def main() -> int:
    """Run the quality audit CLI."""

    arguments = parse_arguments()
    try:
        result = audit_training_dataset(
            dataset_file=arguments.dataset_file,
            manifest_file=arguments.manifest_file,
            report_file=arguments.report_file,
            minimum_class_fraction=arguments.minimum_class_fraction,
        )
        print("Training dataset quality audit passed:")
        print(f"  Rows: {result.row_count}")
        print(f"  Experiments: {result.experiment_count}")
        print(
            f"  Negative labels: {result.negative_label_count} "
            f"({result.negative_label_fraction:.4f})"
        )
        print(
            f"  Positive labels: {result.positive_label_count} "
            f"({result.positive_label_fraction:.4f})"
        )
        print(
            "  Rows per experiment: "
            f"{result.minimum_rows_per_experiment}-"
            f"{result.maximum_rows_per_experiment}"
        )
        print(f"  SHA-256: {result.dataset_sha256}")
        print(f"  Report: {result.report_file}")
        return 0
    except TrainingDatasetAuditError as error:
        print(f"Training dataset audit error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())