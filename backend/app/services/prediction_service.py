from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import (
    ROUND_HALF_UP,
    Decimal,
)
from uuid import UUID

from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

from backend.app.models.prediction import (
    Prediction,
)
from backend.app.repositories.model_repository import (
    get_active_model_version,
)
from backend.app.repositories.prediction_repository import (
    add_prediction,
    get_metric_sample_for_update,
    get_prediction,
    get_prediction_by_metric_sample,
)
from backend.app.services.model_service import (
    ModelServiceError,
    load_validated_predictor,
)
from model_training.inference import InferenceError


SIX_DECIMAL_PLACES = Decimal("0.000001")

STALE_TARGET_WINDOW_FLAG = (
    "STALE_TARGET_WINDOW"
)


class PredictionServiceError(Exception):
    """Expected prediction persistence failure."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message
        self.status_code = status_code


def _database_error() -> PredictionServiceError:
    """Create the standard unexpected database error."""

    return PredictionServiceError(
        code="INTERNAL_ERROR",
        message="An unexpected database error occurred.",
        status_code=500,
    )


def _decimal_probability(
    value: float,
) -> Decimal:
    """Convert a probability to six-decimal storage."""

    decimal_value = Decimal(
        str(value)
    ).quantize(
        SIX_DECIMAL_PLACES,
        rounding=ROUND_HALF_UP,
    )

    if decimal_value < 0 or decimal_value > 1:
        raise PredictionServiceError(
            code="PREDICTION_INPUT_INVALID",
            message=(
                "The model returned a probability "
                "outside the valid range."
            ),
            status_code=409,
        )

    return decimal_value


def _normalize_quality_flags(
    quality_notes: list[str],
    *,
    is_stale: bool,
) -> list[str]:
    """Return unique deterministic prediction quality flags."""

    normalized_flags: list[str] = []

    for quality_note in quality_notes:
        normalized_note = quality_note.strip()

        if (
            normalized_note
            and normalized_note
            not in normalized_flags
        ):
            normalized_flags.append(
                normalized_note
            )

    if (
        is_stale
        and STALE_TARGET_WINDOW_FLAG
        not in normalized_flags
    ):
        normalized_flags.append(
            STALE_TARGET_WINDOW_FLAG
        )

    return normalized_flags


def read_prediction(
    session: Session,
    prediction_id: UUID,
) -> Prediction:
    """Return one stored prediction."""

    try:
        prediction = get_prediction(
            session,
            prediction_id,
        )

        if prediction is None:
            raise PredictionServiceError(
                code="PREDICTION_NOT_FOUND",
                message=(
                    "The requested prediction "
                    "was not found."
                ),
                status_code=404,
            )

        return prediction

    except PredictionServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_prediction_for_metric_sample(
    session: Session,
    metric_sample_id: UUID,
) -> Prediction:
    """Return the prediction stored for one metric sample."""

    try:
        prediction = (
            get_prediction_by_metric_sample(
                session,
                metric_sample_id,
            )
        )

        if prediction is None:
            raise PredictionServiceError(
                code="PREDICTION_NOT_FOUND",
                message=(
                    "No prediction was found for "
                    "the requested metric sample."
                ),
                status_code=404,
            )

        return prediction

    except PredictionServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def create_prediction_for_metric_sample(
    session: Session,
    metric_sample_id: UUID,
) -> Prediction:
    """Create or return one prediction for a metric sample."""

    try:
        metric_sample = (
            get_metric_sample_for_update(
                session,
                metric_sample_id,
            )
        )

        if metric_sample is None:
            raise PredictionServiceError(
                code="METRIC_SAMPLE_NOT_FOUND",
                message=(
                    "The requested metric sample "
                    "was not found."
                ),
                status_code=404,
            )

        existing_prediction = (
            get_prediction_by_metric_sample(
                session,
                metric_sample_id,
            )
        )

        if existing_prediction is not None:
            session.commit()

            return existing_prediction

        if not metric_sample.is_complete:
            raise PredictionServiceError(
                code="METRIC_SAMPLE_INCOMPLETE",
                message=(
                    "The requested metric sample is "
                    "incomplete and cannot be used "
                    "for prediction."
                ),
                status_code=409,
            )

        active_model = get_active_model_version(
            session
        )

        if active_model is None:
            raise PredictionServiceError(
                code="ACTIVE_MODEL_NOT_FOUND",
                message=(
                    "No active model version was found."
                ),
                status_code=404,
            )

        predictor = load_validated_predictor(
            active_model
        )

        try:
            inference_result = (
                predictor.predict_metric_sample(
                    metric_sample
                )
            )
        except InferenceError as error:
            raise PredictionServiceError(
                code="PREDICTION_INPUT_INVALID",
                message=(
                    "The metric sample failed "
                    "inference input validation."
                ),
                status_code=409,
            ) from error

        predicted_at = datetime.now(UTC)

        target_window_start = (
            metric_sample.sampled_at
        )

        target_window_end = (
            target_window_start
            + timedelta(
                seconds=(
                    active_model.target_horizon_sec
                )
            )
        )

        is_stale = (
            predicted_at >= target_window_end
        )

        quality_flags = (
            _normalize_quality_flags(
                metric_sample.quality_notes,
                is_stale=is_stale,
            )
        )

        probability = _decimal_probability(
            inference_result.probability
        )

        threshold_used = _decimal_probability(
            inference_result.threshold_used
        )

        prediction = Prediction(
            experiment_run_id=(
                metric_sample.experiment_run_id
            ),
            metric_sample_id=metric_sample.id,
            model_version_id=active_model.id,
            predicted_at=predicted_at,
            target_window_start=(
                target_window_start
            ),
            target_window_end=target_window_end,
            probability=probability,
            risk_level=(
                inference_result.risk_level
            ),
            threshold_used=threshold_used,
            is_stale=is_stale,
            quality_flags=quality_flags,
            actual_label=None,
        )

        add_prediction(
            session,
            prediction,
        )

        session.flush()
        session.commit()
        session.refresh(prediction)

        return prediction

    except PredictionServiceError:
        session.rollback()
        raise

    except ModelServiceError as error:
        session.rollback()

        raise PredictionServiceError(
            code=error.code,
            message=error.message,
            status_code=error.status_code,
        ) from error

    except IntegrityError as error:
        session.rollback()

        existing_prediction = (
            get_prediction_by_metric_sample(
                session,
                metric_sample_id,
            )
        )

        if existing_prediction is not None:
            session.commit()

            return existing_prediction

        raise PredictionServiceError(
            code="PREDICTION_CONFLICT",
            message=(
                "The prediction conflicted with "
                "another persistence request."
            ),
            status_code=409,
        ) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error