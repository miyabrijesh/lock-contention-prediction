from decimal import Decimal
from uuid import UUID

import pytest

from load_testing.booking_outcomes import (
    is_expected_business_conflict,
)
from load_testing.config import (
    WorkloadConfig,
    WorkloadConfigurationError,
)
from load_testing.demand_profiles import DemandProfileName
from load_testing.run_experiment import (
    PreparedWorkload,
    build_child_environment,
)
from load_testing.traffic_patterns import TrafficPattern


EXPERIMENT_RUN_ID = UUID(
    "11111111-1111-1111-1111-111111111111"
)


def _set_workload_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    demand_profile: str,
) -> None:
    """Set one complete deterministic workload environment."""

    monkeypatch.setenv(
        "LOAD_EXPERIMENT_RUN_ID",
        str(EXPERIMENT_RUN_ID),
    )
    monkeypatch.setenv("LOAD_SHOW_ID", "2")
    monkeypatch.setenv(
        "LOAD_USER_IDS",
        "[1, 2, 3]",
    )
    monkeypatch.setenv(
        "LOAD_SEAT_IDS",
        "[101, 102, 103, 104]",
    )
    monkeypatch.setenv(
        "LOAD_HOTSPOT_SEAT_IDS",
        "[101, 102]",
    )
    monkeypatch.setenv("LOAD_HOTSPOT_RATIO", "0.8")
    monkeypatch.setenv("LOAD_RANDOM_SEED", "42")
    monkeypatch.setenv("LOAD_MAXIMUM_USERS", "3")
    monkeypatch.setenv(
        "LOAD_SPAWN_RATE_PER_SEC",
        "2",
    )
    monkeypatch.setenv("LOAD_DURATION_SEC", "90")
    monkeypatch.setenv(
        "LOAD_TRAFFIC_PATTERN",
        "STEADY",
    )
    monkeypatch.setenv(
        "LOAD_DEMAND_PROFILE",
        demand_profile,
    )


def test_custom_demand_profile_loads_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carry a CUSTOM profile into the Locust configuration."""

    _set_workload_environment(
        monkeypatch,
        demand_profile="CUSTOM",
    )

    configuration = WorkloadConfig.from_environment()

    assert configuration.demand_profile == (
        DemandProfileName.CUSTOM
    )


def test_invalid_demand_profile_environment_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an unknown profile before Locust starts."""

    _set_workload_environment(
        monkeypatch,
        demand_profile="UNKNOWN",
    )

    with pytest.raises(
        WorkloadConfigurationError,
        match="LOAD_DEMAND_PROFILE is invalid",
    ):
        WorkloadConfig.from_environment()


def test_runner_passes_demand_profile_to_locust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Include demand provenance in the child environment."""

    monkeypatch.setattr(
        "load_testing.run_experiment._get_internal_token",
        lambda: "a" * 32,
    )

    workload = PreparedWorkload(
        experiment_run_id=EXPERIMENT_RUN_ID,
        show_id=2,
        user_count=3,
        spawn_rate_per_sec=Decimal("2.00"),
        duration_sec=90,
        traffic_pattern=TrafficPattern.STEADY,
        hotspot_ratio=Decimal("0.8000"),
        hotspot_seat_ids=(101, 102),
        seat_ids=(101, 102, 103, 104),
        user_ids=(1, 2, 3),
        random_seed=42,
        demand_profile=DemandProfileName.CUSTOM,
    )

    child_environment = build_child_environment(
        workload
    )

    assert child_environment[
        "LOAD_DEMAND_PROFILE"
    ] == "CUSTOM"


def test_only_seat_unavailable_is_an_expected_conflict() -> None:
    """Accept seat competition without hiding real API failures."""

    assert is_expected_business_conflict(
        409,
        "SEAT_UNAVAILABLE",
    )

    assert not is_expected_business_conflict(
        500,
        "INTERNAL_ERROR",
    )

    assert not is_expected_business_conflict(
        503,
        "LOCK_TIMEOUT",
    )

    assert not is_expected_business_conflict(
        409,
        "IDEMPOTENCY_KEY_REUSED",
    )