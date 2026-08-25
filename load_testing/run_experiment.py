import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models import (
    ExperimentRun,
    Seat,
    Show,
    SyntheticUser,
)
from backend.app.models.enums import ExperimentStatus
from load_testing.demand_profiles import (
    DemandProfileError,
    DemandProfileName,
    parse_demand_profile_name,
    validate_demand_configuration,
)
from load_testing.traffic_patterns import (
    TrafficPattern,
    TrafficPatternError,
    parse_traffic_pattern,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_HOST = "http://127.0.0.1:8000"
DEFAULT_OUTPUT_DIRECTORY = Path("data/raw/locust")


class WorkloadRunnerError(Exception):
    """A safe, expected workload-runner configuration error."""


@dataclass(frozen=True)
class PreparedWorkload:
    """Validated workload configuration loaded from PostgreSQL."""

    experiment_run_id: UUID
    show_id: int
    user_count: int
    spawn_rate_per_sec: Decimal
    duration_sec: int
    traffic_pattern: TrafficPattern
    hotspot_ratio: Decimal
    hotspot_seat_ids: tuple[int, ...]
    seat_ids: tuple[int, ...]
    user_ids: tuple[int, ...]
    random_seed: int
    demand_profile: DemandProfileName = DemandProfileName.CUSTOM


def _validate_host(host: str) -> str:
    """Allow only an HTTP or HTTPS target URL."""

    normalized_host = host.strip().rstrip("/")
    parsed_host = urlparse(normalized_host)

    if (
        parsed_host.scheme not in {"http", "https"}
        or not parsed_host.netloc
    ):
        raise WorkloadRunnerError(
            "The Locust host must be a valid HTTP or HTTPS URL."
        )

    return normalized_host


def _get_internal_token() -> str:
    """Return the internal token without printing it."""

    token = settings.internal_api_token.strip()
    lowered_token = token.lower()

    if (
        len(token) < 32
        or "replace" in lowered_token
        or "change" in lowered_token
    ):
        raise WorkloadRunnerError(
            "INTERNAL_API_TOKEN is missing or still uses "
            "an insecure placeholder value."
        )

    return token


def _load_experiment(
    session: Session,
    experiment_run_id: UUID,
) -> ExperimentRun:
    """Load and validate the selected running experiment."""

    experiment = session.get(
        ExperimentRun,
        experiment_run_id,
    )

    if experiment is None:
        raise WorkloadRunnerError(
            f"Experiment {experiment_run_id} was not found."
        )

    if experiment.status != ExperimentStatus.RUNNING:
        raise WorkloadRunnerError(
            "The selected experiment must have RUNNING status. "
            f"Current status: {experiment.status.value}."
        )

    if experiment.booking_mode != "EXPERIMENT_RECYCLE":
        raise WorkloadRunnerError(
            "Checkpoint 25 requires booking_mode "
            "EXPERIMENT_RECYCLE."
        )

    return experiment


def _validate_experiment_show(
    session: Session,
    experiment: ExperimentRun,
) -> Show:
    """Verify that the workload targets an experiment show."""

    show = session.get(
        Show,
        experiment.show_id,
    )

    if show is None:
        raise WorkloadRunnerError(
            f"Show {experiment.show_id} was not found."
        )

    if not show.is_experiment_show:
        raise WorkloadRunnerError(
            "The selected show is not marked as an experiment show."
        )

    return show


def _load_seat_ids(
    session: Session,
    experiment: ExperimentRun,
) -> tuple[int, ...]:
    """Load all seats belonging to the experiment show."""

    statement = (
        select(Seat.id)
        .where(Seat.show_id == experiment.show_id)
        .order_by(Seat.id)
    )

    seat_ids = tuple(
        session.scalars(statement).all()
    )

    if not seat_ids:
        raise WorkloadRunnerError(
            "The experiment show does not contain any seats."
        )

    return seat_ids


def _load_user_ids(
    session: Session,
) -> tuple[int, ...]:
    """Load the synthetic users available to Locust."""

    statement = (
        select(SyntheticUser.id)
        .order_by(SyntheticUser.id)
    )

    user_ids = tuple(
        session.scalars(statement).all()
    )

    if not user_ids:
        raise WorkloadRunnerError(
            "No synthetic users are available."
        )

    return user_ids


def prepare_workload(
    session: Session,
    experiment_run_id: UUID,
) -> PreparedWorkload:
    """Build a validated Locust workload from database records."""

    if not settings.experiment_mode_enabled:
        raise WorkloadRunnerError(
            "Experiment mode is disabled. Set "
            "EXPERIMENT_MODE_ENABLED=true before running a workload."
        )

    experiment = _load_experiment(
        session,
        experiment_run_id,
    )

    _validate_experiment_show(
        session,
        experiment,
    )

    seat_ids = _load_seat_ids(
        session,
        experiment,
    )

    user_ids = _load_user_ids(session)

    hotspot_seat_ids = tuple(
        experiment.hotspot_seat_ids
    )

    unknown_hotspot_seats = (
        set(hotspot_seat_ids) - set(seat_ids)
    )

    if unknown_hotspot_seats:
        formatted_ids = ", ".join(
            str(seat_id)
            for seat_id in sorted(unknown_hotspot_seats)
        )

        raise WorkloadRunnerError(
            "The experiment contains hotspot seats that do not "
            f"belong to its show: {formatted_ids}."
        )

    if experiment.user_count > len(user_ids):
        raise WorkloadRunnerError(
            "The experiment requests more concurrent users than "
            "the number of available synthetic users."
        )

    try:
        traffic_pattern = parse_traffic_pattern(
            experiment.traffic_pattern
        )
    except TrafficPatternError as error:
        raise WorkloadRunnerError(
            "The experiment contains an unsupported "
            f"traffic pattern: {experiment.traffic_pattern}."
        ) from error

    try:
        demand_profile = parse_demand_profile_name(
            experiment.demand_profile
        )

        validate_demand_configuration(
            profile_name=demand_profile,
            hotspot_ratio=experiment.hotspot_ratio,
            hotspot_seat_count=len(
                hotspot_seat_ids
            ),
        )
    except DemandProfileError as error:
        raise WorkloadRunnerError(
            "The experiment contains an invalid demand "
            f"profile configuration: {error}"
        ) from error

    return PreparedWorkload(
        experiment_run_id=experiment.id,
        show_id=experiment.show_id,
        user_count=experiment.user_count,
        spawn_rate_per_sec=experiment.spawn_rate_per_sec,
        duration_sec=experiment.duration_sec,
        traffic_pattern=traffic_pattern,
        hotspot_ratio=experiment.hotspot_ratio,
        hotspot_seat_ids=hotspot_seat_ids,
        seat_ids=seat_ids,
        user_ids=user_ids,
        random_seed=experiment.random_seed,
        demand_profile=demand_profile,
    )


def build_child_environment(
    workload: PreparedWorkload,
) -> dict[str, str]:
    """Create the environment supplied to the Locust process."""

    child_environment = os.environ.copy()

    child_environment.update({
        "LOAD_EXPERIMENT_RUN_ID": str(
            workload.experiment_run_id
        ),
        "LOAD_SHOW_ID": str(workload.show_id),
        "LOAD_USER_IDS": json.dumps(
            workload.user_ids
        ),
        "LOAD_SEAT_IDS": json.dumps(
            workload.seat_ids
        ),
        "LOAD_HOTSPOT_SEAT_IDS": json.dumps(
            workload.hotspot_seat_ids
        ),
        "LOAD_HOTSPOT_RATIO": str(
            workload.hotspot_ratio
        ),
        "LOAD_RANDOM_SEED": str(
            workload.random_seed
        ),
        "LOAD_MAXIMUM_USERS": str(
            workload.user_count
        ),
        "LOAD_SPAWN_RATE_PER_SEC": str(
            workload.spawn_rate_per_sec
        ),
        "LOAD_DURATION_SEC": str(
            workload.duration_sec
        ),
        "LOAD_TRAFFIC_PATTERN": (
            workload.traffic_pattern.value
        ),
        "LOAD_DEMAND_PROFILE": (
            workload.demand_profile.value
        ),
        "LOAD_INTERNAL_API_TOKEN": _get_internal_token(),
    })

    return child_environment


def build_locust_command(
    workload: PreparedWorkload,
    *,
    host: str,
    output_prefix: Path,
) -> list[str]:
    """Build the headless Locust command without executing it."""

    return [
        sys.executable,
        "-m",
        "locust",
        "-f",
        "load_testing/locustfile.py",
        "--headless",
        "--host",
        host,
        "--users",
        str(workload.user_count),
        "--spawn-rate",
        str(workload.spawn_rate_per_sec),
        "--run-time",
        f"{workload.duration_sec}s",
        "--stop-timeout",
        "5",
        "--csv",
        str(output_prefix),
        "--csv-full-history",
        "--only-summary",
    ]


def parse_arguments() -> argparse.Namespace:
    """Read command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run one controlled Locust reservation workload "
            "using a RUNNING experiment configuration."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the RUNNING experiment.",
    )

    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            "Reservation API address. "
            f"Default: {DEFAULT_HOST}"
        ),
    )

    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=(
            "Directory where Locust CSV files will be written. "
            "Default: data/raw/locust"
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Prepare and execute one headless Locust workload."""

    arguments = parse_arguments()

    try:
        host = _validate_host(arguments.host)

        with SessionLocal() as session:
            workload = prepare_workload(
                session,
                arguments.experiment_run_id,
            )

        output_directory = arguments.output_directory

        if not output_directory.is_absolute():
            output_directory = (
                PROJECT_ROOT / output_directory
            )

        experiment_output_directory = (
            output_directory
            / str(workload.experiment_run_id)
        )

        experiment_output_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        output_prefix = (
            experiment_output_directory / "locust"
        )

        child_environment = build_child_environment(
            workload
        )

        command = build_locust_command(
            workload,
            host=host,
            output_prefix=output_prefix,
        )

        print("Prepared controlled workload:")
        print(
            f"  Experiment: {workload.experiment_run_id}"
        )
        print(f"  Show: {workload.show_id}")
        print(f"  Users: {workload.user_count}")
        print(
            "  Spawn rate: "
            f"{workload.spawn_rate_per_sec} users/second"
        )
        print(
            f"  Duration: {workload.duration_sec} seconds"
        )
        print(
            "  Traffic pattern: "
            f"{workload.traffic_pattern.value}"
        )
        print(
            "  Demand profile: "
            f"{workload.demand_profile.value}"
        )
        print(
            "  Hotspot ratio: "
            f"{workload.hotspot_ratio}"
        )
        print(
            f"  Output directory: "
            f"{experiment_output_directory}"
        )
        print("Starting Locust...")

        completed_process = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=child_environment,
            check=False,
        )

        if completed_process.returncode != 0:
            print(
                "Locust finished with an error.",
                file=sys.stderr,
            )

        return completed_process.returncode

    except WorkloadRunnerError as error:
        print(
            f"Workload configuration error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())