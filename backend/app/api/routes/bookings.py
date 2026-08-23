from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Response,
    status,
)
from sqlalchemy.orm import Session

from backend.app.api.dependencies import require_internal_token
from backend.app.db.session import get_db
from backend.app.schemas.booking import (
    BookingCreate,
    BookingErrorResponse,
    BookingRead,
)
from backend.app.services.booking_service import (
    BookingCommand,
    BookingServiceError,
    create_booking,
)


router = APIRouter(
    prefix="/api/v1/bookings",
    tags=["Bookings"],
)

DatabaseSession = Annotated[
    Session,
    Depends(get_db),
]

InternalAccess = Annotated[
    None,
    Depends(require_internal_token),
]

IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description="UUID identifying one logical booking request.",
    ),
]


@router.post(
    "",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a transactional seat booking",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": BookingErrorResponse,
            "description": "Invalid booking request",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "model": BookingErrorResponse,
            "description": "Missing or invalid internal token",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": BookingErrorResponse,
            "description": "Experiment mode is disabled",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": BookingErrorResponse,
            "description": "Requested entity was not found",
        },
        status.HTTP_409_CONFLICT: {
            "model": BookingErrorResponse,
            "description": "Booking or idempotency conflict",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": BookingErrorResponse,
            "description": "Database lock or statement timeout",
        },
    },
)
def create_booking_endpoint(
    request_body: BookingCreate,
    response: Response,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
    _internal_access: InternalAccess,
) -> BookingRead:
    """Validate and execute one transactional booking request."""

    command = BookingCommand(
        idempotency_key=idempotency_key,
        user_id=request_body.user_id,
        show_id=request_body.show_id,
        seat_id=request_body.seat_id,
        experiment_run_id=request_body.experiment_run_id,
    )

    try:
        result = create_booking(
            session,
            command,
        )
    except BookingServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
    else:
        response.status_code = status.HTTP_201_CREATED

    return BookingRead.model_validate(result.booking)