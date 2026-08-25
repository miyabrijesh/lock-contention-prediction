from decimal import Decimal

import pytest

from load_testing.demand_profiles import (
    DemandProfileError,
    DemandProfileName,
    load_demand_profiles,
    validate_demand_configuration,
)


def test_loads_all_movielens_demand_profiles() -> None:
    """Load the LOW, MEDIUM and HIGH tracked profiles."""

    profiles = load_demand_profiles()

    assert set(profiles) == {
        DemandProfileName.LOW,
        DemandProfileName.MEDIUM,
        DemandProfileName.HIGH,
    }
    assert (
        profiles[DemandProfileName.HIGH]
        .source_dataset
        == "MovieLens 1M"
    )
    assert (
        profiles[DemandProfileName.HIGH]
        .source_rating_count
        == 457560
    )


def test_accepts_configuration_inside_profile_range() -> None:
    """Accept workload values supported by MovieLens calibration."""

    profile = validate_demand_configuration(
        profile_name=DemandProfileName.MEDIUM,
        hotspot_ratio=Decimal("0.75"),
        hotspot_seat_count=5,
    )

    assert profile is not None
    assert profile.name == DemandProfileName.MEDIUM


def test_rejects_configuration_outside_profile_range() -> None:
    """Reject a profile claim unsupported by its configuration."""

    with pytest.raises(
        DemandProfileError,
        match="HIGH demand requires hotspot_ratio",
    ):
        validate_demand_configuration(
            profile_name=DemandProfileName.HIGH,
            hotspot_ratio=Decimal("0.50"),
            hotspot_seat_count=4,
        )

    with pytest.raises(
        DemandProfileError,
        match="LOW demand requires between",
    ):
        validate_demand_configuration(
            profile_name=DemandProfileName.LOW,
            hotspot_ratio=Decimal("0.20"),
            hotspot_seat_count=5,
        )


def test_custom_configuration_remains_available() -> None:
    """Legacy experiments may remain explicitly uncalibrated."""

    profile = validate_demand_configuration(
        profile_name=DemandProfileName.CUSTOM,
        hotspot_ratio=Decimal("0.95"),
        hotspot_seat_count=20,
    )

    assert profile is None