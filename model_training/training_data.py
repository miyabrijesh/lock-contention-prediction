import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import numpy as np
import pandas as pd


PROJECT_ROOT: Final[Path] = (
    Path(__file__).resolve().parents[1]
)

DEFAULT_SPLIT_DIRECTORY: Final[Path] = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "splits"
    / "contention_training_dataset_v1"
)

DEFAULT_MANIFEST_FILE: Final[Path] = (
    PROJECT_ROOT
    / "docs"
    / "data"
    / "contention_training_dataset_v1_splits.json"
)

TARGET_COLUMN: Final[str] = (
    "future_high_contention_30s"
)

EXPERIMENT_ID_COLUMN: Final[str] = (
    "experiment_run_id"
)

FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "active_transactions",
    "blocked_sessions",
    "waiting_locks",
    "total_locks",
    "total_connections",
    "connection_utilization",
    "estimated_wait_age_ms",
    "deadlock_delta",
    "configured_users",
    "active_users",
    "requests_per_sec",
    "mean_latency_ms",
    "p95_latency_ms",
    "failure_rate",
    "hotspot_ratio",
    "booking_success_rate",
    "conflict_rate",
    "timeout_rate",
)

FUTURE_LEAKAGE_COLUMNS: Final[tuple[str, ...]] = (
    "future_max_blocked_sessions_30s",
    "future_max_waiting_locks_30s",
    "future_deadlock_delta_sum_30s",
    "future_max_timeout_rate_30s",
    "future_max_p95_latency_ms_30s",
)

SPLIT_NAMES: Final[tuple[str, ...]] = (
    "train",
    "validation",
    "test",
)


class TrainingDataError(ValueError):
    """Raised when frozen training data is unsafe or invalid."""


@dataclass(frozen=True)
class LoadedSplit:
    """One validated experiment-grouped dataset split."""

    name: str
    source_file: Path
    frame: pd.DataFrame
    features: pd.DataFrame
    target: pd.Series
    experiment_ids: frozenset[str]
    file_sha256: str

    @property
    def row_count(self) -> int:
        """Return the number of samples in the split."""

        return len(self.frame)

    @property
    def experiment_count(self) -> int:
        """Return the number of experiments in the split."""

        return len(self.experiment_ids)


@dataclass(frozen=True)
class TrainingDataBundle:
    """Validated train, validation, and test datasets."""

    train: LoadedSplit
    validation: LoadedSplit
    test: LoadedSplit
    feature_columns: tuple[str, ...]
    target_column: str
    assignment_sha256: str


def _sha256(path: Path) -> str:
    """Calculate the SHA-256 checksum of one file."""

    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _load_manifest(
    manifest_file: Path,
) -> dict[str, object]:
    """Read and validate the frozen split manifest."""

    if not manifest_file.is_file():
        raise TrainingDataError(
            f"Split manifest was not found: {manifest_file}"
        )

    try:
        manifest = json.loads(
            manifest_file.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise TrainingDataError(
            "Split manifest is not valid JSON."
        ) from error

    if not isinstance(manifest, dict):
        raise TrainingDataError(
            "Split manifest must contain a JSON object."
        )

    if manifest.get("target_column") != TARGET_COLUMN:
        raise TrainingDataError(
            "Split manifest contains an unexpected "
            "target column."
        )

    splits = manifest.get("splits")

    if not isinstance(splits, dict):
        raise TrainingDataError(
            "Split manifest does not contain split metadata."
        )

    missing_splits = set(SPLIT_NAMES) - set(splits)

    if missing_splits:
        raise TrainingDataError(
            "Split manifest is missing: "
            + ", ".join(sorted(missing_splits))
        )

    assignment_sha256 = manifest.get(
        "assignment_sha256"
    )

    if (
        not isinstance(assignment_sha256, str)
        or len(assignment_sha256) != 64
    ):
        raise TrainingDataError(
            "Split manifest contains an invalid "
            "assignment SHA-256."
        )

    return manifest


def _manifest_split(
    manifest: dict[str, object],
    split_name: str,
) -> dict[str, object]:
    """Return metadata for one split."""

    splits = manifest["splits"]

    if not isinstance(splits, dict):
        raise TrainingDataError(
            "Split metadata is invalid."
        )

    split_metadata = splits.get(split_name)

    if not isinstance(split_metadata, dict):
        raise TrainingDataError(
            f"Manifest metadata for {split_name} is invalid."
        )

    return split_metadata


def _validate_required_columns(
    frame: pd.DataFrame,
    split_name: str,
) -> None:
    """Require model inputs, target, and experiment identity."""

    required_columns = {
        EXPERIMENT_ID_COLUMN,
        TARGET_COLUMN,
        *FEATURE_COLUMNS,
    }

    missing_columns = (
        required_columns - set(frame.columns)
    )

    if missing_columns:
        raise TrainingDataError(
            f"{split_name} is missing required columns: "
            + ", ".join(sorted(missing_columns))
        )

    leaked_features = (
        set(FEATURE_COLUMNS)
        & set(FUTURE_LEAKAGE_COLUMNS)
    )

    if leaked_features:
        raise TrainingDataError(
            "Feature contract contains future leakage columns: "
            + ", ".join(sorted(leaked_features))
        )


def _validated_features(
    frame: pd.DataFrame,
    split_name: str,
) -> pd.DataFrame:
    """Return finite numeric model features in fixed order."""

    numeric_features = frame.loc[
        :,
        list(FEATURE_COLUMNS),
    ].apply(
        pd.to_numeric,
        errors="coerce",
    )

    if numeric_features.isna().any().any():
        invalid_columns = numeric_features.columns[
            numeric_features.isna().any()
        ].tolist()

        raise TrainingDataError(
            f"{split_name} contains missing or non-numeric "
            "feature values in: "
            + ", ".join(invalid_columns)
        )

    values = numeric_features.to_numpy(
        dtype=np.float64
    )

    if not np.isfinite(values).all():
        raise TrainingDataError(
            f"{split_name} contains infinite feature values."
        )

    return numeric_features.astype(
        np.float64,
        copy=False,
    )


def _validated_target(
    frame: pd.DataFrame,
    split_name: str,
) -> pd.Series:
    """Return a binary integer target column."""

    raw_target = frame.loc[:, TARGET_COLUMN]

    numeric_target = cast(
        pd.Series,
        pd.to_numeric(
            raw_target,
            errors="coerce",
        ),
    )

    if numeric_target.isna().any():
        raise TrainingDataError(
            f"{split_name} contains missing or invalid "
            "target values."
        )

    integer_target = cast(
        pd.Series,
        numeric_target.astype(np.int64),
    )

    if not np.equal(
        numeric_target.to_numpy(),
        integer_target.to_numpy(),
    ).all():
        raise TrainingDataError(
            f"{split_name} target contains non-integer values."
        )

    unique_values = {
        int(value)
        for value in integer_target.unique()
    }

    if not unique_values <= {0, 1}:
        raise TrainingDataError(
            f"{split_name} target must contain only 0 and 1."
        )

    validated_target = cast(
        pd.Series,
        integer_target.astype(np.int8),
    )

    validated_target.name = TARGET_COLUMN

    return validated_target

def _load_split(
    *,
    split_name: str,
    split_directory: Path,
    manifest: dict[str, object],
) -> LoadedSplit:
    """Load and validate one frozen dataset split."""

    split_metadata = _manifest_split(
        manifest,
        split_name,
    )

    source_file = (
        split_directory / f"{split_name}.csv"
    )

    if not source_file.is_file():
        raise TrainingDataError(
            f"{split_name} CSV was not found: {source_file}"
        )

    expected_sha256 = split_metadata.get(
        "file_sha256"
    )

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
    ):
        raise TrainingDataError(
            f"{split_name} manifest checksum is invalid."
        )

    actual_sha256 = _sha256(source_file)

    if actual_sha256 != expected_sha256:
        raise TrainingDataError(
            f"{split_name} CSV checksum does not match "
            "the frozen manifest."
        )

    frame = pd.read_csv(source_file)

    if frame.empty:
        raise TrainingDataError(
            f"{split_name} CSV is empty."
        )

    expected_row_count = split_metadata.get(
        "row_count"
    )

    if (
        not isinstance(expected_row_count, int)
        or len(frame) != expected_row_count
    ):
        raise TrainingDataError(
            f"{split_name} row count does not match "
            "the frozen manifest."
        )

    _validate_required_columns(
        frame,
        split_name,
    )

    features = _validated_features(
        frame,
        split_name,
    )

    target = _validated_target(
        frame,
        split_name,
    )

    experiment_values = frame[
        EXPERIMENT_ID_COLUMN
    ]

    if experiment_values.isna().any():
        raise TrainingDataError(
            f"{split_name} contains a missing experiment ID."
        )

    experiment_ids : frozenset[str] = frozenset(
        str(value)
        for value in experiment_values.tolist()
    )

    expected_experiment_ids = (
        split_metadata.get("experiment_ids")
    )

    if (
        not isinstance(expected_experiment_ids, list)
        or not all(
            isinstance(value, str)
            for value in expected_experiment_ids
        )
    ):
        raise TrainingDataError(
            f"{split_name} manifest experiment IDs "
            "are invalid."
        )

    if experiment_ids != frozenset(
        expected_experiment_ids
    ):
        raise TrainingDataError(
            f"{split_name} experiment IDs do not match "
            "the frozen manifest."
        )

    expected_experiment_count = (
        split_metadata.get("experiment_count")
    )

    if (
        not isinstance(expected_experiment_count, int)
        or len(experiment_ids)
        != expected_experiment_count
    ):
        raise TrainingDataError(
            f"{split_name} experiment count does not "
            "match the frozen manifest."
        )

    return LoadedSplit(
        name=split_name,
        source_file=source_file,
        frame=frame,
        features=features,
        target=target,
        experiment_ids=experiment_ids,
        file_sha256=actual_sha256,
    )


def _validate_disjoint_experiments(
    train: LoadedSplit,
    validation: LoadedSplit,
    test: LoadedSplit,
) -> None:
    """Prevent one experiment from entering two splits."""

    overlaps = {
        "train/validation": (
            train.experiment_ids
            & validation.experiment_ids
        ),
        "train/test": (
            train.experiment_ids
            & test.experiment_ids
        ),
        "validation/test": (
            validation.experiment_ids
            & test.experiment_ids
        ),
    }

    overlapping_pairs = [
        pair
        for pair, experiment_ids in overlaps.items()
        if experiment_ids
    ]

    if overlapping_pairs:
        raise TrainingDataError(
            "Experiment leakage detected between splits: "
            + ", ".join(overlapping_pairs)
        )


def load_training_data(
    *,
    split_directory: Path = DEFAULT_SPLIT_DIRECTORY,
    manifest_file: Path = DEFAULT_MANIFEST_FILE,
) -> TrainingDataBundle:
    """Load the frozen leakage-safe model datasets."""

    manifest = _load_manifest(manifest_file)

    train = _load_split(
        split_name="train",
        split_directory=split_directory,
        manifest=manifest,
    )

    validation = _load_split(
        split_name="validation",
        split_directory=split_directory,
        manifest=manifest,
    )

    test = _load_split(
        split_name="test",
        split_directory=split_directory,
        manifest=manifest,
    )

    _validate_disjoint_experiments(
        train,
        validation,
        test,
    )

    assignment_sha256 = manifest[
        "assignment_sha256"
    ]

    if not isinstance(assignment_sha256, str):
        raise TrainingDataError(
            "Assignment SHA-256 is invalid."
        )

    return TrainingDataBundle(
        train=train,
        validation=validation,
        test=test,
        feature_columns=FEATURE_COLUMNS,
        target_column=TARGET_COLUMN,
        assignment_sha256=assignment_sha256,
    )