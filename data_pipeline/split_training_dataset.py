import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from data_pipeline.export_experiment import CSV_FIELD_NAMES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_FILE = Path(
    "data/processed/contention_training_dataset_v1.csv"
)
DEFAULT_DATASET_MANIFEST = Path(
    "data/processed/contention_training_dataset_v1.manifest.json"
)
DEFAULT_OUTPUT_DIRECTORY = Path(
    "data/processed/splits/contention_training_dataset_v1"
)
DEFAULT_SPLIT_SEED = 3201
DEFAULT_MAXIMUM_ATTEMPTS = 5_000

SPLIT_NAMES = ("train", "validation", "test")
SPLIT_EXPERIMENT_COUNTS = {
    "train": 42,
    "validation": 9,
    "test": 9,
}


class TrainingDatasetSplitError(Exception):
    """Expected failure while creating leakage-safe data splits."""


@dataclass(frozen=True)
class ScenarioMetadata:
    """Matrix dimensions associated with one experiment."""

    scenario_id: str
    traffic_pattern: str
    demand_profile: str
    pressure_level: str
    random_seed: int


@dataclass(frozen=True)
class SplitSummary:
    """Summary of one generated CSV split."""

    name: str
    file_path: Path
    file_sha256: str
    row_count: int
    experiment_count: int
    negative_label_count: int
    positive_label_count: int


@dataclass(frozen=True)
class TrainingDatasetSplitResult:
    """Files and counts produced by one split operation."""

    manifest_file: Path
    assignment_sha256: str
    train: SplitSummary
    validation: SplitSummary
    test: SplitSummary


def _resolve(path: Path) -> Path:
    """Resolve a relative path from the project root."""

    return path if path.is_absolute() else PROJECT_ROOT / path


def _display_path(path: Path) -> str:
    """Prefer project-relative paths in generated manifests."""

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _sha256(file_path: Path) -> str:
    """Calculate a file checksum incrementally."""

    digest = hashlib.sha256()
    with file_path.open("rb") as input_file:
        for block in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(file_path: Path, description: str) -> dict[str, object]:
    """Load one JSON object."""

    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise TrainingDatasetSplitError(
            f"Could not read {description} {file_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise TrainingDatasetSplitError(
            f"{description.capitalize()} is not valid JSON: {file_path}"
        ) from error
    if not isinstance(value, dict):
        raise TrainingDatasetSplitError(
            f"The {description} root must be a JSON object."
        )
    return value


def _required_text(
    mapping: dict[str, object],
    field_name: str,
    description: str,
) -> str:
    """Read one required nonempty string."""

    value = mapping.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise TrainingDatasetSplitError(
            f"{description} must contain a nonempty {field_name!r}."
        )
    return value.strip()


def _load_scenario_metadata(
    execution_manifest_file: Path,
) -> dict[str, ScenarioMetadata]:
    """Map every completed matrix experiment to its dimensions."""

    manifest = _load_json(
        execution_manifest_file,
        "execution manifest",
    )
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, dict):
        raise TrainingDatasetSplitError(
            "Execution manifest does not contain a scenarios object."
        )

    metadata: dict[str, ScenarioMetadata] = {}
    for scenario_id, raw_entry in scenarios.items():
        if not isinstance(scenario_id, str) or not isinstance(raw_entry, dict):
            raise TrainingDatasetSplitError(
                "Execution manifest contains an invalid scenario entry."
            )
        if raw_entry.get("status") != "COMPLETED":
            raise TrainingDatasetSplitError(
                f"Scenario {scenario_id} is not COMPLETED."
            )
        configuration = raw_entry.get("configuration")
        attempts = raw_entry.get("attempts")
        if not isinstance(configuration, dict):
            raise TrainingDatasetSplitError(
                f"Scenario {scenario_id} configuration is invalid."
            )
        if not isinstance(attempts, list) or not attempts:
            raise TrainingDatasetSplitError(
                f"Scenario {scenario_id} has no completed attempt."
            )
        latest_attempt = attempts[-1]
        if not isinstance(latest_attempt, dict):
            raise TrainingDatasetSplitError(
                f"Scenario {scenario_id} latest attempt is invalid."
            )
        experiment_id = _required_text(
            latest_attempt,
            "experiment_run_id",
            f"Scenario {scenario_id} attempt",
        )
        if experiment_id in metadata:
            raise TrainingDatasetSplitError(
                "Execution manifest contains a duplicate experiment ID."
            )
        random_seed = configuration.get("random_seed")
        if not isinstance(random_seed, int) or isinstance(random_seed, bool):
            raise TrainingDatasetSplitError(
                f"Scenario {scenario_id} random_seed is invalid."
            )
        metadata[experiment_id] = ScenarioMetadata(
            scenario_id=scenario_id,
            traffic_pattern=_required_text(
                configuration,
                "traffic_pattern",
                f"Scenario {scenario_id} configuration",
            ),
            demand_profile=_required_text(
                configuration,
                "demand_profile",
                f"Scenario {scenario_id} configuration",
            ),
            pressure_level=_required_text(
                configuration,
                "pressure_level",
                f"Scenario {scenario_id} configuration",
            ),
            random_seed=random_seed,
        )
    return metadata


def _load_dataset_rows(
    dataset_file: Path,
) -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, Counter[int]],
]:
    """Load rows grouped by experiment without splitting them."""

    rows_by_experiment: dict[str, list[dict[str, str]]] = defaultdict(list)
    labels_by_experiment: dict[str, Counter[int]] = defaultdict(Counter)
    try:
        with dataset_file.open("r", encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            if reader.fieldnames != list(CSV_FIELD_NAMES):
                raise TrainingDatasetSplitError(
                    "Dataset CSV header does not match the export schema."
                )
            for row_number, raw_row in enumerate(reader, start=2):
                if None in raw_row or any(value is None for value in raw_row.values()):
                    raise TrainingDatasetSplitError(
                        f"Row {row_number} contains missing or extra CSV values."
                    )
                row = {str(key): str(value) for key, value in raw_row.items()}
                experiment_id = row["experiment_run_id"].strip()
                if not experiment_id:
                    raise TrainingDatasetSplitError(
                        f"Row {row_number} has no experiment ID."
                    )
                try:
                    label = int(row["future_high_contention_30s"])
                except ValueError as error:
                    raise TrainingDatasetSplitError(
                        f"Row {row_number} has an invalid target label."
                    ) from error
                if label not in {0, 1}:
                    raise TrainingDatasetSplitError(
                        f"Row {row_number} target label must be 0 or 1."
                    )
                rows_by_experiment[experiment_id].append(row)
                labels_by_experiment[experiment_id][label] += 1
    except OSError as error:
        raise TrainingDatasetSplitError(
            f"Could not read dataset {dataset_file}: {error}"
        ) from error
    if not rows_by_experiment:
        raise TrainingDatasetSplitError("Training dataset is empty.")
    return dict(rows_by_experiment), dict(labels_by_experiment)


def _has_full_matrix_coverage(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
    required_values: dict[str, set[object]],
) -> bool:
    """Require every split to cover every matrix dimension marginally."""

    values = {
        "traffic_pattern": {
            metadata[item].traffic_pattern for item in experiment_ids
        },
        "demand_profile": {
            metadata[item].demand_profile for item in experiment_ids
        },
        "pressure_level": {
            metadata[item].pressure_level for item in experiment_ids
        },
        "random_seed": {
            metadata[item].random_seed for item in experiment_ids
        },
    }
    return all(values[name] == required for name, required in required_values.items())


def _label_totals(
    experiment_ids: list[str],
    labels_by_experiment: dict[str, Counter[int]],
) -> Counter[int]:
    """Add label counts for a group of experiments."""

    result: Counter[int] = Counter()
    for experiment_id in experiment_ids:
        result.update(labels_by_experiment[experiment_id])
    return result


def _candidate_order(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
    *,
    split_seed: int,
    attempt: int,
) -> list[str]:
    """Create one cross-version deterministic pseudo-random order."""

    def sort_key(experiment_id: str) -> bytes:
        scenario_id = metadata[experiment_id].scenario_id
        value = f"{split_seed}:{attempt}:{scenario_id}:{experiment_id}"
        return hashlib.sha256(value.encode("utf-8")).digest()

    return sorted(experiment_ids, key=sort_key)


def _find_assignment(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
    labels_by_experiment: dict[str, Counter[int]],
    *,
    split_seed: int,
    maximum_attempts: int,
    minimum_class_fraction: Fraction,
) -> dict[str, list[str]]:
    """Find a deterministic assignment with coverage and class balance."""

    if len(experiment_ids) != sum(SPLIT_EXPERIMENT_COUNTS.values()):
        raise TrainingDatasetSplitError(
            "Checkpoint 32C requires exactly 60 matrix experiments."
        )
    required_values: dict[str, set[object]] = {
        "traffic_pattern": {
            item.traffic_pattern for item in metadata.values()
        },
        "demand_profile": {
            item.demand_profile for item in metadata.values()
        },
        "pressure_level": {
            item.pressure_level for item in metadata.values()
        },
        "random_seed": {
            item.random_seed for item in metadata.values()
        },
    }
    all_labels = _label_totals(experiment_ids, labels_by_experiment)
    total_rows = all_labels[0] + all_labels[1]
    global_positive_fraction = Fraction(all_labels[1], total_rows)
    best_assignment: dict[str, list[str]] | None = None
    best_score: Fraction | None = None

    for attempt in range(maximum_attempts):
        ordered = _candidate_order(
            experiment_ids,
            metadata,
            split_seed=split_seed,
            attempt=attempt,
        )
        train_end = SPLIT_EXPERIMENT_COUNTS["train"]
        validation_end = train_end + SPLIT_EXPERIMENT_COUNTS["validation"]
        assignment = {
            "train": ordered[:train_end],
            "validation": ordered[train_end:validation_end],
            "test": ordered[validation_end:],
        }
        if not all(
            _has_full_matrix_coverage(ids, metadata, required_values)
            for ids in assignment.values()
        ):
            continue

        score = Fraction(0, 1)
        valid_balance = True
        for ids in assignment.values():
            counts = _label_totals(ids, labels_by_experiment)
            split_rows = counts[0] + counts[1]
            if split_rows == 0 or set(counts) != {0, 1}:
                valid_balance = False
                break
            minority_fraction = Fraction(min(counts[0], counts[1]), split_rows)
            if minority_fraction < minimum_class_fraction:
                valid_balance = False
                break
            score += abs(
                Fraction(counts[1], split_rows) - global_positive_fraction
            )
        if not valid_balance:
            continue
        if best_score is None or score < best_score:
            best_assignment = {
                name: sorted(ids) for name, ids in assignment.items()
            }
            best_score = score
            if score == 0:
                break

    if best_assignment is None:
        raise TrainingDatasetSplitError(
            "Could not find a split satisfying matrix coverage and class balance."
        )
    return best_assignment


def _write_csv(file_path: Path, rows: list[dict[str, str]]) -> str:
    """Write one deterministic CSV split and return its checksum."""

    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = file_path.with_suffix(f"{file_path.suffix}.tmp")
    ordered_rows = sorted(
        rows,
        key=lambda row: (row["experiment_run_id"], row["sampled_at"]),
    )
    try:
        with temporary_file.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=CSV_FIELD_NAMES)
            writer.writeheader()
            writer.writerows(ordered_rows)
        temporary_file.replace(file_path)
    except OSError as error:
        raise TrainingDatasetSplitError(
            f"Could not write split CSV {file_path}: {error}"
        ) from error
    return _sha256(file_path)


def _coverage_dictionary(
    experiment_ids: list[str],
    metadata: dict[str, ScenarioMetadata],
) -> dict[str, object]:
    """Describe matrix dimensions represented in a split."""

    return {
        "traffic_patterns": sorted(
            {metadata[item].traffic_pattern for item in experiment_ids}
        ),
        "demand_profiles": sorted(
            {metadata[item].demand_profile for item in experiment_ids}
        ),
        "pressure_levels": sorted(
            {metadata[item].pressure_level for item in experiment_ids}
        ),
        "random_seeds": sorted(
            {metadata[item].random_seed for item in experiment_ids}
        ),
    }


def _write_json(file_path: Path, value: dict[str, object]) -> None:
    """Write a JSON object atomically."""

    temporary_file = file_path.with_suffix(f"{file_path.suffix}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(file_path)
    except OSError as error:
        raise TrainingDatasetSplitError(
            f"Could not write split manifest {file_path}: {error}"
        ) from error


def split_training_dataset(
    *,
    dataset_file: Path = DEFAULT_DATASET_FILE,
    dataset_manifest_file: Path = DEFAULT_DATASET_MANIFEST,
    output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
    split_seed: int = DEFAULT_SPLIT_SEED,
    maximum_attempts: int = DEFAULT_MAXIMUM_ATTEMPTS,
    minimum_class_fraction: Decimal = Decimal("0.20"),
) -> TrainingDatasetSplitResult:
    """Create deterministic experiment-grouped train/validation/test CSVs."""

    if maximum_attempts <= 0:
        raise TrainingDatasetSplitError("maximum_attempts must be positive.")
    if minimum_class_fraction <= 0 or minimum_class_fraction > Decimal("0.5"):
        raise TrainingDatasetSplitError(
            "minimum_class_fraction must be greater than 0 and no more than 0.5."
        )

    dataset_path = _resolve(dataset_file).resolve()
    dataset_manifest_path = _resolve(dataset_manifest_file).resolve()
    output_path = _resolve(output_directory).resolve()
    manifest_path = output_path / "splits.manifest.json"
    split_paths = {
        name: output_path / f"{name}.csv" for name in SPLIT_NAMES
    }
    existing = [
        path for path in [manifest_path, *split_paths.values()] if path.exists()
    ]
    if existing:
        raise TrainingDatasetSplitError(
            "Split output already exists; use a new versioned directory: "
            + ", ".join(str(path) for path in existing)
        )
    if not dataset_path.is_file() or not dataset_manifest_path.is_file():
        raise TrainingDatasetSplitError(
            "The combined dataset and its manifest must both exist."
        )

    dataset_hash = _sha256(dataset_path)
    dataset_manifest_hash = _sha256(dataset_manifest_path)
    dataset_manifest = _load_json(dataset_manifest_path, "dataset manifest")
    if dataset_manifest.get("dataset_sha256") != dataset_hash:
        raise TrainingDatasetSplitError(
            "Combined dataset checksum does not match its manifest."
        )
    source_matrix = dataset_manifest.get("source_matrix")
    if not isinstance(source_matrix, dict):
        raise TrainingDatasetSplitError(
            "Dataset manifest source_matrix is invalid."
        )
    execution_file_value = source_matrix.get("execution_manifest_file")
    execution_hash = source_matrix.get("execution_manifest_sha256")
    if not isinstance(execution_file_value, str) or not isinstance(execution_hash, str):
        raise TrainingDatasetSplitError(
            "Dataset manifest execution-manifest metadata is invalid."
        )
    execution_path = Path(execution_file_value).resolve()
    if not execution_path.is_file() or _sha256(execution_path) != execution_hash:
        raise TrainingDatasetSplitError(
            "Execution manifest is missing or its checksum changed."
        )

    metadata = _load_scenario_metadata(execution_path)
    rows_by_experiment, labels_by_experiment = _load_dataset_rows(dataset_path)
    if set(rows_by_experiment) != set(metadata):
        raise TrainingDatasetSplitError(
            "Dataset experiment IDs do not match the execution manifest."
        )
    assignment = _find_assignment(
        sorted(rows_by_experiment),
        metadata,
        labels_by_experiment,
        split_seed=split_seed,
        maximum_attempts=maximum_attempts,
        minimum_class_fraction=Fraction(minimum_class_fraction),
    )
    assignment_text = json.dumps(assignment, sort_keys=True, separators=(",", ":"))
    assignment_hash = hashlib.sha256(assignment_text.encode("utf-8")).hexdigest()

    summaries: dict[str, SplitSummary] = {}
    manifest_splits: dict[str, object] = {}
    for name in SPLIT_NAMES:
        experiment_ids = assignment[name]
        rows = [
            row
            for experiment_id in experiment_ids
            for row in rows_by_experiment[experiment_id]
        ]
        labels = _label_totals(experiment_ids, labels_by_experiment)
        split_hash = _write_csv(split_paths[name], rows)
        summary = SplitSummary(
            name=name,
            file_path=split_paths[name],
            file_sha256=split_hash,
            row_count=len(rows),
            experiment_count=len(experiment_ids),
            negative_label_count=labels[0],
            positive_label_count=labels[1],
        )
        summaries[name] = summary
        manifest_splits[name] = {
            "file": _display_path(summary.file_path),
            "file_sha256": summary.file_sha256,
            "row_count": summary.row_count,
            "experiment_count": summary.experiment_count,
            "label_distribution": {
                "negative": summary.negative_label_count,
                "positive": summary.positive_label_count,
            },
            "experiment_ids": experiment_ids,
            "matrix_coverage": _coverage_dictionary(experiment_ids, metadata),
        }

    manifest : dict[str, object] = {
        "split_schema_version": "1.0.0",
        "created_at": datetime.now(UTC).isoformat(),
        "strategy": "grouped_by_experiment_with_matrix_coverage",
        "split_seed": split_seed,
        "assignment_sha256": assignment_hash,
        "source_dataset_file": _display_path(dataset_path),
        "source_dataset_sha256": dataset_hash,
        "source_dataset_manifest_file": _display_path(dataset_manifest_path),
        "source_dataset_manifest_sha256": dataset_manifest_hash,
        "total_row_count": sum(item.row_count for item in summaries.values()),
        "total_experiment_count": sum(
            item.experiment_count for item in summaries.values()
        ),
        "target_column": "future_high_contention_30s",
        "splits": manifest_splits,
    }
    _write_json(manifest_path, manifest)
    return TrainingDatasetSplitResult(
        manifest_file=manifest_path,
        assignment_sha256=assignment_hash,
        train=summaries["train"],
        validation=summaries["validation"],
        test=summaries["test"],
    )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create deterministic leakage-safe train, validation, and test "
            "splits grouped by complete experiments."
        )
    )
    parser.add_argument("--dataset-file", type=Path, default=DEFAULT_DATASET_FILE)
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
    parser.add_argument("--split-seed", type=int, default=DEFAULT_SPLIT_SEED)
    parser.add_argument(
        "--maximum-attempts",
        type=int,
        default=DEFAULT_MAXIMUM_ATTEMPTS,
    )
    return parser.parse_args()


def main() -> int:
    """Create and summarize the versioned dataset splits."""

    arguments = parse_arguments()
    try:
        result = split_training_dataset(
            dataset_file=arguments.dataset_file,
            dataset_manifest_file=arguments.dataset_manifest_file,
            output_directory=arguments.output_directory,
            split_seed=arguments.split_seed,
            maximum_attempts=arguments.maximum_attempts,
        )
        print("Leakage-safe dataset splits created:")
        for summary in (result.train, result.validation, result.test):
            print(
                f"  {summary.name}: rows={summary.row_count}, "
                f"experiments={summary.experiment_count}, "
                f"negative={summary.negative_label_count}, "
                f"positive={summary.positive_label_count}"
            )
        print(f"  Assignment SHA-256: {result.assignment_sha256}")
        print(f"  Manifest: {result.manifest_file}")
        return 0
    except TrainingDatasetSplitError as error:
        print(f"Training dataset split error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
