from decimal import Decimal

import pytest

from load_testing.traffic_patterns import (
    TrafficPattern,
    TrafficPatternError,
    build_traffic_pattern_plan,
    parse_traffic_pattern,
    target_users_at,
    traffic_plan_to_dictionary,
)


def test_parse_supported_traffic_patterns() -> None:
    """Parse all supported traffic-pattern names."""

    assert parse_traffic_pattern(
        "steady"
    ) == TrafficPattern.STEADY

    assert parse_traffic_pattern(
        "RAMP"
    ) == TrafficPattern.RAMP

    assert parse_traffic_pattern(
        "flash-sale"
    ) == TrafficPattern.FLASH_SALE

    assert parse_traffic_pattern(
        TrafficPattern.COOLDOWN
    ) == TrafficPattern.COOLDOWN


def test_steady_pattern_gradually_reaches_full_load() -> None:
    """STEADY ramps up and then remains full."""

    plan = build_traffic_pattern_plan(
        "STEADY",
        duration_sec=90,
    )

    assert target_users_at(
        plan,
        elapsed_seconds=0,
        maximum_users=10,
    ) == 1

    assert target_users_at(
        plan,
        elapsed_seconds=5,
        maximum_users=10,
    ) == 5

    assert target_users_at(
        plan,
        elapsed_seconds=10,
        maximum_users=10,
    ) == 10

    assert target_users_at(
        plan,
        elapsed_seconds=89,
        maximum_users=10,
    ) == 10

    assert target_users_at(
        plan,
        elapsed_seconds=90,
        maximum_users=10,
    ) == 0


def test_ramp_pattern_increases_in_stages() -> None:
    """RAMP uses 25, 50, 75 and 100 percent."""

    plan = build_traffic_pattern_plan(
        "RAMP",
        duration_sec=90,
    )

    assert target_users_at(
        plan,
        elapsed_seconds=0,
        maximum_users=12,
    ) == 3

    assert target_users_at(
        plan,
        elapsed_seconds=29,
        maximum_users=12,
    ) == 3

    assert target_users_at(
        plan,
        elapsed_seconds=30,
        maximum_users=12,
    ) == 6

    assert target_users_at(
        plan,
        elapsed_seconds=60,
        maximum_users=12,
    ) == 9

    assert target_users_at(
        plan,
        elapsed_seconds=75,
        maximum_users=12,
    ) == 12


def test_burst_pattern_returns_to_low_load() -> None:
    """BURST rises to maximum and then falls."""

    plan = build_traffic_pattern_plan(
        "BURST",
        duration_sec=90,
    )

    assert target_users_at(
        plan,
        elapsed_seconds=0,
        maximum_users=12,
    ) == 3

    assert target_users_at(
        plan,
        elapsed_seconds=20,
        maximum_users=12,
    ) == 12

    assert target_users_at(
        plan,
        elapsed_seconds=49,
        maximum_users=12,
    ) == 12

    assert target_users_at(
        plan,
        elapsed_seconds=50,
        maximum_users=12,
    ) == 3


def test_flash_sale_pattern_creates_sudden_load() -> None:
    """FLASH_SALE jumps from 10 to 100 percent."""

    plan = build_traffic_pattern_plan(
        "FLASH_SALE",
        duration_sec=90,
    )

    assert target_users_at(
        plan,
        elapsed_seconds=0,
        maximum_users=20,
    ) == 2

    assert target_users_at(
        plan,
        elapsed_seconds=10,
        maximum_users=20,
    ) == 20

    assert target_users_at(
        plan,
        elapsed_seconds=45,
        maximum_users=20,
    ) == 12

    assert target_users_at(
        plan,
        elapsed_seconds=70,
        maximum_users=20,
    ) == 6


def test_cooldown_pattern_reduces_load() -> None:
    """COOLDOWN reduces users as the run continues."""

    plan = build_traffic_pattern_plan(
        "COOLDOWN",
        duration_sec=90,
    )

    assert target_users_at(
        plan,
        elapsed_seconds=0,
        maximum_users=10,
    ) == 10

    assert target_users_at(
        plan,
        elapsed_seconds=30,
        maximum_users=10,
    ) == 5

    assert target_users_at(
        plan,
        elapsed_seconds=60,
        maximum_users=10,
    ) == 2


def test_plan_scales_to_different_duration() -> None:
    """A schedule scales beyond the 90-second baseline."""

    plan = build_traffic_pattern_plan(
        "BURST",
        duration_sec=180,
    )

    assert plan.stages[0].end_sec == Decimal(
        "40"
    )
    assert plan.stages[1].end_sec == Decimal(
        "100"
    )
    assert plan.stages[-1].end_sec == Decimal(
        "180"
    )

    assert target_users_at(
        plan,
        elapsed_seconds=40,
        maximum_users=8,
    ) == 8


def test_plan_metadata_is_serializable() -> None:
    """Traffic plans expose traceability metadata."""

    plan = build_traffic_pattern_plan(
        "RAMP",
        duration_sec=90,
    )

    metadata = traffic_plan_to_dictionary(
        plan
    )

    assert metadata["pattern"] == "RAMP"
    assert metadata["duration_sec"] == 90

    stages = metadata["stages"]

    assert isinstance(stages, list)
    assert len(stages) == 4


def test_invalid_traffic_configuration_is_rejected() -> None:
    """Reject unknown patterns and invalid values."""

    with pytest.raises(
        TrafficPatternError,
        match="Unsupported traffic pattern",
    ):
        build_traffic_pattern_plan(
            "UNKNOWN",
            duration_sec=90,
        )

    with pytest.raises(
        TrafficPatternError,
        match="between 30 and 3600",
    ):
        build_traffic_pattern_plan(
            "STEADY",
            duration_sec=10,
        )

    plan = build_traffic_pattern_plan(
        "STEADY",
        duration_sec=90,
    )

    with pytest.raises(
        TrafficPatternError,
        match="positive integer",
    ):
        target_users_at(
            plan,
            elapsed_seconds=10,
            maximum_users=0,
        )

    with pytest.raises(
        TrafficPatternError,
        match="cannot be negative",
    ):
        target_users_at(
            plan,
            elapsed_seconds=-1,
            maximum_users=10,
        )