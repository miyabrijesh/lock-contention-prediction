from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.app.models.enums import SeatState


class SeatRead(BaseModel):
    """Public representation of a seat and its current state."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    show_id: int
    seat_number: str
    state: SeatState
    hold_expires_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime