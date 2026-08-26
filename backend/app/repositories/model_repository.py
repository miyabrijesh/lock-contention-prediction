from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.operations import AuditEvent
from backend.app.models.prediction import ModelVersion


def list_model_versions(
    session: Session,
) -> list[ModelVersion]:
    """Return registered models in descending creation order."""

    statement = (
        select(ModelVersion)
        .order_by(
            ModelVersion.created_at.desc(),
            ModelVersion.id.desc(),
        )
    )

    return list(
        session.scalars(statement).all()
    )


def get_model_version(
    session: Session,
    model_version_id: UUID,
) -> ModelVersion | None:
    """Return one registered model without locking it."""

    return session.get(
        ModelVersion,
        model_version_id,
    )


def get_active_model_version(
    session: Session,
) -> ModelVersion | None:
    """Return the single active model, when one exists."""

    statement = (
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
    )

    return session.scalar(statement)


def lock_model_versions(
    session: Session,
) -> list[ModelVersion]:
    """Lock every registered model in deterministic order."""

    statement = (
        select(ModelVersion)
        .order_by(ModelVersion.id)
        .with_for_update()
    )

    return list(
        session.scalars(statement).all()
    )


def find_locked_model_version(
    locked_models: list[ModelVersion],
    model_version_id: UUID,
) -> ModelVersion | None:
    """Find the requested model within already-locked rows."""

    return next(
        (
            model
            for model in locked_models
            if model.id == model_version_id
        ),
        None,
    )


def find_locked_active_model(
    locked_models: list[ModelVersion],
) -> ModelVersion | None:
    """Find the active model within already-locked rows."""

    return next(
        (
            model
            for model in locked_models
            if model.is_active
        ),
        None,
    )


def add_model_audit_event(
    session: Session,
    *,
    model_version_id: UUID,
    action: str,
    actor: str,
    details: dict[str, object],
) -> AuditEvent:
    """Add an audit event for a model-management action."""

    audit_event = AuditEvent(
        experiment_run_id=None,
        action=action,
        actor=actor,
        resource_type="model_version",
        resource_id=str(model_version_id),
        details=details,
    )

    session.add(audit_event)

    return audit_event