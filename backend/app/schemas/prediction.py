from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
)

from backend.app.models.enums import RiskLevel


class PredictionRead(BaseModel):
    """Persisted future-contention prediction."""

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: UUID
    experiment_run_id: UUID
    metric_sample_id: UUID
    model_version_id: UUID
    predicted_at: datetime
    target_window_start: datetime
    target_window_end: datetime
    probability: Decimal
    risk_level: RiskLevel
    threshold_used: Decimal
    is_stale: bool
    quality_flags: list[str]
    actual_label: bool | None


class PredictionErrorDetail(BaseModel):
    """Machine-readable prediction error."""

    code: str
    message: str


class PredictionErrorResponse(BaseModel):
    """Standard prediction API error response."""

    detail: PredictionErrorDetail