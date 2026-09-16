import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

from backend.app.models import (
    DemoSession,
    Seat,
)
from backend.app.models.enums import (
    DemoScenarioName,
    DemoSessionStatus,
    SeatState,
)
from backend.app.repositories.demo_session_repository import (
    add_demo_session,
    get_demo_seats_for_update,
    get_demo_session,
    get_demo_session_for_update,
    get_demo_show,
    get_expired_open_demo_sessions,
    get_open_demo_session,
)


DEFAULT_SESSION_LIFETIME_SECONDS = 600
MINIMUM_SELECTED_SEATS = 1
MAXIMUM_SELECTED_SEATS = 5

SEAT_NUMBER_PATTERN = re.compile(
    r"^([A-Za-z]+)([1-9][0-9]*)$"
)


class DemoSessionServiceError(Exception):
    """Expected demo-session service failure."""

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


def _database_error() -> DemoSessionServiceError:
    """Return a safe database failure."""

    return DemoSessionServiceError(
        code="DEMO_SESSION_DATABASE_ERROR",
        message=(
            "The demo session could not be processed "
            "because of a database error."
        ),
        status_code=503,
    )


def _cancel_expired_sessions(
    session: Session,
    *,
    current_time: datetime,
) -> None:
    """Cancel expired sessions that would block a new run."""

    expired_sessions = (
        get_expired_open_demo_sessions(
            session,
            current_time=current_time,
        )
    )

    for expired_session in expired_sessions:
        expired_session.status = (
            DemoSessionStatus.CANCELLED
        )


def _seat_position(
    seat: Seat,
) -> tuple[str, int]:
    """Return the auditorium row and numeric position."""

    match = SEAT_NUMBER_PATTERN.fullmatch(
        seat.seat_number.strip()
    )

    if match is None:
        raise DemoSessionServiceError(
            code="DEMO_SEAT_NUMBER_INVALID",
            message=(
                "One selected seat has an unsupported "
                "seat-number format."
            ),
            status_code=409,
        )

    return (
        match.group(1).upper(),
        int(match.group(2)),
    )


def _validate_selected_seats(
    seats: list[Seat],
    *,
    requested_seat_ids: list[int],
    show_id: int,
) -> list[int]:
    """Validate availability and consecutive seat order."""

    if not (
        MINIMUM_SELECTED_SEATS
        <= len(requested_seat_ids)
        <= MAXIMUM_SELECTED_SEATS
    ):
        raise DemoSessionServiceError(
            code="DEMO_SEAT_COUNT_INVALID",
            message=(
                "Select between 1 and 5 seats."
            ),
            status_code=422,
        )

    if len(requested_seat_ids) != len(
        set(requested_seat_ids)
    ):
        raise DemoSessionServiceError(
            code="DEMO_SEAT_DUPLICATED",
            message=(
                "Selected seat identifiers must be unique."
            ),
            status_code=422,
        )

    seats_by_id = {
        seat.id: seat
        for seat in seats
    }

    missing_seat_ids = sorted(
        set(requested_seat_ids)
        - set(seats_by_id)
    )

    if missing_seat_ids:
        raise DemoSessionServiceError(
            code="DEMO_SEAT_NOT_FOUND",
            message=(
                "One or more selected seats were not found."
            ),
            status_code=404,
        )

    ordered_requested_seats = [
        seats_by_id[seat_id]
        for seat_id in requested_seat_ids
    ]

    if any(
        seat.show_id != show_id
        for seat in ordered_requested_seats
    ):
        raise DemoSessionServiceError(
            code="DEMO_SEAT_SHOW_MISMATCH",
            message=(
                "Every selected seat must belong to "
                "the demo-session show."
            ),
            status_code=409,
        )

    unavailable_seats = [
        seat.seat_number
        for seat in ordered_requested_seats
        if seat.state != SeatState.AVAILABLE
    ]

    if unavailable_seats:
        raise DemoSessionServiceError(
            code="DEMO_SEAT_UNAVAILABLE",
            message=(
                "Every selected seat must be available."
            ),
            status_code=409,
        )

    positions = [
        (
            seat,
            *_seat_position(seat),
        )
        for seat in ordered_requested_seats
    ]

    selected_rows = {
        row
        for _, row, _ in positions
    }

    if len(selected_rows) != 1:
        raise DemoSessionServiceError(
            code="DEMO_SEATS_NOT_SAME_ROW",
            message=(
                "Selected seats must belong to the same row."
            ),
            status_code=422,
        )

    positions.sort(
        key=lambda item: item[2]
    )

    seat_numbers = [
        number
        for _, _, number in positions
    ]

    expected_numbers = list(
        range(
            seat_numbers[0],
            seat_numbers[0]
            + len(seat_numbers),
        )
    )

    if seat_numbers != expected_numbers:
        raise DemoSessionServiceError(
            code="DEMO_SEATS_NOT_CONSECUTIVE",
            message=(
                "Selected seats must be consecutive."
            ),
            status_code=422,
        )

    return [
        seat.id
        for seat, _, _ in positions
    ]


def create_waiting_demo_session(
    session: Session,
    *,
    show_id: int,
    scenario: DemoScenarioName,
    lifetime_seconds: int = (
        DEFAULT_SESSION_LIFETIME_SECONDS
    ),
) -> DemoSession:
    """Create one terminal-owned waiting session."""

    current_time = datetime.now(UTC)

    try:
        show = get_demo_show(
            session,
            show_id,
        )

        if show is None:
            raise DemoSessionServiceError(
                code="SHOW_NOT_FOUND",
                message=(
                    "The requested show was not found."
                ),
                status_code=404,
            )

        if not show.is_experiment_show:
            raise DemoSessionServiceError(
                code="SHOW_NOT_EXPERIMENTAL",
                message=(
                    "Demo sessions require an "
                    "experiment show."
                ),
                status_code=409,
            )

        _cancel_expired_sessions(
            session,
            current_time=current_time,
        )

        existing_session = get_open_demo_session(
            session,
            show_id=show_id,
            for_update=True,
        )

        if existing_session is not None:
            raise DemoSessionServiceError(
                code="DEMO_SESSION_ALREADY_OPEN",
                message=(
                    "An interactive demo session is "
                    "already open for this show."
                ),
                status_code=409,
            )

        if lifetime_seconds < 30:
            raise DemoSessionServiceError(
                code="DEMO_SESSION_LIFETIME_INVALID",
                message=(
                    "Demo-session lifetime must be "
                    "at least 30 seconds."
                ),
                status_code=422,
            )

        demo_session = DemoSession(
            show_id=show_id,
            status=(
                DemoSessionStatus.WAITING_FOR_SELECTION
            ),
            scenario=scenario,
            selected_seat_ids=[],
            experiment_run_id=None,
            expires_at=(
                current_time
                + timedelta(
                    seconds=lifetime_seconds
                )
            ),
            selection_submitted_at=None,
        )

        add_demo_session(
            session,
            demo_session,
        )

        session.commit()
        session.refresh(demo_session)

        return demo_session

    except DemoSessionServiceError:
        session.rollback()
        raise

    except IntegrityError as error:
        session.rollback()

        raise DemoSessionServiceError(
            code="DEMO_SESSION_ALREADY_OPEN",
            message=(
                "An interactive demo session is "
                "already open for this show."
            ),
            status_code=409,
        ) from error

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_current_demo_session(
    session: Session,
    *,
    show_id: int,
) -> DemoSession | None:
    """Return the open session visible to the browser."""

    current_time = datetime.now(UTC)

    try:
        demo_session = get_open_demo_session(
            session,
            show_id=show_id,
            for_update=True,
        )

        if demo_session is None:
            return None

        if demo_session.expires_at <= current_time:
            demo_session.status = (
                DemoSessionStatus.CANCELLED
            )
            session.commit()

            return None

        return demo_session

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def read_demo_session(
    session: Session,
    demo_session_id: UUID,
) -> DemoSession:
    """Return one demo session for the terminal runner."""

    try:
        demo_session = get_demo_session(
            session,
            demo_session_id,
        )

        if demo_session is None:
            raise DemoSessionServiceError(
                code="DEMO_SESSION_NOT_FOUND",
                message=(
                    "The requested demo session "
                    "was not found."
                ),
                status_code=404,
            )

        return demo_session

    except DemoSessionServiceError:
        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error


def submit_demo_seat_selection(
    session: Session,
    *,
    demo_session_id: UUID,
    seat_ids: list[int],
) -> DemoSession:
    """Store one valid consecutive browser selection."""

    current_time = datetime.now(UTC)

    try:
        demo_session = get_demo_session_for_update(
            session,
            demo_session_id,
        )

        if demo_session is None:
            raise DemoSessionServiceError(
                code="DEMO_SESSION_NOT_FOUND",
                message=(
                    "The requested demo session "
                    "was not found."
                ),
                status_code=404,
            )

        if (
            demo_session.status
            == DemoSessionStatus.READY
            and set(
                demo_session.selected_seat_ids
            )
            ==set(seat_ids)
        ):
            return demo_session

        if (
            demo_session.status
            != DemoSessionStatus.WAITING_FOR_SELECTION
        ):
            raise DemoSessionServiceError(
                code="DEMO_SESSION_NOT_WAITING",
                message=(
                    "The demo session is not waiting "
                    "for a seat selection."
                ),
                status_code=409,
            )

        if demo_session.expires_at <= current_time:
            demo_session.status = (
                DemoSessionStatus.CANCELLED
            )
            session.commit()

            raise DemoSessionServiceError(
                code="DEMO_SESSION_EXPIRED",
                message=(
                    "The demo session expired before "
                    "the seats were selected."
                ),
                status_code=409,
            )

        seats = get_demo_seats_for_update(
            session,
            seat_ids,
        )

        ordered_seat_ids = _validate_selected_seats(
            seats,
            requested_seat_ids=seat_ids,
            show_id=demo_session.show_id,
        )

        demo_session.selected_seat_ids = (
            ordered_seat_ids
        )
        demo_session.selection_submitted_at = (
            current_time
        )
        demo_session.status = (
            DemoSessionStatus.READY
        )

        session.commit()
        session.refresh(demo_session)

        return demo_session

    except DemoSessionServiceError:
        if session.in_transaction():
            session.rollback()

        raise

    except SQLAlchemyError as error:
        session.rollback()
        raise _database_error() from error