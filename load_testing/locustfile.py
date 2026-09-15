import os
from itertools import count
from random import Random
from uuid import uuid4

from locust import (
    HttpUser,
    LoadTestShape,
    between,
    events,
    task,
)

from load_testing.config import (
    WorkloadConfig,
    WorkloadConfigurationError,
)
from load_testing.traffic_patterns import (
    build_traffic_pattern_plan,
    target_users_at,
)


_workload_config: WorkloadConfig | None = None
_internal_api_token: str | None = None
_user_sequence = count()

def _is_expected_business_conflict(
    status_code: int,
    error_code: str,
) -> bool:
    """Identify an expected concurrent seat-booking conflict."""

    return (
        status_code == 409
        and error_code == "SEAT_UNAVAILABLE"
    )


def _required_internal_token() -> str:
    """Read the internal API token without printing it."""

    token = os.getenv("LOAD_INTERNAL_API_TOKEN")

    if token is None or not token.strip():
        raise WorkloadConfigurationError(
            "LOAD_INTERNAL_API_TOKEN is required."
        )

    return token.strip()


def _get_workload_config() -> WorkloadConfig:
    """Return the configuration loaded at test start."""

    if _workload_config is None:
        raise WorkloadConfigurationError(
            "The workload configuration has not been loaded."
        )

    return _workload_config


def _get_internal_api_token() -> str:
    """Return the internal token loaded at test start."""

    if _internal_api_token is None:
        raise WorkloadConfigurationError(
            "The internal API token has not been loaded."
        )

    return _internal_api_token


@events.test_start.add_listener
def configure_workload(
    environment: object,
    **_: object,
) -> None:
    """Load and validate configuration before users start."""

    del environment

    global _workload_config
    global _internal_api_token
    global _user_sequence

    _workload_config = (
        WorkloadConfig.from_environment()
    )
    _internal_api_token = (
        _required_internal_token()
    )
    _user_sequence = count()


@events.test_stop.add_listener
def clear_workload(
    environment: object,
    **_: object,
) -> None:
    """Remove in-memory secrets and configuration after a run."""

    del environment

    global _workload_config
    global _internal_api_token

    _workload_config = None
    _internal_api_token = None


class ExperimentTrafficShape(LoadTestShape):
    """Control Locust users using the experiment traffic pattern."""

    def __init__(self) -> None:
        super().__init__()

        self.configuration = (
            WorkloadConfig.from_environment()
        )
        self.traffic_plan = (
            build_traffic_pattern_plan(
                self.configuration.traffic_pattern,
                duration_sec=(
                    self.configuration.duration_sec
                ),
            )
        )

    def tick(self) -> tuple[int, float] | None:
        """Return the desired user count and spawn rate."""

        elapsed_seconds = self.get_run_time()

        if elapsed_seconds >= self.configuration.duration_sec:
            return None

        target_user_count = target_users_at(
            self.traffic_plan,
            elapsed_seconds=elapsed_seconds,
            maximum_users=(
                self.configuration.maximum_users
            ),
        )

        return (
            target_user_count,
            self.configuration.spawn_rate_per_sec,
        )


class ReservationWorkloadUser(HttpUser):
    """Generate controlled reservation requests."""

    wait_time = between(0.01, 0.05)

    random_generator: Random
    synthetic_user_id: int

    def on_start(self) -> None:
        """Assign deterministic state to one virtual user."""

        configuration = _get_workload_config()
        user_index = next(_user_sequence)

        self.random_generator = Random(
            configuration.random_seed
            + user_index
        )

        self.synthetic_user_id = (
            configuration.user_ids[
                user_index
                % len(configuration.user_ids)
            ]
        )

    @task
    def create_recyclable_booking(self) -> None:
        """Send one booking request selected by hotspot ratio."""

        configuration = _get_workload_config()

        seat_id = configuration.select_seat_id(
            self.random_generator
        )
        idempotency_key = uuid4()

        with self.client.post(
            "/api/v1/bookings",
            name="/api/v1/bookings",
            headers={
                "X-Internal-Token": (
                    _get_internal_api_token()
                ),
                "Idempotency-Key": str(
                    idempotency_key
                ),
            },
            json={
                "user_id": self.synthetic_user_id,
                "show_id": configuration.show_id,
                "seat_id": seat_id,
                "experiment_run_id": str(
                    configuration.experiment_run_id
                ),
            },
            catch_response=True,
        ) as response:
            if response.status_code not in {
                200,
                201,
            }:
                try:
                    response_body = response.json()
                    error_code = response_body.get(
                        "detail",
                        {},
                    ).get(
                        "code",
                        "UNKNOWN_ERROR",
                    )
                except ValueError:
                    error_code = "INVALID_ERROR_RESPONSE"

                if _is_expected_business_conflict(
                    response.status_code,
                    error_code,
                ):
                    response.success()
                    return

                response.failure(
                    f"HTTP {response.status_code}: "
                    f"{error_code}"
                )
                return

            try:
                response_body = response.json()
            except ValueError:
                response.failure(
                    "Successful response was not valid JSON."
                )
                return

            if response_body.get("status") != "CANCELLED":
                response.failure(
                    "Recycling request did not return "
                    "a CANCELLED booking."
                )
                return

            if (
                response_body.get("experiment_run_id")
                != str(configuration.experiment_run_id)
            ):
                response.failure(
                    "Booking response used the wrong "
                    "experiment_run_id."
                )
                return

            response.success()