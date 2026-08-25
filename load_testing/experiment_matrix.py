import argparse
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
from pathlib import Path
from typing import Final

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


DEFAULT_MATRIX_FILE: Final = Path(
    "load_testing/config/experiment_matrix_v1.json"
)


class ExperimentMatrixError(ValueError):
    """Raised when an experiment matrix is invalid."""


@dataclass(frozen=True)
class DemandProfileSetting:
    """Validated demand configuration for one matrix profile."""

    name: DemandProfileName
    hotspot_ratio: Decimal
    hotspot_seat_count: int


@dataclass(frozen=True)
class PressureLevel:
    """Validated concurrency settings for one pressure level."""

    name: str
    user_count: int
    spawn_rate_per_sec: Decimal
    simulated_work_ms: int


@dataclass(frozen=True)
class ExperimentScenario:
    """One deterministic experiment generated from the matrix."""

    scenario_id: str
    experiment_name: str
    matrix_name: str
    schema_version: str
    show_id: int
    duration_sec: int
    sample_interval_sec: int
    booking_mode: str
    created_by: str
    traffic_pattern: TrafficPattern
    demand_profile: DemandProfileName
    pressure_level: str
    hotspot_ratio: Decimal
    hotspot_seat_count: int
    user_count: int
    spawn_rate_per_sec: Decimal
    simulated_work_ms: int
    random_seed: int


@dataclass(frozen=True)
class ExperimentMatrix:
    """Validated matrix and all planned experiment scenarios."""

    schema_version: str
    matrix_name: str
    show_id: int
    duration_sec: int
    sample_interval_sec: int
    booking_mode: str
    created_by: str
    prediction_horizon_seconds: int
    estimated_labelled_rows_per_run: int
    expected_run_count: int
    expected_total_labelled_rows: int
    demand_profiles: tuple[DemandProfileSetting, ...]
    pressure_levels: tuple[PressureLevel, ...]
    traffic_patterns: tuple[TrafficPattern, ...]
    random_seeds: tuple[int, ...]
    scenarios: tuple[ExperimentScenario, ...]


def _required_mapping(
    mapping: dict[str, object],
    field_name: str,
) -> dict[str, object]:
    """Read one required JSON object."""

    value = mapping.get(field_name)

    if not isinstance(value, dict):
        raise ExperimentMatrixError(
            f"{field_name} must be a JSON object."
        )

    return value


def _required_text(
    mapping: dict[str, object],
    field_name: str,
) -> str:
    """Read one required nonempty string."""

    value = mapping.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise ExperimentMatrixError(
            f"{field_name} must be a nonempty string."
        )

    return value.strip()


def _required_integer(
    mapping: dict[str, object],
    field_name: str,
    *,
    minimum: int,
) -> int:
    """Read an integer at or above the supplied minimum."""

    value = mapping.get(field_name)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ExperimentMatrixError(
            f"{field_name} must be an integer greater than "
            f"or equal to {minimum}."
        )

    return value


def _required_decimal(
    mapping: dict[str, object],
    field_name: str,
    *,
    minimum: Decimal,
    maximum: Decimal | None = None,
) -> Decimal:
    """Read a decimal within the supplied bounds."""

    value = mapping.get(field_name)

    if isinstance(value, bool) or not isinstance(
        value,
        (str, int, float, Decimal),
    ):
        raise ExperimentMatrixError(
            f"{field_name} must be a number."
        )

    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as error:
        raise ExperimentMatrixError(
            f"{field_name} must be a valid decimal."
        ) from error

    if decimal_value < minimum:
        raise ExperimentMatrixError(
            f"{field_name} must be at least {minimum}."
        )

    if maximum is not None and decimal_value > maximum:
        raise ExperimentMatrixError(
            f"{field_name} must not exceed {maximum}."
        )

    return decimal_value


def _load_traffic_patterns(
    raw_matrix: dict[str, object],
) -> tuple[TrafficPattern, ...]:
    """Parse unique supported traffic patterns."""

    values = raw_matrix.get("traffic_patterns")

    if not isinstance(values, list) or not values:
        raise ExperimentMatrixError(
            "traffic_patterns must be a nonempty JSON array."
        )

    patterns: list[TrafficPattern] = []

    for value in values:
        if not isinstance(value, str):
            raise ExperimentMatrixError(
                "Every traffic pattern must be a string."
            )

        try:
            pattern = parse_traffic_pattern(value)
        except TrafficPatternError as error:
            raise ExperimentMatrixError(
                f"Invalid traffic pattern: {value}."
            ) from error

        patterns.append(pattern)

    if len(patterns) != len(set(patterns)):
        raise ExperimentMatrixError(
            "traffic_patterns must not contain duplicates."
        )

    return tuple(patterns)


def _load_demand_profiles(
    raw_matrix: dict[str, object],
) -> tuple[DemandProfileSetting, ...]:
    """Parse and validate calibrated demand settings."""

    raw_profiles = _required_mapping(
        raw_matrix,
        "demand_profiles",
    )

    profiles: list[DemandProfileSetting] = []

    for raw_name, raw_setting in raw_profiles.items():
        if not isinstance(raw_setting, dict):
            raise ExperimentMatrixError(
                f"Demand profile {raw_name} must be a JSON object."
            )

        try:
            profile_name = parse_demand_profile_name(
                raw_name
            )
        except DemandProfileError as error:
            raise ExperimentMatrixError(
                f"Invalid demand profile: {raw_name}."
            ) from error

        if profile_name == DemandProfileName.CUSTOM:
            raise ExperimentMatrixError(
                "The calibrated training matrix must not use CUSTOM."
            )

        hotspot_ratio = _required_decimal(
            raw_setting,
            "hotspot_ratio",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
        )
        hotspot_seat_count = _required_integer(
            raw_setting,
            "hotspot_seat_count",
            minimum=1,
        )

        try:
            validate_demand_configuration(
                profile_name=profile_name,
                hotspot_ratio=hotspot_ratio,
                hotspot_seat_count=hotspot_seat_count,
            )
        except DemandProfileError as error:
            raise ExperimentMatrixError(
                f"Demand profile {raw_name} is invalid: {error}"
            ) from error

        profiles.append(
            DemandProfileSetting(
                name=profile_name,
                hotspot_ratio=hotspot_ratio,
                hotspot_seat_count=hotspot_seat_count,
            )
        )

    return tuple(profiles)


def _load_pressure_levels(
    raw_matrix: dict[str, object],
) -> tuple[PressureLevel, ...]:
    """Parse the controlled system-pressure settings."""

    raw_levels = _required_mapping(
        raw_matrix,
        "pressure_levels",
    )

    levels: list[PressureLevel] = []

    for raw_name, raw_setting in raw_levels.items():
        normalized_name = raw_name.strip().upper()

        if not normalized_name:
            raise ExperimentMatrixError(
                "Pressure-level names must not be blank."
            )

        if not isinstance(raw_setting, dict):
            raise ExperimentMatrixError(
                f"Pressure level {raw_name} must be a JSON object."
            )

        levels.append(
            PressureLevel(
                name=normalized_name,
                user_count=_required_integer(
                    raw_setting,
                    "user_count",
                    minimum=1,
                ),
                spawn_rate_per_sec=_required_decimal(
                    raw_setting,
                    "spawn_rate_per_sec",
                    minimum=Decimal("0.01"),
                ),
                simulated_work_ms=_required_integer(
                    raw_setting,
                    "simulated_work_ms",
                    minimum=0,
                ),
            )
        )

    names = [level.name for level in levels]

    if len(names) != len(set(names)):
        raise ExperimentMatrixError(
            "Pressure-level names must be unique."
        )

    return tuple(levels)


def _load_random_seeds(
    raw_matrix: dict[str, object],
) -> tuple[int, ...]:
    """Read unique integer random seeds."""

    values = raw_matrix.get("random_seeds")

    if not isinstance(values, list) or not values:
        raise ExperimentMatrixError(
            "random_seeds must be a nonempty JSON array."
        )

    seeds: list[int] = []

    for value in values:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ExperimentMatrixError(
                "Every random seed must be an integer."
            )

        seeds.append(value)

    if len(seeds) != len(set(seeds)):
        raise ExperimentMatrixError(
            "random_seeds must not contain duplicates."
        )

    return tuple(seeds)


def _build_scenarios(
    *,
    schema_version: str,
    matrix_name: str,
    show_id: int,
    duration_sec: int,
    sample_interval_sec: int,
    booking_mode: str,
    created_by: str,
    traffic_patterns: tuple[TrafficPattern, ...],
    demand_profiles: tuple[DemandProfileSetting, ...],
    pressure_levels: tuple[PressureLevel, ...],
    random_seeds: tuple[int, ...],
) -> tuple[ExperimentScenario, ...]:
    """Create the Cartesian product of matrix dimensions."""

    scenarios: list[ExperimentScenario] = []

    for (
        traffic_pattern,
        demand_profile,
        pressure_level,
        random_seed,
    ) in product(
        traffic_patterns,
        demand_profiles,
        pressure_levels,
        random_seeds,
    ):
        scenario_id = "-".join([
            schema_version,
            demand_profile.name.value,
            traffic_pattern.value,
            pressure_level.name,
            f"seed{random_seed}",
        ])

        experiment_name = "-".join([
            "M31",
            f"v{schema_version}",
            demand_profile.name.value,
            traffic_pattern.value,
            pressure_level.name,
            f"s{random_seed}",
        ])

        if len(experiment_name) > 100:
            raise ExperimentMatrixError(
                "Generated experiment name exceeds 100 characters."
            )

        scenarios.append(
            ExperimentScenario(
                scenario_id=scenario_id,
                experiment_name=experiment_name,
                matrix_name=matrix_name,
                schema_version=schema_version,
                show_id=show_id,
                duration_sec=duration_sec,
                sample_interval_sec=sample_interval_sec,
                booking_mode=booking_mode,
                created_by=created_by,
                traffic_pattern=traffic_pattern,
                demand_profile=demand_profile.name,
                pressure_level=pressure_level.name,
                hotspot_ratio=demand_profile.hotspot_ratio,
                hotspot_seat_count=(
                    demand_profile.hotspot_seat_count
                ),
                user_count=pressure_level.user_count,
                spawn_rate_per_sec=(
                    pressure_level.spawn_rate_per_sec
                ),
                simulated_work_ms=(
                    pressure_level.simulated_work_ms
                ),
                random_seed=random_seed,
            )
        )

    scenario_ids = [
        scenario.scenario_id
        for scenario in scenarios
    ]

    if len(scenario_ids) != len(set(scenario_ids)):
        raise ExperimentMatrixError(
            "Generated scenario identities are not unique."
        )

    return tuple(scenarios)


def load_experiment_matrix(
    matrix_file: Path = DEFAULT_MATRIX_FILE,
) -> ExperimentMatrix:
    """Load, validate, and expand one experiment matrix file."""

    try:
        raw_value = json.loads(
            matrix_file.read_text(encoding="utf-8")
        )
    except OSError as error:
        raise ExperimentMatrixError(
            f"Could not read matrix file {matrix_file}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ExperimentMatrixError(
            f"Matrix file {matrix_file} is not valid JSON."
        ) from error

    if not isinstance(raw_value, dict):
        raise ExperimentMatrixError(
            "The matrix root must be a JSON object."
        )

    schema_version = _required_text(
        raw_value,
        "schema_version",
    )
    matrix_name = _required_text(
        raw_value,
        "matrix_name",
    )
    show_id = _required_integer(
        raw_value,
        "show_id",
        minimum=1,
    )
    duration_sec = _required_integer(
        raw_value,
        "duration_sec",
        minimum=30,
    )
    sample_interval_sec = _required_integer(
        raw_value,
        "sample_interval_sec",
        minimum=1,
    )
    booking_mode = _required_text(
        raw_value,
        "booking_mode",
    )
    created_by = _required_text(
        raw_value,
        "created_by",
    )
    prediction_horizon_seconds = _required_integer(
        raw_value,
        "prediction_horizon_seconds",
        minimum=1,
    )
    estimated_labelled_rows_per_run = _required_integer(
        raw_value,
        "estimated_labelled_rows_per_run",
        minimum=1,
    )
    expected_run_count = _required_integer(
        raw_value,
        "expected_run_count",
        minimum=1,
    )
    expected_total_labelled_rows = _required_integer(
        raw_value,
        "estimated_total_labelled_rows",
        minimum=1,
    )

    if booking_mode != "EXPERIMENT_RECYCLE":
        raise ExperimentMatrixError(
            "booking_mode must be EXPERIMENT_RECYCLE."
        )

    if duration_sec > 3600:
        raise ExperimentMatrixError(
            "duration_sec must not exceed 3600."
        )

    if sample_interval_sec > 10:
        raise ExperimentMatrixError(
            "sample_interval_sec must not exceed 10."
        )

    traffic_patterns = _load_traffic_patterns(raw_value)
    demand_profiles = _load_demand_profiles(raw_value)
    pressure_levels = _load_pressure_levels(raw_value)
    random_seeds = _load_random_seeds(raw_value)

    scenarios = _build_scenarios(
        schema_version=schema_version,
        matrix_name=matrix_name,
        show_id=show_id,
        duration_sec=duration_sec,
        sample_interval_sec=sample_interval_sec,
        booking_mode=booking_mode,
        created_by=created_by,
        traffic_patterns=traffic_patterns,
        demand_profiles=demand_profiles,
        pressure_levels=pressure_levels,
        random_seeds=random_seeds,
    )

    if len(scenarios) != expected_run_count:
        raise ExperimentMatrixError(
            "expected_run_count does not match the generated "
            f"scenario count: {len(scenarios)}."
        )

    calculated_total_rows = (
        expected_run_count
        * estimated_labelled_rows_per_run
    )

    if calculated_total_rows != expected_total_labelled_rows:
        raise ExperimentMatrixError(
            "estimated_total_labelled_rows does not match "
            f"{expected_run_count} × "
            f"{estimated_labelled_rows_per_run}."
        )

    return ExperimentMatrix(
        schema_version=schema_version,
        matrix_name=matrix_name,
        show_id=show_id,
        duration_sec=duration_sec,
        sample_interval_sec=sample_interval_sec,
        booking_mode=booking_mode,
        created_by=created_by,
        prediction_horizon_seconds=(
            prediction_horizon_seconds
        ),
        estimated_labelled_rows_per_run=(
            estimated_labelled_rows_per_run
        ),
        expected_run_count=expected_run_count,
        expected_total_labelled_rows=(
            expected_total_labelled_rows
        ),
        demand_profiles=demand_profiles,
        pressure_levels=pressure_levels,
        traffic_patterns=traffic_patterns,
        random_seeds=random_seeds,
        scenarios=scenarios,
    )


def parse_arguments() -> argparse.Namespace:
    """Read matrix dry-run command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate and display the Checkpoint 31 "
            "experiment matrix without executing workloads."
        )
    )
    parser.add_argument(
        "--matrix-file",
        type=Path,
        default=DEFAULT_MATRIX_FILE,
        help=(
            "Matrix JSON file. Default: "
            "load_testing/config/experiment_matrix_v1.json"
        ),
    )
    parser.add_argument(
        "--show-scenarios",
        action="store_true",
        help="Print every generated scenario identity.",
    )

    return parser.parse_args()


def main() -> int:
    """Validate the matrix and print its dry-run summary."""

    arguments = parse_arguments()

    try:
        matrix = load_experiment_matrix(
            arguments.matrix_file
        )
    except ExperimentMatrixError as error:
        print(f"Experiment matrix error: {error}")
        return 2

    print("Experiment matrix dry run passed:")
    print(f"  Matrix: {matrix.matrix_name}")
    print(f"  Schema version: {matrix.schema_version}")
    print(f"  Planned runs: {len(matrix.scenarios)}")
    print(
        "  Estimated labelled rows: "
        f"{matrix.expected_total_labelled_rows}"
    )
    print(
        "  Traffic patterns: "
        f"{len(matrix.traffic_patterns)}"
    )
    print(
        "  Demand profiles: "
        f"{len(matrix.demand_profiles)}"
    )
    print(
        "  Pressure levels: "
        f"{len(matrix.pressure_levels)}"
    )
    print(f"  Random seeds: {len(matrix.random_seeds)}")

    if arguments.show_scenarios:
        print("Scenarios:")

        for scenario in matrix.scenarios:
            print(f"  {scenario.scenario_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())