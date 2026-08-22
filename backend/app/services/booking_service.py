from dataclasses import dataclass
from datetime import UTC, datetime
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
    BookingStatus,
    ExperimentStatus,
    SeatState,
)
from backend.app.repositories.booking_repository import (
    get_booking_by_idempotency_key,
    get_experiment_run,
    get_seat_for_update,
    get_show,
    get_synthetic_user,
)


@dataclass(frozen=True)
class BookingCommand:
    """Validated data required to request a booking."""

    idempotency_key: UUID
    user_id: int
    show_id: int
    seat_id: int
    experiment_run_id: UUID | None = None


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
    """Read PostgreSQL's SQLSTATE value from an operational error."""

    return getattr(error.orig, "sqlstate", None)


def create_booking(
    session: Session,
    command: BookingCommand,
) -> BookingResult:
    """Create one confirmed booking in a database transaction."""

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
        session.commit()
        session.refresh(booking)

        return BookingResult(
            booking=booking,
            replayed=False,
        )

    except BookingServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()

        raise BookingServiceError(
            code="SEAT_UNAVAILABLE",
            message=(
                "The booking conflicted with another "
                "completed booking."
            ),
            status_code=409,
        ) from error

    except OperationalError as error:
        session.rollback()

        sqlstate = _get_sqlstate(error)

        if sqlstate == "55P03":
            raise BookingServiceError(
                code="LOCK_TIMEOUT",
                message=(
                    "The seat lock could not be acquired "
                    "within the configured timeout."
                ),
                status_code=503,
            ) from error

        if sqlstate == "57014":
            raise BookingServiceError(
                code="DATABASE_TIMEOUT",
                message=(
                    "The database statement exceeded "
                    "the configured timeout."
                ),
                status_code=503,
            ) from error

        raise BookingServiceError(
            code="INTERNAL_ERROR",
            message="An unexpected database error occurred.",
            status_code=500,
        ) from error

    except SQLAlchemyError as error:
        session.rollback()

        raise BookingServiceError(
            code="INTERNAL_ERROR",
            message="An unexpected database error occurred.",
            status_code=500,
        ) from error