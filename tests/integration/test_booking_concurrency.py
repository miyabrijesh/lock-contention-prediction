from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import (
    AttemptOutcome,
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
        "Concurrency tests must not use the development database."
    )


@dataclass(frozen=True)
class ConcurrencyScenario:
    """Committed test records used by separate database connections."""

    show_id: int
    seat_id: int
    first_user_id: int
    second_user_id: int


@pytest.fixture(scope="module")
def concurrency_engine() -> Generator[Engine, None, None]:
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
def concurrency_scenario(
    concurrency_engine: Engine,
) -> Generator[ConcurrencyScenario, None, None]:
    """Create and later remove isolated committed test records."""

    unique_suffix = uuid4().hex[:12]

    with Session(concurrency_engine) as session:
        first_user = SyntheticUser(
            external_key=(
                f"concurrency-user-one-{unique_suffix}"
            ),
        )
        second_user = SyntheticUser(
            external_key=(
                f"concurrency-user-two-{unique_suffix}"
            ),
        )
        show = Show(
            name=f"Concurrency Test {unique_suffix}",
            venue="Isolated Test Venue",
            show_time=(
                datetime.now(UTC)
                + timedelta(days=1)
            ),
            is_experiment_show=False,
        )

        session.add_all([
            first_user,
            second_user,
            show,
        ])
        session.flush()

        seat = Seat(
            show_id=show.id,
            seat_number="C1",
            state=SeatState.AVAILABLE,
            hold_expires_at=None,
            version=0,
        )

        session.add(seat)
        session.flush()

        scenario = ConcurrencyScenario(
            show_id=show.id,
            seat_id=seat.id,
            first_user_id=first_user.id,
            second_user_id=second_user.id,
        )

        session.commit()

    try:
        yield scenario
    finally:
        with Session(concurrency_engine) as cleanup_session:
            cleanup_session.execute(
                delete(BookingAttempt).where(
                    BookingAttempt.show_id
                    == scenario.show_id
                )
            )
            cleanup_session.execute(
                delete(Booking).where(
                    Booking.show_id
                    == scenario.show_id
                )
            )
            cleanup_session.execute(
                delete(Seat).where(
                    Seat.show_id
                    == scenario.show_id
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


def execute_simultaneous_booking(
    engine: Engine,
    command: BookingCommand,
    barrier: Barrier,
) -> tuple[str, UUID | None, str | None]:
    """Wait at a barrier and execute one booking transaction."""

    with Session(
        engine,
        expire_on_commit=False,
    ) as session:
        barrier.wait(timeout=5)

        try:
            result = create_booking(
                session,
                command,
            )

            return (
                "success",
                result.booking.id,
                None,
            )
        except BookingServiceError as error:
            return (
                "error",
                None,
                error.code,
            )


def test_simultaneous_bookings_have_exactly_one_winner(
    concurrency_engine: Engine,
    concurrency_scenario: ConcurrencyScenario,
) -> None:
    """Two simultaneous requests cannot confirm the same seat."""

    barrier = Barrier(2)

    first_command = BookingCommand(
        idempotency_key=uuid4(),
        user_id=concurrency_scenario.first_user_id,
        show_id=concurrency_scenario.show_id,
        seat_id=concurrency_scenario.seat_id,
    )
    second_command = BookingCommand(
        idempotency_key=uuid4(),
        user_id=concurrency_scenario.second_user_id,
        show_id=concurrency_scenario.show_id,
        seat_id=concurrency_scenario.seat_id,
    )

    with ThreadPoolExecutor(
        max_workers=2
    ) as executor:
        first_future = executor.submit(
            execute_simultaneous_booking,
            concurrency_engine,
            first_command,
            barrier,
        )
        second_future = executor.submit(
            execute_simultaneous_booking,
            concurrency_engine,
            second_command,
            barrier,
        )

        results = [
            first_future.result(timeout=10),
            second_future.result(timeout=10),
        ]

    successful_results = [
        result
        for result in results
        if result[0] == "success"
    ]
    failed_results = [
        result
        for result in results
        if result[0] == "error"
    ]

    assert len(successful_results) == 1
    assert len(failed_results) == 1
    assert failed_results[0][2] == "SEAT_UNAVAILABLE"

    with Session(concurrency_engine) as verification_session:
        bookings = verification_session.scalars(
            select(Booking).where(
                Booking.show_id
                == concurrency_scenario.show_id,
                Booking.seat_id
                == concurrency_scenario.seat_id,
            )
        ).all()
        attempts = verification_session.scalars(
            select(BookingAttempt).where(
                BookingAttempt.show_id
                == concurrency_scenario.show_id
            )
        ).all()
        seat = verification_session.get(
            Seat,
            concurrency_scenario.seat_id,
        )

        assert len(bookings) == 1
        assert len(attempts) == 2

        assert {
            attempt.outcome
            for attempt in attempts
        } == {
            AttemptOutcome.SUCCESS,
            AttemptOutcome.SEAT_UNAVAILABLE,
        }

        assert seat is not None
        assert seat.state == SeatState.BOOKED
        assert seat.version == 1


def test_locked_seat_produces_database_lock_timeout(
    concurrency_engine: Engine,
    concurrency_scenario: ConcurrencyScenario,
) -> None:
    """A competing transaction must respect PostgreSQL lock_timeout."""

    idempotency_key = uuid4()

    locking_session = Session(
        concurrency_engine
    )

    try:
        locked_seat = locking_session.scalar(
            select(Seat)
            .where(
                Seat.id
                == concurrency_scenario.seat_id
            )
            .with_for_update()
        )

        assert locked_seat is not None

        command = BookingCommand(
            idempotency_key=idempotency_key,
            user_id=(
                concurrency_scenario.first_user_id
            ),
            show_id=concurrency_scenario.show_id,
            seat_id=concurrency_scenario.seat_id,
        )

        started_at = perf_counter()

        with Session(concurrency_engine) as contender_session:
            with pytest.raises(
                BookingServiceError
            ) as captured_error:
                create_booking(
                    contender_session,
                    command,
                )

        elapsed_seconds = perf_counter() - started_at

        assert captured_error.value.code == "LOCK_TIMEOUT"
        assert captured_error.value.status_code == 503

        minimum_expected_wait = (
            settings.db_lock_timeout_ms
            / 1000
            * 0.75
        )

        assert elapsed_seconds >= minimum_expected_wait
        assert elapsed_seconds < 8

    finally:
        locking_session.rollback()
        locking_session.close()

    with Session(concurrency_engine) as verification_session:
        booking = verification_session.scalar(
            select(Booking).where(
                Booking.idempotency_key
                == idempotency_key
            )
        )
        attempt = verification_session.scalar(
            select(BookingAttempt).where(
                BookingAttempt.idempotency_key
                == idempotency_key
            )
        )
        seat = verification_session.get(
            Seat,
            concurrency_scenario.seat_id,
        )

        assert booking is None

        assert attempt is not None
        assert attempt.outcome == AttemptOutcome.LOCK_TIMEOUT
        assert attempt.http_status == 503
        assert attempt.error_code == "LOCK_TIMEOUT"
        assert attempt.latency_ms >= 0

        assert seat is not None
        assert seat.state == SeatState.AVAILABLE
        assert seat.version == 0