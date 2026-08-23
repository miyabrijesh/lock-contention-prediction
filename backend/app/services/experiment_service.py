from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.enums import ExperimentStatus
from backend.app.models.experiment import ExperimentRun
from backend.app.repositories.experiment_repository import (
    add_audit_event,
    add_experiment_run,
    count_non_available_seats,
    get_experiment_run,
    get_experiment_run_for_update,
    get_running_experiment,
    get_seats_by_ids,
    get_show,
    list_experiment_runs,
)
from backend.app.schemas.experiment import (
    ExperimentCreate,
    ExperimentTransition,
)


class ExperimentServiceError(Exception):
    """Expected experiment lifecycle failure."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message
        self.status_code = status_code


def _database_error() -> ExperimentServiceError:
    """Create the standard unexpected database error."""

    return ExperimentServiceError(
        code="INTERNAL_ERROR",
        message="An unexpected database error occurred.",
        status_code=500,
    )


def _integrity_error(
    error: IntegrityError,
) -> ExperimentServiceError:
    """Translate known database-constraint failures."""

    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(
        diagnostic,
        "constraint_name",
        None,
    )

    if (
        constraint_name
        == "uq_experiment_runs_single_running"
    ):
        return ExperimentServiceError(
            code="EXPERIMENT_ALREADY_RUNNING",
            message=(
                "Another experiment is already running. "
                "Complete, fail, or cancel it before starting "
                "another experiment."
            ),
            status_code=409,
        )

    return _database_error()


def _validate_experiment_show(
    session: Session,
    *,
    show_id: int,
    hotspot_seat_ids: list[int],
) -> None:
    """Validate the experiment show and hotspot seats."""

    show = get_show(
        session,
        show_id,
    )

    if show is None:
        raise ExperimentServiceError(
            code="SHOW_NOT_FOUND",
            message="The requested show was not found.",
            status_code=404,
        )

    if not show.is_experiment_show:
        raise ExperimentServiceError(
            code="SHOW_NOT_EXPERIMENTAL",
            message=(
                "Experiments can only use a show marked "
                "as an experiment show."
            ),
            status_code=409,
        )

    hotspot_seats = get_seats_by_ids(
        session,
        hotspot_seat_ids,
    )

    found_seat_ids = {
        seat.id
        for seat in hotspot_seats
    }

    missing_seat_ids = sorted(
        set(hotspot_seat_ids) - found_seat_ids
    )

    if missing_seat_ids:
        raise ExperimentServiceError(
            code="HOTSPOT_SEAT_NOT_FOUND",
            message=(
                "One or more hotspot seat identifiers "
                "were not found."
            ),
            status_code=404,
        )

    mismatched_seats = [
        seat
        for seat in hotspot_seats
        if seat.show_id != show_id
    ]

    if mismatched_seats:
        raise ExperimentServiceError(
            code="HOTSPOT_SEAT_SHOW_MISMATCH",
            message=(
                "Every hotspot seat must belong to "
                "the selected experiment show."
            ),
            status_code=409,
        )


def create_experiment(
    session: Session,
    command: ExperimentCreate,
) -> ExperimentRun:
    """Create a new experiment in DRAFT status."""

    try:
        _validate_experiment_show(
            session,
            show_id=command.show_id,
            hotspot_seat_ids=command.hotspot_seat_ids,
        )

        experiment = ExperimentRun(
            name=command.name,
            show_id=command.show_id,
            status=ExperimentStatus.DRAFT,
            user_count=command.user_count,
            spawn_rate_per_sec=(
                command.spawn_rate_per_sec
            ),
            duration_sec=command.duration_sec,
            hotspot_ratio=command.hotspot_ratio,
            hotspot_seat_ids=(
                command.hotspot_seat_ids
            ),
            sample_interval_sec=(
                command.sample_interval_sec
            ),
            simulated_work_ms=(
                command.simulated_work_ms
            ),
            booking_mode=command.booking_mode,
            random_seed=command.random_seed,
            code_version=command.code_version,
            created_by=command.created_by,
        )

        add_experiment_run(
            session,
            experiment,
        )

        # Flush assigns the UUID before creating the audit event.
        session.flush()

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action="experiment_created",
            actor=command.created_by,
            details={
                "status": ExperimentStatus.DRAFT.value,
                "show_id": command.show_id,
                "booking_mode": command.booking_mode,
            },
        )

        session.commit()
        session.refresh(experiment)

        return experiment

    except ExperimentServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()
        raise _integrity_error(error) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def list_experiments(
    session: Session,
    *,
    experiment_status: ExperimentStatus | None = None,
) -> list[ExperimentRun]:
    """Return experiments, optionally filtered by status."""

    try:
        return list_experiment_runs(
            session,
            experiment_status=experiment_status,
        )

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_experiment(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun:
    """Return one experiment or raise a structured error."""

    try:
        experiment = get_experiment_run(
            session,
            experiment_run_id,
        )

        if experiment is None:
            raise ExperimentServiceError(
                code="EXPERIMENT_NOT_FOUND",
                message="The requested experiment was not found.",
                status_code=404,
            )

        return experiment

    except ExperimentServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def start_experiment(
    session: Session,
    experiment_run_id: UUID,
    transition: ExperimentTransition,
) -> ExperimentRun:
    """Move a DRAFT experiment into RUNNING status."""

    try:
        experiment = get_experiment_run_for_update(
            session,
            experiment_run_id,
        )

        if experiment is None:
            raise ExperimentServiceError(
                code="EXPERIMENT_NOT_FOUND",
                message="The requested experiment was not found.",
                status_code=404,
            )

        # Repeating the same successful transition is safe.
        if experiment.status == ExperimentStatus.RUNNING:
            session.commit()
            session.refresh(experiment)
            return experiment

        if experiment.status != ExperimentStatus.DRAFT:
            raise ExperimentServiceError(
                code="INVALID_EXPERIMENT_TRANSITION",
                message=(
                    "Only a DRAFT experiment can be started."
                ),
                status_code=409,
            )

        if not settings.experiment_mode_enabled:
            raise ExperimentServiceError(
                code="EXPERIMENT_MODE_DISABLED",
                message="Experiment mode is disabled.",
                status_code=403,
            )

        running_experiment = get_running_experiment(
            session,
            exclude_experiment_id=experiment.id,
        )

        if running_experiment is not None:
            raise ExperimentServiceError(
                code="EXPERIMENT_ALREADY_RUNNING",
                message=(
                    "Another experiment is already running. "
                    "Complete, fail, or cancel it first."
                ),
                status_code=409,
            )

        _validate_experiment_show(
            session,
            show_id=experiment.show_id,
            hotspot_seat_ids=(
                experiment.hotspot_seat_ids
            ),
        )

        unavailable_seat_count = (
            count_non_available_seats(
                session,
                experiment.show_id,
            )
        )

        if unavailable_seat_count > 0:
            raise ExperimentServiceError(
                code="EXPERIMENT_SEATS_NOT_READY",
                message=(
                    "All seats in the experiment show must "
                    "be available before the experiment starts."
                ),
                status_code=409,
            )

        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = datetime.now(UTC)
        experiment.ended_at = None

        audit_details: dict[str, object] = {
            "from_status": ExperimentStatus.DRAFT.value,
            "to_status": ExperimentStatus.RUNNING.value,
        }

        if transition.reason is not None:
            audit_details["reason"] = transition.reason

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action="experiment_started",
            actor=transition.actor,
            details=audit_details,
        )

        session.commit()
        session.refresh(experiment)

        return experiment

    except ExperimentServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()
        raise _integrity_error(error) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def _finish_experiment(
    session: Session,
    *,
    experiment_run_id: UUID,
    transition: ExperimentTransition,
    target_status: ExperimentStatus,
    action: str,
    allowed_statuses: set[ExperimentStatus],
) -> ExperimentRun:
    """Apply one terminal experiment status transition."""

    try:
        experiment = get_experiment_run_for_update(
            session,
            experiment_run_id,
        )

        if experiment is None:
            raise ExperimentServiceError(
                code="EXPERIMENT_NOT_FOUND",
                message="The requested experiment was not found.",
                status_code=404,
            )

        # Repeating the same completed transition is safe.
        if experiment.status == target_status:
            session.commit()
            session.refresh(experiment)
            return experiment

        if experiment.status not in allowed_statuses:
            raise ExperimentServiceError(
                code="INVALID_EXPERIMENT_TRANSITION",
                message=(
                    f"An experiment in {experiment.status.value} "
                    f"status cannot transition to "
                    f"{target_status.value}."
                ),
                status_code=409,
            )

        previous_status = experiment.status

        experiment.status = target_status

        # A DRAFT experiment cancelled before starting has
        # no runtime end timestamp.
        if previous_status == ExperimentStatus.RUNNING:
            experiment.ended_at = datetime.now(UTC)

        audit_details: dict[str, object] = {
            "from_status": previous_status.value,
            "to_status": target_status.value,
        }

        if transition.reason is not None:
            audit_details["reason"] = transition.reason

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action=action,
            actor=transition.actor,
            details=audit_details,
        )

        session.commit()
        session.refresh(experiment)

        return experiment

    except ExperimentServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()
        raise _integrity_error(error) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def complete_experiment(
    session: Session,
    experiment_run_id: UUID,
    transition: ExperimentTransition,
) -> ExperimentRun:
    """Complete a running experiment."""

    return _finish_experiment(
        session,
        experiment_run_id=experiment_run_id,
        transition=transition,
        target_status=ExperimentStatus.COMPLETED,
        action="experiment_completed",
        allowed_statuses={
            ExperimentStatus.RUNNING,
        },
    )


def fail_experiment(
    session: Session,
    experiment_run_id: UUID,
    transition: ExperimentTransition,
) -> ExperimentRun:
    """Mark a running experiment as failed."""

    return _finish_experiment(
        session,
        experiment_run_id=experiment_run_id,
        transition=transition,
        target_status=ExperimentStatus.FAILED,
        action="experiment_failed",
        allowed_statuses={
            ExperimentStatus.RUNNING,
        },
    )


def cancel_experiment(
    session: Session,
    experiment_run_id: UUID,
    transition: ExperimentTransition,
) -> ExperimentRun:
    """Cancel a draft or running experiment."""

    return _finish_experiment(
        session,
        experiment_run_id=experiment_run_id,
        transition=transition,
        target_status=ExperimentStatus.CANCELLED,
        action="experiment_cancelled",
        allowed_statuses={
            ExperimentStatus.DRAFT,
            ExperimentStatus.RUNNING,
        },
    )