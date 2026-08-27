from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.orm import Session

import monitoring.collector as collector_module
from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
)
from backend.app.services.prediction_service import (
    PredictionServiceError,
)
from monitoring.application_metrics import (
    ApplicationMetrics,
)
from monitoring.collector import (
    COLLECTOR_VERSION,
    MetricCollectorError,
    collect_and_persist_metric_sample,
)


def _create_running_experiment(
    session: Session,
) -> ExperimentRun:
    """Create an isolated running experiment."""

    now = datetime.now(UTC)

    experiment = ExperimentRun(
        id=uuid4(),
        name=f"Collector prediction test {uuid4()}",
        show_id=2,
        status=ExperimentStatus.RUNNING,
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
        started_at=now - timedelta(seconds=5),
        created_by="pytest",
    )

    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    return experiment


def _database_metrics(
    *,
    sampled_at: datetime,
) -> SimpleNamespace:
    """Return deterministic PostgreSQL telemetry."""

    return SimpleNamespace(
        sampled_at=sampled_at,
        active_transactions=5,
        blocked_sessions=2,
        waiting_locks=2,
        total_locks=20,
        total_connections=15,
        connection_utilization=Decimal("0.500000"),
        estimated_wait_age_ms=Decimal("125.000"),
        cumulative_deadlocks=3,
    )


def _application_metrics() -> ApplicationMetrics:
    """Return deterministic application telemetry."""

    return ApplicationMetrics(
        request_count=13,
        active_users=7,
        requests_per_sec=Decimal("6.500"),
        mean_latency_ms=Decimal("180.000"),
        p95_latency_ms=Decimal("350.000"),
        failure_rate=Decimal("0.010000"),
        hotspot_ratio=Decimal("0.2000"),
        booking_success_rate=Decimal("0.950000"),
        conflict_rate=Decimal("0.040000"),
        timeout_rate=Decimal("0.010000"),
        quality_notes=(),
    )


def _patch_metric_collection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sampled_at: datetime,
) -> None:
    """Replace live telemetry queries with deterministic values."""

    monkeypatch.setattr(
        collector_module,
        "collect_postgresql_metrics",
        lambda session: _database_metrics(
            sampled_at=sampled_at
        ),
    )

    monkeypatch.setattr(
        collector_module,
        "collect_application_metrics",
        lambda session, **kwargs: _application_metrics(),
    )


def test_collector_returns_sample_and_prediction(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist a sample and return its automatic prediction."""

    experiment = _create_running_experiment(
        db_session
    )
    sampled_at = datetime.now(UTC)

    _patch_metric_collection(
        monkeypatch,
        sampled_at=sampled_at,
    )

    expected_prediction = SimpleNamespace(
        id=uuid4(),
    )
    received_sample_ids: list[object] = []

    def create_prediction(
        session: Session,
        metric_sample_id: object,
    ) -> SimpleNamespace:
        received_sample_ids.append(
            metric_sample_id
        )
        return expected_prediction

    monkeypatch.setattr(
        collector_module,
        "create_prediction_for_metric_sample",
        create_prediction,
    )

    result = collect_and_persist_metric_sample(
        db_session,
        experiment_run_id=experiment.id,
        window_started_at=(
            sampled_at - timedelta(seconds=2)
        ),
        previous_cumulative_deadlocks=2,
    )

    assert result.prediction is expected_prediction
    assert result.sample.id is not None
    assert result.sample.is_complete is True
    assert result.sample.collector_version == (
        COLLECTOR_VERSION
    )
    assert result.cumulative_deadlocks == 3
    assert received_sample_ids == [
        result.sample.id
    ]

    stored_sample = db_session.get(
        MetricSample,
        result.sample.id,
    )

    assert stored_sample is not None
    assert stored_sample.experiment_run_id == (
        experiment.id
    )
    assert stored_sample.deadlock_delta == 1


def test_prediction_failure_preserves_committed_sample(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not erase telemetry after prediction failure."""

    experiment = _create_running_experiment(
        db_session
    )
    sampled_at = datetime.now(UTC)

    _patch_metric_collection(
        monkeypatch,
        sampled_at=sampled_at,
    )

    def fail_prediction(
        session: Session,
        metric_sample_id: object,
    ) -> None:
        raise PredictionServiceError(
            code="ACTIVE_MODEL_NOT_FOUND",
            message=(
                "No active model version was found."
            ),
            status_code=404,
        )

    monkeypatch.setattr(
        collector_module,
        "create_prediction_for_metric_sample",
        fail_prediction,
    )

    with pytest.raises(
        MetricCollectorError,
        match=(
            "automatic prediction failed with "
            "ACTIVE_MODEL_NOT_FOUND"
        ),
    ):
        collect_and_persist_metric_sample(
            db_session,
            experiment_run_id=experiment.id,
            window_started_at=(
                sampled_at - timedelta(seconds=2)
            ),
            previous_cumulative_deadlocks=3,
        )

    stored_sample_count = db_session.scalar(
        select(
            func.count(MetricSample.id)
        ).where(
            MetricSample.experiment_run_id
            == experiment.id
        )
    )

    assert stored_sample_count == 1

    stored_sample = db_session.scalar(
        select(MetricSample).where(
            MetricSample.experiment_run_id
            == experiment.id
        )
    )

    assert stored_sample is not None
    assert stored_sample.is_complete is True
    assert stored_sample.collector_version == (
        COLLECTOR_VERSION
    )


def test_runner_prints_prediction_summary() -> None:
    """Require prediction fields in collector output."""

    runner_source = Path(
        "monitoring/run_collector.py"
    ).read_text(
        encoding="utf-8"
    )

    required_output_fields = (
        "prediction_probability=",
        "risk_level=",
        "threshold=",
        "stale=",
    )

    for output_field in required_output_fields:
        assert output_field in runner_source