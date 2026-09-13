from backend.app.api.routes import predictions
from backend.app.api.routes import predictions
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    DataExport,
    ExperimentRun,
    MetricSample,
    Prediction,
)
from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
)
from data_pipeline.export_experiment import (
    COUNT_FEATURE_NAMES,
    DECIMAL_FEATURE_NAMES,
    ExperimentExportError,
    _load_completed_experiment,
    _required_count,
    _required_decimal,
    _validate_sample,
)


SCHEMA_VERSION = "2.0.0"

PROVENANCE_FIELD_NAMES = (
    "schema_version",
    "prediction_id",
    "actual_label_contract_version",
    "actual_label_assigned_at",
)

METADATA_FIELD_NAMES = (
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
)

EVIDENCE_FIELD_NAMES = (
    "future_max_blocked_sessions_30s",
    "future_max_waiting_locks_30s",
    "future_deadlock_delta_sum_30s",
    "future_max_timeout_rate_30s",
    "future_max_p95_latency_ms_30s",
)

CSV_FIELD_NAMES = (
    *PROVENANCE_FIELD_NAMES,
    *METADATA_FIELD_NAMES,
    *COUNT_FEATURE_NAMES,
    *DECIMAL_FEATURE_NAMES,
    *EVIDENCE_FIELD_NAMES,
    "future_high_contention_30s",
)


@dataclass(frozen=True)
class VerifiedExportResult:
    data_export_id: UUID
    experiment_run_id: UUID
    file_path: Path
    file_sha256: str
    row_count: int
    schema_version: str
    total_prediction_count: int
    excluded_counts: dict[str, int]


class VerifiedExperimentExportError(Exception):
    """Expected verified-export failure."""


def _existing_v2_export(
    session: Session,
    experiment_run_id: UUID,
) -> DataExport | None:
    statement = select(DataExport).where(
        DataExport.experiment_run_id
        == experiment_run_id,
        DataExport.schema_version
        == SCHEMA_VERSION,
    )

    return session.scalar(statement)


def _load_prediction_samples(
    session: Session,
    experiment_run_id: UUID,
) -> list[tuple[Prediction, MetricSample]]:
    statement = (
        select(Prediction, MetricSample)
        .join(
            MetricSample,
            Prediction.metric_sample_id
            == MetricSample.id,
        )
        .where(
            Prediction.experiment_run_id
            == experiment_run_id,
            MetricSample.experiment_run_id
            == experiment_run_id,
        )
        .order_by(
            MetricSample.sampled_at.asc(),
            Prediction.id.asc(),
        )
    )

    return [
        (prediction, sample)
        for prediction, sample
        in session.execute(statement).all()
    ]


def _required_evidence_count(
    evidence: dict[str, object],
    field_name: str,
) -> int:
    value = evidence.get(field_name)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise VerifiedExperimentExportError(
            f"Invalid label evidence field: {field_name}."
        )

    return value


def _required_evidence_decimal(
    evidence: dict[str, object],
    field_name: str,
    *,
    maximum: Decimal | None = None,
) -> Decimal:
    value = evidence.get(field_name)

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise VerifiedExperimentExportError(
            f"Invalid label evidence field: {field_name}."
        ) from error

    if (
        not decimal_value.is_finite()
        or decimal_value < 0
        or (
            maximum is not None
            and decimal_value > maximum
        )
    ):
        raise VerifiedExperimentExportError(
            f"Invalid label evidence field: {field_name}."
        )

    return decimal_value


def _classification_reason(
    prediction: Prediction,
) -> str | None:
    if prediction.actual_label is None:
        return "UNLABELED"

    if (
        prediction.actual_label_contract_version
        != LIVE_LABEL_CONTRACT_VERSION
    ):
        return "WRONG_CONTRACT"

    if (
        prediction.actual_label_assigned_at is None
        or prediction.actual_label_evidence is None
    ):
        return "INCOMPLETE_PROVENANCE"

    if prediction.is_stale:
        return "STALE"

    return None


def _build_row(
    *,
    experiment: ExperimentRun,
    prediction: Prediction,
    sample: MetricSample,
) -> dict[str, object]:
    _validate_sample(sample)

    evidence = prediction.actual_label_evidence
    assigned_at = prediction.actual_label_assigned_at

    if assigned_at is None:
        raise VerifiedExperimentExportError(
            f"Prediction {prediction.id} has no label "
            "assignment timestamp."
    )

    if not isinstance(evidence, dict):
        raise VerifiedExperimentExportError(
            f"Prediction {prediction.id} has invalid evidence."
        )

    if not isinstance(prediction.actual_label, bool):
        raise VerifiedExperimentExportError(
            f"Prediction {prediction.id} has an invalid label."
        )

    blocked = _required_evidence_count(
        evidence,
        "future_max_blocked_sessions_30s",
    )
    waiting = _required_evidence_count(
        evidence,
        "future_max_waiting_locks_30s",
    )
    deadlocks = _required_evidence_count(
        evidence,
        "future_deadlock_delta_sum_30s",
    )
    timeout = _required_evidence_decimal(
        evidence,
        "future_max_timeout_rate_30s",
        maximum=Decimal("1"),
    )
    p95 = _required_evidence_decimal(
        evidence,
        "future_max_p95_latency_ms_30s",
    )

    evidence_label = (
        blocked >= 2
        or waiting >= 2
        or deadlocks >= 1
        or timeout > 0
    )

    if evidence_label != prediction.actual_label:
        raise VerifiedExperimentExportError(
            f"Prediction {prediction.id} label contradicts "
            "its stored evidence."
        )

    row: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "prediction_id": str(prediction.id),
        "actual_label_contract_version": (
            prediction.actual_label_contract_version
        ),
        "actual_label_assigned_at": (
            assigned_at.isoformat()
        ),
        "experiment_run_id": str(experiment.id),
        "experiment_name": experiment.name,
        "sampled_at": sample.sampled_at.isoformat(),
        "collector_version": sample.collector_version,
        "code_version": experiment.code_version or "",
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
            sample.quality_notes,
            separators=(",", ":"),
        ),
        "future_max_blocked_sessions_30s": blocked,
        "future_max_waiting_locks_30s": waiting,
        "future_deadlock_delta_sum_30s": deadlocks,
        "future_max_timeout_rate_30s": str(timeout),
        "future_max_p95_latency_ms_30s": str(p95),
        "future_high_contention_30s": int(
            prediction.actual_label
        ),
    }

    for field_name in COUNT_FEATURE_NAMES:
        row[field_name] = _required_count(
            sample,
            field_name,
        )

    for field_name in DECIMAL_FEATURE_NAMES:
        row[field_name] = str(
            _required_decimal(
                sample,
                field_name,
            )
        )

    return row


def build_verified_rows(
    *,
    experiment: ExperimentRun,
    prediction_samples: list[
        tuple[Prediction, MetricSample]
    ],
) -> tuple[list[dict[str, object]], dict[str, int]]:
    rows: list[dict[str, object]] = []
    excluded = Counter[str]()

    for prediction, sample in prediction_samples:
        reason = _classification_reason(prediction)

        if reason is not None:
            excluded[reason] += 1
            continue

        rows.append(
            _build_row(
                experiment=experiment,
                prediction=prediction,
                sample=sample,
            )
        )

    if not rows:
        raise VerifiedExperimentExportError(
            "The experiment has no verified live-label-v2 rows."
        )

    return rows, dict(sorted(excluded.items()))


def _write_csv(
    output_path: Path,
    rows: list[dict[str, object]],
) -> None:
    if output_path.exists():
        raise VerifiedExperimentExportError(
            f"The v2 export already exists: {output_path}"
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


def _calculate_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        while chunk := input_file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def export_verified_experiment_v2(
    session: Session,
    *,
    experiment_run_id: UUID,
    output_directory: Path,
    created_by: str,
) -> VerifiedExportResult:
    try:
        experiment = _load_completed_experiment(
            session,
            experiment_run_id,
        )
    except ExperimentExportError as error:
        raise VerifiedExperimentExportError(
            str(error)
        ) from error

    existing = _existing_v2_export(
        session,
        experiment_run_id,
    )

    if existing is not None:
        raise VerifiedExperimentExportError(
            "A version-2 export is already registered for "
            f"experiment {experiment_run_id}."
        )

    prediction_samples = _load_prediction_samples(
        session,
        experiment_run_id,
    )

    if not prediction_samples:
        raise VerifiedExperimentExportError(
            "The experiment has no predictions."
        )

    rows, excluded_counts = build_verified_rows(
        experiment=experiment,
        prediction_samples=prediction_samples,
    )

    output_path = (
        output_directory
        / f"{experiment_run_id}.csv"
    )
    file_created = False

    try:
        _write_csv(output_path, rows)
        file_created = True
        file_sha256 = _calculate_sha256(output_path)

        data_export = DataExport(
            experiment_run_id=experiment_run_id,
            file_path=str(output_path),
            file_sha256=file_sha256,
            schema_version=SCHEMA_VERSION,
            row_count=len(rows),
            created_by=created_by,
        )

        session.add(data_export)
        session.commit()
        session.refresh(data_export)

        return VerifiedExportResult(
            data_export_id=data_export.id,
            experiment_run_id=experiment_run_id,
            file_path=output_path,
            file_sha256=file_sha256,
            row_count=len(rows),
            schema_version=SCHEMA_VERSION,
            total_prediction_count=len(
                prediction_samples
            ),
            excluded_counts=excluded_counts,
        )

    except VerifiedExperimentExportError:
        session.rollback()
        if file_created and output_path.exists():
            output_path.unlink()
        raise
    except (SQLAlchemyError, OSError) as error:
        session.rollback()
        if file_created and output_path.exists():
            output_path.unlink()

        raise VerifiedExperimentExportError(
            f"Verified export failed: {error}"
        ) from error


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export verified live-label-v2 rows for one "
            "completed experiment."
        )
    )
    parser.add_argument(
        "experiment_run_id",
        type=UUID,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(
            "data/exports/verified_v2"
        ),
    )
    parser.add_argument(
        "--created-by",
        default="verified-v2-exporter",
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            result = export_verified_experiment_v2(
                session,
                experiment_run_id=(
                    arguments.experiment_run_id
                ),
                output_directory=(
                    arguments.output_directory
                ),
                created_by=arguments.created_by,
            )
    except VerifiedExperimentExportError as error:
        print(
            f"Verified export error: {error}",
            file=sys.stderr,
        )
        return 2

    print("Verified experiment export completed:")
    print("  Experiment:", result.experiment_run_id)
    print("  Export ID:", result.data_export_id)
    print("  File:", result.file_path)
    print("  Accepted rows:", result.row_count)
    print(
        "  Total predictions:",
        result.total_prediction_count,
    )
    print(
        "  Excluded predictions:",
        result.excluded_counts,
    )
    print("  Schema version:", result.schema_version)
    print("  SHA-256:", result.file_sha256)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())