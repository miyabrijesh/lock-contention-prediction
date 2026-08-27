from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def add_prediction(
    session: Session,
    prediction: Prediction,
) -> Prediction:
    """Add one prediction to the current transaction."""

    session.add(prediction)

    return prediction