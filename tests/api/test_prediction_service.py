from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.orm import Session

from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
    ModelVersion,
    Prediction,
    RiskLevel,
)
from backend.app.services.prediction_service import (
    PredictionServiceError,
    create_prediction_for_metric_sample,
    read_prediction,
    read_prediction_for_metric_sample,
)
from model_training.register_model import (
    build_registration_spec,
)


def _create_experiment(
    session: Session,
) -> ExperimentRun:
    """Create one completed experiment for prediction tests."""

    now = datetime.now(UTC)

    experiment = ExperimentRun(
        id=uuid4(),
        name=(
            "Prediction service test "
            f"{uuid4()}"
        ),
        show_id=2,
        status=ExperimentStatus.COMPLETED,
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
        random_seed=4101,
        code_version="pytest",
        started_at=now - timedelta(
            seconds=120
        ),
        ended_at=now,
        created_by="pytest",
    )

    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    return experiment


def _create_metric_sample(
    session: Session,
    *,
    is_complete: bool = True,
    sampled_at: datetime | None = None,
    active_transactions: int | None = 5,
    quality_notes: list[str] | None = None,
) -> MetricSample:
    """Create one metric sample with valid model features."""

    experiment = _create_experiment(
        session
    )

    if sampled_at is None:
        sampled_at = datetime.now(UTC)

    if quality_notes is None:
        quality_notes = []

    metric_sample = MetricSample(
        id=uuid4(),
        experiment_run_id=experiment.id,
        sampled_at=sampled_at,
        active_transactions=active_transactions,
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
        requests_per_sec=Decimal(
            "6.500"
        ),
        mean_latency_ms=Decimal(
            "180.000"
        ),
        p95_latency_ms=Decimal(
            "350.000"
        ),
        failure_rate=Decimal(
            "0.010000"
        ),
        hotspot_ratio=Decimal(
            "0.2000"
        ),
        booking_success_rate=Decimal(
            "0.950000"
        ),
        conflict_rate=Decimal(
            "0.040000"
        ),
        timeout_rate=Decimal(
            "0.010000"
        ),
        is_complete=is_complete,
        quality_notes=quality_notes,
        collector_version="pytest",
    )

    session.add(metric_sample)
    session.commit()
    session.refresh(metric_sample)

    return metric_sample


def _create_active_model(
    session: Session,
) -> ModelVersion:
    """Create one valid active registered model."""

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
        is_active=True,
    )

    session.add(model_version)
    session.commit()
    session.refresh(model_version)

    return model_version


def test_missing_metric_sample_is_rejected(
    db_session: Session,
) -> None:
    """Reject a prediction request for an unknown sample."""

    with pytest.raises(
        PredictionServiceError,
        match="was not found",
    ) as captured_error:
        create_prediction_for_metric_sample(
            db_session,
            uuid4(),
        )

    assert (
        captured_error.value.code
        == "METRIC_SAMPLE_NOT_FOUND"
    )

    assert captured_error.value.status_code == 404


def test_incomplete_metric_sample_is_rejected(
    db_session: Session,
) -> None:
    """Do not predict from incomplete telemetry."""

    metric_sample = _create_metric_sample(
        db_session,
        is_complete=False,
    )

    _create_active_model(
        db_session
    )

    with pytest.raises(
        PredictionServiceError,
        match="incomplete",
    ) as captured_error:
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )

    assert (
        captured_error.value.code
        == "METRIC_SAMPLE_INCOMPLETE"
    )

    prediction_count = db_session.scalar(
        select(func.count())
        .select_from(Prediction)
    )

    assert prediction_count == 0


def test_active_model_is_required(
    db_session: Session,
) -> None:
    """Do not select an inactive or nonexistent model."""

    metric_sample = _create_metric_sample(
        db_session
    )

    with pytest.raises(
        PredictionServiceError,
        match="No active model",
    ) as captured_error:
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )

    assert (
        captured_error.value.code
        == "ACTIVE_MODEL_NOT_FOUND"
    )

    assert captured_error.value.status_code == 404


def test_create_live_prediction(
    db_session: Session,
) -> None:
    """Persist a current non-stale prediction."""

    metric_sample = _create_metric_sample(
        db_session,
        sampled_at=datetime.now(UTC),
    )

    active_model = _create_active_model(
        db_session
    )

    prediction = (
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    assert prediction.id is not None

    assert prediction.experiment_run_id == (
        metric_sample.experiment_run_id
    )

    assert prediction.metric_sample_id == (
        metric_sample.id
    )

    assert prediction.model_version_id == (
        active_model.id
    )

    assert (
        Decimal("0.000000")
        <= prediction.probability
        <= Decimal("1.000000")
    )

    assert prediction.risk_level in {
        RiskLevel.LOW,
        RiskLevel.HIGH,
    }

    assert prediction.threshold_used == (
        Decimal("0.340000")
    )

    assert prediction.target_window_start == (
        metric_sample.sampled_at
    )

    assert (
        prediction.target_window_end
        - prediction.target_window_start
    ) == timedelta(seconds=30)

    assert prediction.is_stale is False
    assert prediction.quality_flags == []
    assert prediction.actual_label is None


def test_historical_prediction_is_marked_stale(
    db_session: Session,
) -> None:
    """Mark a prediction whose target window already ended."""

    historical_time = (
        datetime.now(UTC)
        - timedelta(minutes=5)
    )

    metric_sample = _create_metric_sample(
        db_session,
        sampled_at=historical_time,
        quality_notes=[
            " SOURCE_DELAY ",
            "SOURCE_DELAY",
            "",
        ],
    )

    _create_active_model(
        db_session
    )

    prediction = (
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    assert prediction.is_stale is True

    assert prediction.quality_flags == [
        "SOURCE_DELAY",
        "STALE_TARGET_WINDOW",
    ]


def test_prediction_retry_is_idempotent(
    db_session: Session,
) -> None:
    """Return the same prediction for a repeated sample."""

    metric_sample = _create_metric_sample(
        db_session
    )

    _create_active_model(
        db_session
    )

    first_prediction = (
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    second_prediction = (
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    assert first_prediction.id == (
        second_prediction.id
    )

    prediction_count = db_session.scalar(
        select(func.count())
        .select_from(Prediction)
        .where(
            Prediction.metric_sample_id
            == metric_sample.id
        )
    )

    assert prediction_count == 1


def test_invalid_complete_sample_rolls_back(
    db_session: Session,
) -> None:
    """Reject missing inference features without persistence."""

    metric_sample = _create_metric_sample(
        db_session,
        is_complete=True,
        active_transactions=None,
    )

    _create_active_model(
        db_session
    )

    with pytest.raises(
        PredictionServiceError,
        match="input validation",
    ) as captured_error:
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )

    assert (
        captured_error.value.code
        == "PREDICTION_INPUT_INVALID"
    )

    prediction_count = db_session.scalar(
        select(func.count())
        .select_from(Prediction)
    )

    assert prediction_count == 0


def test_read_persisted_prediction(
    db_session: Session,
) -> None:
    """Read a prediction by prediction and sample IDs."""

    metric_sample = _create_metric_sample(
        db_session
    )

    _create_active_model(
        db_session
    )

    prediction = (
        create_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    loaded_by_id = read_prediction(
        db_session,
        prediction.id,
    )

    loaded_by_sample = (
        read_prediction_for_metric_sample(
            db_session,
            metric_sample.id,
        )
    )

    assert loaded_by_id.id == prediction.id
    assert loaded_by_sample.id == prediction.id