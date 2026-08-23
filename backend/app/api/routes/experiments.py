from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from backend.app.api.dependencies import require_admin_token
from backend.app.db.session import get_db
from backend.app.models.enums import ExperimentStatus
from backend.app.schemas.experiment import (
    ExperimentCreate,
    ExperimentRead,
    ExperimentTransition,
)
from backend.app.services.experiment_service import (
    ExperimentServiceError,
    cancel_experiment as cancel_experiment_service,
    complete_experiment as complete_experiment_service,
    create_experiment as create_experiment_service,
    fail_experiment as fail_experiment_service,
    list_experiments as list_experiments_service,
    read_experiment as read_experiment_service,
    start_experiment as start_experiment_service,
)


router = APIRouter(
    prefix="/api/v1/experiments",
    tags=["Experiments"],
    dependencies=[
        Depends(require_admin_token),
    ],
)

DatabaseSession = Annotated[
    Session,
    Depends(get_db),
]


def _http_exception(
    error: ExperimentServiceError,
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
    "",
    response_model=ExperimentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an experiment",
)
def create_experiment(
    request_body: ExperimentCreate,
    session: DatabaseSession,
) -> ExperimentRead:
    """Create a reproducible experiment configuration."""

    try:
        experiment = create_experiment_service(
            session,
            request_body,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "",
    response_model=list[ExperimentRead],
    summary="List experiments",
)
def list_experiments(
    session: DatabaseSession,
    experiment_status: Annotated[
        ExperimentStatus | None,
        Query(alias="status"),
    ] = None,
) -> list[ExperimentRead]:
    """List experiments with an optional status filter."""

    try:
        experiments = list_experiments_service(
            session,
            experiment_status=experiment_status,
        )

        return [
            ExperimentRead.model_validate(experiment)
            for experiment in experiments
        ]

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "/{experiment_run_id}",
    response_model=ExperimentRead,
    summary="Get one experiment",
)
def get_experiment(
    experiment_run_id: UUID,
    session: DatabaseSession,
) -> ExperimentRead:
    """Return one experiment configuration and status."""

    try:
        experiment = read_experiment_service(
            session,
            experiment_run_id,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.post(
    "/{experiment_run_id}/start",
    response_model=ExperimentRead,
    summary="Start an experiment",
)
def start_experiment(
    experiment_run_id: UUID,
    request_body: ExperimentTransition,
    session: DatabaseSession,
) -> ExperimentRead:
    """Transition a draft experiment to running."""

    try:
        experiment = start_experiment_service(
            session,
            experiment_run_id,
            request_body,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.post(
    "/{experiment_run_id}/complete",
    response_model=ExperimentRead,
    summary="Complete an experiment",
)
def complete_experiment(
    experiment_run_id: UUID,
    request_body: ExperimentTransition,
    session: DatabaseSession,
) -> ExperimentRead:
    """Transition a running experiment to completed."""

    try:
        experiment = complete_experiment_service(
            session,
            experiment_run_id,
            request_body,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.post(
    "/{experiment_run_id}/fail",
    response_model=ExperimentRead,
    summary="Fail an experiment",
)
def fail_experiment(
    experiment_run_id: UUID,
    request_body: ExperimentTransition,
    session: DatabaseSession,
) -> ExperimentRead:
    """Transition a running experiment to failed."""

    try:
        experiment = fail_experiment_service(
            session,
            experiment_run_id,
            request_body,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error


@router.post(
    "/{experiment_run_id}/cancel",
    response_model=ExperimentRead,
    summary="Cancel an experiment",
)
def cancel_experiment(
    experiment_run_id: UUID,
    request_body: ExperimentTransition,
    session: DatabaseSession,
) -> ExperimentRead:
    """Cancel a draft or running experiment."""

    try:
        experiment = cancel_experiment_service(
            session,
            experiment_run_id,
            request_body,
        )

        return ExperimentRead.model_validate(
            experiment
        )

    except ExperimentServiceError as error:
        raise _http_exception(error) from error