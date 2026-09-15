import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models import Seat
from backend.app.models.enums import SeatState
from load_testing.run_synchronized_experiment import (
    SynchronizedRunnerError,
    _api_request,
    run_synchronized_experiment,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8001"

DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/raw/locust/interactive_demo"
)


class InteractiveDemoError(ValueError):
    """Raised when the interactive demo cannot be prepared."""


def _positive_integer(value: str) -> int:
    """Parse one positive command-line integer."""

    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Value must be an integer."
        ) from error

    if parsed_value <= 0:
        raise argparse.ArgumentTypeError(
            "Value must be greater than zero."
        )

    return parsed_value


def _seat_number(value: str) -> str:
    """Parse and normalize one auditorium seat number."""

    normalized_value = value.strip().upper()

    if not re.fullmatch(
        r"[A-Z]+[1-9][0-9]*",
        normalized_value,
    ):
        raise argparse.ArgumentTypeError(
            "Seat number must look like A1, C8 or D9."
        )

    return normalized_value


def _duration_seconds(value: str) -> int:
    """Parse a supported experiment duration."""

    parsed_value = _positive_integer(value)

    if parsed_value < 60 or parsed_value > 600:
        raise argparse.ArgumentTypeError(
            "Duration must be between 60 and 600 seconds."
        )

    return parsed_value


def _simulated_work_ms(value: str) -> int:
    """Parse a safe artificial lock-holding duration."""

    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Simulated work must be an integer."
        ) from error

    if parsed_value < 0 or parsed_value > 1500:
        raise argparse.ArgumentTypeError(
            "Simulated work must be between 0 and 1500 ms."
        )

    return parsed_value


def resolve_seat_id(
    *,
    show_id: int,
    seat_number: str,
) -> int:
    """Resolve an available seat number to its database ID."""

    with SessionLocal() as session:
        statement = select(Seat).where(
            Seat.show_id == show_id,
            Seat.seat_number == seat_number,
        )

        seat = session.scalar(statement)

        if seat is None:
            raise InteractiveDemoError(
                f"Seat {seat_number} was not found "
                f"for show {show_id}."
            )

        if seat.state != SeatState.AVAILABLE:
            raise InteractiveDemoError(
                f"Seat {seat_number} is currently "
                f"{seat.state.value}."
            )

        return seat.id


def create_interactive_experiment(
    *,
    seat_id: int,
    seat_number: str,
    show_id: int,
    user_count: int,
    duration_sec: int,
    simulated_work_ms: int,
    base_url: str,
) -> UUID:
    """Create one safe DRAFT experiment for a selected seat."""

    timestamp = datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    experiment = _api_request(
        base_url=base_url,
        method="POST",
        path="/api/v1/experiments",
        payload={
            "name": (
                "Interactive seat contention demo "
                f"{seat_number} {timestamp}"
            ),
            "show_id": show_id,
            "user_count": user_count,
            "spawn_rate_per_sec": float(user_count),
            "duration_sec": duration_sec,
            "traffic_pattern": "STEADY",
            "demand_profile": "CUSTOM",
            "hotspot_ratio": 1.0,
            "hotspot_seat_ids": [seat_id],
            "sample_interval_sec": 2,
            "simulated_work_ms": simulated_work_ms,
            "booking_mode": "EXPERIMENT_RECYCLE",
            "random_seed": 9200 + seat_id,
            "code_version": "interactive-demo-v3",
            "created_by": "interactive-demo-runner",
        },
    )

    experiment_id_value = experiment.get("id")
    experiment_status = experiment.get("status")
    experiment_show_id = experiment.get("show_id")
    hotspot_seat_ids = experiment.get(
        "hotspot_seat_ids"
    )

    if not isinstance(experiment_id_value, str):
        raise InteractiveDemoError(
            "The experiment API did not return an ID."
        )

    try:
        experiment_id = UUID(experiment_id_value)
    except ValueError as error:
        raise InteractiveDemoError(
            "The experiment API returned an invalid ID."
        ) from error

    if experiment_status != "DRAFT":
        raise InteractiveDemoError(
            "The new experiment was not created in DRAFT state."
        )

    if experiment_show_id != show_id:
        raise InteractiveDemoError(
            "The new experiment uses the wrong show."
        )

    if hotspot_seat_ids != [seat_id]:
        raise InteractiveDemoError(
            "The new experiment uses the wrong hotspot seat."
        )

    return experiment_id


def parse_arguments() -> argparse.Namespace:
    """Parse interactive-demo command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create and run a safe synchronized contention "
            "experiment targeting one customer-selected seat."
        )
    )

    parser.add_argument(
        "--seat-number",
        type=_seat_number,
        required=True,
        help=(
            "Experiment-show seat selected by the customer, "
            "for example D9."
        ),
    )

    parser.add_argument(
        "--show-id",
        type=_positive_integer,
        default=2,
        help="Experiment show ID. Default: 2.",
    )

    parser.add_argument(
        "--users",
        type=_positive_integer,
        default=2,
        help=(
            "Concurrent Locust users. Default: 2. "
            "Keep this below the database pool limit."
        ),
    )

    parser.add_argument(
        "--duration",
        type=_duration_seconds,
        default=120,
        help="Experiment duration in seconds. Default: 120.",
    )

    parser.add_argument(
        "--simulated-work-ms",
        type=_simulated_work_ms,
        default=600,
        help=(
            "Time each Locust transaction holds the seat "
            "lock. Default: 600 ms."
        ),
    )

    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "Running FastAPI address. "
            f"Default: {DEFAULT_BASE_URL}."
        ),
    )

    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=(
            "Base directory for Locust CSV output. "
            "Default: data/raw/locust/interactive_demo."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Create and run one interactive contention demo."""

    arguments = parse_arguments()

    if not settings.experiment_mode_enabled:
        print(
            "Interactive demo error: Experiment mode is "
            "disabled. Set EXPERIMENT_MODE_ENABLED=true.",
            file=sys.stderr,
        )
        return 2

    if arguments.users >= settings.db_pool_max:
        print(
            "Interactive demo error: Locust user count must "
            "remain below DB_POOL_MAX.",
            file=sys.stderr,
        )
        return 2

    try:
        seat_id = resolve_seat_id(
            show_id=arguments.show_id,
            seat_number=arguments.seat_number,
        )

        experiment_id = create_interactive_experiment(
            seat_id=seat_id,
            seat_number=arguments.seat_number,
            show_id=arguments.show_id,
            user_count=arguments.users,
            duration_sec=arguments.duration,
            simulated_work_ms=(
                arguments.simulated_work_ms
            ),
            base_url=arguments.base_url,
        )

        print("Interactive demo experiment created:")
        print(f"  Experiment: {experiment_id}")
        print(f"  Show: {arguments.show_id}")
        print(
            "  Contested seat: "
            f"{arguments.seat_number} "
            f"(database ID {seat_id})"
        )
        print(f"  Locust users: {arguments.users}")
        print(
            "  Simulated lock work: "
            f"{arguments.simulated_work_ms} ms"
        )
        print(f"  Duration: {arguments.duration} seconds")

        run_synchronized_experiment(
            experiment_run_id=experiment_id,
            base_url=arguments.base_url,
            actor="interactive-demo-runner",
            output_directory=(
                arguments.output_directory
            ),
        )

        return 0

    except (
        InteractiveDemoError,
        SynchronizedRunnerError,
    ) as error:
        print(
            f"Interactive demo error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())