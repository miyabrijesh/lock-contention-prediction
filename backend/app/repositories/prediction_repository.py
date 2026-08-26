from uuid import UUID

from sqlalchemy import select
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


def add_prediction(
    session: Session,
    prediction: Prediction,
) -> Prediction:
    """Add one prediction to the current transaction."""

    session.add(prediction)

    return prediction