from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ShowRead(BaseModel):
    """Public representation of a reservation show."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    venue: str
    show_time: datetime
    is_experiment_show: bool
    created_at: datetime
    updated_at: datetime