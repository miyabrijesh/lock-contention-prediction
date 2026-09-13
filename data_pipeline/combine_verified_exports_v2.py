import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
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
from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
)
from data_pipeline.export_experiment import (
    COUNT_FEATURE_NAMES,
    DECIMAL_FEATURE_NAMES,
)
from data_pipeline.export_verified_experiment_v2 import (
    CSV_FIELD_NAMES,
    SCHEMA_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_EXECUTION_MANIFEST = Path(
    "data/raw/experiment_matrix/"
    "phase_8a_pilot_v2.manifest.json"
)
DEFAULT_OUTPUT_FILE = Path(
    "data/processed/"
    "phase_8a_verified_dataset_v2.csv"
)

TARGET_COLUMN = "future_high_contention_30s"

INTEGER_EVIDENCE_FIELDS = (
    "future_max_blocked_sessions_30s",
    "future_max_waiting_locks_30s",
    "future_deadlock_delta_sum_30s",
)

DECIMAL_EVIDENCE_FIELDS = (
    "future_max_timeout_rate_30s",
    "future_max_p95_latency_ms_30s",
)

RATE_FIELDS = {
    "connection_utilization",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
    "future_max_timeout_rate_30s",
}


@dataclass(frozen=True)
class SourceExport:
    data_export_id: UUID
    experiment_run_id: UUID
    file_path: Path
    file_sha256: str
    schema_version: str
    row_count: int
    created_at: datetime


@dataclass(frozen=True)
class CombinedVerifiedDatasetResult:
    output_file: Path
    manifest_file: Path
    dataset_sha256: str
    row_count: int
    experiment_count: int
    negative_label_count: int
    positive_label_count: int


class CombinedVerifiedDatasetError(Exception):
    """Expected verified-dataset combination failure."""


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()

    return (PROJECT_ROOT / path).resolve()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _calculate_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    try:
        with file_path.open("rb") as input_file:
            while True:
                chunk = input_file.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)
    except OSError as error:
        raise CombinedVerifiedDatasetError(
            f"Could not read {file_path}: {error}"
        ) from error

    return digest.hexdigest()


def _required_text(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> str:
    value = row.get(field_name)

    if value is None or not value.strip():
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"empty {field_name} value."
        )

    return value.strip()


def _parse_integer(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> int:
    value = _required_text(
        row,
        field_name,
        file_path=file_path,
        row_number=row_number,
    )

    try:
        parsed = int(value)
    except ValueError as error:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"invalid integer for {field_name}: {value}."
        ) from error

    if parsed < 0:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has a "
            f"negative {field_name} value."
        )

    return parsed


def _parse_decimal(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> Decimal:
    value = _required_text(
        row,
        field_name,
        file_path=file_path,
        row_number=row_number,
    )

    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"invalid decimal for {field_name}: {value}."
        ) from error

    if not parsed.is_finite() or parsed < 0:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"invalid nonnegative decimal for {field_name}."
        )

    if field_name in RATE_FIELDS and parsed > 1:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"out-of-range {field_name} value."
        )

    return parsed


def _parse_timestamp(
    row: Mapping[str, str],
    field_name: str,
    *,
    file_path: Path,
    row_number: int,
) -> datetime:
    value = _required_text(
        row,
        field_name,
        file_path=file_path,
        row_number=row_number,
    )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            f"invalid {field_name} timestamp."
        ) from error

    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has a "
            f"timezone-naive {field_name} timestamp."
        )

    return parsed


def _load_experiment_ids(
    execution_manifest_file: Path,
) -> list[UUID]:
    try:
        value = json.loads(
            execution_manifest_file.read_text(
                encoding="utf-8"
            )
        )
    except OSError as error:
        raise CombinedVerifiedDatasetError(
            "Could not read execution manifest "
            f"{execution_manifest_file}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise CombinedVerifiedDatasetError(
            "Execution manifest is not valid JSON."
        ) from error

    if not isinstance(value, dict):
        raise CombinedVerifiedDatasetError(
            "Execution manifest root must be an object."
        )

    scenarios = value.get("scenarios")

    if not isinstance(scenarios, dict):
        raise CombinedVerifiedDatasetError(
            "Execution manifest does not contain a "
            "scenarios object."
        )

    experiment_ids: list[UUID] = []

    for scenario_id, entry in scenarios.items():
        if (
            not isinstance(scenario_id, str)
            or not isinstance(entry, dict)
        ):
            raise CombinedVerifiedDatasetError(
                "Execution manifest contains an invalid "
                "scenario entry."
            )

        if entry.get("status") != "COMPLETED":
            raise CombinedVerifiedDatasetError(
                f"Scenario {scenario_id} is not COMPLETED."
            )

        attempts = entry.get("attempts")

        if not isinstance(attempts, list) or not attempts:
            raise CombinedVerifiedDatasetError(
                f"Scenario {scenario_id} has no attempt."
            )

        latest_attempt = attempts[-1]

        if not isinstance(latest_attempt, dict):
            raise CombinedVerifiedDatasetError(
                f"Scenario {scenario_id} has an invalid "
                "latest attempt."
            )

        raw_experiment_id = latest_attempt.get(
            "experiment_run_id"
        )

        if not isinstance(raw_experiment_id, str):
            raise CombinedVerifiedDatasetError(
                f"Scenario {scenario_id} has no valid "
                "experiment_run_id."
            )

        try:
            experiment_id = UUID(raw_experiment_id)
        except ValueError as error:
            raise CombinedVerifiedDatasetError(
                f"Scenario {scenario_id} contains an "
                "invalid experiment UUID."
            ) from error

        experiment_ids.append(experiment_id)

    if len(experiment_ids) < 3:
        raise CombinedVerifiedDatasetError(
            "At least three completed experiments are "
            "required."
        )

    if len(set(experiment_ids)) != len(experiment_ids):
        raise CombinedVerifiedDatasetError(
            "Execution manifest contains duplicate "
            "experiment IDs."
        )

    return experiment_ids


def _load_source_export(
    session: Session,
    experiment_run_id: UUID,
) -> SourceExport:
    experiment = session.get(
        ExperimentRun,
        experiment_run_id,
    )

    if experiment is None:
        raise CombinedVerifiedDatasetError(
            f"Experiment {experiment_run_id} was not found."
        )

    if experiment.status != ExperimentStatus.COMPLETED:
        raise CombinedVerifiedDatasetError(
            f"Experiment {experiment_run_id} is not "
            "COMPLETED."
        )

    data_export = session.scalar(
        select(DataExport).where(
            DataExport.experiment_run_id
            == experiment_run_id,
            DataExport.schema_version
            == SCHEMA_VERSION,
        )
    )

    if data_export is None:
        raise CombinedVerifiedDatasetError(
            f"Experiment {experiment_run_id} has no "
            f"registered schema-{SCHEMA_VERSION} export."
        )

    file_path = _resolve(
        Path(data_export.file_path)
    )

    if not file_path.is_file():
        raise CombinedVerifiedDatasetError(
            f"Export file does not exist: {file_path}"
        )

    calculated_sha256 = _calculate_sha256(
        file_path
    )

    if (
        calculated_sha256.lower()
        != data_export.file_sha256.lower()
    ):
        raise CombinedVerifiedDatasetError(
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


def _normalise_row(
    raw_row: Mapping[str | None, str | None],
    *,
    file_path: Path,
    row_number: int,
) -> dict[str, str]:
    row: dict[str, str] = {}

    for field_name, field_value in raw_row.items():
        if field_name is None:
            raise CombinedVerifiedDatasetError(
                f"Row {row_number} in {file_path} has "
                "more values than header fields."
            )

        if field_value is None:
            raise CombinedVerifiedDatasetError(
                f"Row {row_number} in {file_path} is "
                f"missing {field_name}."
            )

        row[field_name] = field_value

    return row


def _validate_row(
    row: Mapping[str, str],
    *,
    source_export: SourceExport,
    row_number: int,
) -> None:
    file_path = source_export.file_path

    schema_version = _required_text(
        row,
        "schema_version",
        file_path=file_path,
        row_number=row_number,
    )

    if schema_version != SCHEMA_VERSION:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} uses "
            f"schema {schema_version}."
        )

    experiment_id_text = _required_text(
        row,
        "experiment_run_id",
        file_path=file_path,
        row_number=row_number,
    )

    try:
        experiment_id = UUID(
            experiment_id_text
        )
    except ValueError as error:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            "invalid experiment_run_id."
        ) from error

    if experiment_id != source_export.experiment_run_id:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} belongs "
            "to the wrong experiment."
        )

    prediction_id_text = _required_text(
        row,
        "prediction_id",
        file_path=file_path,
        row_number=row_number,
    )

    try:
        UUID(prediction_id_text)
    except ValueError as error:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            "invalid prediction_id."
        ) from error

    contract_version = _required_text(
        row,
        "actual_label_contract_version",
        file_path=file_path,
        row_number=row_number,
    )

    if contract_version != LIVE_LABEL_CONTRACT_VERSION:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} does not "
            "use live-label-v2."
        )

    _parse_timestamp(
        row,
        "sampled_at",
        file_path=file_path,
        row_number=row_number,
    )
    _parse_timestamp(
        row,
        "actual_label_assigned_at",
        file_path=file_path,
        row_number=row_number,
    )

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
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} is not "
            "marked complete."
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

    evidence_counts = {
        field_name: _parse_integer(
            row,
            field_name,
            file_path=file_path,
            row_number=row_number,
        )
        for field_name in INTEGER_EVIDENCE_FIELDS
    }

    evidence_decimals = {
        field_name: _parse_decimal(
            row,
            field_name,
            file_path=file_path,
            row_number=row_number,
        )
        for field_name in DECIMAL_EVIDENCE_FIELDS
    }

    target = _parse_integer(
        row,
        TARGET_COLUMN,
        file_path=file_path,
        row_number=row_number,
    )

    if target not in {0, 1}:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has an "
            "invalid target label."
        )

    expected_target = int(
        evidence_counts[
            "future_max_blocked_sessions_30s"
        ]
        >= 2
        or evidence_counts[
            "future_max_waiting_locks_30s"
        ]
        >= 2
        or evidence_counts[
            "future_deadlock_delta_sum_30s"
        ]
        >= 1
        or evidence_decimals[
            "future_max_timeout_rate_30s"
        ]
        > 0
    )

    if target != expected_target:
        raise CombinedVerifiedDatasetError(
            f"Row {row_number} in {file_path} has a "
            "target inconsistent with its stored evidence."
        )


def _read_source_rows(
    source_export: SourceExport,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    try:
        with source_export.file_path.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as input_file:
            reader = csv.DictReader(input_file)

            if reader.fieldnames != list(
                CSV_FIELD_NAMES
            ):
                raise CombinedVerifiedDatasetError(
                    "CSV header mismatch in "
                    f"{source_export.file_path}."
                )

            for row_number, raw_row in enumerate(
                reader,
                start=2,
            ):
                row = _normalise_row(
                    raw_row,
                    file_path=source_export.file_path,
                    row_number=row_number,
                )

                _validate_row(
                    row,
                    source_export=source_export,
                    row_number=row_number,
                )

                rows.append(row)
    except CombinedVerifiedDatasetError:
        raise
    except (OSError, csv.Error) as error:
        raise CombinedVerifiedDatasetError(
            f"Could not read export "
            f"{source_export.file_path}: {error}"
        ) from error

    if len(rows) != source_export.row_count:
        raise CombinedVerifiedDatasetError(
            f"Export {source_export.file_path} contains "
            f"{len(rows)} rows, but its registration "
            f"reports {source_export.row_count}."
        )

    return rows


def _combine_rows(
    source_rows: Sequence[
        Sequence[dict[str, str]]
    ],
) -> list[dict[str, str]]:
    combined: list[dict[str, str]] = []
    prediction_ids: set[str] = set()
    sample_keys: set[tuple[str, str]] = set()

    for rows in source_rows:
        for row in rows:
            prediction_id = row["prediction_id"]
            sample_key = (
                row["experiment_run_id"],
                row["sampled_at"],
            )

            if prediction_id in prediction_ids:
                raise CombinedVerifiedDatasetError(
                    "Duplicate prediction_id detected: "
                    f"{prediction_id}."
                )

            if sample_key in sample_keys:
                raise CombinedVerifiedDatasetError(
                    "Duplicate experiment/sample pair "
                    f"detected: {sample_key}."
                )

            prediction_ids.add(prediction_id)
            sample_keys.add(sample_key)
            combined.append(row)

    combined.sort(
        key=lambda row: (
            row["experiment_run_id"],
            row["sampled_at"],
            row["prediction_id"],
        )
    )

    return combined


def _write_csv_atomically(
    output_file: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    if output_file.exists():
        raise CombinedVerifiedDatasetError(
            f"Output file already exists: {output_file}"
        )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(
        f"{output_file.suffix}.tmp"
    )

    if temporary_file.exists():
        raise CombinedVerifiedDatasetError(
            f"Temporary output already exists: "
            f"{temporary_file}"
        )

    try:
        with temporary_file.open(
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
            writer.writerows(rows)

        temporary_file.replace(output_file)
    except Exception:
        temporary_file.unlink(missing_ok=True)
        raise


def _write_manifest_atomically(
    manifest_file: Path,
    manifest: Mapping[str, object],
) -> None:
    if manifest_file.exists():
        raise CombinedVerifiedDatasetError(
            f"Manifest file already exists: "
            f"{manifest_file}"
        )

    manifest_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = manifest_file.with_suffix(
        f"{manifest_file.suffix}.tmp"
    )

    if temporary_file.exists():
        raise CombinedVerifiedDatasetError(
            f"Temporary manifest already exists: "
            f"{temporary_file}"
        )

    try:
        with temporary_file.open(
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

        temporary_file.replace(manifest_file)
    except Exception:
        temporary_file.unlink(missing_ok=True)
        raise


def combine_verified_exports_v2(
    session: Session,
    *,
    execution_manifest_file: Path,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    manifest_file: Path | None = None,
    created_by: str = "phase-8a-combiner",
) -> CombinedVerifiedDatasetResult:
    if not created_by.strip():
        raise CombinedVerifiedDatasetError(
            "created_by must not be empty."
        )

    resolved_execution_manifest = _resolve(
        execution_manifest_file
    )
    resolved_output_file = _resolve(output_file)

    resolved_manifest_file = (
        _resolve(manifest_file)
        if manifest_file is not None
        else resolved_output_file.with_suffix(
            ".manifest.json"
        )
    )

    if resolved_output_file == resolved_manifest_file:
        raise CombinedVerifiedDatasetError(
            "Dataset and manifest paths must differ."
        )

    if (
        resolved_output_file.exists()
        or resolved_manifest_file.exists()
    ):
        raise CombinedVerifiedDatasetError(
            "The combined dataset or manifest already "
            "exists. Versioned outputs are immutable."
        )

    experiment_ids = _load_experiment_ids(
        resolved_execution_manifest
    )

    source_exports: list[SourceExport] = []
    source_rows: list[list[dict[str, str]]] = []

    for experiment_id in experiment_ids:
        source_export = _load_source_export(
            session,
            experiment_id,
        )
        source_exports.append(source_export)
        source_rows.append(
            _read_source_rows(source_export)
        )

    combined_rows = _combine_rows(source_rows)

    labels = Counter(
        row[TARGET_COLUMN]
        for row in combined_rows
    )
    negative_count = labels["0"]
    positive_count = labels["1"]

    if negative_count == 0 or positive_count == 0:
        raise CombinedVerifiedDatasetError(
            "Combined dataset must contain both labels."
        )

    _write_csv_atomically(
        resolved_output_file,
        combined_rows,
    )

    try:
        dataset_sha256 = _calculate_sha256(
            resolved_output_file
        )

        execution_manifest_sha256 = (
            _calculate_sha256(
                resolved_execution_manifest
            )
        )

        manifest: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "dataset_role": (
                "phase_8a_verified_training_dataset"
            ),
            "created_at": datetime.now(
                UTC
            ).isoformat(),
            "created_by": created_by.strip(),
            "dataset_file": _display_path(
                resolved_output_file
            ),
            "dataset_sha256": dataset_sha256,
            "row_count": len(combined_rows),
            "experiment_count": len(
                experiment_ids
            ),
            "column_count": len(
                CSV_FIELD_NAMES
            ),
            "csv_field_names": list(
                CSV_FIELD_NAMES
            ),
            "prediction_horizon_seconds": 30,
            "target_column": TARGET_COLUMN,
            "actual_label_contract_version": (
                LIVE_LABEL_CONTRACT_VERSION
            ),
            "label_distribution": {
                "negative": negative_count,
                "positive": positive_count,
            },
            "source_matrix": {
                "execution_manifest_file": (
                    _display_path(
                        resolved_execution_manifest
                    )
                ),
                "execution_manifest_sha256": (
                    execution_manifest_sha256
                ),
            },
            "source_exports": [
                {
                    "data_export_id": str(
                        source.data_export_id
                    ),
                    "experiment_run_id": str(
                        source.experiment_run_id
                    ),
                    "file_path": _display_path(
                        source.file_path
                    ),
                    "file_sha256": (
                        source.file_sha256
                    ),
                    "schema_version": (
                        source.schema_version
                    ),
                    "row_count": source.row_count,
                    "created_at": (
                        source.created_at.isoformat()
                    ),
                }
                for source in source_exports
            ],
        }

        _write_manifest_atomically(
            resolved_manifest_file,
            manifest,
        )
    except Exception:
        resolved_output_file.unlink(
            missing_ok=True
        )
        raise

    return CombinedVerifiedDatasetResult(
        output_file=resolved_output_file,
        manifest_file=resolved_manifest_file,
        dataset_sha256=dataset_sha256,
        row_count=len(combined_rows),
        experiment_count=len(experiment_ids),
        negative_label_count=negative_count,
        positive_label_count=positive_count,
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine registered schema-2.0.0 exports "
            "into one immutable verified dataset."
        )
    )
    parser.add_argument(
        "--execution-manifest",
        type=Path,
        default=DEFAULT_EXECUTION_MANIFEST,
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
    )
    parser.add_argument(
        "--manifest-file",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--created-by",
        default="phase-8a-combiner",
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            result = combine_verified_exports_v2(
                session,
                execution_manifest_file=(
                    arguments.execution_manifest
                ),
                output_file=arguments.output_file,
                manifest_file=(
                    arguments.manifest_file
                ),
                created_by=arguments.created_by,
            )
    except CombinedVerifiedDatasetError as error:
        print(
            f"Verified dataset combination error: "
            f"{error}",
            file=sys.stderr,
        )
        return 2

    print("Verified dataset combination completed:")
    print(
        "  Dataset:",
        _display_path(result.output_file),
    )
    print(
        "  Manifest:",
        _display_path(result.manifest_file),
    )
    print(
        "  Experiments:",
        result.experiment_count,
    )
    print("  Rows:", result.row_count)
    print(
        "  Negative labels:",
        result.negative_label_count,
    )
    print(
        "  Positive labels:",
        result.positive_label_count,
    )
    print(
        "  Schema version:",
        SCHEMA_VERSION,
    )
    print(
        "  SHA-256:",
        result.dataset_sha256,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())