from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    Booking,
    BookingAttempt,
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    AttemptOutcome,
    BookingStatus,
    ExperimentStatus,
    SeatState,
)
from backend.app.services.booking_service import (
    BookingCommand,
    BookingServiceError,
    create_booking,
)


DEVELOPMENT_DATABASE_URL = make_url(
    settings.database_url
)

CONCURRENCY_DATABASE_URL = DEVELOPMENT_DATABASE_URL.set(
    database="lock_contention_test_db"
)

if (
    CONCURRENCY_DATABASE_URL.database
    == DEVELOPMENT_DATABASE_URL.database
):
    raise RuntimeError(
        "Recycling concurrency tests must not use "
        "the development database."
    )


@dataclass(frozen=True)
class RecyclingScenario:
    """Committed records visible to concurrent connections."""

    experiment_run_id: UUID
    show_id: int
    seat_id: int
    first_user_id: int
    second_user_id: int
    initial_seat_version: int


@pytest.fixture(scope="module")
def recycling_engine() -> Generator[Engine, None, None]:
    """Create a test engine with production timeout settings."""

    engine = create_engine(
        CONCURRENCY_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "options": (
                f"-c lock_timeout="
                f"{settings.db_lock_timeout_ms}ms "
                f"-c statement_timeout="
                f"{settings.db_statement_timeout_ms}ms"
            )
        },
    )

    yield engine

    engine.dispose()


@pytest.fixture
def recycling_scenario(
    recycling_engine: Engine,
) -> Generator[RecyclingScenario, None, None]:
    """Create and remove a committed recycling scenario."""

    unique_suffix = uuid4().hex[:12]

    with Session(recycling_engine) as session:
        first_user = SyntheticUser(
            external_key=(
                f"recycle-user-one-{unique_suffix}"
            ),
        )
        second_user = SyntheticUser(
            external_key=(
                f"recycle-user-two-{unique_suffix}"
            ),
        )
        show = Show(
            name=f"Recycling Test {unique_suffix}",
            venue="Isolated Recycling Test Venue",
            show_time=(
                datetime.now(UTC)
                + timedelta(days=1)
            ),
            is_experiment_show=True,
        )

        session.add_all([
            first_user,
            second_user,
            show,
        ])
        session.flush()

        seat = Seat(
            show_id=show.id,
            seat_number="R1",
            state=SeatState.AVAILABLE,
            hold_expires_at=None,
            version=0,
        )

        session.add(seat)
        session.flush()

        experiment = ExperimentRun(
            name=f"Recycling Experiment {unique_suffix}",
            show_id=show.id,
            status=ExperimentStatus.RUNNING,
            user_count=2,
            spawn_rate_per_sec=Decimal("2.00"),
            duration_sec=60,
            hotspot_ratio=Decimal("1.0000"),
            hotspot_seat_ids=[seat.id],
            sample_interval_sec=2,
            simulated_work_ms=400,
            booking_mode="EXPERIMENT_RECYCLE",
            random_seed=42,
            code_version="pytest",
            started_at=datetime.now(UTC),
            created_by="pytest",
        )

        session.add(experiment)
        session.flush()

        scenario = RecyclingScenario(
            experiment_run_id=experiment.id,
            show_id=show.id,
            seat_id=seat.id,
            first_user_id=first_user.id,
            second_user_id=second_user.id,
            initial_seat_version=seat.version,
        )

        session.commit()

    try:
        yield scenario
    finally:
        with Session(recycling_engine) as cleanup_session:
            cleanup_session.execute(
                delete(BookingAttempt).where(
                    BookingAttempt.experiment_run_id
                    == scenario.experiment_run_id
                )
            )
            cleanup_session.execute(
                delete(Booking).where(
                    Booking.experiment_run_id
                    == scenario.experiment_run_id
                )
            )
            cleanup_session.execute(
                delete(ExperimentRun).where(
                    ExperimentRun.id
                    == scenario.experiment_run_id
                )
            )
            cleanup_session.execute(
                delete(Seat).where(
                    Seat.show_id == scenario.show_id
                )
            )
            cleanup_session.execute(
                delete(Show).where(
                    Show.id == scenario.show_id
                )
            )
            cleanup_session.execute(
                delete(SyntheticUser).where(
                    SyntheticUser.id.in_([
                        scenario.first_user_id,
                        scenario.second_user_id,
                    ])
                )
            )
            cleanup_session.commit()


def execute_recycle_booking(
    engine: Engine,
    command: BookingCommand,
    barrier: Barrier,
) -> tuple[str, UUID | None, str | None, float]:
    """Run one recycling request using its own connection."""

    with Session(
        engine,
        expire_on_commit=False,
    ) as session:
        barrier.wait(timeout=5)
        started_at = perf_counter()

        try:
            result = create_booking(
                session,
                command,
            )

            elapsed_seconds = (
                perf_counter() - started_at
            )

            return (
                "success",
                result.booking.id,
                None,
                elapsed_seconds,
            )

        except BookingServiceError as error:
            elapsed_seconds = (
                perf_counter() - started_at
            )

            return (
                "error",
                None,
                error.code,
                elapsed_seconds,
            )


def test_simultaneous_recycle_bookings_both_succeed(
    recycling_engine: Engine,
    recycling_scenario: RecyclingScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests serialize on one row and both recycle it."""

    monkeypatch.setattr(
        settings,
        "experiment_mode_enabled",
        True,
    )

    barrier = Barrier(2)

    first_command = BookingCommand(
        idempotency_key=uuid4(),
        user_id=recycling_scenario.first_user_id,
        show_id=recycling_scenario.show_id,
        seat_id=recycling_scenario.seat_id,
        experiment_run_id=(
            recycling_scenario.experiment_run_id
        ),
    )
    second_command = BookingCommand(
        idempotency_key=uuid4(),
        user_id=recycling_scenario.second_user_id,
        show_id=recycling_scenario.show_id,
        seat_id=recycling_scenario.seat_id,
        experiment_run_id=(
            recycling_scenario.experiment_run_id
        ),
    )

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:
        first_future = executor.submit(
            execute_recycle_booking,
            recycling_engine,
            first_command,
            barrier,
        )
        second_future = executor.submit(
            execute_recycle_booking,
            recycling_engine,
            second_command,
            barrier,
        )

        results = [
            first_future.result(timeout=10),
            second_future.result(timeout=10),
        ]

    assert all(
        result[0] == "success"
        for result in results
    )
    assert all(
        result[1] is not None
        for result in results
    )
    assert all(
        result[2] is None
        for result in results
    )

    elapsed_times = sorted(
        result[3]
        for result in results
    )

    # One transaction holds the lock for about 400 ms.
    assert elapsed_times[0] >= 0.30

    # The second transaction waits for the first lock and then
    # performs its own 400 ms simulated work.
    assert elapsed_times[1] >= 0.65

    # Their duration difference demonstrates lock waiting.
    assert (
        elapsed_times[1] - elapsed_times[0]
        >= 0.25
    )

    with Session(recycling_engine) as verification_session:
        bookings = list(
            verification_session.scalars(
                select(Booking).where(
                    Booking.experiment_run_id
                    == recycling_scenario.experiment_run_id
                )
            )
        )
        attempts = list(
            verification_session.scalars(
                select(BookingAttempt).where(
                    BookingAttempt.experiment_run_id
                    == recycling_scenario.experiment_run_id
                )
            )
        )
        seat = verification_session.get(
            Seat,
            recycling_scenario.seat_id,
        )

        assert len(bookings) == 2
        assert len(attempts) == 2

        assert all(
            booking.status
            == BookingStatus.CANCELLED
            for booking in bookings
        )
        assert all(
            booking.cancelled_at is not None
            for booking in bookings
        )
        assert all(
            attempt.outcome
            == AttemptOutcome.SUCCESS
            for attempt in attempts
        )

        attempt_latencies = sorted(
            attempt.latency_ms
            for attempt in attempts
        )

        assert attempt_latencies[0] >= Decimal(
            "300.000"
        )
        assert attempt_latencies[1] >= Decimal(
            "650.000"
        )

        assert seat is not None
        assert seat.state == SeatState.AVAILABLE
        assert seat.hold_expires_at is None
        assert seat.version == (
            recycling_scenario.initial_seat_version
            + 2
        )