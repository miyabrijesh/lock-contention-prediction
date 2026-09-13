import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    DataExport,
    ExperimentRun,
    MetricSample,
)
from backend.app.models.enums import ExperimentStatus


SCHEMA_VERSION = "1.0.0"
PREDICTION_HORIZON_SECONDS = 30

COUNT_FEATURE_NAMES = (
    "active_transactions",
    "blocked_sessions",
    "waiting_locks",
    "total_locks",
    "total_connections",
    "deadlock_delta",
    "configured_users",
    "active_users",
)

DECIMAL_FEATURE_NAMES = (
    "connection_utilization",
    "estimated_wait_age_ms",
    "requests_per_sec",
    "mean_latency_ms",
    "p95_latency_ms",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
)

CSV_FIELD_NAMES = (
    "schema_version",
    "experiment_run_id",
    "experiment_name",
    "sampled_at",
    "collector_version",
    "code_version",
    "booking_mode",
    "random_seed",
    "sample_interval_sec",
    "simulated_work_ms",
    "is_complete",
    "quality_notes",
    "active_transactions",
    "blocked_sessions",
    "waiting_locks",
    "total_locks",
    "total_connections",
    "connection_utilization",
    "estimated_wait_age_ms",
    "deadlock_delta",
    "configured_users",
    "active_users",
    "requests_per_sec",
    "mean_latency_ms",
    "p95_latency_ms",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
    "future_max_blocked_sessions_30s",
    "future_max_waiting_locks_30s",
    "future_deadlock_delta_sum_30s",
    "future_max_timeout_rate_30s",
    "future_max_p95_latency_ms_30s",
    "future_high_contention_30s",
)


@dataclass(frozen=True)
class LabelEvidence:
    """Future-window evidence used to create one target."""

    future_max_blocked_sessions: int
    future_max_waiting_locks: int
    future_deadlock_delta_sum: int
    future_max_timeout_rate: Decimal
    future_max_p95_latency_ms: Decimal
    future_high_contention: int


@dataclass(frozen=True)
class ExperimentExportResult:
    """Result returned after exporting one experiment."""

    data_export_id: UUID
    experiment_run_id: UUID
    file_path: Path
    file_sha256: str
    row_count: int
    schema_version: str


class ExperimentExportError(Exception):
    """Expected experiment dataset export failure."""


def _required_count(
    sample: MetricSample,
    field_name: str,
) -> int:
    """Read one required nonnegative integer metric."""

    value = getattr(
        sample,
        field_name,
        None,
    )

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
    ):
        raise ExperimentExportError(
            f"Metric sample {sample.id} has an invalid "
            f"{field_name} value."
        )

    if value < 0:
        raise ExperimentExportError(
            f"Metric sample {sample.id} has a negative "
            f"{field_name} value."
        )

    return value


def _required_decimal(
    sample: MetricSample,
    field_name: str,
) -> Decimal:
    """Read one required nonnegative decimal metric."""

    value = getattr(
        sample,
        field_name,
        None,
    )

    if not isinstance(value, Decimal):
        raise ExperimentExportError(
            f"Metric sample {sample.id} has an invalid "
            f"{field_name} value."
        )

    if value < 0:
        raise ExperimentExportError(
            f"Metric sample {sample.id} has a negative "
            f"{field_name} value."
        )

    return value


def _validate_rate(
    *,
    sample: MetricSample,
    field_name: str,
    value: Decimal,
) -> None:
    """Validate one metric whose range is zero to one."""

    if value > 1:
        raise ExperimentExportError(
            f"Metric sample {sample.id} has an out-of-range "
            f"{field_name} value."
        )


def _validate_sample(
    sample: MetricSample,
) -> None:
    """Validate one candidate feature sample."""

    if not sample.is_complete:
        raise ExperimentExportError(
            f"Metric sample {sample.id} is incomplete."
        )

    for field_name in COUNT_FEATURE_NAMES:
        _required_count(
            sample,
            field_name,
        )

    for field_name in DECIMAL_FEATURE_NAMES:
        decimal_value = _required_decimal(
            sample,
            field_name,
        )

        if field_name in {
            "connection_utilization",
            "failure_rate",
            "hotspot_ratio",
            "booking_success_rate",
            "conflict_rate",
            "timeout_rate",
        }:
            _validate_rate(
                sample=sample,
                field_name=field_name,
                value=decimal_value,
            )


def _load_completed_experiment(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun:
    """Load one completed experiment."""

    experiment = session.get(
        ExperimentRun,
        experiment_run_id,
    )

    if experiment is None:
        raise ExperimentExportError(
            f"Experiment {experiment_run_id} was not found."
        )

    if experiment.status != ExperimentStatus.COMPLETED:
        raise ExperimentExportError(
            "Only COMPLETED experiments may be exported. "
            f"The current status is {experiment.status.value}."
        )

    return experiment


def _load_metric_samples(
    session: Session,
    experiment_run_id: UUID,
) -> list[MetricSample]:
    """Load one experiment's metric samples in time order."""

    statement = (
        select(MetricSample)
        .where(
            MetricSample.experiment_run_id
            == experiment_run_id
        )
        .order_by(
            MetricSample.sampled_at.asc(),
            MetricSample.id.asc(),
        )
    )

    samples = list(
        session.scalars(statement).all()
    )

    if not samples:
        raise ExperimentExportError(
            "The experiment has no metric samples."
        )

    previous_timestamp: datetime | None = None

    for sample in samples:
        _validate_sample(sample)

        if (
            previous_timestamp is not None
            and sample.sampled_at
            <= previous_timestamp
        ):
            raise ExperimentExportError(
                "Metric sample timestamps must be "
                "strictly increasing."
            )

        previous_timestamp = sample.sampled_at

    return samples


def _existing_export(
    session: Session,
    experiment_run_id: UUID,
    *,
    schema_version: str = SCHEMA_VERSION,
) -> DataExport | None:
    """Find an existing export of the requested schema version."""

    statement = select(DataExport).where(
        DataExport.experiment_run_id
        == experiment_run_id,
        DataExport.schema_version
        == schema_version,
    )

    return session.scalar(statement)

def _calculate_label_evidence(
    future_samples: list[MetricSample],
) -> LabelEvidence:
    """Calculate target evidence from future samples."""

    if not future_samples:
        raise ExperimentExportError(
            "Cannot create a label without future samples."
        )

    future_blocked_sessions = [
        _required_count(
            sample,
            "blocked_sessions",
        )
        for sample in future_samples
    ]

    future_waiting_locks = [
        _required_count(
            sample,
            "waiting_locks",
        )
        for sample in future_samples
    ]

    future_deadlock_deltas = [
        _required_count(
            sample,
            "deadlock_delta",
        )
        for sample in future_samples
    ]

    future_timeout_rates = [
        _required_decimal(
            sample,
            "timeout_rate",
        )
        for sample in future_samples
    ]

    future_p95_latencies = [
        _required_decimal(
            sample,
            "p95_latency_ms",
        )
        for sample in future_samples
    ]

    maximum_blocked_sessions = max(
        future_blocked_sessions
    )
    maximum_waiting_locks = max(
        future_waiting_locks
    )
    deadlock_delta_sum = sum(
        future_deadlock_deltas
    )
    maximum_timeout_rate = max(
        future_timeout_rates
    )
    maximum_p95_latency = max(
        future_p95_latencies
    )

    high_contention = int(
        maximum_blocked_sessions >= 2
        or maximum_waiting_locks >= 2
        or deadlock_delta_sum >= 1
        or maximum_timeout_rate > 0
    )

    return LabelEvidence(
        future_max_blocked_sessions=(
            maximum_blocked_sessions
        ),
        future_max_waiting_locks=(
            maximum_waiting_locks
        ),
        future_deadlock_delta_sum=(
            deadlock_delta_sum
        ),
        future_max_timeout_rate=(
            maximum_timeout_rate
        ),
        future_max_p95_latency_ms=(
            maximum_p95_latency
        ),
        future_high_contention=high_contention,
    )


def build_labelled_rows(
    *,
    experiment: ExperimentRun,
    samples: list[MetricSample],
) -> list[dict[str, object]]:
    """Create eligible labelled rows for one experiment."""

    if not samples:
        raise ExperimentExportError(
            "Cannot build a dataset without metric samples."
        )

    # Validate every sample before using it as a feature
    # row or as evidence for a future prediction label.
    for sample in samples:
        _validate_sample(sample)

    latest_sampled_at = samples[-1].sampled_at
    prediction_horizon = timedelta(
        seconds=PREDICTION_HORIZON_SECONDS
    )

    labelled_rows: list[dict[str, object]] = []

    for current_sample in samples:
        horizon_ends_at = (
            current_sample.sampled_at
            + prediction_horizon
        )

        if horizon_ends_at > latest_sampled_at:
            continue

        future_samples = [
            future_sample
            for future_sample in samples
            if (
                future_sample.sampled_at
                > current_sample.sampled_at
                and future_sample.sampled_at
                <= horizon_ends_at
            )
        ]

        label_evidence = _calculate_label_evidence(
            future_samples
        )

        row: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "experiment_run_id": str(experiment.id),
            "experiment_name": experiment.name,
            "sampled_at": (
                current_sample.sampled_at.isoformat()
            ),
            "collector_version": (
                current_sample.collector_version
            ),
            "code_version": (
                experiment.code_version or ""
            ),
            "booking_mode": experiment.booking_mode,
            "random_seed": experiment.random_seed,
            "sample_interval_sec": (
                experiment.sample_interval_sec
            ),
            "simulated_work_ms": (
                experiment.simulated_work_ms
            ),
            "is_complete": "true",
            "quality_notes": json.dumps(
                current_sample.quality_notes,
                separators=(",", ":"),
            ),
            "active_transactions": _required_count(
                current_sample,
                "active_transactions",
            ),
            "blocked_sessions": _required_count(
                current_sample,
                "blocked_sessions",
            ),
            "waiting_locks": _required_count(
                current_sample,
                "waiting_locks",
            ),
            "total_locks": _required_count(
                current_sample,
                "total_locks",
            ),
            "total_connections": _required_count(
                current_sample,
                "total_connections",
            ),
            "connection_utilization": str(
                _required_decimal(
                    current_sample,
                    "connection_utilization",
                )
            ),
            "estimated_wait_age_ms": str(
                _required_decimal(
                    current_sample,
                    "estimated_wait_age_ms",
                )
            ),
            "deadlock_delta": _required_count(
                current_sample,
                "deadlock_delta",
            ),
            "configured_users": _required_count(
                current_sample,
                "configured_users",
            ),
            "active_users": _required_count(
                current_sample,
                "active_users",
            ),
            "requests_per_sec": str(
                _required_decimal(
                    current_sample,
                    "requests_per_sec",
                )
            ),
            "mean_latency_ms": str(
                _required_decimal(
                    current_sample,
                    "mean_latency_ms",
                )
            ),
            "p95_latency_ms": str(
                _required_decimal(
                    current_sample,
                    "p95_latency_ms",
                )
            ),
            "failure_rate": str(
                _required_decimal(
                    current_sample,
                    "failure_rate",
                )
            ),
            "hotspot_ratio": str(
                _required_decimal(
                    current_sample,
                    "hotspot_ratio",
                )
            ),
            "booking_success_rate": str(
                _required_decimal(
                    current_sample,
                    "booking_success_rate",
                )
            ),
            "conflict_rate": str(
                _required_decimal(
                    current_sample,
                    "conflict_rate",
                )
            ),
            "timeout_rate": str(
                _required_decimal(
                    current_sample,
                    "timeout_rate",
                )
            ),
            "future_max_blocked_sessions_30s": (
                label_evidence
                .future_max_blocked_sessions
            ),
            "future_max_waiting_locks_30s": (
                label_evidence
                .future_max_waiting_locks
            ),
            "future_deadlock_delta_sum_30s": (
                label_evidence
                .future_deadlock_delta_sum
            ),
            "future_max_timeout_rate_30s": str(
                label_evidence
                .future_max_timeout_rate
            ),
            "future_max_p95_latency_ms_30s": str(
                label_evidence
                .future_max_p95_latency_ms
            ),
            "future_high_contention_30s": (
                label_evidence
                .future_high_contention
            ),
        }

        labelled_rows.append(row)

    if not labelled_rows:
        raise ExperimentExportError(
            "The experiment has no samples with a complete "
            f"{PREDICTION_HORIZON_SECONDS}-second future "
            "window. Run a longer experiment."
        )

    return labelled_rows


def _write_csv(
    *,
    output_path: Path,
    rows: list[dict[str, object]],
) -> None:
    """Write labelled rows to an immutable CSV file."""

    if output_path.exists():
        raise ExperimentExportError(
            f"The export file already exists: {output_path}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = output_path.with_suffix(
        f"{output_path.suffix}.tmp"
    )

    try:
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=CSV_FIELD_NAMES,
                extrasaction="raise",
            )

            writer.writeheader()
            writer.writerows(rows)

        temporary_path.replace(output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def _calculate_sha256(
    file_path: Path,
) -> str:
    """Calculate the SHA-256 digest of one file."""

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        while True:
            chunk = input_file.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def export_completed_experiment(
    session: Session,
    *,
    experiment_run_id: UUID,
    output_directory: Path,
    created_by: str,
) -> ExperimentExportResult:
    """Export and register one completed experiment."""

    experiment = _load_completed_experiment(
        session,
        experiment_run_id,
    )

    existing_export = _existing_export(
        session,
        experiment_run_id,
    )

    if existing_export is not None:
        raise ExperimentExportError(
            "A registered export already exists for "
            f"experiment {experiment_run_id}."
        )

    samples = _load_metric_samples(
        session,
        experiment_run_id,
    )

    labelled_rows = build_labelled_rows(
        experiment=experiment,
        samples=samples,
    )

    output_path = (
        output_directory
        / f"{experiment_run_id}.csv"
    )

    file_created = False

    try:
        _write_csv(
            output_path=output_path,
            rows=labelled_rows,
        )
        file_created = True

        file_sha256 = _calculate_sha256(
            output_path
        )

        data_export = DataExport(
            experiment_run_id=experiment_run_id,
            file_path=str(output_path),
            file_sha256=file_sha256,
            schema_version=SCHEMA_VERSION,
            row_count=len(labelled_rows),
            created_by=created_by,
        )

        session.add(data_export)
        session.commit()
        session.refresh(data_export)

        return ExperimentExportResult(
            data_export_id=data_export.id,
            experiment_run_id=experiment_run_id,
            file_path=output_path,
            file_sha256=file_sha256,
            row_count=len(labelled_rows),
            schema_version=SCHEMA_VERSION,
        )

    except ExperimentExportError:
        session.rollback()

        if file_created and output_path.exists():
            output_path.unlink()

        raise
    except SQLAlchemyError as error:
        session.rollback()

        if file_created and output_path.exists():
            output_path.unlink()

        raise ExperimentExportError(
            "The database export record could not be saved."
        ) from error
    except OSError as error:
        session.rollback()

        if file_created and output_path.exists():
            output_path.unlink()

        raise ExperimentExportError(
            f"The dataset file could not be written: {error}"
        ) from error


def parse_arguments() -> argparse.Namespace:
    """Read exporter command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create an immutable labelled CSV export "
            "for one completed experiment."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the COMPLETED experiment.",
    )

    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(
            "data/exports/experiments"
        ),
        help=(
            "Directory for per-experiment exports. "
            "Default: data/exports/experiments"
        ),
    )

    parser.add_argument(
        "--created-by",
        default="dataset-exporter",
        help=(
            "Actor recorded in the data_exports table."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Export one completed experiment."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            result = export_completed_experiment(
                session,
                experiment_run_id=(
                    arguments.experiment_run_id
                ),
                output_directory=(
                    arguments.output_directory
                ),
                created_by=arguments.created_by,
            )
    except ExperimentExportError as error:
        print(
            f"Experiment export error: {error}",
            file=sys.stderr,
        )
        return 2

    print("Experiment export completed:")
    print(
        f"  Experiment: {result.experiment_run_id}"
    )
    print(
        f"  Export ID: {result.data_export_id}"
    )
    print(
        f"  File: {result.file_path}"
    )
    print(
        f"  Rows: {result.row_count}"
    )
    print(
        f"  Schema version: "
        f"{result.schema_version}"
    )
    print(
        f"  SHA-256: {result.file_sha256}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())