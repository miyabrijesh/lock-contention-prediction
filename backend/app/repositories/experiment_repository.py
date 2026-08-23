from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.models.enums import (
    ExperimentStatus,
    SeatState,
)
from backend.app.models.experiment import ExperimentRun
from backend.app.models.operations import AuditEvent
from backend.app.models.reservation import (
    Seat,
    Show,
)


def add_experiment_run(
    session: Session,
    experiment_run: ExperimentRun,
) -> ExperimentRun:
    """Add a new experiment run to the current transaction."""

    session.add(experiment_run)

    return experiment_run


def get_experiment_run(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun | None:
    """Find an experiment run by primary key."""

    return session.get(
        ExperimentRun,
        experiment_run_id,
    )


def get_experiment_run_for_update(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun | None:
    """Lock and return one experiment for a transition."""

    statement = (
        select(ExperimentRun)
        .where(
            ExperimentRun.id == experiment_run_id
        )
        .with_for_update()
    )

    return session.scalar(statement)


def list_experiment_runs(
    session: Session,
    experiment_status: ExperimentStatus | None = None,
) -> list[ExperimentRun]:
    """List experiments in reverse creation order."""

    statement = select(ExperimentRun)

    if experiment_status is not None:
        statement = statement.where(
            ExperimentRun.status == experiment_status
        )

    statement = statement.order_by(
        ExperimentRun.created_at.desc(),
        ExperimentRun.id.desc(),
    )

    return list(
        session.scalars(statement).all()
    )


def get_running_experiment(
    session: Session,
    *,
    exclude_experiment_id: UUID | None = None,
) -> ExperimentRun | None:
    """Find the currently running experiment, if one exists."""

    statement = select(ExperimentRun).where(
        ExperimentRun.status == ExperimentStatus.RUNNING
    )

    if exclude_experiment_id is not None:
        statement = statement.where(
            ExperimentRun.id
            != exclude_experiment_id
        )

    return session.scalar(statement)


def get_show(
    session: Session,
    show_id: int,
) -> Show | None:
    """Find a show by primary key."""

    return session.get(
        Show,
        show_id,
    )


def get_seats_by_ids(
    session: Session,
    seat_ids: list[int],
) -> list[Seat]:
    """Return all seats matching the supplied identifiers."""

    if not seat_ids:
        return []

    statement = (
        select(Seat)
        .where(Seat.id.in_(seat_ids))
        .order_by(Seat.id)
    )

    return list(
        session.scalars(statement).all()
    )


def count_non_available_seats(
    session: Session,
    show_id: int,
) -> int:
    """Count experiment-show seats that are not available."""

    statement = (
        select(func.count())
        .select_from(Seat)
        .where(
            Seat.show_id == show_id,
            Seat.state != SeatState.AVAILABLE,
        )
    )

    return session.scalar(statement) or 0


def add_audit_event(
    session: Session,
    *,
    experiment_run_id: UUID,
    action: str,
    actor: str,
    details: dict[str, object],
) -> AuditEvent:
    """Add one experiment lifecycle audit event."""

    audit_event = AuditEvent(
        experiment_run_id=experiment_run_id,
        action=action,
        actor=actor,
        resource_type="experiment_run",
        resource_id=str(experiment_run_id),
        details=details,
    )

    session.add(audit_event)

    return audit_event