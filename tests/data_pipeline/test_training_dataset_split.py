import csv
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from data_pipeline.export_experiment import CSV_FIELD_NAMES
from data_pipeline.split_training_dataset import (
    TrainingDatasetSplitError,
    split_training_dataset,
)


TRAFFIC_PATTERNS = (
    "STEADY",
    "RAMP",
    "BURST",
    "FLASH_SALE",
    "COOLDOWN",
)
DEMAND_PROFILES = ("LOW", "MEDIUM", "HIGH")
PRESSURE_LEVELS = ("STANDARD", "STRESSED")
RANDOM_SEEDS = (4101, 4102)


def _sha256(file_path: Path) -> str:
    """Return one fixture-file checksum."""

    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _dataset_row(
    experiment_id: str,
    *,
    label: int,
) -> dict[str, str]:
    """Create the minimal schema-complete row used by the splitter."""

    row = {field: "0" for field in CSV_FIELD_NAMES}
    row.update(
        {
            "schema_version": "1.0.0",
            "experiment_run_id": experiment_id,
            "experiment_name": f"Experiment {experiment_id}",
            "sampled_at": "2026-08-25T00:00:00+00:00",
            "collector_version": "pytest",
            "code_version": "pytest",
            "booking_mode": "EXPERIMENT_RECYCLE",
            "is_complete": "true",
            "quality_notes": "{}",
            "future_high_contention_30s": str(label),
        }
    )
    return row


def _write_fixture(
    temporary_path: Path,
) -> tuple[Path, Path]:
    """Create a complete synthetic 60-experiment matrix dataset."""

    execution_manifest_file = temporary_path / "execution.json"
    dataset_file = temporary_path / "dataset.csv"
    dataset_manifest_file = temporary_path / "dataset.manifest.json"
    scenarios: dict[str, object] = {}
    rows: list[dict[str, str]] = []
    scenario_number = 0

    for traffic_pattern in TRAFFIC_PATTERNS:
        for demand_profile in DEMAND_PROFILES:
            for pressure_level in PRESSURE_LEVELS:
                for random_seed in RANDOM_SEEDS:
                    scenario_number += 1
                    experiment_id = str(UUID(int=scenario_number))
                    scenario_id = (
                        f"test-{demand_profile}-{traffic_pattern}-"
                        f"{pressure_level}-seed{random_seed}"
                    )
                    scenarios[scenario_id] = {
                        "status": "COMPLETED",
                        "configuration": {
                            "traffic_pattern": traffic_pattern,
                            "demand_profile": demand_profile,
                            "pressure_level": pressure_level,
                            "random_seed": random_seed,
                        },
                        "attempts": [
                            {
                                "status": "COMPLETED",
                                "experiment_run_id": experiment_id,
                            }
                        ],
                    }
                    rows.append(
                        _dataset_row(
                            experiment_id,
                            label=scenario_number % 2,
                        )
                    )

    execution_manifest_file.write_text(
        json.dumps({"scenarios": scenarios}),
        encoding="utf-8",
    )
    with dataset_file.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELD_NAMES)
        writer.writeheader()
        writer.writerows(rows)

    dataset_manifest_file.write_text(
        json.dumps(
            {
                "dataset_sha256": _sha256(dataset_file),
                "source_matrix": {
                    "execution_manifest_file": str(
                        execution_manifest_file
                    ),
                    "execution_manifest_sha256": _sha256(
                        execution_manifest_file
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    return dataset_file, dataset_manifest_file


def _split_experiment_ids(
    manifest_file: Path,
) -> dict[str, set[str]]:
    """Read experiment assignments from a generated manifest."""

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    return {
        name: set(value["experiment_ids"])
        for name, value in manifest["splits"].items()
    }


def test_split_is_grouped_complete_and_deterministic(
    tmp_path: Path,
) -> None:
    """Keep experiments disjoint while preserving matrix coverage."""

    dataset_file, dataset_manifest_file = _write_fixture(tmp_path)
    first_result = split_training_dataset(
        dataset_file=dataset_file,
        dataset_manifest_file=dataset_manifest_file,
        output_directory=tmp_path / "first",
    )
    second_result = split_training_dataset(
        dataset_file=dataset_file,
        dataset_manifest_file=dataset_manifest_file,
        output_directory=tmp_path / "second",
    )

    assert first_result.train.experiment_count == 42
    assert first_result.validation.experiment_count == 9
    assert first_result.test.experiment_count == 9
    assert first_result.assignment_sha256 == (
        second_result.assignment_sha256
    )

    assignments = _split_experiment_ids(first_result.manifest_file)
    assert assignments["train"].isdisjoint(assignments["validation"])
    assert assignments["train"].isdisjoint(assignments["test"])
    assert assignments["validation"].isdisjoint(assignments["test"])
    assert len(set().union(*assignments.values())) == 60

    manifest = json.loads(
        first_result.manifest_file.read_text(encoding="utf-8")
    )
    for split_value in manifest["splits"].values():
        coverage = split_value["matrix_coverage"]
        assert len(coverage["traffic_patterns"]) == 5
        assert len(coverage["demand_profiles"]) == 3
        assert len(coverage["pressure_levels"]) == 2
        assert len(coverage["random_seeds"]) == 2


def test_reject_changed_source_dataset(tmp_path: Path) -> None:
    """Reject a CSV changed after its manifest was written."""

    dataset_file, dataset_manifest_file = _write_fixture(tmp_path)
    dataset_file.write_text(
        dataset_file.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TrainingDatasetSplitError,
        match="checksum does not match",
    ):
        split_training_dataset(
            dataset_file=dataset_file,
            dataset_manifest_file=dataset_manifest_file,
            output_directory=tmp_path / "splits",
        )


def test_reject_existing_split_output(tmp_path: Path) -> None:
    """Never silently overwrite a versioned split artifact."""

    dataset_file, dataset_manifest_file = _write_fixture(tmp_path)
    output_directory = tmp_path / "splits"
    output_directory.mkdir()
    (output_directory / "train.csv").touch()

    with pytest.raises(
        TrainingDatasetSplitError,
        match="already exists",
    ):
        split_training_dataset(
            dataset_file=dataset_file,
            dataset_manifest_file=dataset_manifest_file,
            output_directory=output_directory,
        )
