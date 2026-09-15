from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)
from pydantic.alias_generators import to_camel

from backend.app.models.enums import RiskLevel


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