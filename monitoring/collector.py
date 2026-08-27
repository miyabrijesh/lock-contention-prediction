from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models import (
    ExperimentRun,
    MetricSample,
    Prediction,
)
from backend.app.models.enums import ExperimentStatus
from monitoring.application_metrics import (
    collect_application_metrics,
)
from monitoring.postgres_metrics import (
    PostgreSQLMetrics,
    collect_postgresql_metrics,
)

from backend.app.services.prediction_service import (
    PredictionServiceError,
    create_prediction_for_metric_sample,
)

COLLECTOR_VERSION = "checkpoint-38-v1"


@dataclass(frozen=True)
class MetricCollectionResult:
    """Result of collecting and persisting one metric sample."""

    sample: MetricSample
    prediction: Prediction
    cumulative_deadlocks: int


class MetricCollectorError(Exception):
    """Expected metric-collection failure."""


def _calculate_deadlock_delta(
    current_cumulative_deadlocks: int,
    previous_cumulative_deadlocks: int | None,
) -> tuple[int, str | None]:
    """Convert PostgreSQL's cumulative count into a sample delta."""

    if previous_cumulative_deadlocks is None:
        return (
            0,
            "DEADLOCK_BASELINE_INITIALIZED",
        )

    if (
        current_cumulative_deadlocks
        < previous_cumulative_deadlocks
    ):
        return (
            0,
            "POSTGRESQL_DEADLOCK_COUNTER_RESET",
        )

    return (
        current_cumulative_deadlocks
        - previous_cumulative_deadlocks,
        None,
    )


def _validate_running_experiment(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun:
    """Load one experiment that is eligible for collection."""

    experiment = session.get(
        ExperimentRun,
        experiment_run_id,
    )

    if experiment is None:
        raise MetricCollectorError(
            f"Experiment {experiment_run_id} was not found."
        )

    if experiment.status != ExperimentStatus.RUNNING:
        raise MetricCollectorError(
            "Metrics may only be collected for a "
            "RUNNING experiment."
        )

    return experiment


def _build_metric_sample(
    *,
    experiment: ExperimentRun,
    database_metrics: PostgreSQLMetrics,
    application_metrics: object,
    deadlock_delta: int,
    quality_notes: list[str],
) -> MetricSample:
    """Build one ORM metric sample."""

    from monitoring.application_metrics import (
        ApplicationMetrics,
    )

    if not isinstance(
        application_metrics,
        ApplicationMetrics,
    ):
        raise MetricCollectorError(
            "Invalid application metrics object."
        )

    return MetricSample(
        experiment_run_id=experiment.id,
        sampled_at=database_metrics.sampled_at,
        active_transactions=(
            database_metrics.active_transactions
        ),
        blocked_sessions=(
            database_metrics.blocked_sessions
        ),
        waiting_locks=(
            database_metrics.waiting_locks
        ),
        total_locks=database_metrics.total_locks,
        total_connections=(
            database_metrics.total_connections
        ),
        connection_utilization=(
            database_metrics.connection_utilization
        ),
        estimated_wait_age_ms=(
            database_metrics.estimated_wait_age_ms
        ),
        deadlock_delta=deadlock_delta,
        configured_users=experiment.user_count,
        active_users=application_metrics.active_users,
        requests_per_sec=(
            application_metrics.requests_per_sec
        ),
        mean_latency_ms=(
            application_metrics.mean_latency_ms
        ),
        p95_latency_ms=(
            application_metrics.p95_latency_ms
        ),
        failure_rate=application_metrics.failure_rate,
        hotspot_ratio=application_metrics.hotspot_ratio,
        booking_success_rate=(
            application_metrics.booking_success_rate
        ),
        conflict_rate=application_metrics.conflict_rate,
        timeout_rate=application_metrics.timeout_rate,
        is_complete=True,
        quality_notes=quality_notes,
        collector_version=COLLECTOR_VERSION,
    )


def collect_and_persist_metric_sample(
    session: Session,
    *,
    experiment_run_id: UUID,
    window_started_at: datetime,
    previous_cumulative_deadlocks: int | None,
) -> MetricCollectionResult:
    """Collect and commit one complete metric-sample row."""

    try:
        experiment = _validate_running_experiment(
            session,
            experiment_run_id,
        )

        database_metrics = (
            collect_postgresql_metrics(session)
        )

        application_metrics = (
            collect_application_metrics(
                session,
                experiment_run_id=experiment.id,
                hotspot_seat_ids=tuple(
                    experiment.hotspot_seat_ids
                ),
                window_started_at=window_started_at,
                window_ended_at=(
                    database_metrics.sampled_at
                ),
            )
        )

        deadlock_delta, deadlock_note = (
            _calculate_deadlock_delta(
                database_metrics.cumulative_deadlocks,
                previous_cumulative_deadlocks,
            )
        )

        quality_notes = list(
            application_metrics.quality_notes
        )

        if deadlock_note is not None:
            quality_notes.append(deadlock_note)

        sample = _build_metric_sample(
            experiment=experiment,
            database_metrics=database_metrics,
            application_metrics=application_metrics,
            deadlock_delta=deadlock_delta,
            quality_notes=quality_notes,
        )

        session.add(sample)
        session.commit()
        session.refresh(sample)

        try:
            prediction = (
                create_prediction_for_metric_sample(
                    session,
                    sample.id,
                )
            )
        except PredictionServiceError as error:
            raise MetricCollectorError(
                "The metric sample was persisted, but "
                "automatic prediction failed with "
                f"{error.code}."
            ) from error

        return MetricCollectionResult(
            sample=sample,
            prediction=prediction,
            cumulative_deadlocks=(
                database_metrics.cumulative_deadlocks
            ),
        )

    except MetricCollectorError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()

        raise MetricCollectorError(
            "The metric sample could not be persisted."
        ) from error