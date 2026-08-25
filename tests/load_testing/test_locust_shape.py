import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID


PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPERIMENT_RUN_ID = UUID(
    "11111111-1111-1111-1111-111111111111"
)


def _shape_environment() -> dict[str, str]:
    """Build an isolated environment for the Locust subprocess."""

    environment = os.environ.copy()

    environment.update({
        "LOAD_EXPERIMENT_RUN_ID": str(
            EXPERIMENT_RUN_ID
        ),
        "LOAD_SHOW_ID": "2",
        "LOAD_USER_IDS": (
            "[1, 2, 3, 4, 5, 6, "
            "7, 8, 9, 10, 11, 12]"
        ),
        "LOAD_SEAT_IDS": "[101, 102, 103, 104]",
        "LOAD_HOTSPOT_SEAT_IDS": "[101, 102]",
        "LOAD_HOTSPOT_RATIO": "0.8",
        "LOAD_RANDOM_SEED": "42",
        "LOAD_MAXIMUM_USERS": "12",
        "LOAD_SPAWN_RATE_PER_SEC": "5",
        "LOAD_DURATION_SEC": "90",
        "LOAD_TRAFFIC_PATTERN": "BURST",
        "LOAD_INTERNAL_API_TOKEN": (
            "test-internal-token-value-1234567890"
        ),
    })

    return environment


def _run_shape_check(source_code: str) -> None:
    """Run one Locust shape check in an isolated interpreter."""

    completed_process = subprocess.run(
        [
            sys.executable,
            "-c",
            source_code,
        ],
        cwd=PROJECT_ROOT,
        env=_shape_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed_process.returncode == 0, (
        completed_process.stderr
        or completed_process.stdout
    )


def test_burst_shape_changes_target_users() -> None:
    """The Locust shape follows the stored BURST pattern."""

    _run_shape_check(
        "\n".join([
            (
                "from load_testing.locustfile import "
                "ExperimentTrafficShape"
            ),
            "shape = ExperimentTrafficShape()",
            "shape.get_run_time = lambda: 10.0",
            "assert shape.tick() == (3, 5.0)",
            "shape.get_run_time = lambda: 20.0",
            "assert shape.tick() == (12, 5.0)",
            "shape.get_run_time = lambda: 70.0",
            "assert shape.tick() == (3, 5.0)",
        ])
    )


def test_shape_stops_at_experiment_duration() -> None:
    """Returning None stops Locust at the configured duration."""

    _run_shape_check(
        "\n".join([
            (
                "from load_testing.locustfile import "
                "ExperimentTrafficShape"
            ),
            "shape = ExperimentTrafficShape()",
            "shape.get_run_time = lambda: 90.0",
            "assert shape.tick() is None",
        ])
    )