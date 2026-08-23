from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from backend.app.models.enums import ExperimentStatus


BookingMode = Literal[
    "NORMAL",
    "EXPERIMENT_RECYCLE",
]


class ExperimentCreate(BaseModel):
    """Validated configuration for a new experiment."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=100,
    )
    show_id: int = Field(gt=0)
    user_count: int = Field(
        ge=1,
        le=2000,
    )
    spawn_rate_per_sec: Decimal = Field(
        gt=Decimal("0"),
        max_digits=8,
        decimal_places=2,
    )
    duration_sec: int = Field(
        ge=30,
        le=3600,
    )
    hotspot_ratio: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=5,
        decimal_places=4,
    )
    hotspot_seat_ids: list[int] = Field(
        min_length=1,
    )
    sample_interval_sec: int = Field(
        default=2,
        ge=1,
        le=10,
    )
    simulated_work_ms: int = Field(
        default=100,
        ge=0,
        le=1000,
    )
    booking_mode: BookingMode
    random_seed: int
    code_version: str | None = Field(
        default=None,
        max_length=64,
    )
    created_by: str = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator(
        "name",
        "created_by",
    )
    @classmethod
    def strip_required_text(
        cls,
        value: str,
    ) -> str:
        """Remove surrounding whitespace and reject blank values."""

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                "Value must not contain only whitespace."
            )

        return normalized_value

    @field_validator("code_version")
    @classmethod
    def strip_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        """Normalize optional version text."""

        if value is None:
            return None

        normalized_value = value.strip()

        return normalized_value or None

    @field_validator("hotspot_seat_ids")
    @classmethod
    def validate_hotspot_seat_ids(
        cls,
        value: list[int],
    ) -> list[int]:
        """Require positive and unique hotspot seat identifiers."""

        if any(seat_id <= 0 for seat_id in value):
            raise ValueError(
                "Every hotspot seat ID must be positive."
            )

        if len(value) != len(set(value)):
            raise ValueError(
                "Hotspot seat IDs must be unique."
            )

        return value


class ExperimentTransition(BaseModel):
    """Actor and optional reason for a state transition."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(
        min_length=1,
        max_length=100,
    )
    reason: str | None = Field(
        default=None,
        max_length=1000,
    )

    @field_validator("actor")
    @classmethod
    def strip_actor(
        cls,
        value: str,
    ) -> str:
        """Normalize and validate the transition actor."""

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError(
                "Actor must not contain only whitespace."
            )

        return normalized_value

    @field_validator("reason")
    @classmethod
    def strip_reason(
        cls,
        value: str | None,
    ) -> str | None:
        """Normalize optional transition reasons."""

        if value is None:
            return None

        normalized_value = value.strip()

        return normalized_value or None


class ExperimentRead(BaseModel):
    """Experiment configuration and lifecycle state."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    show_id: int
    status: ExperimentStatus
    user_count: int
    spawn_rate_per_sec: Decimal
    duration_sec: int
    hotspot_ratio: Decimal
    hotspot_seat_ids: list[int]
    sample_interval_sec: int
    simulated_work_ms: int
    booking_mode: str
    random_seed: int
    code_version: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_by: str
    created_at: datetime


class ExperimentErrorDetail(BaseModel):
    """Machine-readable experiment error."""

    code: str
    message: str


class ExperimentErrorResponse(BaseModel):
    """Standard experiment API error response."""

    detail: ExperimentErrorDetail