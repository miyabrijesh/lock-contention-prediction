from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
    ModelVersion,
    Prediction,
    RiskLevel,
)
from model_training.register_model import (
    build_registration_spec,
)
from monitoring.prediction_coverage import (
    PredictionCoverageError,
    build_prediction_coverage_report,
)


def _create_experiment(
    session: Session,
    *,
    status: ExperimentStatus = (
        ExperimentStatus.COMPLETED
    ),
) -> ExperimentRun:
    """Create one isolated coverage-test experiment."""

    now = datetime.now(UTC)

    experiment = ExperimentRun(
        id=uuid4(),
        name=f"Prediction coverage test {uuid4()}",
        show_id=2,
        status=status,
        user_count=8,
        spawn_rate_per_sec=Decimal("4.00"),
        duration_sec=120,
        traffic_pattern="STEADY",
        demand_profile="LOW",
        hotspot_ratio=Decimal("0.2000"),
        hotspot_seat_ids=[101],
        sample_interval_sec=2,
        simulated_work_ms=75,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=3901,
        code_version="pytest",
        started_at=now - timedelta(seconds=120),
        ended_at=(
            now
            if status == ExperimentStatus.COMPLETED
            else None
        ),
        created_by="pytest",
    )

    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    return experiment


def _create_model_version(
    session: Session,
) -> ModelVersion:
    """Create one registered model for prediction rows."""

    specification = build_registration_spec()

    model_version = ModelVersion(
        id=uuid4(),
        name=specification.name,
        algorithm=specification.algorithm,
        feature_schema=dict(
            specification.feature_schema
        ),
        target_horizon_sec=(
            specification.target_horizon_sec
        ),
        training_run_ids=list(
            specification.training_run_ids
        ),
        validation_strategy=(
            specification.validation_strategy
        ),
        risk_thresholds=dict(
            specification.risk_thresholds
        ),
        evaluation_metrics=dict(
            specification.evaluation_metrics
        ),
        artifact_path=specification.artifact_path,
        artifact_sha256=(
            specification.artifact_sha256
        ),
        code_version=specification.code_version,
        is_active=False,
    )

    session.add(model_version)
    session.commit()
    session.refresh(model_version)

    return model_version


def _create_metric_sample(
    session: Session,
    *,
    experiment: ExperimentRun,
    sampled_at: datetime,
) -> MetricSample:
    """Create one complete metric sample."""

    sample = MetricSample(
        id=uuid4(),
        experiment_run_id=experiment.id,
        sampled_at=sampled_at,
        active_transactions=5,
        blocked_sessions=2,
        waiting_locks=2,
        total_locks=20,
        total_connections=15,
        connection_utilization=Decimal(
            "0.500000"
        ),
        estimated_wait_age_ms=Decimal(
            "125.000"
        ),
        deadlock_delta=0,
        configured_users=8,
        active_users=7,
        requests_per_sec=Decimal("6.500"),
        mean_latency_ms=Decimal("180.000"),
        p95_latency_ms=Decimal("350.000"),
        failure_rate=Decimal("0.010000"),
        hotspot_ratio=Decimal("0.2000"),
        booking_success_rate=Decimal(
            "0.950000"
        ),
        conflict_rate=Decimal("0.040000"),
        timeout_rate=Decimal("0.010000"),
        is_complete=True,
        quality_notes=[],
        collector_version="pytest",
    )

    session.add(sample)
    session.commit()
    session.refresh(sample)

    return sample


def _create_prediction(
    session: Session,
    *,
    experiment: ExperimentRun,
    sample: MetricSample,
    model_version: ModelVersion,
    is_stale: bool = False,
    actual_label: bool | None = None,
) -> Prediction:
    """Create one prediction linked to a sample."""

    target_window_end = (
        sample.sampled_at
        + timedelta(seconds=30)
    )

    actual_label_assigned_at: (
        datetime | None
    ) = None

    actual_label_contract_version: (
        str | None
    ) = None

    actual_label_evidence: (
        dict[str, object] | None
    ) = None

    if actual_label is not None:
        actual_label_assigned_at = (
            target_window_end
        )

        actual_label_contract_version = (
            "live-label-v1"
        )

        maximum_contention_count = (
            2
            if actual_label
            else 0
        )

        actual_label_evidence = {
            "future_sample_count": 15,
            "expected_future_sample_count": 15,
            (
                "future_max_"
                "blocked_sessions_30s"
            ): maximum_contention_count,
            (
                "future_max_"
                "waiting_locks_30s"
            ): maximum_contention_count,
            (
                "future_deadlock_"
                "delta_sum_30s"
            ): 0,
            (
                "future_max_"
                "timeout_rate_30s"
            ): "0.000000",
            (
                "future_max_"
                "p95_latency_ms_30s"
            ): "350.000",
            "first_future_sampled_at": (
                sample.sampled_at
                + timedelta(seconds=2)
            ).isoformat(),
            "last_future_sampled_at": (
                target_window_end.isoformat()
            ),
            "maximum_sample_gap_ms": 2000,
        }

    prediction = Prediction(
        id=uuid4(),
        experiment_run_id=experiment.id,
        metric_sample_id=sample.id,
        model_version_id=model_version.id,
        predicted_at=sample.sampled_at,
        target_window_start=(
            sample.sampled_at
        ),
        target_window_end=target_window_end,
        probability=Decimal("0.900000"),
        risk_level=RiskLevel.HIGH,
        threshold_used=Decimal("0.340000"),
        is_stale=is_stale,
        quality_flags=(
            ["STALE_TARGET_WINDOW"]
            if is_stale
            else []
        ),
        actual_label=actual_label,
        actual_label_assigned_at=(
            actual_label_assigned_at
        ),
        actual_label_contract_version=(
            actual_label_contract_version
        ),
        actual_label_evidence=(
            actual_label_evidence
        ),
    )

    session.add(prediction)
    session.commit()
    session.refresh(prediction)

    return prediction


def test_complete_prediction_coverage_passes(
    db_session: Session,
) -> None:
    """Accept one prediction for every complete sample."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    first_sampled_at = datetime.now(UTC)

    samples = [
        _create_metric_sample(
            db_session,
            experiment=experiment,
            sampled_at=(
                first_sampled_at
                + timedelta(seconds=index * 2)
            ),
        )
        for index in range(2)
    ]

    for sample in samples:
        _create_prediction(
            db_session,
            experiment=experiment,
            sample=sample,
            model_version=model_version,
        )

    report = build_prediction_coverage_report(
        db_session,
        experiment.id,
    )

    assert report.complete_sample_count == 2
    assert report.prediction_count == 2
    assert report.distinct_predicted_sample_count == 2
    assert report.missing_prediction_count == 0
    assert report.duplicate_prediction_count == 0
    assert report.stale_prediction_count == 0
    assert report.labeled_prediction_count == 0
    assert report.model_version_count == 1
    assert report.coverage == Decimal("1")
    assert report.passed is True


def test_missing_prediction_fails_coverage(
    db_session: Session,
) -> None:
    """Report incomplete prediction coverage."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    first_sampled_at = datetime.now(UTC)

    predicted_sample = _create_metric_sample(
        db_session,
        experiment=experiment,
        sampled_at=first_sampled_at,
    )

    _create_metric_sample(
        db_session,
        experiment=experiment,
        sampled_at=(
            first_sampled_at
            + timedelta(seconds=2)
        ),
    )

    _create_prediction(
        db_session,
        experiment=experiment,
        sample=predicted_sample,
        model_version=model_version,
    )

    report = build_prediction_coverage_report(
        db_session,
        experiment.id,
    )

    assert report.complete_sample_count == 2
    assert report.prediction_count == 1
    assert report.missing_prediction_count == 1
    assert report.coverage == Decimal("0.5")
    assert report.passed is False


def test_stale_or_labeled_prediction_fails(
    db_session: Session,
) -> None:
    """Reject stale or prematurely labeled live predictions."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    sample = _create_metric_sample(
        db_session,
        experiment=experiment,
        sampled_at=datetime.now(UTC),
    )

    _create_prediction(
        db_session,
        experiment=experiment,
        sample=sample,
        model_version=model_version,
        is_stale=True,
        actual_label=True,
    )

    report = build_prediction_coverage_report(
        db_session,
        experiment.id,
    )

    assert report.coverage == Decimal("1")
    assert report.stale_prediction_count == 1
    assert report.labeled_prediction_count == 1
    assert report.passed is False


def test_experiment_without_samples_fails(
    db_session: Session,
) -> None:
    """Reject an experiment without coverage evidence."""

    experiment = _create_experiment(
        db_session
    )

    report = build_prediction_coverage_report(
        db_session,
        experiment.id,
    )

    assert report.complete_sample_count == 0
    assert report.prediction_count == 0
    assert report.coverage == Decimal("0")
    assert report.passed is False


def test_unknown_experiment_is_rejected(
    db_session: Session,
) -> None:
    """Reject an unknown experiment identifier."""

    unknown_experiment_id = uuid4()

    with pytest.raises(
        PredictionCoverageError,
        match=str(unknown_experiment_id),
    ):
        build_prediction_coverage_report(
            db_session,
            unknown_experiment_id,
        )