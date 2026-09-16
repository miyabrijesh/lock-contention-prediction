from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)
from pydantic.alias_generators import to_camel

from backend.app.models.enums import (
    DemoScenarioName,
    DemoSessionStatus,
    ExperimentStatus,
    RiskLevel,
)


class DemoContentionStatusRead(BaseModel):
    """Public read-only contention status for the demo UI."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    available: bool
    experiment_run_id: UUID | None
    probability: float | None
    risk_level: RiskLevel | None
    predicted_at: datetime | None
    threshold: float | None
    is_stale: bool | None
    active_transactions: int | None
    blocked_sessions: int | None
    waiting_locks: int | None
    requests_per_second: float | None
    p95_latency_ms: float | None


class DemoActiveExperimentRead(BaseModel):
    """Public state of the currently running demo experiment."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    active: bool
    experiment_run_id: UUID | None
    show_id: int | None
    status: ExperimentStatus | None
    hotspot_seat_ids: list[int]
    started_at: datetime | None
    duration_sec: int | None


class DemoSeatSelectionCreate(BaseModel):
    """Browser-selected consecutive demo seats."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    seat_ids: list[int] = Field(
        min_length=1,
        max_length=5,
    )

    @field_validator("seat_ids")
    @classmethod
    def validate_seat_ids(
        cls,
        value: list[int],
    ) -> list[int]:
        """Require positive and unique seat identifiers."""

        if any(seat_id <= 0 for seat_id in value):
            raise ValueError(
                "Every seat ID must be positive."
            )

        if len(value) != len(set(value)):
            raise ValueError(
                "Seat IDs must be unique."
            )

        return value


class DemoSessionRead(BaseModel):
    """Public browser-visible demo-session state."""

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    available: bool
    session_id: UUID | None
    show_id: int | None
    status: DemoSessionStatus | None
    scenario: DemoScenarioName | None
    selected_seat_ids: list[int]
    experiment_run_id: UUID | None
    expires_at: datetime | None
    selection_submitted_at: datetime | None