from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class ModelActivationRequest(BaseModel):
    """Administrator request to activate one model version."""

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
        """Normalize and validate the activation actor."""

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
        """Normalize an optional activation reason."""

        if value is None:
            return None

        normalized_value = value.strip()

        return normalized_value or None


class ModelVersionRead(BaseModel):
    """Registered model version and activation state."""

    model_config = ConfigDict(
        from_attributes=True,
    )

    id: UUID
    name: str
    algorithm: str
    feature_schema: dict[str, object]
    target_horizon_sec: int
    training_run_ids: list[str]
    validation_strategy: str
    risk_thresholds: dict[str, object]
    evaluation_metrics: dict[str, object]
    artifact_path: str
    artifact_sha256: str
    code_version: str
    created_at: datetime
    is_active: bool

    @model_validator(mode="after")
    def normalize_artifact_sha256(self) -> Self:
        """Remove database padding from the CHAR checksum."""

        self.artifact_sha256 = (
            self.artifact_sha256.strip()
        )

        return self


class ModelErrorDetail(BaseModel):
    """Machine-readable model-management error."""

    code: str
    message: str


class ModelErrorResponse(BaseModel):
    """Standard model-management API error response."""

    detail: ModelErrorDetail