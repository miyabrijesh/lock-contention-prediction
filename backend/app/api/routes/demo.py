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
    ExperimentRun,
    MetricSample,
    Prediction,
)
from backend.app.models.enums import ExperimentStatus
from backend.app.schemas.booking import (
    BookingCreate,
    BookingErrorResponse,
    BookingRead,
)
from backend.app.schemas.demo import (
    DemoActiveExperimentRead,
    DemoContentionStatusRead,
)
from backend.app.services.booking_service import (
    BookingCommand,
    BookingServiceError,
    create_booking,
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