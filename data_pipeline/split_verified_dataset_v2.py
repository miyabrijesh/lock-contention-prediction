import argparse
import csv
import hashlib
import itertools
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from uuid import UUID

from data_pipeline.export_verified_experiment_v2 import (
    CSV_FIELD_NAMES,
    SCHEMA_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DATASET_FILE = Path(
    "data/processed/"
    "phase_8a_verified_dataset_v2.csv"
)
DEFAULT_DATASET_MANIFEST = Path(
    "data/processed/"
    "phase_8a_verified_dataset_v2.manifest.json"
)
DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/processed/splits/"
    "phase_8a_verified_dataset_v2"
)

TARGET_COLUMN = "future_high_contention_30s"
SPLIT_NAMES = ("train", "validation", "test")
SPLIT_EXPERIMENT_COUNTS = {
    "train": 9,
    "validation": 3,
    "test": 3,
}
EXPECTED_EXPERIMENT_COUNT = 15
MINIMUM_CLASS_FRACTION = Fraction(1, 10)


@dataclass(frozen=True)
class ScenarioMetadata:
    scenario_id: str
    experiment_run_id: str
    traffic_pattern: str
    demand_profile: str
    pressure_level: str
    random_seed: int


@dataclass(frozen=True)
class SplitSummary:
    name: str
    file_path: Path
    file_sha256: str
    row_count: int
    experiment_count: int
    negative_label_count: int
    positive_label_count: int


@dataclass(frozen=True)
class VerifiedDatasetSplitResult:
    manifest_file: Path
    assignment_sha256: str
    train: SplitSummary
    validation: SplitSummary
    test: SplitSummary


class VerifiedDatasetSplitError(Exception):
    """Expected verified-dataset split failure."""


def _resolve(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()

    return (PROJECT_ROOT / path).resolve()


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _sha256(file_path: Path) -> str:
    digest = hashlib.sha256()

    try:
        with file_path.open("rb") as input_file:
            while True:
                chunk = input_file.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)
    except OSError as error:
        raise VerifiedDatasetSplitError(
            f"Could not read {file_path}: {error}"
        ) from error

    return digest.hexdigest()


def _load_json(
    file_path: Path,
    description: str,
) -> dict[str, object]:
    try:
        value = json.loads(
            file_path.read_text(encoding="utf-8")
        )
    except OSError as error:
        raise VerifiedDatasetSplitError(
            f"Could not read {description} "
            f"{file_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise VerifiedDatasetSplitError(
            f"{description.capitalize()} is not "
            "valid JSON."
        ) from error

    if not isinstance(value, dict):
        raise VerifiedDatasetSplitError(
            f"{description.capitalize()} must "
            "contain a JSON object."
        )

    return value


def _required_text(
    mapping: dict[str, object],
    field_name: str,
    description: str,
) -> str:
    value = mapping.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise VerifiedDatasetSplitError(
            f"{description} must contain a nonempty "
            f"{field_name!r}."
        )

    return value.strip()


def _load_scenario_metadata(
    execution_manifest_file: Path,
) -> dict[str, ScenarioMetadata]:
    manifest = _load_json(
        execution_manifest_file,
        "execution manifest",
    )

    scenarios = manifest.get("scenarios")

    if not isinstance(scenarios, dict):
        raise VerifiedDatasetSplitError(
            "Execution manifest does not contain a "
            "scenarios object."
        )

    if len(scenarios) != EXPECTED_EXPERIMENT_COUNT:
        raise VerifiedDatasetSplitError(
            "Phase 8A requires exactly "
            f"{EXPECTED_EXPERIMENT_COUNT} scenarios."
        )

    metadata: dict[str, ScenarioMetadata] = {}

    for scenario_id, raw_entry in scenarios.items():
        if (
            not isinstance(scenario_id, str)
            or not isinstance(raw_entry, dict)
        ):
            raise VerifiedDatasetSplitError(
                "Execution manifest contains an "
                "invalid scenario entry."
            )

        if raw_entry.get("status") != "COMPLETED":
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} is not "
                "COMPLETED."
            )

        configuration = raw_entry.get(
            "configuration"
        )
        attempts = raw_entry.get("attempts")

        if not isinstance(configuration, dict):
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} has an "
                "invalid configuration."
            )

        if not isinstance(attempts, list) or not attempts:
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} has no "
                "completed attempt."
            )

        latest_attempt = attempts[-1]

        if not isinstance(latest_attempt, dict):
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} has an "
                "invalid latest attempt."
            )

        experiment_id = _required_text(
            latest_attempt,
            "experiment_run_id",
            f"Scenario {scenario_id} attempt",
        )

        try:
            UUID(experiment_id)
        except ValueError as error:
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} has an "
                "invalid experiment UUID."
            ) from error

        if experiment_id in metadata:
            raise VerifiedDatasetSplitError(
                "Execution manifest contains a "
                "duplicate experiment ID."
            )

        random_seed = configuration.get(
            "random_seed"
        )

        if (
            not isinstance(random_seed, int)
            or isinstance(random_seed, bool)
        ):
            raise VerifiedDatasetSplitError(
                f"Scenario {scenario_id} has an "
                "invalid random seed."
            )

        metadata[experiment_id] = (
            ScenarioMetadata(
                scenario_id=scenario_id,
                experiment_run_id=experiment_id,
                traffic_pattern=_required_text(
                    configuration,
                    "traffic_pattern",
                    (
                        f"Scenario {scenario_id} "
                        "configuration"
                    ),
                ),
                demand_profile=_required_text(
                    configuration,
                    "demand_profile",
                    (
                        f"Scenario {scenario_id} "
                        "configuration"
                    ),
                ),
                pressure_level=_required_text(
                    configuration,
                    "pressure_level",
                    (
                        f"Scenario {scenario_id} "
                        "configuration"
                    ),
                ),
                random_seed=random_seed,
            )
        )

    return metadata


def _load_dataset_rows(
    dataset_file: Path,
) -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, Counter[int]],
]:
    rows_by_experiment: dict[
        str,
        list[dict[str, str]],
    ] = defaultdict(list)
    labels_by_experiment: dict[
        str,
        Counter[int],
    ] = defaultdict(Counter)

    prediction_ids: set[str] = set()
    sample_keys: set[tuple[str, str]] = set()

    try:
        with dataset_file.open(
            "r",
            encoding="utf-8",
            newline="",
        ) as input_file:
            reader = csv.DictReader(input_file)

            if reader.fieldnames != list(
                CSV_FIELD_NAMES
            ):
                raise VerifiedDatasetSplitError(
                    "Dataset CSV header does not "
                    "match schema 2.0.0."
                )

            for row_number, raw_row in enumerate(
                reader,
                start=2,
            ):
                if (
                    None in raw_row
                    or any(
                        value is None
                        for value in raw_row.values()
                    )
                ):
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} contains "
                        "missing or extra values."
                    )

                row = {
                    str(key): str(value)
                    for key, value in raw_row.items()
                }

                if (
                    row["schema_version"]
                    != SCHEMA_VERSION
                ):
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} does not "
                        "use schema 2.0.0."
                    )

                if (
                    row[
                        "actual_label_contract_version"
                    ]
                    != "live-label-v2"
                ):
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} does not "
                        "use live-label-v2."
                    )

                experiment_id = row[
                    "experiment_run_id"
                ].strip()
                prediction_id = row[
                    "prediction_id"
                ].strip()
                sampled_at = row[
                    "sampled_at"
                ].strip()

                try:
                    UUID(experiment_id)
                    UUID(prediction_id)
                except ValueError as error:
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} contains "
                        "an invalid UUID."
                    ) from error

                if prediction_id in prediction_ids:
                    raise VerifiedDatasetSplitError(
                        "Dataset contains a duplicate "
                        f"prediction ID: {prediction_id}."
                    )

                sample_key = (
                    experiment_id,
                    sampled_at,
                )

                if sample_key in sample_keys:
                    raise VerifiedDatasetSplitError(
                        "Dataset contains a duplicate "
                        "experiment/sample pair."
                    )

                try:
                    label = int(
                        row[TARGET_COLUMN]
                    )
                except ValueError as error:
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} has an "
                        "invalid target label."
                    ) from error

                if label not in {0, 1}:
                    raise VerifiedDatasetSplitError(
                        f"Row {row_number} target "
                        "must be 0 or 1."
                    )

                prediction_ids.add(prediction_id)
                sample_keys.add(sample_key)
                rows_by_experiment[
                    experiment_id
                ].append(row)
                labels_by_experiment[
                    experiment_id
                ][label] += 1

    except VerifiedDatasetSplitError:
        raise
    except (OSError, csv.Error) as error:
        raise VerifiedDatasetSplitError(
            f"Could not read dataset "
            f"{dataset_file}: {error}"
        ) from error

    if not rows_by_experiment:
        raise VerifiedDatasetSplitError(
            "Verified dataset is empty."
        )

    return (
        dict(rows_by_experiment),
        dict(labels_by_experiment),
    )


def _label_totals(
    experiment_ids: tuple[str, ...]
    | list[str],
    labels_by_experiment: dict[
        str,
        Counter[int],
    ],
) -> Counter[int]:
    totals: Counter[int] = Counter()

    for experiment_id in experiment_ids:
        totals.update(
            labels_by_experiment[
                experiment_id
            ]
        )

    return totals


def _class_fraction_is_valid(
    experiment_ids: tuple[str, ...]
    | list[str],
    labels_by_experiment: dict[
        str,
        Counter[int],
    ],
) -> bool:
    totals = _label_totals(
        experiment_ids,
        labels_by_experiment,
    )
    total_rows = totals[0] + totals[1]

    if (
        total_rows == 0
        or totals[0] == 0
        or totals[1] == 0
    ):
        return False

    minority_fraction = Fraction(
        min(totals[0], totals[1]),
        total_rows,
    )

    return (
        minority_fraction
        >= MINIMUM_CLASS_FRACTION
    )


def _demand_profiles(
    experiment_ids: tuple[str, ...]
    | list[str],
    metadata: dict[str, ScenarioMetadata],
) -> set[str]:
    return {
        metadata[
            experiment_id
        ].demand_profile
        for experiment_id in experiment_ids
    }


def _traffic_patterns(
    experiment_ids: tuple[str, ...]
    | list[str],
    metadata: dict[str, ScenarioMetadata],
) -> set[str]:
    return {
        metadata[
            experiment_id
        ].traffic_pattern
        for experiment_id in experiment_ids
    }


def _valid_three_experiment_split(
    experiment_ids: tuple[str, ...],
    metadata: dict[str, ScenarioMetadata],
    labels_by_experiment: dict[
        str,
        Counter[int],
    ],
) -> bool:
    return (
        len(experiment_ids) == 3
        and _demand_profiles(
            experiment_ids,
            metadata,
        )
        == {"LOW", "MEDIUM", "HIGH"}
        and len(
            _traffic_patterns(
                experiment_ids,
                metadata,
            )
        )
        == 3
        and _class_fraction_is_valid(
            experiment_ids,
            labels_by_experiment,
        )
    )


def _assignment_tie_key(
    assignment: dict[str, list[str]],
    metadata: dict[str, ScenarioMetadata],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(
            sorted(
                metadata[
                    experiment_id
                ].scenario_id
                for experiment_id in assignment[
                    split_name
                ]
            )
        )
        for split_name in SPLIT_NAMES
    )


def _find_assignment(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
    labels_by_experiment: dict[
        str,
        Counter[int],
    ],
) -> dict[str, list[str]]:
    ordered_ids = sorted(
        experiment_ids,
        key=lambda experiment_id: (
            metadata[
                experiment_id
            ].scenario_id
        ),
    )

    three_experiment_groups = [
        group
        for group in itertools.combinations(
            ordered_ids,
            3,
        )
        if _valid_three_experiment_split(
            group,
            metadata,
            labels_by_experiment,
        )
    ]

    global_totals = _label_totals(
        ordered_ids,
        labels_by_experiment,
    )
    global_rows = (
        global_totals[0] + global_totals[1]
    )
    global_positive_fraction = Fraction(
        global_totals[1],
        global_rows,
    )

    best_assignment: dict[
        str,
        list[str],
    ] | None = None
    best_key: tuple[
        Fraction,
        tuple[tuple[str, ...], ...],
    ] | None = None

    all_id_set = set(ordered_ids)

    for validation_group in (
        three_experiment_groups
    ):
        validation_set = set(
            validation_group
        )

        for test_group in (
            three_experiment_groups
        ):
            test_set = set(test_group)

            if validation_set & test_set:
                continue

            train_ids = sorted(
                all_id_set
                - validation_set
                - test_set,
                key=lambda experiment_id: (
                    metadata[
                        experiment_id
                    ].scenario_id
                ),
            )

            if len(train_ids) != 9:
                continue

            if (
                _demand_profiles(
                    train_ids,
                    metadata,
                )
                != {"LOW", "MEDIUM", "HIGH"}
            ):
                continue

            if len(
                _traffic_patterns(
                    train_ids,
                    metadata,
                )
            ) != 5:
                continue

            if not _class_fraction_is_valid(
                train_ids,
                labels_by_experiment,
            ):
                continue

            assignment = {
                "train": train_ids,
                "validation": sorted(
                    validation_group,
                    key=lambda experiment_id: (
                        metadata[
                            experiment_id
                        ].scenario_id
                    ),
                ),
                "test": sorted(
                    test_group,
                    key=lambda experiment_id: (
                        metadata[
                            experiment_id
                        ].scenario_id
                    ),
                ),
            }

            score = Fraction(0, 1)

            for split_ids in (
                assignment.values()
            ):
                totals = _label_totals(
                    split_ids,
                    labels_by_experiment,
                )
                rows = totals[0] + totals[1]
                positive_fraction = Fraction(
                    totals[1],
                    rows,
                )
                score += abs(
                    positive_fraction
                    - global_positive_fraction
                )

            key = (
                score,
                _assignment_tie_key(
                    assignment,
                    metadata,
                ),
            )

            if (
                best_key is None
                or key < best_key
            ):
                best_key = key
                best_assignment = assignment

    if best_assignment is None:
        raise VerifiedDatasetSplitError(
            "Could not find a deterministic split "
            "satisfying demand, traffic, and class "
            "coverage requirements."
        )

    return best_assignment


def _write_csv_atomically(
    file_path: Path,
    rows: list[dict[str, str]],
) -> str:
    if file_path.exists():
        raise VerifiedDatasetSplitError(
            f"Split file already exists: "
            f"{file_path}"
        )

    file_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    temporary_file = file_path.with_suffix(
        f"{file_path.suffix}.tmp"
    )

    if temporary_file.exists():
        raise VerifiedDatasetSplitError(
            f"Temporary split file exists: "
            f"{temporary_file}"
        )

    ordered_rows = sorted(
        rows,
        key=lambda row: (
            row["experiment_run_id"],
            row["sampled_at"],
            row["prediction_id"],
        ),
    )

    try:
        with temporary_file.open(
            "x",
            encoding="utf-8",
            newline="",
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=CSV_FIELD_NAMES,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(ordered_rows)

        temporary_file.replace(file_path)
    except Exception:
        temporary_file.unlink(
            missing_ok=True
        )
        raise

    return _sha256(file_path)


def _write_json_atomically(
    file_path: Path,
    value: dict[str, object],
) -> None:
    if file_path.exists():
        raise VerifiedDatasetSplitError(
            f"Split manifest already exists: "
            f"{file_path}"
        )

    temporary_file = file_path.with_suffix(
        f"{file_path.suffix}.tmp"
    )

    if temporary_file.exists():
        raise VerifiedDatasetSplitError(
            f"Temporary manifest exists: "
            f"{temporary_file}"
        )

    try:
        with temporary_file.open(
            "x",
            encoding="utf-8",
        ) as output_file:
            json.dump(
                value,
                output_file,
                indent=2,
                sort_keys=True,
            )
            output_file.write("\n")

        temporary_file.replace(file_path)
    except Exception:
        temporary_file.unlink(
            missing_ok=True
        )
        raise


def _coverage_dictionary(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
) -> dict[str, object]:
    return {
        "traffic_patterns": sorted(
            _traffic_patterns(
                experiment_ids,
                metadata,
            )
        ),
        "demand_profiles": sorted(
            _demand_profiles(
                experiment_ids,
                metadata,
            )
        ),
        "pressure_levels": sorted({
            metadata[
                experiment_id
            ].pressure_level
            for experiment_id in experiment_ids
        }),
        "random_seeds": sorted({
            metadata[
                experiment_id
            ].random_seed
            for experiment_id in experiment_ids
        }),
        "scenario_ids": sorted(
            metadata[
                experiment_id
            ].scenario_id
            for experiment_id in experiment_ids
        ),
    }


def split_verified_dataset_v2(
    *,
    dataset_file: Path = DEFAULT_DATASET_FILE,
    dataset_manifest_file: Path = (
        DEFAULT_DATASET_MANIFEST
    ),
    output_directory: Path = (
        DEFAULT_OUTPUT_DIRECTORY
    ),
) -> VerifiedDatasetSplitResult:
    dataset_path = _resolve(dataset_file)
    dataset_manifest_path = _resolve(
        dataset_manifest_file
    )
    output_path = _resolve(
        output_directory
    )

    if (
        not dataset_path.is_file()
        or not dataset_manifest_path.is_file()
    ):
        raise VerifiedDatasetSplitError(
            "The verified dataset and its "
            "manifest must both exist."
        )

    split_paths = {
        split_name: (
            output_path / f"{split_name}.csv"
        )
        for split_name in SPLIT_NAMES
    }
    split_manifest_path = (
        output_path / "splits.manifest.json"
    )

    existing_paths = [
        path
        for path in [
            *split_paths.values(),
            split_manifest_path,
        ]
        if path.exists()
    ]

    if existing_paths:
        raise VerifiedDatasetSplitError(
            "Split output already exists; use a "
            "new versioned directory."
        )

    dataset_sha256 = _sha256(dataset_path)
    dataset_manifest_sha256 = _sha256(
        dataset_manifest_path
    )
    dataset_manifest = _load_json(
        dataset_manifest_path,
        "dataset manifest",
    )

    if (
        dataset_manifest.get(
            "schema_version"
        )
        != SCHEMA_VERSION
    ):
        raise VerifiedDatasetSplitError(
            "Dataset manifest does not use "
            "schema 2.0.0."
        )

    if (
        dataset_manifest.get(
            "dataset_sha256"
        )
        != dataset_sha256
    ):
        raise VerifiedDatasetSplitError(
            "Dataset checksum does not match "
            "its manifest."
        )

    source_matrix = dataset_manifest.get(
        "source_matrix"
    )

    if not isinstance(source_matrix, dict):
        raise VerifiedDatasetSplitError(
            "Dataset manifest source_matrix "
            "is invalid."
        )

    execution_manifest_value = (
        source_matrix.get(
            "execution_manifest_file"
        )
    )
    execution_manifest_sha256 = (
        source_matrix.get(
            "execution_manifest_sha256"
        )
    )

    if (
        not isinstance(
            execution_manifest_value,
            str,
        )
        or not isinstance(
            execution_manifest_sha256,
            str,
        )
    ):
        raise VerifiedDatasetSplitError(
            "Dataset manifest contains invalid "
            "execution-manifest provenance."
        )

    execution_manifest_path = _resolve(
        Path(execution_manifest_value)
    )

    if (
        not execution_manifest_path.is_file()
        or _sha256(
            execution_manifest_path
        )
        != execution_manifest_sha256
    ):
        raise VerifiedDatasetSplitError(
            "Execution manifest is missing or "
            "its checksum changed."
        )

    metadata = _load_scenario_metadata(
        execution_manifest_path
    )
    (
        rows_by_experiment,
        labels_by_experiment,
    ) = _load_dataset_rows(dataset_path)

    if set(rows_by_experiment) != set(metadata):
        raise VerifiedDatasetSplitError(
            "Dataset experiment IDs do not "
            "match the execution manifest."
        )

    if len(rows_by_experiment) != (
        EXPECTED_EXPERIMENT_COUNT
    ):
        raise VerifiedDatasetSplitError(
            "Verified dataset must contain "
            f"{EXPECTED_EXPERIMENT_COUNT} "
            "experiments."
        )

    if dataset_manifest.get(
        "row_count"
    ) != sum(
        len(rows)
        for rows in rows_by_experiment.values()
    ):
        raise VerifiedDatasetSplitError(
            "Dataset row count does not match "
            "its manifest."
        )

    assignment = _find_assignment(
        list(rows_by_experiment),
        metadata,
        labels_by_experiment,
    )

    assignment_text = json.dumps(
        assignment,
        sort_keys=True,
        separators=(",", ":"),
    )
    assignment_sha256 = hashlib.sha256(
        assignment_text.encode("utf-8")
    ).hexdigest()

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    summaries: dict[str, SplitSummary] = {}
    manifest_splits: dict[str, object] = {}
    created_files: list[Path] = []

    try:
        for split_name in SPLIT_NAMES:
            experiment_ids = assignment[
                split_name
            ]
            rows = [
                row
                for experiment_id in experiment_ids
                for row in rows_by_experiment[
                    experiment_id
                ]
            ]
            labels = _label_totals(
                experiment_ids,
                labels_by_experiment,
            )

            file_sha256 = (
                _write_csv_atomically(
                    split_paths[split_name],
                    rows,
                )
            )
            created_files.append(
                split_paths[split_name]
            )

            summary = SplitSummary(
                name=split_name,
                file_path=(
                    split_paths[split_name]
                ),
                file_sha256=file_sha256,
                row_count=len(rows),
                experiment_count=len(
                    experiment_ids
                ),
                negative_label_count=(
                    labels[0]
                ),
                positive_label_count=(
                    labels[1]
                ),
            )
            summaries[split_name] = summary

            manifest_splits[split_name] = {
                "file": _display_path(
                    summary.file_path
                ),
                "file_sha256": (
                    summary.file_sha256
                ),
                "row_count": (
                    summary.row_count
                ),
                "experiment_count": (
                    summary.experiment_count
                ),
                "negative_label_count": (
                    summary
                    .negative_label_count
                ),
                "positive_label_count": (
                    summary
                    .positive_label_count
                ),
                "label_distribution": {
                    "negative": (
                        summary
                        .negative_label_count
                    ),
                    "positive": (
                        summary
                        .positive_label_count
                    ),
                },
                "experiment_ids": (
                    experiment_ids
                ),
                "matrix_coverage": (
                    _coverage_dictionary(
                        experiment_ids,
                        metadata,
                    )
                ),
            }

        manifest: dict[str, object] = {
            "split_schema_version": (
                SCHEMA_VERSION
            ),
            "dataset_schema_version": (
                SCHEMA_VERSION
            ),
            "created_at": datetime.now(
                UTC
            ).isoformat(),
            "strategy": (
                "experiment_grouped_9_3_3_"
                "demand_and_class_balanced"
            ),
            "assignment_sha256": (
                assignment_sha256
            ),
            "source_dataset_file": (
                _display_path(dataset_path)
            ),
            "source_dataset_sha256": (
                dataset_sha256
            ),
            "source_dataset_manifest_file": (
                _display_path(
                    dataset_manifest_path
                )
            ),
            "source_dataset_manifest_sha256": (
                dataset_manifest_sha256
            ),
            "total_row_count": sum(
                summary.row_count
                for summary in (
                    summaries.values()
                )
            ),
            "total_experiment_count": sum(
                summary.experiment_count
                for summary in (
                    summaries.values()
                )
            ),
            "target_column": TARGET_COLUMN,
            "csv_field_names": list(
                CSV_FIELD_NAMES
            ),
            "splits": manifest_splits,
        }

        _write_json_atomically(
            split_manifest_path,
            manifest,
        )
        created_files.append(
            split_manifest_path
        )

    except Exception:
        for created_file in created_files:
            created_file.unlink(
                missing_ok=True
            )
        raise

    return VerifiedDatasetSplitResult(
        manifest_file=split_manifest_path,
        assignment_sha256=(
            assignment_sha256
        ),
        train=summaries["train"],
        validation=summaries[
            "validation"
        ],
        test=summaries["test"],
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create deterministic experiment-grouped "
            "splits for the verified Phase 8A "
            "schema-2.0.0 dataset."
        )
    )
    parser.add_argument(
        "--dataset-file",
        type=Path,
        default=DEFAULT_DATASET_FILE,
    )
    parser.add_argument(
        "--dataset-manifest-file",
        type=Path,
        default=DEFAULT_DATASET_MANIFEST,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )

    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        result = split_verified_dataset_v2(
            dataset_file=arguments.dataset_file,
            dataset_manifest_file=(
                arguments.dataset_manifest_file
            ),
            output_directory=(
                arguments.output_directory
            ),
        )
    except VerifiedDatasetSplitError as error:
        print(
            f"Verified dataset split error: "
            f"{error}",
            file=sys.stderr,
        )
        return 2

    print("Verified dataset splits created:")

    for summary in (
        result.train,
        result.validation,
        result.test,
    ):
        print(
            f"  {summary.name}: "
            f"rows={summary.row_count}, "
            f"experiments="
            f"{summary.experiment_count}, "
            f"negative="
            f"{summary.negative_label_count}, "
            f"positive="
            f"{summary.positive_label_count}"
        )

    print(
        "  Assignment SHA-256:",
        result.assignment_sha256,
    )
    print(
        "  Manifest:",
        _display_path(
            result.manifest_file
        ),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())