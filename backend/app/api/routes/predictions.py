from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from backend.app.api.dependencies import (
    require_admin_token,
    require_internal_token,
)
from backend.app.db.session import get_db
from backend.app.schemas.prediction import (
    PredictionRead,
)
from backend.app.services.prediction_service import (
    PredictionServiceError,
    create_prediction_for_metric_sample,
    read_prediction,
    read_prediction_for_metric_sample,
)


router = APIRouter(
    prefix="/api/v1/predictions",
    tags=["Predictions"],
)

DatabaseSession = Annotated[
    Session,
    Depends(get_db),
]


def _http_exception(
    error: PredictionServiceError,
) -> HTTPException:
    """Convert a service error into an API response."""

    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.code,
            "message": error.message,
        },
    )


@router.post(
    "/metric-samples/{metric_sample_id}",
    response_model=PredictionRead,
    status_code=status.HTTP_200_OK,
    dependencies=[
        Depends(require_internal_token),
    ],
    summary="Create or return a prediction",
)
def create_metric_sample_prediction(
    metric_sample_id: UUID,
    session: DatabaseSession,
) -> PredictionRead:
    """Create or return the prediction for a metric sample."""

    try:
        prediction = (
            create_prediction_for_metric_sample(
                session,
                metric_sample_id,
            )
        )

        return PredictionRead.model_validate(
            prediction
        )

    except PredictionServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "/metric-samples/{metric_sample_id}",
    response_model=PredictionRead,
    dependencies=[
        Depends(require_admin_token),
    ],
    summary="Read a metric-sample prediction",
)
def read_metric_sample_prediction(
    metric_sample_id: UUID,
    session: DatabaseSession,
) -> PredictionRead:
    """Return the prediction stored for a metric sample."""

    try:
        prediction = (
            read_prediction_for_metric_sample(
                session,
                metric_sample_id,
            )
        )

        return PredictionRead.model_validate(
            prediction
        )

    except PredictionServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "/{prediction_id}",
    response_model=PredictionRead,
    dependencies=[
        Depends(require_admin_token),
    ],
    summary="Read a prediction",
)
def read_prediction_by_id(
    prediction_id: UUID,
    session: DatabaseSession,
) -> PredictionRead:
    """Return one persisted prediction."""

    try:
        prediction = read_prediction(
            session,
            prediction_id,
        )

        return PredictionRead.model_validate(
            prediction
        )

    except PredictionServiceError as error:
        raise _http_exception(error) from error