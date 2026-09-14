import csv
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from data_pipeline.export_verified_experiment_v2 import (
    CSV_FIELD_NAMES,
    SCHEMA_VERSION,
)
from data_pipeline.split_verified_dataset_v2 import (
    VerifiedDatasetSplitError,
    split_verified_dataset_v2,
)


TRAFFIC_PATTERNS = (
    "STEADY",
    "RAMP",
    "BURST",
    "FLASH_SALE",
    "COOLDOWN",
)

DEMAND_PROFILES = (
    "LOW",
    "MEDIUM",
    "HIGH",
)


def _sha256(file_path: Path) -> str:
    """Calculate one fixture checksum."""

    return hashlib.sha256(
        file_path.read_bytes()
    ).hexdigest()


def _dataset_row(
    *,
    experiment_id: str,
    prediction_number: int,
    sampled_at: datetime,
    positive: bool,
) -> dict[str, str]:
    """Create one complete schema-v2 fixture row."""

    row = {
        field_name: "0"
        for field_name in CSV_FIELD_NAMES
    }

    row.update({
        "schema_version": SCHEMA_VERSION,
        "prediction_id": str(
            UUID(int=prediction_number)
        ),
        "actual_label_contract_version": (
            "live-label-v2"
        ),
        "actual_label_assigned_at": (
            sampled_at
            + timedelta(seconds=31)
        ).isoformat(),
        "experiment_run_id": experiment_id,
        "experiment_name": (
            f"Experiment {experiment_id}"
        ),
        "sampled_at": sampled_at.isoformat(),
        "collector_version": "pytest",
        "code_version": "pytest",
        "booking_mode": (
            "EXPERIMENT_RECYCLE"
        ),
        "random_seed": "8101",
        "sample_interval_sec": "2",
        "simulated_work_ms": "150",
        "is_complete": "true",
        "quality_notes": "[]",
        "connection_utilization": "0.1000",
        "estimated_wait_age_ms": "0.000",
        "requests_per_sec": "20.000",
        "mean_latency_ms": "200.000",
        "p95_latency_ms": "400.000",
        "failure_rate": "0.0000",
        "hotspot_ratio": "0.2000",
        "booking_success_rate": "1.0000",
        "conflict_rate": "0.0000",
        "timeout_rate": "0.0000",
        "future_max_timeout_rate_30s": (
            "0.0000"
        ),
        "future_max_p95_latency_ms_30s": (
            "450.000"
        ),
        "future_high_contention_30s": (
            "1" if positive else "0"
        ),
    })

    if positive:
        row[
            "future_max_blocked_sessions_30s"
        ] = "2"

    return row


def _write_fixture(
    temporary_directory: Path,
) -> tuple[Path, Path, Path]:
    """Create a complete synthetic 15-experiment dataset."""

    execution_manifest_file = (
        temporary_directory / "execution.json"
    )
    dataset_file = (
        temporary_directory / "dataset.csv"
    )
    dataset_manifest_file = (
        temporary_directory
        / "dataset.manifest.json"
    )

    scenarios: dict[str, object] = {}
    rows: list[dict[str, str]] = []
    scenario_number = 0

    base_time = datetime(
        2026,
        9,
        13,
        12,
        0,
        tzinfo=UTC,
    )

    for traffic_pattern in TRAFFIC_PATTERNS:
        for demand_profile in DEMAND_PROFILES:
            scenario_number += 1

            experiment_id = str(
                UUID(int=scenario_number)
            )
            scenario_id = (
                f"test-{demand_profile}-"
                f"{traffic_pattern}"
            )

            scenarios[scenario_id] = {
                "status": "COMPLETED",
                "configuration": {
                    "traffic_pattern": (
                        traffic_pattern
                    ),
                    "demand_profile": (
                        demand_profile
                    ),
                    "pressure_level": (
                        "INTERMEDIATE"
                    ),
                    "random_seed": 8101,
                },
                "attempts": [
                    {
                        "status": "COMPLETED",
                        "experiment_run_id": (
                            experiment_id
                        ),
                    }
                ],
            }

            rows.append(
                _dataset_row(
                    experiment_id=experiment_id,
                    prediction_number=(
                        scenario_number
                    ),
                    sampled_at=(
                        base_time
                        + timedelta(
                            minutes=scenario_number
                        )
                    ),
                    positive=(
                        demand_profile != "LOW"
                    ),
                )
            )

    execution_manifest_file.write_text(
        json.dumps(
            {
                "matrix_name": (
                    "phase_8a_test_matrix"
                ),
                "scenarios": scenarios,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with dataset_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=CSV_FIELD_NAMES,
        )
        writer.writeheader()
        writer.writerows(rows)

    dataset_manifest_file.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "dataset_sha256": (
                    _sha256(dataset_file)
                ),
                "row_count": len(rows),
                "experiment_count": 15,
                "source_matrix": {
                    "execution_manifest_file": str(
                        execution_manifest_file
                    ),
                    "execution_manifest_sha256": (
                        _sha256(
                            execution_manifest_file
                        )
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return (
        dataset_file,
        dataset_manifest_file,
        execution_manifest_file,
    )


def _split_ids(
    manifest_file: Path,
) -> dict[str, set[str]]:
    """Read experiment assignments from a split manifest."""

    manifest = json.loads(
        manifest_file.read_text(
            encoding="utf-8"
        )
    )

    return {
        name: set(
            value["experiment_ids"]
        )
        for name, value
        in manifest["splits"].items()
    }


def test_split_is_grouped_and_deterministic(
    tmp_path: Path,
) -> None:
    """Create stable, disjoint 9/3/3 splits."""

    (
        dataset_file,
        dataset_manifest_file,
        _,
    ) = _write_fixture(tmp_path)

    first = split_verified_dataset_v2(
        dataset_file=dataset_file,
        dataset_manifest_file=(
            dataset_manifest_file
        ),
        output_directory=tmp_path / "first",
    )

    second = split_verified_dataset_v2(
        dataset_file=dataset_file,
        dataset_manifest_file=(
            dataset_manifest_file
        ),
        output_directory=tmp_path / "second",
    )

    assert first.assignment_sha256 == (
        second.assignment_sha256
    )

    assert first.train.experiment_count == 9
    assert (
        first.validation.experiment_count
        == 3
    )
    assert first.test.experiment_count == 3

    assignments = _split_ids(
        first.manifest_file
    )

    assert assignments["train"].isdisjoint(
        assignments["validation"]
    )
    assert assignments["train"].isdisjoint(
        assignments["test"]
    )
    assert assignments[
        "validation"
    ].isdisjoint(
        assignments["test"]
    )

    assert len(
        set().union(
            *assignments.values()
        )
    ) == 15

    manifest = json.loads(
        first.manifest_file.read_text(
            encoding="utf-8"
        )
    )

    assert manifest[
        "split_schema_version"
    ] == SCHEMA_VERSION
    assert manifest[
        "total_experiment_count"
    ] == 15
    assert manifest["total_row_count"] == 15

    for summary in (
        first.train,
        first.validation,
        first.test,
    ):
        assert summary.negative_label_count > 0
        assert summary.positive_label_count > 0
        assert summary.file_sha256 == (
            _sha256(summary.file_path)
        )

    train_coverage = manifest["splits"][
        "train"
    ]["matrix_coverage"]
    validation_coverage = manifest[
        "splits"
    ]["validation"]["matrix_coverage"]
    test_coverage = manifest["splits"][
        "test"
    ]["matrix_coverage"]

    assert len(
        train_coverage["traffic_patterns"]
    ) == 5
    assert len(
        validation_coverage[
            "traffic_patterns"
        ]
    ) == 3
    assert len(
        test_coverage["traffic_patterns"]
    ) == 3

    for coverage in (
        train_coverage,
        validation_coverage,
        test_coverage,
    ):
        assert set(
            coverage["demand_profiles"]
        ) == {
            "LOW",
            "MEDIUM",
            "HIGH",
        }


def test_changed_dataset_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject a dataset changed after manifest creation."""

    (
        dataset_file,
        dataset_manifest_file,
        _,
    ) = _write_fixture(tmp_path)

    with dataset_file.open(
        "a",
        encoding="utf-8",
    ) as output_file:
        output_file.write("\n")

    with pytest.raises(
        VerifiedDatasetSplitError,
        match="checksum does not match",
    ):
        split_verified_dataset_v2(
            dataset_file=dataset_file,
            dataset_manifest_file=(
                dataset_manifest_file
            ),
            output_directory=(
                tmp_path / "splits"
            ),
        )


def test_incomplete_scenario_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject a matrix containing an incomplete scenario."""

    (
        dataset_file,
        dataset_manifest_file,
        execution_manifest_file,
    ) = _write_fixture(tmp_path)

    execution_manifest = json.loads(
        execution_manifest_file.read_text(
            encoding="utf-8"
        )
    )

    first_scenario = next(
        iter(
            execution_manifest[
                "scenarios"
            ].values()
        )
    )
    first_scenario["status"] = "FAILED"

    execution_manifest_file.write_text(
        json.dumps(
            execution_manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    dataset_manifest = json.loads(
        dataset_manifest_file.read_text(
            encoding="utf-8"
        )
    )
    dataset_manifest["source_matrix"][
        "execution_manifest_sha256"
    ] = _sha256(
        execution_manifest_file
    )

    dataset_manifest_file.write_text(
        json.dumps(
            dataset_manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        VerifiedDatasetSplitError,
        match="is not COMPLETED",
    ):
        split_verified_dataset_v2(
            dataset_file=dataset_file,
            dataset_manifest_file=(
                dataset_manifest_file
            ),
            output_directory=(
                tmp_path / "splits"
            ),
        )


def test_wrong_header_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject columns outside the exact v2 schema."""

    (
        dataset_file,
        dataset_manifest_file,
        _,
    ) = _write_fixture(tmp_path)

    with dataset_file.open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        rows = list(
            csv.DictReader(input_file)
        )

    wrong_fields = list(
        CSV_FIELD_NAMES
    ) + ["probability"]

    with dataset_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=wrong_fields,
        )
        writer.writeheader()

        for row in rows:
            row["probability"] = "0.900000"
            writer.writerow(row)

    dataset_manifest = json.loads(
        dataset_manifest_file.read_text(
            encoding="utf-8"
        )
    )
    dataset_manifest["dataset_sha256"] = (
        _sha256(dataset_file)
    )

    dataset_manifest_file.write_text(
        json.dumps(
            dataset_manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        VerifiedDatasetSplitError,
        match="header does not match",
    ):
        split_verified_dataset_v2(
            dataset_file=dataset_file,
            dataset_manifest_file=(
                dataset_manifest_file
            ),
            output_directory=(
                tmp_path / "splits"
            ),
        )


def test_existing_output_is_not_overwritten(
    tmp_path: Path,
) -> None:
    """Preserve an existing immutable split file."""

    (
        dataset_file,
        dataset_manifest_file,
        _,
    ) = _write_fixture(tmp_path)

    output_directory = tmp_path / "splits"
    output_directory.mkdir()

    existing_file = (
        output_directory / "train.csv"
    )
    existing_file.write_text(
        "existing\n",
        encoding="utf-8",
    )

    with pytest.raises(
        VerifiedDatasetSplitError,
        match="already exists",
    ):
        split_verified_dataset_v2(
            dataset_file=dataset_file,
            dataset_manifest_file=(
                dataset_manifest_file
            ),
            output_directory=(
                output_directory
            ),
        )

    assert existing_file.read_text(
        encoding="utf-8"
    ) == "existing\n"