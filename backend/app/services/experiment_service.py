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
from load_testing.demand_profiles import (
    DemandProfileError,
    validate_demand_configuration,
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

        try:
            validate_demand_configuration(
                profile_name=command.demand_profile,
                hotspot_ratio=command.hotspot_ratio,
                hotspot_seat_count=len(
                    command.hotspot_seat_ids
                ),
            )
        except DemandProfileError as error:
            raise ExperimentServiceError(
                code="DEMAND_PROFILE_MISMATCH",
                message=str(error),
                status_code=422,
            ) from error

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
            traffic_pattern=command.traffic_pattern,
            demand_profile=command.demand_profile,
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
                "traffic_pattern": command.traffic_pattern,
                "demand_profile": command.demand_profile,
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
    command: ExperimentTransition,
) -> ExperimentRun:
    """Transition a draft experiment to running."""

    if not settings.experiment_mode_enabled:
        raise ExperimentServiceError(
            code="EXPERIMENT_MODE_DISABLED",
            message="Experiment execution is disabled.",
            status_code=403,
        )

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

        if experiment.status != ExperimentStatus.DRAFT:
            raise ExperimentServiceError(
                code="INVALID_EXPERIMENT_TRANSITION",
                message=(
                    "Only a DRAFT experiment can be started."
                ),
                status_code=409,
            )

        running_experiment = get_running_experiment(
            session,
        )

        if running_experiment is not None:
            raise ExperimentServiceError(
                code="EXPERIMENT_ALREADY_RUNNING",
                message=(
                    "Another experiment is already running."
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
                show_id=experiment.show_id,
            )
        )

        if unavailable_seat_count > 0:
            raise ExperimentServiceError(
                code="EXPERIMENT_SEATS_NOT_READY",
                message=(
                    "All experiment-show seats must be available "
                    "before an experiment can start."
                ),
                status_code=409,
            )

        started_at = datetime.now(UTC)

        experiment.status = ExperimentStatus.RUNNING
        experiment.started_at = started_at
        experiment.ended_at = None

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action="experiment_started",
            actor=command.actor,
            details={
                "from_status": ExperimentStatus.DRAFT.value,
                "to_status": ExperimentStatus.RUNNING.value,
                "reason": command.reason,
                "started_at": started_at.isoformat(),
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


def _finish_experiment(
    session: Session,
    experiment_run_id: UUID,
    command: ExperimentTransition,
    *,
    target_status: ExperimentStatus,
    audit_action: str,
) -> ExperimentRun:
    """Transition a running experiment to a terminal status."""

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

        if experiment.status != ExperimentStatus.RUNNING:
            raise ExperimentServiceError(
                code="INVALID_EXPERIMENT_TRANSITION",
                message=(
                    "Only a RUNNING experiment can transition "
                    f"to {target_status.value}."
                ),
                status_code=409,
            )

        ended_at = datetime.now(UTC)

        experiment.status = target_status
        experiment.ended_at = ended_at

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action=audit_action,
            actor=command.actor,
            details={
                "from_status": ExperimentStatus.RUNNING.value,
                "to_status": target_status.value,
                "reason": command.reason,
                "ended_at": ended_at.isoformat(),
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


def complete_experiment(
    session: Session,
    experiment_run_id: UUID,
    command: ExperimentTransition,
) -> ExperimentRun:
    """Transition a running experiment to completed."""

    return _finish_experiment(
        session,
        experiment_run_id,
        command,
        target_status=ExperimentStatus.COMPLETED,
        audit_action="experiment_completed",
    )


def fail_experiment(
    session: Session,
    experiment_run_id: UUID,
    command: ExperimentTransition,
) -> ExperimentRun:
    """Transition a running experiment to failed."""

    return _finish_experiment(
        session,
        experiment_run_id,
        command,
        target_status=ExperimentStatus.FAILED,
        audit_action="experiment_failed",
    )


def cancel_experiment(
    session: Session,
    experiment_run_id: UUID,
    command: ExperimentTransition,
) -> ExperimentRun:
    """Cancel a draft or running experiment."""

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

        if experiment.status not in {
            ExperimentStatus.DRAFT,
            ExperimentStatus.RUNNING,
        }:
            raise ExperimentServiceError(
                code="INVALID_EXPERIMENT_TRANSITION",
                message=(
                    "Only a DRAFT or RUNNING experiment "
                    "can be cancelled."
                ),
                status_code=409,
            )

        previous_status = experiment.status
        ended_at = datetime.now(UTC)

        experiment.status = ExperimentStatus.CANCELLED
        experiment.ended_at = ended_at

        add_audit_event(
            session,
            experiment_run_id=experiment.id,
            action="experiment_cancelled",
            actor=command.actor,
            details={
                "from_status": previous_status.value,
                "to_status": ExperimentStatus.CANCELLED.value,
                "reason": command.reason,
                "ended_at": ended_at.isoformat(),
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