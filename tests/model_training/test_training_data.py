from pathlib import Path

import pandas as pd
import pytest

from model_training.training_data import (
    FEATURE_COLUMNS,
    FUTURE_LEAKAGE_COLUMNS,
    TARGET_COLUMN,
    TrainingDataError,
    load_training_data,
)


EXPECTED_ASSIGNMENT_SHA256 = (
    "4a24f55d3d1121f50da74ae3ec21cfba"
    "4a6f3f9044069edff7113d228c4c8374"
)


def test_feature_contract_contains_only_current_data() -> None:
    """Future measurements must never become model inputs."""

    assert len(FEATURE_COLUMNS) == 18
    assert len(set(FEATURE_COLUMNS)) == 18

    assert set(FEATURE_COLUMNS).isdisjoint(
        FUTURE_LEAKAGE_COLUMNS
    )

    assert TARGET_COLUMN not in FEATURE_COLUMNS


def test_load_frozen_training_data() -> None:
    """Load and verify all three frozen splits."""

    bundle = load_training_data()

    assert bundle.assignment_sha256 == (
        EXPECTED_ASSIGNMENT_SHA256
    )

    assert bundle.feature_columns == FEATURE_COLUMNS
    assert bundle.target_column == TARGET_COLUMN

    assert bundle.train.row_count == 1864
    assert bundle.validation.row_count == 400
    assert bundle.test.row_count == 402

    assert bundle.train.experiment_count == 42
    assert bundle.validation.experiment_count == 9
    assert bundle.test.experiment_count == 9


def test_loaded_features_are_numeric_and_complete() -> None:
    """Every model feature must be usable by scikit-learn."""

    bundle = load_training_data()

    for split in (
        bundle.train,
        bundle.validation,
        bundle.test,
    ):
        assert list(split.features.columns) == list(
            FEATURE_COLUMNS
        )

        assert not split.features.isna().any().any()

        assert all(
            pd.api.types.is_numeric_dtype(dtype)
            for dtype in split.features.dtypes
        )

        assert set(split.target.unique()) <= {0, 1}


def test_experiment_groups_are_disjoint() -> None:
    """One experiment must occur in exactly one split."""

    bundle = load_training_data()

    assert bundle.train.experiment_ids.isdisjoint(
        bundle.validation.experiment_ids
    )

    assert bundle.train.experiment_ids.isdisjoint(
        bundle.test.experiment_ids
    )

    assert bundle.validation.experiment_ids.isdisjoint(
        bundle.test.experiment_ids
    )

    all_experiment_ids = (
        bundle.train.experiment_ids
        | bundle.validation.experiment_ids
        | bundle.test.experiment_ids
    )

    assert len(all_experiment_ids) == 60


def test_changed_split_file_is_rejected(
    tmp_path: Path,
) -> None:
    """A split must not load after its contents are modified."""

    source_directory = Path(
        "data/processed/splits/"
        "contention_training_dataset_v1"
    )

    temporary_directory = tmp_path / "splits"
    temporary_directory.mkdir()

    for split_name in (
        "train",
        "validation",
        "test",
    ):
        source_file = (
            source_directory / f"{split_name}.csv"
        )

        destination_file = (
            temporary_directory / f"{split_name}.csv"
        )

        destination_file.write_bytes(
            source_file.read_bytes()
        )

    with (
        temporary_directory / "train.csv"
    ).open("a", encoding="utf-8") as file:
        file.write("\n")

    with pytest.raises(
        TrainingDataError,
        match="checksum",
    ):
        load_training_data(
            split_directory=temporary_directory,
        )