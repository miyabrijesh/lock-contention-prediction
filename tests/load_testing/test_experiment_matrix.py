import json
from pathlib import Path

import pytest

from load_testing.experiment_matrix import (
    ExperimentMatrixError,
    load_experiment_matrix,
)


MATRIX_FILE = Path(
    "load_testing/config/experiment_matrix_v1.json"
)


def test_training_matrix_generates_sixty_unique_scenarios() -> None:
    """Expand every planned matrix dimension exactly once."""

    matrix = load_experiment_matrix(MATRIX_FILE)

    assert len(matrix.scenarios) == 60
    assert matrix.expected_run_count == 60
    assert matrix.expected_total_labelled_rows == 2700

    scenario_ids = {
        scenario.scenario_id
        for scenario in matrix.scenarios
    }
    experiment_names = {
        scenario.experiment_name
        for scenario in matrix.scenarios
    }

    assert len(scenario_ids) == 60
    assert len(experiment_names) == 60


def test_matrix_contains_every_controlled_dimension() -> None:
    """Preserve all profiles, patterns, pressures, and seeds."""

    matrix = load_experiment_matrix(MATRIX_FILE)

    assert {
        pattern.value
        for pattern in matrix.traffic_patterns
    } == {
        "STEADY",
        "RAMP",
        "BURST",
        "FLASH_SALE",
        "COOLDOWN",
    }
    assert {
        profile.name.value
        for profile in matrix.demand_profiles
    } == {
        "LOW",
        "MEDIUM",
        "HIGH",
    }
    assert {
        level.name
        for level in matrix.pressure_levels
    } == {
        "STANDARD",
        "STRESSED",
    }
    assert matrix.random_seeds == (4101, 4102)


def test_incorrect_expected_run_count_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject a manifest estimate that disagrees with the matrix."""

    raw_matrix = json.loads(
        MATRIX_FILE.read_text(encoding="utf-8")
    )
    raw_matrix["expected_run_count"] = 59

    invalid_file = tmp_path / "invalid_matrix.json"
    invalid_file.write_text(
        json.dumps(raw_matrix),
        encoding="utf-8",
    )

    with pytest.raises(
        ExperimentMatrixError,
        match="expected_run_count",
    ):
        load_experiment_matrix(invalid_file)