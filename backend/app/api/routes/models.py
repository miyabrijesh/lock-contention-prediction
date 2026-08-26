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
)
from backend.app.db.session import get_db
from backend.app.schemas.model import (
    ModelActivationRequest,
    ModelVersionRead,
)
from backend.app.services.model_service import (
    ModelServiceError,
    activate_model_version as activate_model_service,
    list_registered_models as list_models_service,
    read_active_model as read_active_model_service,
    read_registered_model as read_model_service,
)


router = APIRouter(
    prefix="/api/v1/models",
    tags=["Models"],
    dependencies=[
        Depends(require_admin_token),
    ],
)

DatabaseSession = Annotated[
    Session,
    Depends(get_db),
]


def _http_exception(
    error: ModelServiceError,
) -> HTTPException:
    """Convert a service error into an API response."""

    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.code,
            "message": error.message,
        },
    )


@router.get(
    "",
    response_model=list[ModelVersionRead],
    summary="List registered models",
)
def list_models(
    session: DatabaseSession,
) -> list[ModelVersionRead]:
    """Return every registered model version."""

    try:
        model_versions = list_models_service(
            session
        )

        return [
            ModelVersionRead.model_validate(
                model_version
            )
            for model_version in model_versions
        ]

    except ModelServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "/active",
    response_model=ModelVersionRead,
    summary="Read the active model",
)
def read_active_model(
    session: DatabaseSession,
) -> ModelVersionRead:
    """Return the active model without selecting a fallback."""

    try:
        model_version = (
            read_active_model_service(
                session
            )
        )

        return ModelVersionRead.model_validate(
            model_version
        )

    except ModelServiceError as error:
        raise _http_exception(error) from error


@router.get(
    "/{model_version_id}",
    response_model=ModelVersionRead,
    summary="Read a registered model",
)
def read_model(
    model_version_id: UUID,
    session: DatabaseSession,
) -> ModelVersionRead:
    """Return one registered model version."""

    try:
        model_version = read_model_service(
            session,
            model_version_id,
        )

        return ModelVersionRead.model_validate(
            model_version
        )

    except ModelServiceError as error:
        raise _http_exception(error) from error


@router.post(
    "/{model_version_id}/activate",
    response_model=ModelVersionRead,
    status_code=status.HTTP_200_OK,
    summary="Activate a registered model",
)
def activate_model(
    model_version_id: UUID,
    request_body: ModelActivationRequest,
    session: DatabaseSession,
) -> ModelVersionRead:
    """Verify and activate one registered model."""

    try:
        model_version = activate_model_service(
            session,
            model_version_id,
            request_body,
        )

        return ModelVersionRead.model_validate(
            model_version
        )

    except ModelServiceError as error:
        raise _http_exception(error) from error