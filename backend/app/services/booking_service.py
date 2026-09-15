import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter, sleep
from uuid import UUID

from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import Booking
from backend.app.models.enums import (
    AttemptOutcome,
    BookingStatus,
    ExperimentStatus,
    SeatState,
)
from backend.app.repositories.booking_repository import (
    add_booking_attempt,
    get_booking_by_idempotency_key,
    get_experiment_run,
    get_seat_for_update,
    get_show,
    get_synthetic_user,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BookingCommand:
    """Validated data required to request a booking."""

    idempotency_key: UUID
    user_id: int
    show_id: int
    seat_id: int
    experiment_run_id: UUID | None = None
    persist_experiment_booking: bool = False

@dataclass(frozen=True)
class BookingResult:
    """Result returned by the booking transaction service."""

    booking: Booking
    replayed: bool


class BookingServiceError(Exception):
    """Expected booking failure that can be mapped to an API error."""

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


def _request_matches_booking(
    command: BookingCommand,
    booking: Booking,
) -> bool:
    """Check whether an existing booking represents the same request."""

    return (
        booking.user_id == command.user_id
        and booking.show_id == command.show_id
        and booking.seat_id == command.seat_id
        and booking.experiment_run_id
        == command.experiment_run_id
    )


def _get_sqlstate(
    error: OperationalError,
) -> str | None:
    """Read PostgreSQL's SQLSTATE from an operational error."""

    return getattr(error.orig, "sqlstate", None)


def _calculate_attempt_timing(
    requested_at: datetime,
    started_at: float,
) -> tuple[datetime, Decimal]:
    """Calculate completion time and nonnegative latency."""

    completed_at = datetime.now(UTC)
    elapsed_ms = max(
        0.0,
        (perf_counter() - started_at) * 1000,
    )
    latency_ms = Decimal(f"{elapsed_ms:.3f}")

    if completed_at < requested_at:
        completed_at = requested_at

    return completed_at, latency_ms


def _outcome_for_error(
    error: BookingServiceError,
) -> AttemptOutcome:
    """Map a service error to a telemetry outcome."""

    if error.code == "SEAT_UNAVAILABLE":
        return AttemptOutcome.SEAT_UNAVAILABLE

    if error.code in {
        "LOCK_TIMEOUT",
        "DATABASE_TIMEOUT",
    }:
        return AttemptOutcome.LOCK_TIMEOUT

    if error.status_code < 500:
        return AttemptOutcome.VALIDATION_ERROR

    return AttemptOutcome.INTERNAL_ERROR


def _add_attempt_to_session(
    session: Session,
    *,
    command: BookingCommand,
    requested_at: datetime,
    started_at: float,
    persisted_experiment_run_id: UUID | None,
    outcome: AttemptOutcome,
    http_status: int,
    error_code: str | None,
) -> None:
    """Create an attempt record without committing it."""

    completed_at, latency_ms = _calculate_attempt_timing(
        requested_at,
        started_at,
    )

    add_booking_attempt(
        session,
        idempotency_key=command.idempotency_key,
        experiment_run_id=persisted_experiment_run_id,
        user_id=command.user_id,
        show_id=command.show_id,
        seat_id=command.seat_id,
        requested_at=requested_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
        outcome=outcome,
        http_status=http_status,
        error_code=error_code,
    )


def _record_failed_attempt(
    session: Session,
    *,
    command: BookingCommand,
    requested_at: datetime,
    started_at: float,
    persisted_experiment_run_id: UUID | None,
    error: BookingServiceError,
) -> None:
    """Roll back booking work and persist failure telemetry."""

    session.rollback()

    try:
        _add_attempt_to_session(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            outcome=_outcome_for_error(error),
            http_status=error.status_code,
            error_code=error.code,
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception(
            "Unable to persist booking-attempt telemetry."
        )


def create_booking(
    session: Session,
    command: BookingCommand,
) -> BookingResult:
    """Create one booking and record its attempt telemetry."""

    requested_at = datetime.now(UTC)
    started_at = perf_counter()

    # This stays null until the requested experiment is confirmed to exist.
    # It prevents telemetry for an invalid experiment UUID from violating
    # the booking_attempts foreign-key constraint.
    persisted_experiment_run_id: UUID | None = None

    # These values are populated only after a valid experiment is found.
    recycle_experiment = False
    simulated_work_ms = 0

    try:
        existing_booking = get_booking_by_idempotency_key(
            session,
            command.idempotency_key,
        )

        if existing_booking is not None:
            if not _request_matches_booking(
                command,
                existing_booking,
            ):
                raise BookingServiceError(
                    code="IDEMPOTENCY_KEY_REUSED",
                    message=(
                        "The idempotency key was previously "
                        "used for a different booking request."
                    ),
                    status_code=409,
                )

            _add_attempt_to_session(
                session,
                command=command,
                requested_at=requested_at,
                started_at=started_at,
                persisted_experiment_run_id=(
                    existing_booking.experiment_run_id
                ),
                outcome=AttemptOutcome.SUCCESS,
                http_status=200,
                error_code=None,
            )
            session.commit()
            session.refresh(existing_booking)

            return BookingResult(
                booking=existing_booking,
                replayed=True,
            )

        synthetic_user = get_synthetic_user(
            session,
            command.user_id,
        )

        if synthetic_user is None:
            raise BookingServiceError(
                code="USER_NOT_FOUND",
                message="The requested synthetic user was not found.",
                status_code=404,
            )

        show = get_show(
            session,
            command.show_id,
        )

        if show is None:
            raise BookingServiceError(
                code="SHOW_NOT_FOUND",
                message="The requested show was not found.",
                status_code=404,
            )

        if command.experiment_run_id is None:
            if show.is_experiment_show:
                raise BookingServiceError(
                    code="EXPERIMENT_SHOW_MISMATCH",
                    message=(
                        "An experiment show requires "
                        "an experiment run identifier."
                    ),
                    status_code=409,
                )
        else:
            if not settings.experiment_mode_enabled:
                raise BookingServiceError(
                    code="EXPERIMENT_MODE_DISABLED",
                    message="Experiment booking mode is disabled.",
                    status_code=403,
                )

            experiment_run = get_experiment_run(
                session,
                command.experiment_run_id,
            )

            if experiment_run is None:
                raise BookingServiceError(
                    code="EXPERIMENT_NOT_FOUND",
                    message="The requested experiment was not found.",
                    status_code=404,
                )

            persisted_experiment_run_id = experiment_run.id

            if experiment_run.status != ExperimentStatus.RUNNING:
                raise BookingServiceError(
                    code="EXPERIMENT_NOT_RUNNING",
                    message="The requested experiment is not running.",
                    status_code=409,
                )

            if (
                experiment_run.show_id != command.show_id
                or not show.is_experiment_show
            ):
                raise BookingServiceError(
                    code="EXPERIMENT_SHOW_MISMATCH",
                    message=(
                        "The experiment does not belong "
                        "to the requested show."
                    ),
                    status_code=409,
                )

            recycle_experiment = (
                experiment_run.booking_mode
                == "EXPERIMENT_RECYCLE"
                and not command.persist_experiment_booking
            )
            simulated_work_ms = (
                experiment_run.simulated_work_ms
            )

        seat = get_seat_for_update(
            session,
            command.seat_id,
        )

        if seat is None:
            raise BookingServiceError(
                code="SEAT_NOT_FOUND",
                message="The requested seat was not found.",
                status_code=404,
            )

        if seat.show_id != command.show_id:
            raise BookingServiceError(
                code="SEAT_SHOW_MISMATCH",
                message=(
                    "The requested seat does not belong "
                    "to the requested show."
                ),
                status_code=409,
            )

        current_time = datetime.now(UTC)

        if (
            seat.state == SeatState.HELD
            and seat.hold_expires_at is not None
            and seat.hold_expires_at <= current_time
        ):
            seat.state = SeatState.AVAILABLE
            seat.hold_expires_at = None

        if seat.state != SeatState.AVAILABLE:
            raise BookingServiceError(
                code="SEAT_UNAVAILABLE",
                message="The requested seat is not available.",
                status_code=409,
            )

        if recycle_experiment:
            # The SELECT FOR UPDATE lock has already been acquired.
            # Sleeping here deliberately keeps that PostgreSQL row
            # locked so concurrent requests must wait.
            if simulated_work_ms > 0:
                sleep(simulated_work_ms / 1000)

            recycled_at = datetime.now(UTC)

            # The experimental seat is immediately reusable after
            # this transaction commits.
            seat.state = SeatState.AVAILABLE
            seat.hold_expires_at = None
            seat.version += 1

            # A cancelled booking preserves idempotency and traceability
            # without permanently consuming the experiment seat.
            booking = Booking(
                idempotency_key=command.idempotency_key,
                user_id=command.user_id,
                show_id=command.show_id,
                seat_id=command.seat_id,
                experiment_run_id=command.experiment_run_id,
                status=BookingStatus.CANCELLED,
                cancelled_at=recycled_at,
            )
        else:
            # Normal bookings and NORMAL-mode experiments permanently
            # consume the selected seat.
            seat.state = SeatState.BOOKED
            seat.hold_expires_at = None
            seat.version += 1

            booking = Booking(
                idempotency_key=command.idempotency_key,
                user_id=command.user_id,
                show_id=command.show_id,
                seat_id=command.seat_id,
                experiment_run_id=command.experiment_run_id,
                status=BookingStatus.CONFIRMED,
            )

        session.add(booking)

        _add_attempt_to_session(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            outcome=AttemptOutcome.SUCCESS,
            http_status=201,
            error_code=None,
        )

        session.commit()
        session.refresh(booking)

        return BookingResult(
            booking=booking,
            replayed=False,
        )

    except BookingServiceError as error:
        _record_failed_attempt(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            error=error,
        )
        raise

    except IntegrityError as database_error:
        service_error = BookingServiceError(
            code="SEAT_UNAVAILABLE",
            message=(
                "The booking conflicted with another "
                "completed booking."
            ),
            status_code=409,
        )

        _record_failed_attempt(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            error=service_error,
        )

        raise service_error from database_error

    except OperationalError as database_error:
        sqlstate = _get_sqlstate(database_error)

        if sqlstate == "55P03":
            service_error = BookingServiceError(
                code="LOCK_TIMEOUT",
                message=(
                    "The seat lock could not be acquired "
                    "within the configured timeout."
                ),
                status_code=503,
            )
        elif sqlstate == "57014":
            service_error = BookingServiceError(
                code="DATABASE_TIMEOUT",
                message=(
                    "The database statement exceeded "
                    "the configured timeout."
                ),
                status_code=503,
            )
        else:
            service_error = BookingServiceError(
                code="INTERNAL_ERROR",
                message=(
                    "An unexpected database error occurred."
                ),
                status_code=500,
            )

        _record_failed_attempt(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            error=service_error,
        )

        raise service_error from database_error

    except SQLAlchemyError as database_error:
        service_error = BookingServiceError(
            code="INTERNAL_ERROR",
            message="An unexpected database error occurred.",
            status_code=500,
        )

        _record_failed_attempt(
            session,
            command=command,
            requested_at=requested_at,
            started_at=started_at,
            persisted_experiment_run_id=(
                persisted_experiment_run_id
            ),
            error=service_error,
        )

        raise service_error from database_error