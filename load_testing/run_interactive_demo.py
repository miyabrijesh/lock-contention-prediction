import argparse
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from time import sleep
from uuid import UUID

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models import (
    Booking,
    Seat,
)
from backend.app.models.enums import (
    BookingStatus,
    DemoScenarioName,
    DemoSessionStatus,
    SeatState,
)
from backend.app.services.demo_session_service import (
    DemoSessionServiceError,
    create_waiting_demo_session,
    finish_demo_session,
    read_demo_session,
    start_demo_session,
)
from load_testing.run_synchronized_experiment import (
    SynchronizedRunnerError,
    _api_request,
    run_synchronized_experiment,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8001"

DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/raw/locust/interactive_demo"
)

COMPLETION_GRACE_SECONDS = 5.0
SELECTION_POLL_SECONDS = 0.5
DEFAULT_SELECTION_TIMEOUT_SECONDS = 600


class ScenarioChoice(str, Enum):
    """Command-line scenario selection."""

    AUTO = "auto"
    BASELINE = "baseline"
    CONTENTION = "contention"


@dataclass(frozen=True)
class DemoScenario:
    """Validated workload for one demonstration scenario."""

    name: DemoScenarioName
    user_count: int
    simulated_work_ms: int
    demand_profile: str
    hotspot_ratio: float
    expected_risk: str
    description: str


DEMO_SCENARIOS = {
    DemoScenarioName.BASELINE: DemoScenario(
        name=DemoScenarioName.BASELINE,
        user_count=5,
        simulated_work_ms=20,
        demand_profile="CUSTOM",
        hotspot_ratio=0.1,
        expected_risk="LOW",
        description=(
            "Low-pressure workload used to demonstrate "
            "normal booking operation."
        ),
    ),
    DemoScenarioName.CONTENTION: DemoScenario(
        name=DemoScenarioName.CONTENTION,
        user_count=2,
        simulated_work_ms=600,
        demand_profile="CUSTOM",
        hotspot_ratio=1.0,
        expected_risk="HIGH",
        description=(
            "Focused workload that deliberately creates "
            "row-lock contention on the selected seats."
        ),
    ),
}


class InteractiveDemoError(ValueError):
    """Raised when the interactive demo cannot proceed."""


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


def _duration_seconds(value: str) -> int:
    """Parse a supported experiment duration."""

    parsed_value = _positive_integer(value)

    if parsed_value < 60 or parsed_value > 600:
        raise argparse.ArgumentTypeError(
            "Duration must be between 60 and 600 seconds."
        )

    return parsed_value


def _selection_timeout_seconds(value: str) -> int:
    """Parse the browser-selection timeout."""

    parsed_value = _positive_integer(value)

    if parsed_value < 30 or parsed_value > 1800:
        raise argparse.ArgumentTypeError(
            "Selection timeout must be between "
            "30 and 1800 seconds."
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

    if parsed_value < 0 or parsed_value > 1000:
        raise argparse.ArgumentTypeError(
            "Simulated work must be between 0 and 1000 ms."
        )

    return parsed_value


def _scenario_choice(value: str) -> ScenarioChoice:
    """Parse one scenario command-line choice."""

    normalized_value = value.strip().lower()

    try:
        return ScenarioChoice(normalized_value)
    except ValueError as error:
        supported_values = ", ".join(
            choice.value
            for choice in ScenarioChoice
        )

        raise argparse.ArgumentTypeError(
            "Scenario must be one of: "
            f"{supported_values}."
        ) from error


def choose_scenario(
    choice: ScenarioChoice,
) -> DemoScenario:
    """Resolve an explicit or random scenario."""

    if choice == ScenarioChoice.AUTO:
        scenario_name = secrets.choice(
            (
                DemoScenarioName.BASELINE,
                DemoScenarioName.CONTENTION,
            )
        )
    elif choice == ScenarioChoice.BASELINE:
        scenario_name = DemoScenarioName.BASELINE
    else:
        scenario_name = DemoScenarioName.CONTENTION

    return DEMO_SCENARIOS[scenario_name]


def create_browser_session(
    *,
    show_id: int,
    scenario: DemoScenario,
    selection_timeout_seconds: int,
) -> UUID:
    """Create a session that waits for browser seat selection."""

    with SessionLocal() as session:
        demo_session = create_waiting_demo_session(
            session,
            show_id=show_id,
            scenario=scenario.name,
            lifetime_seconds=(
                selection_timeout_seconds
            ),
        )

        return demo_session.id


def wait_for_browser_selection(
    *,
    demo_session_id: UUID,
) -> list[int]:
    """Wait until the browser submits a valid seat selection."""

    while True:
        with SessionLocal() as session:
            demo_session = read_demo_session(
                session,
                demo_session_id,
            )

            status = demo_session.status
            expires_at = demo_session.expires_at
            selected_seat_ids = list(
                demo_session.selected_seat_ids
            )

        if status == DemoSessionStatus.READY:
            return selected_seat_ids

        if status in {
            DemoSessionStatus.COMPLETED,
            DemoSessionStatus.FAILED,
            DemoSessionStatus.CANCELLED,
        }:
            raise InteractiveDemoError(
                "The browser-selection session ended "
                f"with status {status.value}."
            )

        if datetime.now(UTC) >= expires_at:
            raise InteractiveDemoError(
                "Timed out while waiting for the browser "
                "to select seats."
            )

        sleep(SELECTION_POLL_SECONDS)


def resolve_selected_seats(
    *,
    show_id: int,
    seat_ids: list[int],
) -> list[str]:
    """Return selected seat numbers in browser order."""

    with SessionLocal() as session:
        seats = list(
            session.scalars(
                select(Seat).where(
                    Seat.id.in_(seat_ids)
                )
            ).all()
        )

        seats_by_id = {
            seat.id: seat
            for seat in seats
        }

        if set(seats_by_id) != set(seat_ids):
            raise InteractiveDemoError(
                "One or more selected seats were not found."
            )

        ordered_seats = [
            seats_by_id[seat_id]
            for seat_id in seat_ids
        ]

        if any(
            seat.show_id != show_id
            for seat in ordered_seats
        ):
            raise InteractiveDemoError(
                "One or more selected seats belong "
                "to the wrong show."
            )

        if any(
            seat.state != SeatState.AVAILABLE
            for seat in ordered_seats
        ):
            raise InteractiveDemoError(
                "One or more selected seats are no "
                "longer available."
            )

        return [
            seat.seat_number
            for seat in ordered_seats
        ]


def create_interactive_experiment(
    *,
    seat_ids: list[int],
    seat_numbers: list[str],
    show_id: int,
    scenario: DemoScenario,
    user_count: int,
    duration_sec: int,
    simulated_work_ms: int,
    demand_profile: str,
    base_url: str,
) -> UUID:
    """Create one DRAFT experiment using the selected seats."""

    timestamp = datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    seat_label = "-".join(seat_numbers)

    experiment = _api_request(
        base_url=base_url,
        method="POST",
        path="/api/v1/experiments",
        payload={
            "name": (
                "Interactive prediction demo "
                f"{scenario.name.value.lower()} "
                f"{seat_label} {timestamp}"
            ),
            "show_id": show_id,
            "user_count": user_count,
            "spawn_rate_per_sec": float(user_count),
            "duration_sec": duration_sec,
            "traffic_pattern": "STEADY",
            "demand_profile": demand_profile,
            "hotspot_ratio": scenario.hotspot_ratio,
            "hotspot_seat_ids": seat_ids,
            "sample_interval_sec": 2,
            "simulated_work_ms": simulated_work_ms,
            "booking_mode": "EXPERIMENT_RECYCLE",
            "random_seed": (
                9200
                + sum(seat_ids)
            ),
            "code_version": "interactive-demo-v6",
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
            "The experiment was not created in DRAFT state."
        )

    if experiment_show_id != show_id:
        raise InteractiveDemoError(
            "The experiment uses the wrong show."
        )

    if set(hotspot_seat_ids or []) != set(seat_ids):
        raise InteractiveDemoError(
            "The experiment uses the wrong hotspot seats."
        )

    return experiment_id


def all_browser_bookings_confirmed(
    *,
    experiment_run_id: UUID,
    seat_ids: list[int],
) -> bool:
    """Return whether every browser-selected seat is confirmed."""

    with SessionLocal() as session:
        statement = select(
            Booking.seat_id
        ).where(
            Booking.experiment_run_id
            == experiment_run_id,
            Booking.seat_id.in_(seat_ids),
            Booking.status
            == BookingStatus.CONFIRMED,
        )

        confirmed_seat_ids = set(
            session.scalars(statement).all()
        )

        return set(seat_ids).issubset(
            confirmed_seat_ids
        )


def mark_demo_session_running(
    *,
    demo_session_id: UUID,
    experiment_run_id: UUID,
) -> None:
    """Link the prepared experiment to the browser session."""

    with SessionLocal() as session:
        start_demo_session(
            session,
            demo_session_id=demo_session_id,
            experiment_run_id=experiment_run_id,
        )


def mark_demo_session_finished(
    *,
    demo_session_id: UUID,
    final_status: DemoSessionStatus,
) -> None:
    """Apply a final demo-session status."""

    with SessionLocal() as session:
        finish_demo_session(
            session,
            demo_session_id=demo_session_id,
            final_status=final_status,
        )


def safely_finish_demo_session(
    *,
    demo_session_id: UUID | None,
    final_status: DemoSessionStatus,
) -> None:
    """Finish a demo session without hiding the original error."""

    if demo_session_id is None:
        return

    try:
        mark_demo_session_finished(
            demo_session_id=demo_session_id,
            final_status=final_status,
        )
    except DemoSessionServiceError as error:
        print(
            "Unable to update demo-session status: "
            f"{error.message}",
            file=sys.stderr,
        )


def parse_arguments() -> argparse.Namespace:
    """Parse interactive-demo command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Randomly run a LOW or HIGH contention "
            "demonstration using 1–5 consecutive seats "
            "selected in the browser."
        )
    )

    parser.add_argument(
        "--scenario",
        type=_scenario_choice,
        default=ScenarioChoice.AUTO,
        help=(
            "Scenario: auto, baseline or contention. "
            "Default: auto randomly chooses baseline "
            "or contention."
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
        default=None,
        help=(
            "Optional concurrent-user override. "
            "This removes the expected-risk guarantee."
        ),
    )

    parser.add_argument(
        "--duration",
        type=_duration_seconds,
        default=120,
        help=(
            "Maximum experiment duration. "
            "Default: 120 seconds."
        ),
    )

    parser.add_argument(
        "--selection-timeout",
        type=_selection_timeout_seconds,
        default=DEFAULT_SELECTION_TIMEOUT_SECONDS,
        help=(
            "Seconds allowed for browser seat selection. "
            "Default: 600."
        ),
    )

    parser.add_argument(
        "--simulated-work-ms",
        type=_simulated_work_ms,
        default=None,
        help=(
            "Optional lock-work override. "
            "This removes the expected-risk guarantee."
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
    """Run one browser-driven prediction demonstration."""

    arguments = parse_arguments()
    demo_session_id: UUID | None = None

    if not settings.experiment_mode_enabled:
        print(
            "Interactive demo error: Experiment mode is "
            "disabled. Set EXPERIMENT_MODE_ENABLED=true.",
            file=sys.stderr,
        )
        return 2

    scenario = choose_scenario(
        arguments.scenario
    )

    user_count = (
        arguments.users
        if arguments.users is not None
        else scenario.user_count
    )

    simulated_work_ms = (
        arguments.simulated_work_ms
        if arguments.simulated_work_ms is not None
        else scenario.simulated_work_ms
    )

    uses_manual_override = (
        arguments.users is not None
        or arguments.simulated_work_ms is not None
    )

    demand_profile = (
        "CUSTOM"
        if uses_manual_override
        else scenario.demand_profile
    )

    expected_risk = (
        "NOT GUARANTEED — manual override supplied"
        if uses_manual_override
        else scenario.expected_risk
    )

    if user_count >= settings.db_pool_max:
        print(
            "Interactive demo error: Locust user count must "
            "remain below DB_POOL_MAX.",
            file=sys.stderr,
        )
        return 2

    try:
        demo_session_id = create_browser_session(
            show_id=arguments.show_id,
            scenario=scenario,
            selection_timeout_seconds=(
                arguments.selection_timeout
            ),
        )

        print("Interactive demo session created:")
        print(f"  Session: {demo_session_id}")
        print(
            "  Random scenario: "
            f"{scenario.name.value}"
        )
        print(
            "  Expected model risk: "
            f"{expected_risk}"
        )
        print(f"  Description: {scenario.description}")
        print(f"  Show: {arguments.show_id}")
        print(
            "  Selection timeout: "
            f"{arguments.selection_timeout} seconds"
        )
        print()
        print(
            "Open the experiment show in the browser and "
            "select 1–5 consecutive available seats."
        )
        print("Waiting for browser seat selection...")

        seat_ids = wait_for_browser_selection(
            demo_session_id=demo_session_id,
        )

        seat_numbers = resolve_selected_seats(
            show_id=arguments.show_id,
            seat_ids=seat_ids,
        )

        print(
            "Browser selection received: "
            + ", ".join(seat_numbers)
        )

        experiment_id = create_interactive_experiment(
            seat_ids=seat_ids,
            seat_numbers=seat_numbers,
            show_id=arguments.show_id,
            scenario=scenario,
            user_count=user_count,
            duration_sec=arguments.duration,
            simulated_work_ms=simulated_work_ms,
            demand_profile=demand_profile,
            base_url=arguments.base_url,
        )

        mark_demo_session_running(
            demo_session_id=demo_session_id,
            experiment_run_id=experiment_id,
        )

        print("Interactive experiment prepared:")
        print(f"  Experiment: {experiment_id}")
        print(
            "  Selected seats: "
            + ", ".join(seat_numbers)
        )
        print(f"  Locust users: {user_count}")
        print(
            "  Simulated lock work: "
            f"{simulated_work_ms} ms"
        )
        print(
            "  Hotspot ratio: "
            f"{scenario.hotspot_ratio:.2f}"
        )
        print(
            "  Completion rule: stop "
            f"{COMPLETION_GRACE_SECONDS:.0f} seconds "
            "after every selected seat is confirmed"
        )

        run_synchronized_experiment(
            experiment_run_id=experiment_id,
            base_url=arguments.base_url,
            actor="interactive-demo-runner",
            output_directory=(
                arguments.output_directory
            ),
            stop_condition=lambda: (
                all_browser_bookings_confirmed(
                    experiment_run_id=experiment_id,
                    seat_ids=seat_ids,
                )
            ),
            stop_grace_seconds=(
                COMPLETION_GRACE_SECONDS
            ),
        )

        mark_demo_session_finished(
            demo_session_id=demo_session_id,
            final_status=(
                DemoSessionStatus.COMPLETED
            ),
        )

        print("Interactive demo session completed.")

        return 0

    except KeyboardInterrupt:
        safely_finish_demo_session(
            demo_session_id=demo_session_id,
            final_status=(
                DemoSessionStatus.CANCELLED
            ),
        )

        print(
            "\nInteractive demo cancelled by the operator.",
            file=sys.stderr,
        )

        return 130

    except (
        DemoSessionServiceError,
        InteractiveDemoError,
        SynchronizedRunnerError,
    ) as error:
        safely_finish_demo_session(
            demo_session_id=demo_session_id,
            final_status=DemoSessionStatus.FAILED,
        )

        message = (
            error.message
            if isinstance(
                error,
                DemoSessionServiceError,
            )
            else str(error)
        )

        print(
            f"Interactive demo error: {message}",
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())
