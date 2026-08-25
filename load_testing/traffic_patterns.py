from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
)
from enum import Enum
from typing import Final


REFERENCE_DURATION_SEC: Final = Decimal("90")
MINIMUM_DURATION_SEC: Final = 30
MAXIMUM_DURATION_SEC: Final = 3600


class TrafficPattern(str, Enum):
    """Supported experimental traffic-arrival patterns."""

    STEADY = "STEADY"
    RAMP = "RAMP"
    BURST = "BURST"
    FLASH_SALE = "FLASH_SALE"
    COOLDOWN = "COOLDOWN"


class TrafficPatternError(Exception):
    """Expected traffic-pattern validation failure."""


@dataclass(frozen=True)
class TrafficStage:
    """One time period within a traffic pattern."""

    stage_number: int
    start_sec: Decimal
    end_sec: Decimal
    start_user_fraction: Decimal
    end_user_fraction: Decimal


@dataclass(frozen=True)
class TrafficPatternPlan:
    """Complete traffic schedule for one experiment."""

    pattern: TrafficPattern
    duration_sec: int
    stages: tuple[TrafficStage, ...]


StageDefinition = tuple[
    int,
    int,
    str,
    str,
]


PATTERN_STAGE_DEFINITIONS: Final[
    dict[
        TrafficPattern,
        tuple[StageDefinition, ...],
    ]
] = {
    TrafficPattern.STEADY: (
        (
            0,
            10,
            "0.00",
            "1.00",
        ),
        (
            10,
            90,
            "1.00",
            "1.00",
        ),
    ),
    TrafficPattern.RAMP: (
        (
            0,
            30,
            "0.25",
            "0.25",
        ),
        (
            30,
            60,
            "0.50",
            "0.50",
        ),
        (
            60,
            75,
            "0.75",
            "0.75",
        ),
        (
            75,
            90,
            "1.00",
            "1.00",
        ),
    ),
    TrafficPattern.BURST: (
        (
            0,
            20,
            "0.25",
            "0.25",
        ),
        (
            20,
            50,
            "1.00",
            "1.00",
        ),
        (
            50,
            90,
            "0.25",
            "0.25",
        ),
    ),
    TrafficPattern.FLASH_SALE: (
        (
            0,
            10,
            "0.10",
            "0.10",
        ),
        (
            10,
            45,
            "1.00",
            "1.00",
        ),
        (
            45,
            70,
            "0.60",
            "0.60",
        ),
        (
            70,
            90,
            "0.30",
            "0.30",
        ),
    ),
    TrafficPattern.COOLDOWN: (
        (
            0,
            30,
            "1.00",
            "1.00",
        ),
        (
            30,
            60,
            "0.50",
            "0.50",
        ),
        (
            60,
            90,
            "0.20",
            "0.20",
        ),
    ),
}


def parse_traffic_pattern(
    value: TrafficPattern | str,
) -> TrafficPattern:
    """Convert text into a supported traffic pattern."""

    if isinstance(value, TrafficPattern):
        return value

    if not isinstance(value, str):
        raise TrafficPatternError(
            "Traffic pattern must be text."
        )

    normalized_value = (
        value.strip()
        .upper()
        .replace("-", "_")
    )

    try:
        return TrafficPattern(
            normalized_value
        )
    except ValueError as error:
        supported_values = ", ".join(
            pattern.value
            for pattern in TrafficPattern
        )

        raise TrafficPatternError(
            f"Unsupported traffic pattern "
            f"{value!r}. Supported values: "
            f"{supported_values}."
        ) from error


def _validate_duration(
    duration_sec: int,
) -> None:
    """Validate the experiment duration."""

    if (
        not isinstance(duration_sec, int)
        or isinstance(duration_sec, bool)
    ):
        raise TrafficPatternError(
            "Experiment duration must be an integer."
        )

    if not (
        MINIMUM_DURATION_SEC
        <= duration_sec
        <= MAXIMUM_DURATION_SEC
    ):
        raise TrafficPatternError(
            "Experiment duration must be between "
            f"{MINIMUM_DURATION_SEC} and "
            f"{MAXIMUM_DURATION_SEC} seconds."
        )


def _scale_reference_time(
    reference_second: int,
    duration_sec: int,
) -> Decimal:
    """Scale a 90-second reference time to a duration."""

    return (
        Decimal(duration_sec)
        * Decimal(reference_second)
        / REFERENCE_DURATION_SEC
    )


def _validate_stage_fraction(
    fraction: Decimal,
) -> None:
    """Validate one user fraction."""

    if (
        not fraction.is_finite()
        or fraction < Decimal("0")
        or fraction > Decimal("1")
    ):
        raise TrafficPatternError(
            "Traffic-stage user fractions must "
            "remain between zero and one."
        )


def build_traffic_pattern_plan(
    pattern: TrafficPattern | str,
    *,
    duration_sec: int = 90,
) -> TrafficPatternPlan:
    """Build a scaled traffic schedule."""

    parsed_pattern = parse_traffic_pattern(
        pattern
    )
    _validate_duration(duration_sec)

    definitions = PATTERN_STAGE_DEFINITIONS[
        parsed_pattern
    ]

    stages: list[TrafficStage] = []

    for stage_index, definition in enumerate(
        definitions,
        start=1,
    ):
        (
            reference_start,
            reference_end,
            start_fraction_text,
            end_fraction_text,
        ) = definition

        start_fraction = Decimal(
            start_fraction_text
        )
        end_fraction = Decimal(
            end_fraction_text
        )

        _validate_stage_fraction(
            start_fraction
        )
        _validate_stage_fraction(
            end_fraction
        )

        stage = TrafficStage(
            stage_number=stage_index,
            start_sec=_scale_reference_time(
                reference_start,
                duration_sec,
            ),
            end_sec=_scale_reference_time(
                reference_end,
                duration_sec,
            ),
            start_user_fraction=start_fraction,
            end_user_fraction=end_fraction,
        )

        if stage.end_sec <= stage.start_sec:
            raise TrafficPatternError(
                "Traffic stage must end after "
                "it starts."
            )

        if stages:
            previous_stage = stages[-1]

            if (
                previous_stage.end_sec
                != stage.start_sec
            ):
                raise TrafficPatternError(
                    "Traffic stages must be "
                    "continuous."
                )

        stages.append(stage)

    if not stages:
        raise TrafficPatternError(
            "Traffic pattern contains no stages."
        )

    if stages[0].start_sec != Decimal("0"):
        raise TrafficPatternError(
            "Traffic pattern must begin at "
            "zero seconds."
        )

    if stages[-1].end_sec != Decimal(
        duration_sec
    ):
        raise TrafficPatternError(
            "Traffic pattern must cover the "
            "complete experiment duration."
        )

    return TrafficPatternPlan(
        pattern=parsed_pattern,
        duration_sec=duration_sec,
        stages=tuple(stages),
    )


def _decimal_elapsed_time(
    elapsed_seconds: int | float | Decimal,
) -> Decimal:
    """Convert elapsed time into a finite decimal."""

    if isinstance(elapsed_seconds, bool):
        raise TrafficPatternError(
            "Elapsed time must be numeric."
        )

    try:
        elapsed = Decimal(
            str(elapsed_seconds)
        )
    except (
        InvalidOperation,
        ValueError,
    ) as error:
        raise TrafficPatternError(
            "Elapsed time must be numeric."
        ) from error

    if not elapsed.is_finite():
        raise TrafficPatternError(
            "Elapsed time must be finite."
        )

    if elapsed < Decimal("0"):
        raise TrafficPatternError(
            "Elapsed time cannot be negative."
        )

    return elapsed


def _interpolated_user_fraction(
    stage: TrafficStage,
    elapsed: Decimal,
) -> Decimal:
    """Calculate the user fraction inside a stage."""

    if (
        stage.start_user_fraction
        == stage.end_user_fraction
    ):
        return stage.start_user_fraction

    stage_duration = (
        stage.end_sec - stage.start_sec
    )

    progress = (
        elapsed - stage.start_sec
    ) / stage_duration

    fraction_change = (
        stage.end_user_fraction
        - stage.start_user_fraction
    )

    calculated_fraction = (
        stage.start_user_fraction
        + fraction_change * progress
    )

    return min(
        Decimal("1"),
        max(
            Decimal("0"),
            calculated_fraction,
        ),
    )


def target_users_at(
    plan: TrafficPatternPlan,
    *,
    elapsed_seconds: int | float | Decimal,
    maximum_users: int,
) -> int:
    """Return the target users at one elapsed time."""

    if (
        not isinstance(maximum_users, int)
        or isinstance(maximum_users, bool)
        or maximum_users <= 0
    ):
        raise TrafficPatternError(
            "Maximum users must be a positive integer."
        )

    elapsed = _decimal_elapsed_time(
        elapsed_seconds
    )

    if elapsed >= Decimal(
        plan.duration_sec
    ):
        return 0

    selected_stage: TrafficStage | None = None

    for stage in plan.stages:
        if (
            stage.start_sec
            <= elapsed
            < stage.end_sec
        ):
            selected_stage = stage
            break

    if selected_stage is None:
        raise TrafficPatternError(
            "Elapsed time does not belong to "
            "a traffic stage."
        )

    user_fraction = (
        _interpolated_user_fraction(
            selected_stage,
            elapsed,
        )
    )

    calculated_users = int(
        (
            Decimal(maximum_users)
            * user_fraction
        ).to_integral_value(
            rounding=ROUND_CEILING
        )
    )

    return min(
        maximum_users,
        max(
            1,
            calculated_users,
        ),
    )


def traffic_plan_to_dictionary(
    plan: TrafficPatternPlan,
) -> dict[str, object]:
    """Convert a plan into serializable metadata."""

    return {
        "pattern": plan.pattern.value,
        "duration_sec": plan.duration_sec,
        "stages": [
            {
                "stage_number": (
                    stage.stage_number
                ),
                "start_sec": str(
                    stage.start_sec
                ),
                "end_sec": str(
                    stage.end_sec
                ),
                "start_user_fraction": str(
                    stage.start_user_fraction
                ),
                "end_user_fraction": str(
                    stage.end_user_fraction
                ),
            }
            for stage in plan.stages
        ],
    }