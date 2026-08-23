import json
import os
from dataclasses import dataclass
from random import Random
from uuid import UUID


class WorkloadConfigurationError(ValueError):
    """Raised when workload environment data is invalid."""


def _required_environment_value(
    name: str,
) -> str:
    """Read one required nonempty environment variable."""

    value = os.getenv(name)

    if value is None or not value.strip():
        raise WorkloadConfigurationError(
            f"{name} is required."
        )

    return value.strip()


def _read_positive_integer(
    name: str,
) -> int:
    """Read one positive integer environment value."""

    raw_value = _required_environment_value(name)

    try:
        value = int(raw_value)
    except ValueError as error:
        raise WorkloadConfigurationError(
            f"{name} must be an integer."
        ) from error

    if value <= 0:
        raise WorkloadConfigurationError(
            f"{name} must be greater than zero."
        )

    return value


def _read_integer(
    name: str,
) -> int:
    """Read one integer environment value."""

    raw_value = _required_environment_value(name)

    try:
        return int(raw_value)
    except ValueError as error:
        raise WorkloadConfigurationError(
            f"{name} must be an integer."
        ) from error


def _read_ratio(
    name: str,
) -> float:
    """Read a floating-point ratio between zero and one."""

    raw_value = _required_environment_value(name)

    try:
        value = float(raw_value)
    except ValueError as error:
        raise WorkloadConfigurationError(
            f"{name} must be a number."
        ) from error

    if value < 0 or value > 1:
        raise WorkloadConfigurationError(
            f"{name} must be between 0 and 1."
        )

    return value


def _read_positive_integer_list(
    name: str,
) -> tuple[int, ...]:
    """Read a nonempty JSON array of unique positive integers."""

    raw_value = _required_environment_value(name)

    try:
        parsed_value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise WorkloadConfigurationError(
            f"{name} must contain a valid JSON array."
        ) from error

    if not isinstance(parsed_value, list):
        raise WorkloadConfigurationError(
            f"{name} must contain a JSON array."
        )

    if not parsed_value:
        raise WorkloadConfigurationError(
            f"{name} must not be empty."
        )

    values: list[int] = []

    for item in parsed_value:
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or item <= 0
        ):
            raise WorkloadConfigurationError(
                f"{name} must contain only "
                "positive integers."
            )

        values.append(item)

    if len(values) != len(set(values)):
        raise WorkloadConfigurationError(
            f"{name} must not contain duplicate values."
        )

    return tuple(values)


@dataclass(frozen=True)
class WorkloadConfig:
    """Immutable configuration used by Locust users."""

    experiment_run_id: UUID
    show_id: int
    user_ids: tuple[int, ...]
    seat_ids: tuple[int, ...]
    hotspot_seat_ids: tuple[int, ...]
    hotspot_ratio: float
    random_seed: int

    def __post_init__(self) -> None:
        """Validate values supplied directly or from the environment."""

        if self.show_id <= 0:
            raise WorkloadConfigurationError(
                "show_id must be greater than zero."
            )

        if not self.user_ids:
            raise WorkloadConfigurationError(
                "At least one synthetic user is required."
            )

        if not self.seat_ids:
            raise WorkloadConfigurationError(
                "At least one seat is required."
            )

        if not self.hotspot_seat_ids:
            raise WorkloadConfigurationError(
                "At least one hotspot seat is required."
            )

        if self.hotspot_ratio < 0 or self.hotspot_ratio > 1:
            raise WorkloadConfigurationError(
                "hotspot_ratio must be between 0 and 1."
            )

        unknown_hotspot_ids = (
            set(self.hotspot_seat_ids)
            - set(self.seat_ids)
        )

        if unknown_hotspot_ids:
            raise WorkloadConfigurationError(
                "Every hotspot seat must be part "
                "of the experiment show."
            )

    @property
    def cold_seat_ids(self) -> tuple[int, ...]:
        """Return seats outside the configured hotspot."""

        hotspot_ids = set(self.hotspot_seat_ids)

        return tuple(
            seat_id
            for seat_id in self.seat_ids
            if seat_id not in hotspot_ids
        )

    def select_user_id(
        self,
        random_generator: Random,
    ) -> int:
        """Select one configured synthetic user."""

        return random_generator.choice(
            self.user_ids
        )

    def select_seat_id(
        self,
        random_generator: Random,
    ) -> int:
        """Select a hotspot or cold seat using the ratio."""

        cold_seats = self.cold_seat_ids

        if not cold_seats:
            return random_generator.choice(
                self.hotspot_seat_ids
            )

        use_hotspot = (
            random_generator.random()
            < self.hotspot_ratio
        )

        if use_hotspot:
            return random_generator.choice(
                self.hotspot_seat_ids
            )

        return random_generator.choice(
            cold_seats
        )

    @classmethod
    def from_environment(cls) -> "WorkloadConfig":
        """Build a workload configuration from runner variables."""

        experiment_run_id_value = (
            _required_environment_value(
                "LOAD_EXPERIMENT_RUN_ID"
            )
        )

        try:
            experiment_run_id = UUID(
                experiment_run_id_value
            )
        except ValueError as error:
            raise WorkloadConfigurationError(
                "LOAD_EXPERIMENT_RUN_ID must "
                "contain a valid UUID."
            ) from error

        return cls(
            experiment_run_id=experiment_run_id,
            show_id=_read_positive_integer(
                "LOAD_SHOW_ID"
            ),
            user_ids=_read_positive_integer_list(
                "LOAD_USER_IDS"
            ),
            seat_ids=_read_positive_integer_list(
                "LOAD_SEAT_IDS"
            ),
            hotspot_seat_ids=(
                _read_positive_integer_list(
                    "LOAD_HOTSPOT_SEAT_IDS"
                )
            ),
            hotspot_ratio=_read_ratio(
                "LOAD_HOTSPOT_RATIO"
            ),
            random_seed=_read_integer(
                "LOAD_RANDOM_SEED"
            ),
        )