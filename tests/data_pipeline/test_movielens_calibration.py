import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from data_pipeline.calibrate_movielens import (
    MovieLensCalibrationError,
    calibrate_movielens,
)


def create_valid_source(
    temporary_directory: Path,
) -> Path:
    """Create a small valid MovieLens-style fixture."""

    source_directory = (
        temporary_directory / "ml-1m"
    )
    source_directory.mkdir()

    (source_directory / "movies.dat").write_text(
        "\n".join([
            "1::Popular Movie (2000)::Action",
            "2::Medium Movie (2000)::Drama",
            "3::Low Movie (2000)::Comedy",
            "4::Unrated Movie (2000)::Documentary",
        ])
        + "\n",
        encoding="latin-1",
    )

    (source_directory / "ratings.dat").write_text(
        "\n".join([
            "1::1::5::100",
            "2::1::4::101",
            "3::1::3::102",
            "4::1::2::103",
            "1::2::5::104",
            "2::2::3::105",
            "3::3::2::106",
        ])
        + "\n",
        encoding="latin-1",
    )

    return source_directory


def run_fixture_calibration(
    temporary_directory: Path,
    source_directory: Path,
):
    """Run calibration using temporary output files."""

    return calibrate_movielens(
        source_directory=source_directory,
        output_file=(
            temporary_directory
            / "movie_popularity.csv"
        ),
        summary_file=(
            temporary_directory
            / "summary.json"
        ),
        acquisition_file=None,
    )


def test_calibration_builds_expected_records(
    tmp_path: Path,
) -> None:
    """Calculate popularity from a valid fixture."""

    source_directory = create_valid_source(
        tmp_path
    )

    result = run_fixture_calibration(
        tmp_path,
        source_directory,
    )

    assert result.movie_count == 4
    assert result.rating_count == 7
    assert result.user_count == 4

    with result.output_file.open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        rows = list(csv.DictReader(input_file))

    assert len(rows) == 4

    rows_by_id = {
        int(row["movie_id"]): row
        for row in rows
    }

    assert rows_by_id[1]["rating_count"] == "4"
    assert rows_by_id[1]["average_rating"] == "3.5000"
    assert rows_by_id[1]["popularity_tier"] == "HIGH"

    assert rows_by_id[2]["rating_count"] == "2"
    assert rows_by_id[2]["average_rating"] == "4.0000"
    assert rows_by_id[2]["popularity_tier"] == "MEDIUM"

    assert rows_by_id[3]["rating_count"] == "1"
    assert rows_by_id[3]["popularity_tier"] == "LOW"

    assert rows_by_id[4]["rating_count"] == "0"
    assert rows_by_id[4]["average_rating"] == "0.0000"
    assert rows_by_id[4]["selection_weight"] == (
        "0E-12"
    )

    total_selection_weight = sum(
        Decimal(row["selection_weight"])
        for row in rows
    )

    assert abs(
        total_selection_weight - Decimal("1")
    ) < Decimal("0.000001")


def test_calibration_writes_summary(
    tmp_path: Path,
) -> None:
    """Write a non-row-level provenance summary."""

    source_directory = create_valid_source(
        tmp_path
    )

    result = run_fixture_calibration(
        tmp_path,
        source_directory,
    )

    summary = json.loads(
        result.summary_file.read_text(
            encoding="utf-8"
        )
    )

    expected_sha256 = hashlib.sha256(
        result.output_file.read_bytes()
    ).hexdigest()

    assert summary["schema_version"] == "1.0.0"
    assert summary["movie_count"] == 4
    assert summary["rating_count"] == 7
    assert summary["user_count"] == 4
    assert summary["zero_rating_movie_count"] == 1
    assert (
        summary["calibration_output_sha256"]
        == expected_sha256
    )
    assert summary["contention_label_source"] == (
        "Measured PostgreSQL experiment telemetry"
    )
    assert summary["raw_data_redistributed"] is False


def test_calibration_rejects_unknown_movie(
    tmp_path: Path,
) -> None:
    """A rating cannot reference missing movie metadata."""

    source_directory = create_valid_source(
        tmp_path
    )

    with (
        source_directory / "ratings.dat"
    ).open(
        "a",
        encoding="latin-1",
    ) as output_file:
        output_file.write(
            "1::999::5::200\n"
        )

    with pytest.raises(
        MovieLensCalibrationError,
        match="unknown movie ID 999",
    ):
        run_fixture_calibration(
            tmp_path,
            source_directory,
        )


def test_calibration_rejects_invalid_rating(
    tmp_path: Path,
) -> None:
    """Ratings must remain between one and five."""

    source_directory = create_valid_source(
        tmp_path
    )

    with (
        source_directory / "ratings.dat"
    ).open(
        "a",
        encoding="latin-1",
    ) as output_file:
        output_file.write(
            "1::1::6::200\n"
        )

    with pytest.raises(
        MovieLensCalibrationError,
        match="expected 1 through 5",
    ):
        run_fixture_calibration(
            tmp_path,
            source_directory,
        )


def test_calibration_rejects_malformed_movie(
    tmp_path: Path,
) -> None:
    """Movie metadata must contain three fields."""

    source_directory = create_valid_source(
        tmp_path
    )

    (source_directory / "movies.dat").write_text(
        "1::Missing genres\n",
        encoding="latin-1",
    )

    with pytest.raises(
        MovieLensCalibrationError,
        match="exactly three fields",
    ):
        run_fixture_calibration(
            tmp_path,
            source_directory,
        )