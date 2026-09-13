import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    DataExport,
    ExperimentRun,
)
from backend.app.models.enums import ExperimentStatus
from data_pipeline.combine_verified_exports_v2 import (
    CombinedVerifiedDatasetError,
    SourceExport,
    _combine_rows,
    _load_experiment_ids,
    _validate_row,
    combine_verified_exports_v2,
)
from data_pipeline.export_verified_experiment_v2 import (
    CSV_FIELD_NAMES,
    SCHEMA_VERSION,
)


def _sha256(file_path: Path) -> str:
    return hashlib.sha256(
        file_path.read_bytes()
    ).hexdigest()


def _experiment(
    *,
    experiment_id: UUID,
    index: int,
) -> ExperimentRun:
    started_at = datetime(
        2026,
        9,
        13,
        12,
        0,
        tzinfo=UTC,
    ) + timedelta(minutes=index)

    return ExperimentRun(
        id=experiment_id,
        name=f"Verified Combine Test {index}",
        show_id=2,
        status=ExperimentStatus.COMPLETED,
        user_count=30,
        spawn_rate_per_sec=Decimal("10.00"),
        duration_sec=120,
        traffic_pattern="STEADY",
        demand_profile="LOW",
        hotspot_ratio=Decimal("0.2000"),
        hotspot_seat_ids=list(range(101, 121)),
        sample_interval_sec=2,
        simulated_work_ms=150,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=8100 + index,
        code_version="pytest-version",
        started_at=started_at,
        ended_at=(
            started_at + timedelta(seconds=120)
        ),
        created_by="pytest",
    )


def _row(
    *,
    experiment_id: UUID,
    prediction_id: UUID | None = None,
    sampled_at: datetime | None = None,
    positive: bool = False,
) -> dict[str, str]:
    row = {
        field_name: "0"
        for field_name in CSV_FIELD_NAMES
    }

    sample_time = (
        sampled_at
        if sampled_at is not None
        else datetime(
            2026,
            9,
            13,
            12,
            0,
            2,
            tzinfo=UTC,
        )
    )

    row.update({
        "schema_version": SCHEMA_VERSION,
        "prediction_id": str(
            prediction_id or uuid4()
        ),
        "actual_label_contract_version": (
            "live-label-v2"
        ),
        "actual_label_assigned_at": (
            sample_time
            + timedelta(seconds=31)
        ).isoformat(),
        "experiment_run_id": str(
            experiment_id
        ),
        "experiment_name": (
            "Verified Combine Test"
        ),
        "sampled_at": sample_time.isoformat(),
        "collector_version": (
            "pytest-collector"
        ),
        "code_version": "pytest-version",
        "booking_mode": (
            "EXPERIMENT_RECYCLE"
        ),
        "random_seed": "8101",
        "sample_interval_sec": "2",
        "simulated_work_ms": "150",
        "is_complete": "true",
        "quality_notes": "[]",
        "connection_utilization": "0.1000",
        "estimated_wait_age_ms": "0.000",
        "requests_per_sec": "20.000",
        "mean_latency_ms": "200.000",
        "p95_latency_ms": "400.000",
        "failure_rate": "0.0000",
        "hotspot_ratio": "0.2000",
        "booking_success_rate": "1.0000",
        "conflict_rate": "0.0000",
        "timeout_rate": "0.0000",
        "future_max_timeout_rate_30s": (
            "0.0000"
        ),
        "future_max_p95_latency_ms_30s": (
            "450.000"
        ),
        "future_high_contention_30s": (
            "1" if positive else "0"
        ),
    })

    if positive:
        row[
            "future_max_blocked_sessions_30s"
        ] = "2"

    return row


def _write_export(
    *,
    session: Session,
    directory: Path,
    experiment: ExperimentRun,
    row: dict[str, str],
) -> DataExport:
    file_path = (
        directory / f"{experiment.id}.csv"
    )

    with file_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=CSV_FIELD_NAMES,
        )
        writer.writeheader()
        writer.writerow(row)

    data_export = DataExport(
        experiment_run_id=experiment.id,
        file_path=str(file_path),
        file_sha256=_sha256(file_path),
        schema_version=SCHEMA_VERSION,
        row_count=1,
        created_by="pytest",
    )

    session.add(data_export)

    return data_export


def _write_execution_manifest(
    *,
    file_path: Path,
    experiment_ids: list[UUID],
) -> None:
    scenarios = {}

    for index, experiment_id in enumerate(
        experiment_ids,
        start=1,
    ):
        scenarios[f"scenario-{index}"] = {
            "status": "COMPLETED",
            "configuration": {
                "traffic_pattern": "STEADY",
                "demand_profile": "LOW",
                "pressure_level": (
                    "INTERMEDIATE"
                ),
                "random_seed": 8100 + index,
            },
            "attempts": [
                {
                    "status": "COMPLETED",
                    "experiment_run_id": str(
                        experiment_id
                    ),
                }
            ],
        }

    file_path.write_text(
        json.dumps(
            {
                "matrix_name": (
                    "phase_8a_test_matrix"
                ),
                "scenarios": scenarios,
            }
        ),
        encoding="utf-8",
    )


def _source_export(
    experiment_id: UUID,
) -> SourceExport:
    return SourceExport(
        data_export_id=uuid4(),
        experiment_run_id=experiment_id,
        file_path=Path("fixture.csv"),
        file_sha256="a" * 64,
        schema_version=SCHEMA_VERSION,
        row_count=1,
        created_at=datetime.now(UTC),
    )


def test_schema_has_provenance_without_model_outputs() -> None:
    forbidden = {
        "probability",
        "risk_level",
        "threshold_used",
    }

    assert SCHEMA_VERSION == "2.0.0"
    assert not forbidden.intersection(
        CSV_FIELD_NAMES
    )
    assert "prediction_id" in CSV_FIELD_NAMES
    assert (
        "actual_label_contract_version"
        in CSV_FIELD_NAMES
    )


def test_load_experiment_ids_requires_completed_scenarios(
    tmp_path: Path,
) -> None:
    experiment_ids = [
        uuid4(),
        uuid4(),
        uuid4(),
    ]
    manifest_file = tmp_path / "execution.json"

    _write_execution_manifest(
        file_path=manifest_file,
        experiment_ids=experiment_ids,
    )

    loaded = _load_experiment_ids(
        manifest_file
    )

    assert loaded == experiment_ids

    manifest = json.loads(
        manifest_file.read_text(
            encoding="utf-8"
        )
    )
    manifest["scenarios"]["scenario-2"][
        "status"
    ] = "FAILED"

    manifest_file.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(
        CombinedVerifiedDatasetError,
        match="is not COMPLETED",
    ):
        _load_experiment_ids(
            manifest_file
        )


def test_target_must_match_stored_evidence() -> None:
    experiment_id = uuid4()
    row = _row(
        experiment_id=experiment_id,
        positive=False,
    )

    row[
        "future_max_waiting_locks_30s"
    ] = "2"

    with pytest.raises(
        CombinedVerifiedDatasetError,
        match="inconsistent with its stored evidence",
    ):
        _validate_row(
            row,
            source_export=_source_export(
                experiment_id
            ),
            row_number=2,
        )


def test_duplicate_prediction_id_is_rejected() -> None:
    prediction_id = uuid4()

    first = _row(
        experiment_id=uuid4(),
        prediction_id=prediction_id,
    )
    second = _row(
        experiment_id=uuid4(),
        prediction_id=prediction_id,
    )

    with pytest.raises(
        CombinedVerifiedDatasetError,
        match="Duplicate prediction_id",
    ):
        _combine_rows([
            [first],
            [second],
        ])


def test_duplicate_experiment_sample_is_rejected() -> None:
    experiment_id = uuid4()
    sampled_at = datetime(
        2026,
        9,
        13,
        12,
        0,
        2,
        tzinfo=UTC,
    )

    first = _row(
        experiment_id=experiment_id,
        sampled_at=sampled_at,
    )
    second = _row(
        experiment_id=experiment_id,
        sampled_at=sampled_at,
    )

    with pytest.raises(
        CombinedVerifiedDatasetError,
        match="Duplicate experiment/sample pair",
    ):
        _combine_rows([
            [first, second],
        ])


def test_combination_is_verified_and_deterministic(
    db_session: Session,
    tmp_path: Path,
) -> None:
    experiment_ids = [
        uuid4(),
        uuid4(),
        uuid4(),
    ]

    experiments = [
        _experiment(
            experiment_id=experiment_id,
            index=index,
        )
        for index, experiment_id in enumerate(
            experiment_ids,
            start=1,
        )
    ]

    db_session.add_all(experiments)
    db_session.flush()

    export_directory = tmp_path / "exports"
    export_directory.mkdir()

    rows = [
        _row(
            experiment_id=experiment_ids[0],
            positive=True,
        ),
        _row(
            experiment_id=experiment_ids[1],
            positive=False,
        ),
        _row(
            experiment_id=experiment_ids[2],
            positive=True,
        ),
    ]

    for experiment, row in zip(
        experiments,
        rows,
        strict=True,
    ):
        _write_export(
            session=db_session,
            directory=export_directory,
            experiment=experiment,
            row=row,
        )

    db_session.flush()

    execution_manifest = (
        tmp_path / "execution.json"
    )
    _write_execution_manifest(
        file_path=execution_manifest,
        experiment_ids=experiment_ids,
    )

    output_file = tmp_path / "combined.csv"

    result = combine_verified_exports_v2(
        db_session,
        execution_manifest_file=(
            execution_manifest
        ),
        output_file=output_file,
        created_by="pytest",
    )

    assert result.row_count == 3
    assert result.experiment_count == 3
    assert result.negative_label_count == 1
    assert result.positive_label_count == 2
    assert result.dataset_sha256 == (
        _sha256(output_file)
    )

    with output_file.open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        combined_rows = list(
            csv.DictReader(input_file)
        )

    observed_order = [
        (
            row["experiment_run_id"],
            row["sampled_at"],
            row["prediction_id"],
        )
        for row in combined_rows
    ]

    assert observed_order == sorted(
        observed_order
    )

    manifest = json.loads(
        result.manifest_file.read_text(
            encoding="utf-8"
        )
    )

    assert manifest["schema_version"] == (
        SCHEMA_VERSION
    )
    assert manifest["row_count"] == 3
    assert manifest["experiment_count"] == 3
    assert manifest["label_distribution"] == {
        "negative": 1,
        "positive": 2,
    }
    assert len(
        manifest["source_exports"]
    ) == 3


def test_existing_output_is_not_overwritten(
    db_session: Session,
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "combined.csv"
    output_file.write_text(
        "existing\n",
        encoding="utf-8",
    )

    execution_manifest = (
        tmp_path / "execution.json"
    )
    _write_execution_manifest(
        file_path=execution_manifest,
        experiment_ids=[
            uuid4(),
            uuid4(),
            uuid4(),
        ],
    )

    with pytest.raises(
        CombinedVerifiedDatasetError,
        match="immutable",
    ):
        combine_verified_exports_v2(
            db_session,
            execution_manifest_file=(
                execution_manifest
            ),
            output_file=output_file,
        )

    assert output_file.read_text(
        encoding="utf-8"
    ) == "existing\n"