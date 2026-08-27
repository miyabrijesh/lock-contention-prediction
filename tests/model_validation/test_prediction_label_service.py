from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import Decimal
from uuid import UUID, uuid4

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
from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
    PredictionLabelStatus,
    assign_actual_label,
)
from model_training.register_model import (
    build_registration_spec,
)


def _create_experiment(
    session: Session,
    *,
    status: ExperimentStatus = (
        ExperimentStatus.COMPLETED
    ),
    sample_interval_sec: int = 2,
) -> ExperimentRun:
    """Create one isolated label-service experiment."""

    now = datetime.now(UTC)

    experiment = ExperimentRun(
        id=uuid4(),
        name=f"Prediction label test {uuid4()}",
        show_id=2,
        status=status,
        user_count=10,
        spawn_rate_per_sec=Decimal("5.00"),
        duration_sec=120,
        traffic_pattern="STEADY",
        demand_profile="MEDIUM",
        hotspot_ratio=Decimal("0.5000"),
        hotspot_seat_ids=[101],
        sample_interval_sec=sample_interval_sec,
        simulated_work_ms=100,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=4001,
        code_version="pytest",
        started_at=now - timedelta(minutes=5),
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
    """Create one registered model version."""

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
    blocked_sessions: int | None = 0,
    waiting_locks: int | None = 0,
    deadlock_delta: int | None = 0,
    timeout_rate: Decimal | None = Decimal(
        "0.000000"
    ),
    p95_latency_ms: Decimal | None = Decimal(
        "200.000"
    ),
    is_complete: bool = True,
) -> MetricSample:
    """Create one metric sample used by label tests."""

    sample = MetricSample(
        id=uuid4(),
        experiment_run_id=experiment.id,
        sampled_at=sampled_at,
        active_transactions=5,
        blocked_sessions=blocked_sessions,
        waiting_locks=waiting_locks,
        total_locks=20,
        total_connections=15,
        connection_utilization=Decimal(
            "0.500000"
        ),
        estimated_wait_age_ms=Decimal(
            "100.000"
        ),
        deadlock_delta=deadlock_delta,
        configured_users=10,
        active_users=9,
        requests_per_sec=Decimal("7.000"),
        mean_latency_ms=Decimal("150.000"),
        p95_latency_ms=p95_latency_ms,
        failure_rate=Decimal("0.000000"),
        hotspot_ratio=Decimal("0.5000"),
        booking_success_rate=Decimal(
            "1.000000"
        ),
        conflict_rate=Decimal("0.000000"),
        timeout_rate=timeout_rate,
        is_complete=is_complete,
        quality_notes=(
            []
            if is_complete
            else ["INCOMPLETE_TEST_SAMPLE"]
        ),
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
    model_version: ModelVersion,
    window_start: datetime,
    actual_label: bool | None = None,
) -> Prediction:
    """Create one prediction and its source sample."""

    source_sample = _create_metric_sample(
        session,
        experiment=experiment,
        sampled_at=window_start,
    )

    target_window_end = (
        window_start
        + timedelta(seconds=30)
    )

    assigned_at: datetime | None = None
    contract_version: str | None = None
    evidence: dict[str, object] | None = None

    if actual_label is not None:
        assigned_at = (
            target_window_end
            + timedelta(seconds=1)
        )
        contract_version = (
            LIVE_LABEL_CONTRACT_VERSION
        )
        evidence = {
            "original_evidence": True,
        }

    prediction = Prediction(
        id=uuid4(),
        experiment_run_id=experiment.id,
        metric_sample_id=source_sample.id,
        model_version_id=model_version.id,
        predicted_at=window_start,
        target_window_start=window_start,
        target_window_end=target_window_end,
        probability=Decimal("0.900000"),
        risk_level=RiskLevel.HIGH,
        threshold_used=Decimal("0.340000"),
        is_stale=False,
        quality_flags=[],
        actual_label=actual_label,
        actual_label_assigned_at=assigned_at,
        actual_label_contract_version=(
            contract_version
        ),
        actual_label_evidence=evidence,
    )

    session.add(prediction)
    session.commit()
    session.refresh(prediction)

    return prediction


def _create_complete_future_window(
    session: Session,
    *,
    experiment: ExperimentRun,
    window_start: datetime,
) -> list[MetricSample]:
    """Create 15 valid two-second future samples."""

    return [
        _create_metric_sample(
            session,
            experiment=experiment,
            sampled_at=(
                window_start
                + timedelta(seconds=index * 2)
            ),
        )
        for index in range(1, 16)
    ]


def _historical_window_start() -> datetime:
    """Return a safely mature prediction timestamp."""

    return (
        datetime.now(UTC)
        - timedelta(minutes=5)
    )


def _assert_prediction_is_unlabeled(
    session: Session,
    prediction_id: UUID,
) -> None:
    """Require all label fields to remain null."""

    prediction = session.get(
        Prediction,
        prediction_id,
    )

    assert prediction is not None
    assert prediction.actual_label is None
    assert (
        prediction.actual_label_assigned_at
        is None
    )
    assert (
        prediction.actual_label_contract_version
        is None
    )
    assert prediction.actual_label_evidence is None


@pytest.mark.parametrize(
    ("condition", "expected_value"),
    [
        ("blocked_sessions", 2),
        ("waiting_locks", 2),
        ("deadlock_delta", 1),
        ("timeout_rate", Decimal("0.010000")),
    ],
)
def test_each_contention_condition_labels_positive(
    db_session: Session,
    condition: str,
    expected_value: object,
) -> None:
    """Require each target condition to produce true."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=window_start,
        )
    )

    selected_sample = future_samples[6]

    if condition == "blocked_sessions":
        selected_sample.blocked_sessions = 2
    elif condition == "waiting_locks":
        selected_sample.waiting_locks = 2
    elif condition == "deadlock_delta":
        selected_sample.deadlock_delta = 1
    elif condition == "timeout_rate":
        selected_sample.timeout_rate = Decimal(
            "0.010000"
        )
    else:
        raise AssertionError(
            f"Unknown condition: {condition}"
        )

    db_session.commit()

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.LABELED_POSITIVE
    )
    assert result.actual_label is True
    assert result.future_sample_count == 15
    assert (
        result.expected_future_sample_count
        == 15
    )

    db_session.refresh(prediction)

    assert prediction.actual_label is True
    assigned_at = (
        prediction.actual_label_assigned_at
    )

    assert assigned_at is not None
    assert assigned_at >= (
        prediction.target_window_end
)
    assert (
        prediction.actual_label_contract_version
        == LIVE_LABEL_CONTRACT_VERSION
    )
    assert prediction.actual_label_evidence is not None
    assert (
        prediction.actual_label_evidence[
            "future_sample_count"
        ]
        == 15
    )
    assert expected_value is not None


def test_all_negative_evidence_labels_negative(
    db_session: Session,
) -> None:
    """Require valid all-negative evidence to produce false."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=window_start,
        )
    )

    future_samples[5].p95_latency_ms = Decimal(
        "9000.000"
    )
    db_session.commit()

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.LABELED_NEGATIVE
    )
    assert result.actual_label is False

    db_session.refresh(prediction)

    assert prediction.actual_label is False
    assert prediction.actual_label_evidence is not None
    assert (
        prediction.actual_label_evidence[
            "future_max_p95_latency_ms_30s"
        ]
        == "9000.000"
    )


def test_exact_window_boundaries_are_enforced(
    db_session: Session,
) -> None:
    """Exclude t and include the sample at t plus 30."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    source_sample = db_session.get(
        MetricSample,
        prediction.metric_sample_id,
    )

    assert source_sample is not None

    source_sample.blocked_sessions = 10
    db_session.commit()

    future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=window_start,
        )
    )

    future_samples[-1].waiting_locks = 2
    db_session.commit()

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.LABELED_POSITIVE
    )

    db_session.refresh(prediction)

    evidence = prediction.actual_label_evidence

    assert evidence is not None
    assert (
        evidence[
            "future_max_blocked_sessions_30s"
        ]
        == 0
    )
    assert (
        evidence[
            "future_max_waiting_locks_30s"
        ]
        == 2
    )
    first_future_sampled_at = evidence[
        "first_future_sampled_at"
    ]
    last_future_sampled_at = evidence[
        "last_future_sampled_at"
    ]

    assert isinstance(
        first_future_sampled_at,
        str,
    )
    assert isinstance(
        last_future_sampled_at,
        str,
    )

    assert datetime.fromisoformat(
        first_future_sampled_at
    ) == (
        window_start
        + timedelta(seconds=2)
    )

    assert datetime.fromisoformat(
        last_future_sampled_at
    ) == prediction.target_window_end


def test_cross_experiment_samples_are_excluded(
    db_session: Session,
) -> None:
    """Prevent another experiment from affecting a label."""

    experiment = _create_experiment(
        db_session
    )
    other_experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    _create_metric_sample(
        db_session,
        experiment=other_experiment,
        sampled_at=(
            window_start
            + timedelta(seconds=10)
        ),
        blocked_sessions=20,
        waiting_locks=20,
        deadlock_delta=5,
        timeout_rate=Decimal("1.000000"),
    )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.LABELED_NEGATIVE
    )
    assert result.actual_label is False


def test_immature_prediction_remains_unlabeled(
    db_session: Session,
) -> None:
    """Refuse to label before the target window ends."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            - timedelta(microseconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.WINDOW_NOT_MATURE
    )

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_noncompleted_experiment_remains_unlabeled(
    db_session: Session,
) -> None:
    """Refuse labels for a non-completed experiment."""

    experiment = _create_experiment(
        db_session,
        status=ExperimentStatus.DRAFT,
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus
        .EXPERIMENT_NOT_COMPLETED
    )

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_missing_future_sample_is_insufficient(
    db_session: Session,
) -> None:
    """Refuse a future window with only 14 samples."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    for index in range(1, 15):
        _create_metric_sample(
            db_session,
            experiment=experiment,
            sampled_at=(
                window_start
                + timedelta(seconds=index * 2)
            ),
        )

    # Prove that collection continued beyond the horizon.
    _create_metric_sample(
        db_session,
        experiment=experiment,
        sampled_at=(
            window_start
            + timedelta(seconds=32)
        ),
    )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=2)
        ),
    )

    assert result.status == (
        PredictionLabelStatus
        .INSUFFICIENT_FUTURE_COVERAGE
    )
    assert result.future_sample_count == 14
    assert (
        result.expected_future_sample_count
        == 15
    )

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_excessive_sample_gap_is_insufficient(
    db_session: Session,
) -> None:
    """Reject a gap greater than the frozen 3.5 seconds."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    offsets = [
        1,
        3,
        5,
        7,
        11,
        13,
        15,
        17,
        19,
        21,
        23,
        25,
        27,
        29,
        30,
    ]

    for offset in offsets:
        _create_metric_sample(
            db_session,
            experiment=experiment,
            sampled_at=(
                window_start
                + timedelta(seconds=offset)
            ),
        )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus
        .INSUFFICIENT_FUTURE_COVERAGE
    )
    assert result.future_sample_count == 15

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_incomplete_future_sample_is_invalid(
    db_session: Session,
) -> None:
    """Reject an incomplete future evidence sample."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    for index in range(1, 16):
        _create_metric_sample(
            db_session,
            experiment=experiment,
            sampled_at=(
                window_start
                + timedelta(seconds=index * 2)
            ),
            is_complete=(index != 7),
        )

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus
        .INVALID_FUTURE_EVIDENCE
    )

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_invalid_required_metric_is_rejected(
    db_session: Session,
) -> None:
    """Reject a complete sample with missing evidence."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=window_start,
        )
    )

    future_samples[4].blocked_sessions = None
    db_session.commit()

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=1)
        ),
    )

    assert result.status == (
        PredictionLabelStatus
        .INVALID_FUTURE_EVIDENCE
    )

    _assert_prediction_is_unlabeled(
        db_session,
        prediction.id,
    )


def test_existing_label_is_idempotent(
    db_session: Session,
) -> None:
    """Never overwrite a previously assigned label."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
        actual_label=False,
    )

    original_assigned_at = (
        prediction.actual_label_assigned_at
    )
    original_evidence = dict(
        prediction.actual_label_evidence
        or {}
    )

    future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=window_start,
        )
    )

    future_samples[0].blocked_sessions = 10
    db_session.commit()

    result = assign_actual_label(
        db_session,
        prediction.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=10)
        ),
    )

    assert result.status == (
        PredictionLabelStatus.ALREADY_LABELED
    )
    assert result.actual_label is False

    db_session.refresh(prediction)

    assert prediction.actual_label is False
    assert (
        prediction.actual_label_assigned_at
        == original_assigned_at
    )
    assert (
        prediction.actual_label_evidence
        == original_evidence
    )


def test_unknown_prediction_returns_not_found(
    db_session: Session,
) -> None:
    """Return a safe result for an unknown prediction."""

    prediction_id = uuid4()

    result = assign_actual_label(
        db_session,
        prediction_id,
        current_time=datetime.now(UTC),
    )

    assert result.prediction_id == prediction_id
    assert result.status == (
        PredictionLabelStatus.PREDICTION_NOT_FOUND
    )
    assert result.actual_label is None


def test_naive_current_time_is_rejected(
    db_session: Session,
) -> None:
    """Require a timezone-aware assignment timestamp."""

    with pytest.raises(
        ValueError,
        match="timezone-aware",
    ):
        assign_actual_label(
            db_session,
            uuid4(),
            current_time=datetime.now(),
        )