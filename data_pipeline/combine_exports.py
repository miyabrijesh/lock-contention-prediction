import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    DataExport,
    ExperimentRun,
)
from backend.app.models.enums import ExperimentStatus
from data_pipeline.export_experiment import (
    COUNT_FEATURE_NAMES,
    CSV_FIELD_NAMES,
    DECIMAL_FEATURE_NAMES,
    SCHEMA_VERSION,
)


MINIMUM_EXPERIMENT_COUNT = 3
MINIMUM_PILOT_ROW_COUNT = 60
TARGET_COLUMN = "future_high_contention_30s"

DEFAULT_OUTPUT_FILE = Path(
    "data/processed/pilot_contention_dataset_v1.csv"
)


@dataclass(frozen=True)
class SourceExport:
    """Validated source export used by the combined dataset."""

    data_export_id: UUID
    experiment_run_id: UUID
    file_path: Path
    file_sha256: str
    schema_version: str
    row_count: int
    created_at: datetime


@dataclass(frozen=True)
class CombinedDatasetResult:
    """Details about one successfully combined dataset."""

    output_file: Path
    manifest_file: Path
    row_count: int
    experiment_count: int
    negative_label_count: int
    positive_label_count: int
    dataset_sha256: str


class CombinedDatasetError(Exception):
    """Expected combined-dataset validation or writing failure."""


def _calculate_file_sha256(
    file_path: Path,
) -> str:
    """Calculate the SHA-256 checksum of one file."""

    digest = hashlib.sha256()

    try:
        with file_path.open("rb") as input_file:
            while True:
                chunk = input_file.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)
    except OSError as error:
        raise CombinedDatasetError(
            f"Unable to read file {file_path}: {error}"
        ) from error

    return digest.hexdigest()


def _resolve_export_path(
    stored_path: str,
) -> Path:
    """Convert a stored export path into an absolute path."""

    file_path = Path(stored_path).expanduser()

    if not file_path.is_absolute():
        file_path = Path.cwd() / file_path

    return file_path.resolve()


def _load_source_export(
    session: Session,
    experiment_run_id: UUID,
) -> SourceExport:
    """Load and validate one registered experiment export."""

    experiment = session.get(
        ExperimentRun,
        experiment_run_id,
    )

    if experiment is None:
        raise CombinedDatasetError(
            f"Experiment {experiment_run_id} was not found."
        )

    if experiment.status != ExperimentStatus.COMPLETED:
        raise CombinedDatasetError(
            f"Experiment {experiment_run_id} must be COMPLETED "
            "before its export can be combined."
        )

    statement = select(DataExport).where(
        DataExport.experiment_run_id
        == experiment_run_id,
        DataExport.schema_version
        == SCHEMA_VERSION,
    )

    data_export = session.scalar(statement)

    if data_export is None:
        raise CombinedDatasetError(
            f"Experiment {experiment_run_id} does not have "
            "a registered dataset export."
        )

    if data_export.schema_version != SCHEMA_VERSION:
        raise CombinedDatasetError(
            f"Experiment {experiment_run_id} uses schema version "
            f"{data_export.schema_version}, but version "
            f"{SCHEMA_VERSION} is required."
        )

    file_path = _resolve_export_path(
        data_export.file_path
    )

    if not file_path.is_file():
        raise CombinedDatasetError(
            f"Export file {file_path} does not exist."
        )

    calculated_sha256 = _calculate_file_sha256(
        file_path
    )

    if (
        calculated_sha256.lower()
        != data_export.file_sha256.lower()
    ):
        raise CombinedDatasetError(
            f"Checksum validation failed for {file_path}."
        )

    return SourceExport(
        data_export_id=data_export.id,
        experiment_run_id=experiment_run_id,
        file_path=file_path,
        file_sha256=calculated_sha256,
        schema_version=data_export.schema_version,
        row_count=data_export.row_count,
        created_at=data_export.created_at,
    )


def _validate_header(
    field_names: Sequence[str],
    file_path: Path,
) -> None:
    """Require the exact version-1 CSV schema."""

    expected_field_names = list(CSV_FIELD_NAMES)
    actual_field_names = list(field_names)

    if actual_field_names != expected_field_names:
        raise CombinedDatasetError(
            f"CSV header mismatch in {file_path}. "
            f"Expected {expected_field_names}, "
            f"received {actual_field_names}."
        )


def _normalise_csv_row(
    raw_row: Mapping[str | None, str | None],
    *,
    file_path: Path,
    row_number: int,
) -> dict[str, str]:
    """Convert one DictReader row into a fully typed string mapping."""

    normalised_row: dict[str, str] = {}

    for field_name, field_value in raw_row.items():
        if field_name is None:
            raise CombinedDatasetError(
                f"Row {row_number} in {file_path} contains "
                "more values than the CSV header."
            )

        if field_value is None:
            raise CombinedDatasetError(
                f"Row {row_number} in {file_path} is missing "
                f"a value for {field_name}."
            )

        normalised_row[field_name] = field_value

    return normalised_row


def _required_text(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> str:
    """Return one required non-empty CSV value."""

    value = row.get(field_name)

    if value is None or not value.strip():
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} has an empty "
            f"required field: {field_name}."
        )

    return value.strip()


def _parse_integer(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> int:
    """Validate and return one integer CSV value."""

    value = _required_text(
        row,
        field_name,
        file_path=file_path,
        row_number=row_number,
    )

    try:
        return int(value)
    except ValueError as error:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            f"an invalid integer for {field_name}: {value}."
        ) from error


def _parse_decimal(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> Decimal:
    """Validate and return one decimal CSV value."""

    value = _required_text(
        row,
        field_name,
        file_path=file_path,
        row_number=row_number,
    )

    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            f"an invalid decimal for {field_name}: {value}."
        ) from error

    if not decimal_value.is_finite():
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            f"a non-finite decimal for {field_name}: {value}."
        )

    return decimal_value


def _validate_complete_row(
    row: Mapping[str, str],
    *,
    source_export: SourceExport,
    row_number: int,
) -> None:
    """Validate one labelled dataset row."""

    file_path = source_export.file_path

    schema_version = _required_text(
        row,
        "schema_version",
        file_path=file_path,
        row_number=row_number,
    )

    if schema_version != SCHEMA_VERSION:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} uses schema "
            f"version {schema_version} instead of "
            f"{SCHEMA_VERSION}."
        )

    experiment_id_text = _required_text(
        row,
        "experiment_run_id",
        file_path=file_path,
        row_number=row_number,
    )

    try:
        row_experiment_id = UUID(
            experiment_id_text
        )
    except ValueError as error:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            "an invalid experiment_run_id."
        ) from error

    if (
        row_experiment_id
        != source_export.experiment_run_id
    ):
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} belongs to "
            f"experiment {row_experiment_id}, but the export "
            f"is registered to "
            f"{source_export.experiment_run_id}."
        )

    sampled_at_text = _required_text(
        row,
        "sampled_at",
        file_path=file_path,
        row_number=row_number,
    )

    try:
        datetime.fromisoformat(
            sampled_at_text.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError as error:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            f"an invalid sampled_at timestamp: "
            f"{sampled_at_text}."
        ) from error

    if "is_complete" in row:
        is_complete = _required_text(
            row,
            "is_complete",
            file_path=file_path,
            row_number=row_number,
        ).lower()

        if is_complete not in {
            "true",
            "1",
            "t",
            "yes",
        }:
            raise CombinedDatasetError(
                f"Row {row_number} in {file_path} is not "
                "marked as a complete metric sample."
            )

    for field_name in COUNT_FEATURE_NAMES:
        _parse_integer(
            row,
            field_name,
            file_path=file_path,
            row_number=row_number,
        )

    for field_name in DECIMAL_FEATURE_NAMES:
        _parse_decimal(
            row,
            field_name,
            file_path=file_path,
            row_number=row_number,
        )

    target_value = _parse_integer(
        row,
        TARGET_COLUMN,
        file_path=file_path,
        row_number=row_number,
    )

    if target_value not in {0, 1}:
        raise CombinedDatasetError(
            f"Row {row_number} in {file_path} contains "
            f"an invalid {TARGET_COLUMN} value: "
            f"{target_value}. Expected 0 or 1."
        )


def _read_source_rows(
    source_export: SourceExport,
) -> list[dict[str, str]]:
    """Read and validate every row in one source CSV."""

    rows: list[dict[str, str]] = []

    try:
        with source_export.file_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as input_file:
            reader = csv.DictReader(input_file)

            raw_field_names = reader.fieldnames

            if raw_field_names is None:
                raise CombinedDatasetError(
                    f"CSV file {source_export.file_path} "
                    "does not contain a header."
                )

            # DictReader.fieldnames is typed as
            # Sequence[str] | None. After checking for None,
            # convert it to a concrete list for validation.
            field_names = list(raw_field_names)

            _validate_header(
                field_names,
                source_export.file_path,
            )

            for row_number, raw_row in enumerate(
                reader,
                start=2,
            ):
                row = _normalise_csv_row(
                    raw_row,
                    file_path=source_export.file_path,
                    row_number=row_number,
                )

                _validate_complete_row(
                    row,
                    source_export=source_export,
                    row_number=row_number,
                )

                rows.append(row)
    except CombinedDatasetError:
        raise
    except OSError as error:
        raise CombinedDatasetError(
            f"Unable to read export "
            f"{source_export.file_path}: {error}"
        ) from error
    except csv.Error as error:
        raise CombinedDatasetError(
            f"Unable to parse export "
            f"{source_export.file_path}: {error}"
        ) from error

    if len(rows) != source_export.row_count:
        raise CombinedDatasetError(
            f"Export {source_export.file_path} contains "
            f"{len(rows)} rows, but its database record "
            f"reports {source_export.row_count} rows."
        )

    return rows


def _combine_rows(
    source_rows: Sequence[
        Sequence[dict[str, str]]
    ],
) -> list[dict[str, str]]:
    """Combine, deduplicate and sort source rows."""

    combined_rows: list[dict[str, str]] = []
    observed_keys: set[tuple[str, str]] = set()

    for experiment_rows in source_rows:
        for row in experiment_rows:
            experiment_run_id = row[
                "experiment_run_id"
            ]
            sampled_at = row["sampled_at"]

            row_key = (
                experiment_run_id,
                sampled_at,
            )

            if row_key in observed_keys:
                raise CombinedDatasetError(
                    "Duplicate metric row detected for "
                    f"experiment {experiment_run_id} at "
                    f"{sampled_at}."
                )

            observed_keys.add(row_key)
            combined_rows.append(row)

    combined_rows.sort(
        key=lambda row: (
            row["experiment_run_id"],
            row["sampled_at"],
        )
    )

    return combined_rows


def _calculate_label_distribution(
    rows: Sequence[Mapping[str, str]],
) -> tuple[int, int]:
    """Count negative and positive target labels."""

    negative_count = 0
    positive_count = 0

    for row in rows:
        target_value = row.get(TARGET_COLUMN)

        if target_value == "0":
            negative_count += 1
        elif target_value == "1":
            positive_count += 1
        else:
            raise CombinedDatasetError(
                f"Unexpected target value: {target_value}."
            )

    return (
        negative_count,
        positive_count,
    )


def _validate_pilot_dataset(
    *,
    experiment_count: int,
    rows: Sequence[Mapping[str, str]],
    negative_label_count: int,
    positive_label_count: int,
) -> None:
    """Validate the minimum pilot-dataset requirements."""

    if (
        experiment_count
        < MINIMUM_EXPERIMENT_COUNT
    ):
        raise CombinedDatasetError(
            "The pilot dataset requires at least "
            f"{MINIMUM_EXPERIMENT_COUNT} different "
            "experiments."
        )

    if len(rows) < MINIMUM_PILOT_ROW_COUNT:
        raise CombinedDatasetError(
            "The pilot dataset requires at least "
            f"{MINIMUM_PILOT_ROW_COUNT} eligible rows. "
            f"Only {len(rows)} rows were available."
        )

    if negative_label_count == 0:
        raise CombinedDatasetError(
            "The pilot dataset does not contain any "
            "negative contention labels."
        )

    if positive_label_count == 0:
        raise CombinedDatasetError(
            "The pilot dataset does not contain any "
            "positive contention labels."
        )


def _write_combined_csv(
    output_file: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    """Write an immutable combined CSV dataset."""

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_file.exists():
        raise CombinedDatasetError(
            f"Output file {output_file} already exists. "
            "Combined datasets are immutable; choose a "
            "different filename."
        )

    try:
        with output_file.open(
            "x",
            encoding="utf-8",
            newline="",
        ) as output_stream:
            writer = csv.DictWriter(
                output_stream,
                fieldnames=list(CSV_FIELD_NAMES),
                extrasaction="raise",
            )

            writer.writeheader()

            for row in rows:
                writer.writerow(row)
    except FileExistsError as error:
        raise CombinedDatasetError(
            f"Output file {output_file} already exists."
        ) from error
    except (OSError, csv.Error, ValueError) as error:
        if output_file.exists():
            output_file.unlink(missing_ok=True)

        raise CombinedDatasetError(
            f"Unable to write combined dataset "
            f"{output_file}: {error}"
        ) from error


def _build_manifest(
    *,
    output_file: Path,
    dataset_sha256: str,
    source_exports: Sequence[SourceExport],
    row_count: int,
    negative_label_count: int,
    positive_label_count: int,
    created_by: str,
) -> dict[str, object]:
    """Build the provenance manifest for a combined dataset."""

    source_records: list[dict[str, object]] = []

    for source_export in source_exports:
        source_records.append({
            "data_export_id": str(
                source_export.data_export_id
            ),
            "experiment_run_id": str(
                source_export.experiment_run_id
            ),
            "file_path": str(
                source_export.file_path
            ),
            "file_sha256": (
                source_export.file_sha256
            ),
            "schema_version": (
                source_export.schema_version
            ),
            "row_count": (
                source_export.row_count
            ),
            "created_at": (
                source_export.created_at.isoformat()
            ),
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_role": "pilot_training_dataset",
        "created_at": datetime.now(
            UTC
        ).isoformat(),
        "created_by": created_by,
        "dataset_file": str(output_file),
        "dataset_sha256": dataset_sha256,
        "row_count": row_count,
        "experiment_count": len(
            source_exports
        ),
        "prediction_horizon_seconds": 30,
        "target_column": TARGET_COLUMN,
        "label_definition": {
            "positive_when_any_future_sample_has": [
                "blocked_sessions >= 2",
                "waiting_locks >= 2",
                "deadlock_delta >= 1",
                "timeout_rate > 0",
            ],
        },
        "label_distribution": {
            "negative": negative_label_count,
            "positive": positive_label_count,
        },
        "source_exports": source_records,
    }


def _write_manifest(
    manifest_file: Path,
    manifest: Mapping[str, object],
) -> None:
    """Write an immutable JSON provenance manifest."""

    manifest_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if manifest_file.exists():
        raise CombinedDatasetError(
            f"Manifest file {manifest_file} already exists. "
            "Choose a different filename."
        )

    try:
        with manifest_file.open(
            "x",
            encoding="utf-8",
        ) as output_stream:
            json.dump(
                manifest,
                output_stream,
                indent=2,
                sort_keys=True,
            )
            output_stream.write("\n")
    except FileExistsError as error:
        raise CombinedDatasetError(
            f"Manifest file {manifest_file} "
            "already exists."
        ) from error
    except (OSError, TypeError, ValueError) as error:
        if manifest_file.exists():
            manifest_file.unlink(
                missing_ok=True
            )

        raise CombinedDatasetError(
            f"Unable to write manifest "
            f"{manifest_file}: {error}"
        ) from error


def build_combined_pilot_dataset(
    session: Session,
    experiment_run_ids: Sequence[UUID],
    *,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    manifest_file: Path | None = None,
    created_by: str = "local-admin",
) -> CombinedDatasetResult:
    """Build one validated pilot dataset from experiment exports."""

    unique_experiment_ids = list(
        dict.fromkeys(experiment_run_ids)
    )

    if (
        len(unique_experiment_ids)
        != len(experiment_run_ids)
    ):
        raise CombinedDatasetError(
            "The experiment list contains duplicate UUIDs."
        )

    if (
        len(unique_experiment_ids)
        < MINIMUM_EXPERIMENT_COUNT
    ):
        raise CombinedDatasetError(
            "Provide at least "
            f"{MINIMUM_EXPERIMENT_COUNT} completed "
            "experiment UUIDs."
        )

    if not created_by.strip():
        raise CombinedDatasetError(
            "created_by must not be empty."
        )

    resolved_output_file = (
        output_file.expanduser().resolve()
    )

    if manifest_file is None:
        resolved_manifest_file = (
            resolved_output_file.with_suffix(
                ".manifest.json"
            )
        )
    else:
        resolved_manifest_file = (
            manifest_file.expanduser().resolve()
        )

    if (
        resolved_manifest_file
        == resolved_output_file
    ):
        raise CombinedDatasetError(
            "The dataset and manifest must use "
            "different file paths."
        )

    source_exports: list[SourceExport] = []
    all_source_rows: list[
        list[dict[str, str]]
    ] = []

    for experiment_run_id in unique_experiment_ids:
        source_export = _load_source_export(
            session,
            experiment_run_id,
        )

        source_exports.append(
            source_export
        )

        rows = _read_source_rows(
            source_export
        )

        all_source_rows.append(rows)

    combined_rows = _combine_rows(
        all_source_rows
    )

    (
        negative_label_count,
        positive_label_count,
    ) = _calculate_label_distribution(
        combined_rows
    )

    _validate_pilot_dataset(
        experiment_count=len(
            source_exports
        ),
        rows=combined_rows,
        negative_label_count=(
            negative_label_count
        ),
        positive_label_count=(
            positive_label_count
        ),
    )

    _write_combined_csv(
        resolved_output_file,
        combined_rows,
    )

    try:
        dataset_sha256 = _calculate_file_sha256(
            resolved_output_file
        )

        manifest = _build_manifest(
            output_file=resolved_output_file,
            dataset_sha256=dataset_sha256,
            source_exports=source_exports,
            row_count=len(combined_rows),
            negative_label_count=(
                negative_label_count
            ),
            positive_label_count=(
                positive_label_count
            ),
            created_by=created_by.strip(),
        )

        _write_manifest(
            resolved_manifest_file,
            manifest,
        )
    except Exception:
        # This file was created by this operation, so it is safe
        # to remove if manifest generation fails.
        resolved_output_file.unlink(
            missing_ok=True
        )
        raise

    return CombinedDatasetResult(
        output_file=resolved_output_file,
        manifest_file=resolved_manifest_file,
        row_count=len(combined_rows),
        experiment_count=len(
            source_exports
        ),
        negative_label_count=(
            negative_label_count
        ),
        positive_label_count=(
            positive_label_count
        ),
        dataset_sha256=dataset_sha256,
    )


def parse_arguments() -> argparse.Namespace:
    """Read combined-dataset command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Combine immutable completed-experiment "
            "exports into one validated pilot "
            "lock-contention dataset."
        )
    )

    parser.add_argument(
        "experiment_run_ids",
        type=UUID,
        nargs="+",
        help=(
            "UUIDs of at least three completed "
            "experiments with registered exports."
        ),
    )

    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=(
            "Combined CSV output path. Default: "
            "data/processed/"
            "pilot_contention_dataset_v1.csv"
        ),
    )

    parser.add_argument(
        "--manifest-file",
        type=Path,
        default=None,
        help=(
            "Optional JSON manifest path. The default "
            "is derived from the CSV filename."
        ),
    )

    parser.add_argument(
        "--created-by",
        default="local-admin",
        help=(
            "Name recorded in the dataset manifest."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Build the combined pilot dataset."""

    arguments = parse_arguments()

    experiment_run_ids: list[UUID] = list(
        arguments.experiment_run_ids
    )
    output_file = Path(
        arguments.output_file
    )

    manifest_file: Path | None

    if arguments.manifest_file is None:
        manifest_file = None
    else:
        manifest_file = Path(
            arguments.manifest_file
        )

    created_by = str(
        arguments.created_by
    )

    try:
        with SessionLocal() as session:
            result = build_combined_pilot_dataset(
                session,
                experiment_run_ids,
                output_file=output_file,
                manifest_file=manifest_file,
                created_by=created_by,
            )
    except CombinedDatasetError as error:
        print(
            f"Combined dataset error: {error}",
            file=sys.stderr,
        )
        return 2

    print("Combined pilot dataset created:")
    print(
        f"  Dataset: {result.output_file}"
    )
    print(
        f"  Manifest: {result.manifest_file}"
    )
    print(
        f"  Experiments: "
        f"{result.experiment_count}"
    )
    print(
        f"  Rows: {result.row_count}"
    )
    print(
        f"  Negative labels: "
        f"{result.negative_label_count}"
    )
    print(
        f"  Positive labels: "
        f"{result.positive_label_count}"
    )
    print(
        f"  SHA-256: {result.dataset_sha256}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())