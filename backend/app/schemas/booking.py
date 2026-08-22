from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.enums import BookingStatus


class BookingCreate(BaseModel):
    """Validated request body for creating a booking."""

    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    show_id: int = Field(gt=0)
    seat_id: int = Field(gt=0)
    experiment_run_id: UUID | None = None


class BookingRead(BaseModel):
    """Booking information returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    idempotency_key: UUID
    user_id: int
    show_id: int
    seat_id: int
    experiment_run_id: UUID | None
    status: BookingStatus
    booked_at: datetime
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BookingErrorDetail(BaseModel):
    """Machine-readable description of a booking error."""

    code: str
    message: str


class BookingErrorResponse(BaseModel):
    """Standard error response for the booking API."""

    detail: BookingErrorDetail