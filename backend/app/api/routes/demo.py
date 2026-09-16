from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models import (
    DemoSession,
    ExperimentRun,
    MetricSample,
    Prediction,
)
from backend.app.models.enums import (
    DemoSessionStatus,
    ExperimentStatus,
)
from backend.app.schemas.booking import (
    BookingCreate,
    BookingErrorResponse,
    BookingRead,
)
from backend.app.schemas.demo import (
    DemoActiveExperimentRead,
    DemoBookingBatchCreate,
    DemoBookingBatchRead,
    DemoContentionStatusRead,
    DemoSeatSelectionCreate,
    DemoSessionRead,
)
from backend.app.services.booking_service import (
    BatchBookingCommand,
    BookingCommand,
    BookingServiceError,
    create_booking,
    create_bookings_atomically,
)
from backend.app.services.demo_session_service import (
    DemoSessionServiceError,
    read_current_demo_session,
    submit_demo_seat_selection,
)


router = APIRouter(
    prefix="/api/v1/demo",
    tags=["Demo"],
)

DatabaseSession = Annotated[
    Session,
    Depends(get_db),
]

IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description=(
            "UUID identifying one demo booking request."
        ),
    ),
]


def _demo_session_http_exception(
    error: DemoSessionServiceError,
) -> HTTPException:
    """Convert a demo-session error into an API response."""

    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.code,
            "message": error.message,
        },
    )


def _demo_session_response(
    demo_session: DemoSession | None,
) -> DemoSessionRead:
    """Build the public demo-session response."""

    if demo_session is None:
        return DemoSessionRead(
            available=False,
            session_id=None,
            show_id=None,
            status=None,
            scenario=None,
            selected_seat_ids=[],
            experiment_run_id=None,
            expires_at=None,
            selection_submitted_at=None,
        )

    return DemoSessionRead(
        available=True,
        session_id=demo_session.id,
        show_id=demo_session.show_id,
        status=demo_session.status,
        scenario=demo_session.scenario,
        selected_seat_ids=list(
            demo_session.selected_seat_ids
        ),
        experiment_run_id=(
            demo_session.experiment_run_id
        ),
        expires_at=demo_session.expires_at,
        selection_submitted_at=(
            demo_session.selection_submitted_at
        ),
    )


@router.post(
    "/bookings",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a normal booking for the demo UI",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": BookingErrorResponse,
        },
        status.HTTP_404_NOT_FOUND: {
            "model": BookingErrorResponse,
        },
        status.HTTP_409_CONFLICT: {
            "model": BookingErrorResponse,
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": BookingErrorResponse,
        },
    },
)
def create_demo_booking(
    request_body: BookingCreate,
    response: Response,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> BookingRead:
    """Create a booking without exposing an internal token."""

    if request_body.experiment_run_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DEMO_EXPERIMENT_NOT_ALLOWED",
                "message": (
                    "The public demo booking endpoint "
                    "does not accept experiment identifiers."
                ),
            },
        )

    active_experiment_statement = (
        select(ExperimentRun)
        .where(
            ExperimentRun.show_id
            == request_body.show_id,
            ExperimentRun.status
            == ExperimentStatus.RUNNING,
        )
        .order_by(
            ExperimentRun.started_at.desc(),
            ExperimentRun.id.desc(),
        )
        .limit(1)
    )

    active_experiment = session.scalar(
        active_experiment_statement
    )

    command = BookingCommand(
        idempotency_key=idempotency_key,
        user_id=request_body.user_id,
        show_id=request_body.show_id,
        seat_id=request_body.seat_id,
        experiment_run_id=(
            active_experiment.id
            if active_experiment is not None
            else None
        ),
        persist_experiment_booking=(
            active_experiment is not None
        ),
    )

    try:
        result = create_booking(
            session,
            command,
        )
    except BookingServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers[
            "Idempotent-Replayed"
        ] = "true"
    else:
        response.status_code = status.HTTP_201_CREATED

    return BookingRead.model_validate(
        result.booking
    )


@router.post(
    "/bookings/batch",
    response_model=DemoBookingBatchRead,
    status_code=status.HTTP_201_CREATED,
    summary="Atomically book 1–5 selected demo seats",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": BookingErrorResponse,
        },
        status.HTTP_409_CONFLICT: {
            "model": BookingErrorResponse,
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": BookingErrorResponse,
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": BookingErrorResponse,
        },
    },
)
def create_demo_batch_booking(
    request_body: DemoBookingBatchCreate,
    response: Response,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> DemoBookingBatchRead:
    """Book every selected seat in one transaction."""

    active_experiment = session.scalar(
        select(ExperimentRun)
        .where(
            ExperimentRun.show_id
            == request_body.show_id,
            ExperimentRun.status
            == ExperimentStatus.RUNNING,
        )
        .order_by(
            ExperimentRun.started_at.desc(),
            ExperimentRun.id.desc(),
        )
        .limit(1)
    )

    if active_experiment is not None:
        demo_session = session.scalar(
            select(DemoSession)
            .where(
                DemoSession.experiment_run_id
                == active_experiment.id,
                DemoSession.status
                == DemoSessionStatus.RUNNING,
            )
            .limit(1)
        )

        if (
            demo_session is None
            or set(
                demo_session.selected_seat_ids
            ) != set(request_body.seat_ids)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "DEMO_SELECTION_MISMATCH",
                    "message": (
                        "The requested seats do not match "
                        "the active browser selection."
                    ),
                },
            )

    command = BatchBookingCommand(
        batch_idempotency_key=idempotency_key,
        user_id=request_body.user_id,
        show_id=request_body.show_id,
        seat_ids=tuple(request_body.seat_ids),
        experiment_run_id=(
            active_experiment.id
            if active_experiment is not None
            else None
        ),
    )

    try:
        result = create_bookings_atomically(
            session,
            command,
        )
    except BookingServiceError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={
                "code": error.code,
                "message": error.message,
            },
        ) from error

    if result.replayed:
        response.status_code = status.HTTP_200_OK
        response.headers[
            "Idempotent-Replayed"
        ] = "true"
    else:
        response.status_code = status.HTTP_201_CREATED

    return DemoBookingBatchRead(
        bookings=[
            BookingRead.model_validate(booking)
            for booking in result.bookings
        ],
        replayed=result.replayed,
    )



@router.get(
    "/session",
    response_model=DemoSessionRead,
    summary="Read the browser-visible demo session",
)
def get_demo_session(
    session: DatabaseSession,
    show_id: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "Show whose open demo session "
                "should be returned."
            ),
        ),
    ],
) -> DemoSessionRead:
    """Return the terminal-created session awaiting the browser."""

    try:
        demo_session = read_current_demo_session(
            session,
            show_id=show_id,
        )

        return _demo_session_response(
            demo_session
        )

    except DemoSessionServiceError as error:
        raise _demo_session_http_exception(
            error
        ) from error


@router.post(
    "/sessions/{demo_session_id}/selection",
    response_model=DemoSessionRead,
    summary="Submit consecutive seats to a waiting demo",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Demo session or seat not found.",
        },
        status.HTTP_409_CONFLICT: {
            "description": (
                "Session state or seat availability conflict."
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": (
                "Seat count, row or consecutiveness is invalid."
            ),
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Database unavailable.",
        },
    },
)
def submit_demo_selection(
    demo_session_id: UUID,
    request_body: DemoSeatSelectionCreate,
    session: DatabaseSession,
) -> DemoSessionRead:
    """Store the browser's 1–5 consecutive seats."""

    try:
        demo_session = submit_demo_seat_selection(
            session,
            demo_session_id=demo_session_id,
            seat_ids=request_body.seat_ids,
        )

        return _demo_session_response(
            demo_session
        )

    except DemoSessionServiceError as error:
        raise _demo_session_http_exception(
            error
        ) from error


@router.get(
    "/active-experiment",
    response_model=DemoActiveExperimentRead,
    summary="Read the currently running demo experiment",
)
def get_active_demo_experiment(
    session: DatabaseSession,
    show_id: Annotated[
        int,
        Query(
            ge=1,
            description=(
                "Show whose running experiment "
                "should be returned."
            ),
        ),
    ],
) -> DemoActiveExperimentRead:
    """Return the latest running experiment for one show."""

    statement = (
        select(ExperimentRun)
        .where(
            ExperimentRun.show_id == show_id,
            ExperimentRun.status
            == ExperimentStatus.RUNNING,
        )
        .order_by(
            ExperimentRun.started_at.desc(),
            ExperimentRun.id.desc(),
        )
        .limit(1)
    )

    active_experiment = session.scalar(statement)

    if active_experiment is None:
        return DemoActiveExperimentRead(
            active=False,
            experiment_run_id=None,
            show_id=None,
            status=None,
            hotspot_seat_ids=[],
            started_at=None,
            duration_sec=None,
        )

    return DemoActiveExperimentRead(
        active=True,
        experiment_run_id=active_experiment.id,
        show_id=active_experiment.show_id,
        status=active_experiment.status,
        hotspot_seat_ids=list(
            active_experiment.hotspot_seat_ids
        ),
        started_at=active_experiment.started_at,
        duration_sec=active_experiment.duration_sec,
    )


@router.get(
    "/contention-status",
    response_model=DemoContentionStatusRead,
    summary="Read the latest prediction and telemetry",
)
def get_demo_contention_status(
    session: DatabaseSession,
) -> DemoContentionStatusRead:
    """Return the latest complete prediction snapshot."""

    statement = (
        select(
            Prediction,
            MetricSample,
        )
        .join(
            MetricSample,
            MetricSample.id
            == Prediction.metric_sample_id,
        )
        .join(
            ExperimentRun,
            ExperimentRun.id
            == Prediction.experiment_run_id,
        )
        .where(
            MetricSample.is_complete.is_(True),
            ExperimentRun.status
            == ExperimentStatus.RUNNING,
        )
        .order_by(
            Prediction.predicted_at.desc(),
            Prediction.id.desc(),
        )
        .limit(1)
    )

    result = session.execute(
        statement
    ).first()

    if result is None:
        return DemoContentionStatusRead(
            available=False,
            experiment_run_id=None,
            probability=None,
            risk_level=None,
            predicted_at=None,
            threshold=None,
            is_stale=None,
            active_transactions=None,
            blocked_sessions=None,
            waiting_locks=None,
            requests_per_second=None,
            p95_latency_ms=None,
        )

    prediction, sample = result

    return DemoContentionStatusRead(
        available=True,
        experiment_run_id=(
            prediction.experiment_run_id
        ),
        probability=float(
            prediction.probability
        ),
        risk_level=prediction.risk_level,
        predicted_at=prediction.predicted_at,
        threshold=float(
            prediction.threshold_used
        ),
        is_stale=prediction.is_stale,
        active_transactions=(
            sample.active_transactions
        ),
        blocked_sessions=(
            sample.blocked_sessions
        ),
        waiting_locks=sample.waiting_locks,
        requests_per_second=(
            float(sample.requests_per_sec)
            if sample.requests_per_sec is not None
            else None
        ),
        p95_latency_ms=(
            float(sample.p95_latency_ms)
            if sample.p95_latency_ms is not None
            else None
        ),
    )
