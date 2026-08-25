from random import Random
from uuid import UUID

import pytest

from load_testing.config import (
    WorkloadConfig,
    WorkloadConfigurationError,
)
from load_testing.traffic_patterns import TrafficPattern


EXPERIMENT_RUN_ID = UUID(
    "11111111-1111-1111-1111-111111111111"
)


def test_load_workload_configuration_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load a complete valid workload configuration."""

    monkeypatch.setenv(
        "LOAD_EXPERIMENT_RUN_ID",
        str(EXPERIMENT_RUN_ID),
    )
    monkeypatch.setenv(
        "LOAD_SHOW_ID",
        "2",
    )
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
    monkeypatch.setenv(
        "LOAD_HOTSPOT_RATIO",
        "0.8",
    )
    monkeypatch.setenv(
        "LOAD_RANDOM_SEED",
        "42",
    )
    monkeypatch.setenv(
        "LOAD_MAXIMUM_USERS",
        "3",
    )
    monkeypatch.setenv(
        "LOAD_SPAWN_RATE_PER_SEC",
        "2.5",
    )
    monkeypatch.setenv(
        "LOAD_DURATION_SEC",
        "90",
    )
    monkeypatch.setenv(
        "LOAD_TRAFFIC_PATTERN",
        "BURST",
    )

    configuration = (
        WorkloadConfig.from_environment()
    )

    assert configuration.experiment_run_id == (
        EXPERIMENT_RUN_ID
    )
    assert configuration.show_id == 2
    assert configuration.user_ids == (1, 2, 3)
    assert configuration.seat_ids == (
        101,
        102,
        103,
        104,
    )
    assert configuration.hotspot_seat_ids == (
        101,
        102,
    )
    assert configuration.cold_seat_ids == (
        103,
        104,
    )
    assert configuration.hotspot_ratio == 0.8
    assert configuration.random_seed == 42
    assert configuration.maximum_users == 3
    assert configuration.spawn_rate_per_sec == 2.5
    assert configuration.duration_sec == 90
    assert (
        configuration.traffic_pattern
        == TrafficPattern.BURST
    )


def test_ratio_one_selects_only_hotspot_seats() -> None:
    """A ratio of one always targets the hotspot."""

    configuration = WorkloadConfig(
        experiment_run_id=EXPERIMENT_RUN_ID,
        show_id=2,
        user_ids=(1, 2),
        seat_ids=(101, 102, 103, 104),
        hotspot_seat_ids=(101, 102),
        hotspot_ratio=1.0,
        random_seed=42,
    )

    random_generator = Random(
        configuration.random_seed
    )

    selected_seats = {
        configuration.select_seat_id(
            random_generator
        )
        for _ in range(100)
    }

    assert selected_seats <= {101, 102}


def test_ratio_zero_selects_only_cold_seats() -> None:
    """A ratio of zero avoids hotspot seats."""

    configuration = WorkloadConfig(
        experiment_run_id=EXPERIMENT_RUN_ID,
        show_id=2,
        user_ids=(1, 2),
        seat_ids=(101, 102, 103, 104),
        hotspot_seat_ids=(101, 102),
        hotspot_ratio=0.0,
        random_seed=42,
    )

    random_generator = Random(
        configuration.random_seed
    )

    selected_seats = {
        configuration.select_seat_id(
            random_generator
        )
        for _ in range(100)
    }

    assert selected_seats <= {103, 104}


def test_reject_hotspot_seat_from_another_show() -> None:
    """Reject a hotspot absent from the complete seat list."""

    with pytest.raises(
        WorkloadConfigurationError,
        match="Every hotspot seat",
    ):
        WorkloadConfig(
            experiment_run_id=EXPERIMENT_RUN_ID,
            show_id=2,
            user_ids=(1, 2),
            seat_ids=(101, 102),
            hotspot_seat_ids=(999,),
            hotspot_ratio=0.8,
            random_seed=42,
        )