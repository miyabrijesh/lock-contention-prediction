from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    DataExport,
    ExperimentRun,
    MetricSample,
    ModelVersion,
    Prediction,
)
from backend.app.models.enums import (
    ExperimentStatus,
    RiskLevel,
)
from data_pipeline.export_verified_experiment_v2 import (
    CSV_FIELD_NAMES,
    SCHEMA_VERSION,
    VerifiedExperimentExportError,
    build_verified_rows,
    export_verified_experiment_v2,
)


def _experiment() -> ExperimentRun:
    """Create one completed in-memory experiment."""
    started_at = datetime(
        2026,
        9,
        13,
        12,
        0,
        tzinfo=UTC,
    )

    return ExperimentRun(
        id=uuid4(),
        name="Verified Export Test",
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
        random_seed=8101,
        code_version="test-version",
        started_at=started_at,
        ended_at=(
            started_at + timedelta(seconds=120)
        ),
        created_by="pytest",
    )


def _model_version() -> ModelVersion:
    """Create one inactive model version for database tests."""
    return ModelVersion(
        id=uuid4(),
        name=f"verified-export-test-{uuid4()}",
        algorithm="logistic_regression",
        feature_schema={},
        target_horizon_sec=30,
        training_run_ids=["pytest-training-run"],
        validation_strategy="pytest",
        risk_thresholds={"high": "0.34"},
        evaluation_metrics={},
        artifact_path="/tmp/pytest-model.joblib",
        artifact_sha256="c" * 64,
        code_version="pytest",
        is_active=False,
    )


def _sample(
    experiment: ExperimentRun,
) -> MetricSample:
    """Create one complete metric sample."""
    started_at = experiment.started_at

    assert started_at is not None

    return MetricSample(
        id=uuid4(),
        experiment_run_id=experiment.id,
        sampled_at=(
            started_at + timedelta(seconds=2)
        ),
        active_transactions=3,
        blocked_sessions=0,
        waiting_locks=0,
        total_locks=7,
        total_connections=12,
        connection_utilization=Decimal("0.1200"),
        estimated_wait_age_ms=Decimal("0.000"),
        deadlock_delta=0,
        configured_users=30,
        active_users=25,
        requests_per_sec=Decimal("25.500"),
        mean_latency_ms=Decimal("400.000"),
        p95_latency_ms=Decimal("700.000"),
        failure_rate=Decimal("0.0000"),
        hotspot_ratio=Decimal("0.2000"),
        booking_success_rate=Decimal("1.0000"),
        conflict_rate=Decimal("0.0000"),
        timeout_rate=Decimal("0.0000"),
        is_complete=True,
        quality_notes=[],
        collector_version="pytest-collector",
    )


def _negative_evidence() -> dict[str, object]:
    """Create valid negative live-label-v2 evidence."""
    return {
        "future_sample_count": 15,
        "expected_future_sample_count": 15,
        "minimum_future_sample_count": 14,
        "future_max_blocked_sessions_30s": 0,
        "future_max_waiting_locks_30s": 0,
        "future_deadlock_delta_sum_30s": 0,
        "future_max_timeout_rate_30s": "0.0000",
        "future_max_p95_latency_ms_30s": "750.000",
        "first_future_sampled_at": (
            "2026-09-13T12:00:04+00:00"
        ),
        "last_future_sampled_at": (
            "2026-09-13T12:00:32+00:00"
        ),
        "maximum_sample_gap_ms": 2000,
        "first_boundary_gap_ms": 2000,
        "final_boundary_gap_ms": 0,
        "maximum_allowed_gap_ms": 3500,
    }


def _prediction(
    experiment: ExperimentRun,
    sample: MetricSample,
    *,
    model_version_id: UUID | None = None,
    actual_label: bool | None = False,
    contract_version: str | None = "live-label-v2",
    stale: bool = False,
) -> Prediction:
    """Create one prediction linked to a metric sample."""
    assigned_at = (
        sample.sampled_at + timedelta(seconds=31)
        if actual_label is not None
        else None
    )
    evidence = (
        _negative_evidence()
        if actual_label is not None
        else None
    )

    return Prediction(
        id=uuid4(),
        experiment_run_id=experiment.id,
        metric_sample_id=sample.id,
        model_version_id=(
            model_version_id
            if model_version_id is not None
            else uuid4()
        ),
        predicted_at=sample.sampled_at,
        target_window_start=sample.sampled_at,
        target_window_end=(
            sample.sampled_at
            + timedelta(seconds=30)
        ),
        probability=Decimal("0.250000"),
        risk_level=RiskLevel.LOW,
        threshold_used=Decimal("0.340000"),
        is_stale=stale,
        quality_flags=[],
        actual_label=actual_label,
        actual_label_assigned_at=assigned_at,
        actual_label_contract_version=(
            contract_version
        ),
        actual_label_evidence=evidence,
    )


def test_v2_schema_excludes_model_outputs() -> None:
    """Prevent model-output leakage into dataset v2."""
    forbidden = {
        "probability",
        "risk_level",
        "threshold_used",
    }
    required = {
        "prediction_id",
        "actual_label_contract_version",
        "actual_label_assigned_at",
        "future_high_contention_30s",
    }

    assert SCHEMA_VERSION == "2.0.0"
    assert not forbidden.intersection(
        CSV_FIELD_NAMES
    )
    assert required.issubset(CSV_FIELD_NAMES)
    assert len(CSV_FIELD_NAMES) == len(
        set(CSV_FIELD_NAMES)
    )


def test_valid_live_label_v2_prediction_is_exported() -> None:
    """Accept a complete verified live-label-v2 prediction."""
    experiment = _experiment()
    sample = _sample(experiment)
    prediction = _prediction(
        experiment,
        sample,
    )

    rows, excluded = build_verified_rows(
        experiment=experiment,
        prediction_samples=[
            (prediction, sample),
        ],
    )

    assert len(rows) == 1
    assert excluded == {}
    assert rows[0]["prediction_id"] == str(
        prediction.id
    )
    assert (
        rows[0]["actual_label_contract_version"]
        == "live-label-v2"
    )


def test_target_and_evidence_come_from_prediction() -> None:
    """Use stored verified evidence rather than recalculation."""
    experiment = _experiment()
    sample = _sample(experiment)
    prediction = _prediction(
        experiment,
        sample,
    )

    rows, _ = build_verified_rows(
        experiment=experiment,
        prediction_samples=[
            (prediction, sample),
        ],
    )

    row = rows[0]

    assert row["future_high_contention_30s"] == 0
    assert (
        row["future_max_p95_latency_ms_30s"]
        == "750.000"
    )

    # The source sample contains 700 ms. The exported
    # future evidence contains the stored 750 ms value.
    assert row["p95_latency_ms"] == "700.000"


def test_unlabeled_prediction_is_excluded() -> None:
    """Exclude predictions without verified outcomes."""
    experiment = _experiment()

    valid_sample = _sample(experiment)
    valid_prediction = _prediction(
        experiment,
        valid_sample,
    )

    excluded_sample = _sample(experiment)
    excluded_prediction = _prediction(
        experiment,
        excluded_sample,
        actual_label=None,
        contract_version=None,
    )

    rows, excluded = build_verified_rows(
        experiment=experiment,
        prediction_samples=[
            (valid_prediction, valid_sample),
            (excluded_prediction, excluded_sample),
        ],
    )

    assert len(rows) == 1
    assert excluded == {"UNLABELED": 1}


def test_live_label_v1_prediction_is_excluded() -> None:
    """Exclude labels created under the historical contract."""
    experiment = _experiment()

    valid_sample = _sample(experiment)
    valid_prediction = _prediction(
        experiment,
        valid_sample,
    )

    excluded_sample = _sample(experiment)
    excluded_prediction = _prediction(
        experiment,
        excluded_sample,
        contract_version="live-label-v1",
    )

    rows, excluded = build_verified_rows(
        experiment=experiment,
        prediction_samples=[
            (valid_prediction, valid_sample),
            (excluded_prediction, excluded_sample),
        ],
    )

    assert len(rows) == 1
    assert excluded == {"WRONG_CONTRACT": 1}


def test_stale_prediction_is_excluded() -> None:
    """Exclude predictions produced from stale inputs."""
    experiment = _experiment()

    valid_sample = _sample(experiment)
    valid_prediction = _prediction(
        experiment,
        valid_sample,
    )

    excluded_sample = _sample(experiment)
    excluded_prediction = _prediction(
        experiment,
        excluded_sample,
        stale=True,
    )

    rows, excluded = build_verified_rows(
        experiment=experiment,
        prediction_samples=[
            (valid_prediction, valid_sample),
            (excluded_prediction, excluded_sample),
        ],
    )

    assert len(rows) == 1
    assert excluded == {"STALE": 1}


def test_v1_and_v2_exports_coexist_and_duplicate_is_rejected(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """Allow v1 and v2 while refusing duplicate v2 exports."""
    experiment = _experiment()
    model_version = _model_version()
    sample = _sample(experiment)
    prediction = _prediction(
        experiment,
        sample,
        model_version_id=model_version.id,
    )

    db_session.add_all([
        experiment,
        model_version,
    ])
    db_session.flush()

    db_session.add(sample)
    db_session.flush()

    db_session.add(prediction)
    db_session.flush()

    version_1_export = DataExport(
        experiment_run_id=experiment.id,
        file_path="/tmp/existing-version-1.csv",
        file_sha256="d" * 64,
        schema_version="1.0.0",
        row_count=1,
        created_by="pytest",
    )

    db_session.add(version_1_export)
    db_session.flush()

    result = export_verified_experiment_v2(
        db_session,
        experiment_run_id=experiment.id,
        output_directory=tmp_path,
        created_by="pytest",
    )

    assert result.schema_version == "2.0.0"
    assert result.row_count == 1
    assert result.total_prediction_count == 1
    assert result.excluded_counts == {}
    assert result.file_path.is_file()

    exports = list(
        db_session.scalars(
            select(DataExport)
            .where(
                DataExport.experiment_run_id
                == experiment.id
            )
            .order_by(
                DataExport.schema_version
            )
        ).all()
    )

    assert [
        export.schema_version
        for export in exports
    ] == [
        "1.0.0",
        "2.0.0",
    ]

    with pytest.raises(
        VerifiedExperimentExportError,
        match="already registered",
    ):
        export_verified_experiment_v2(
            db_session,
            experiment_run_id=experiment.id,
            output_directory=tmp_path,
            created_by="pytest",
        )