from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.enums import (
    ExperimentStatus,
)
from backend.app.models.experiment import (
    ExperimentRun,
)
from backend.app.models.metrics import MetricSample
from backend.app.models.prediction import Prediction


def get_metric_sample_for_update(
    session: Session,
    metric_sample_id: UUID,
) -> MetricSample | None:
    """Load and lock one metric sample."""

    statement = (
        select(MetricSample)
        .where(
            MetricSample.id == metric_sample_id
        )
        .with_for_update()
    )

    return session.scalar(statement)


def get_prediction_by_metric_sample(
    session: Session,
    metric_sample_id: UUID,
) -> Prediction | None:
    """Return the prediction already stored for a sample."""

    statement = (
        select(Prediction)
        .where(
            Prediction.metric_sample_id
            == metric_sample_id
        )
    )

    return session.scalar(statement)


def get_prediction(
    session: Session,
    prediction_id: UUID,
) -> Prediction | None:
    """Return one persisted prediction."""

    return session.get(
        Prediction,
        prediction_id,
    )


def get_prediction_for_labeling(
    session: Session,
    prediction_id: UUID,
) -> Prediction | None:
    """Load and lock one prediction for label assignment."""

    statement = (
        select(Prediction)
        .where(
            Prediction.id == prediction_id
        )
        .with_for_update()
    )

    return session.scalar(statement)


def get_future_metric_samples(
    session: Session,
    *,
    experiment_run_id: UUID,
    target_window_start: datetime,
    target_window_end: datetime,
) -> list[MetricSample]:
    """Load metric samples inside one exact future window."""

    statement = (
        select(MetricSample)
        .where(
            MetricSample.experiment_run_id
            == experiment_run_id,
            MetricSample.sampled_at
            > target_window_start,
            MetricSample.sampled_at
            <= target_window_end,
        )
        .order_by(
            MetricSample.sampled_at.asc(),
            MetricSample.id.asc(),
        )
    )

    return list(
        session.scalars(statement).all()
    )


def get_latest_complete_sampled_at(
    session: Session,
    experiment_run_id: UUID,
) -> datetime | None:
    """Return the latest complete sample timestamp."""

    statement = select(
        func.max(MetricSample.sampled_at)
    ).where(
        MetricSample.experiment_run_id
        == experiment_run_id,
        MetricSample.is_complete.is_(True),
    )

    return session.scalar(statement)


def get_eligible_unlabeled_prediction_ids(
    session: Session,
    *,
    current_time: datetime,
    experiment_run_id: UUID | None = None,
    limit: int = 100,
) -> list[UUID]:
    """Return deterministic live-label candidates."""

    latest_complete_samples = (
        select(
            MetricSample.experiment_run_id.label(
                "experiment_run_id"
            ),
            func.max(
                MetricSample.sampled_at
            ).label(
                "latest_complete_sampled_at"
            ),
        )
        .where(
            MetricSample.is_complete.is_(True)
        )
        .group_by(
            MetricSample.experiment_run_id
        )
        .subquery()
    )

    statement = (
        select(Prediction.id)
        .join(
            ExperimentRun,
            ExperimentRun.id
            == Prediction.experiment_run_id,
        )
        .join(
            latest_complete_samples,
            latest_complete_samples.c
            .experiment_run_id
            == Prediction.experiment_run_id,
        )
        .where(
            ExperimentRun.status
            == ExperimentStatus.COMPLETED,
            Prediction.actual_label.is_(None),
            Prediction.target_window_end
            <= current_time,
            latest_complete_samples.c
            .latest_complete_sampled_at
            >= Prediction.target_window_end,
        )
               .order_by(
            Prediction.target_window_end.asc(),
            Prediction.id.asc(),
        )
        .limit(limit)
    )

    if experiment_run_id is not None:
        statement = statement.where(
            Prediction.experiment_run_id
            == experiment_run_id
        )

    return list(
        session.scalars(statement).all()
    )


def add_prediction(
    session: Session,
    prediction: Prediction,
) -> Prediction:
    """Add one prediction to the current transaction."""

    session.add(prediction)

    return prediction