import json
from pathlib import Path

import pytest

from data_pipeline.build_demand_profiles import (
    DemandProfileError,
    build_demand_profiles,
)


def write_valid_summary(
    temporary_directory: Path,
) -> Path:
    """Write a small valid calibration summary."""

    summary_file = (
        temporary_directory / "summary.json"
    )

    summary = {
        "movie_count": 100,
        "rating_count": 1000,
        "source_archive_sha256": "a" * 64,
        "calibration_output_sha256": "b" * 64,
        "tier_movie_counts": {
            "LOW": 50,
            "MEDIUM": 40,
            "HIGH": 10,
        },
        "tier_rating_counts": {
            "LOW": 100,
            "MEDIUM": 500,
            "HIGH": 400,
        },
    }

    summary_file.write_text(
        json.dumps(summary),
        encoding="utf-8",
    )

    return summary_file


def test_builds_three_demand_profiles(
    tmp_path: Path,
) -> None:
    """Build LOW, MEDIUM and HIGH profiles."""

    summary_file = write_valid_summary(
        tmp_path
    )
    output_file = tmp_path / "profiles.json"

    result = build_demand_profiles(
        summary_file=summary_file,
        output_file=output_file,
    )

    document = json.loads(
        output_file.read_text(
            encoding="utf-8"
        )
    )

    assert result.profile_count == 3
    assert result.source_movie_count == 100
    assert result.source_rating_count == 1000

    assert set(document["profiles"]) == {
        "LOW",
        "MEDIUM",
        "HIGH",
    }

    assert document["profiles"]["LOW"][
        "workload_levels"
    ] == [
        "VERY_LOW",
        "LOW",
    ]

    assert document["profiles"]["MEDIUM"][
        "workload_levels"
    ] == [
        "BORDERLINE",
        "MEDIUM",
    ]

    assert document["profiles"]["HIGH"][
        "workload_levels"
    ] == [
        "HIGH",
        "EXTREME",
    ]


def test_calculates_interaction_shares(
    tmp_path: Path,
) -> None:
    """Calculate real-data concentration values."""

    summary_file = write_valid_summary(
        tmp_path
    )
    output_file = tmp_path / "profiles.json"

    build_demand_profiles(
        summary_file=summary_file,
        output_file=output_file,
    )

    document = json.loads(
        output_file.read_text(
            encoding="utf-8"
        )
    )
    profiles = document["profiles"]

    assert profiles["LOW"][
        "movie_share"
    ] == "0.500000"
    assert profiles["LOW"][
        "interaction_share"
    ] == "0.100000"

    assert profiles["MEDIUM"][
        "movie_share"
    ] == "0.400000"
    assert profiles["MEDIUM"][
        "interaction_share"
    ] == "0.500000"

    assert profiles["HIGH"][
        "movie_share"
    ] == "0.100000"
    assert profiles["HIGH"][
        "interaction_share"
    ] == "0.400000"


def test_rejects_incorrect_movie_total(
    tmp_path: Path,
) -> None:
    """Tier movie counts must match the total."""

    summary_file = write_valid_summary(
        tmp_path
    )
    summary = json.loads(
        summary_file.read_text(
            encoding="utf-8"
        )
    )
    summary["tier_movie_counts"]["LOW"] = 49

    summary_file.write_text(
        json.dumps(summary),
        encoding="utf-8",
    )

    with pytest.raises(
        DemandProfileError,
        match="Tier movie counts",
    ):
        build_demand_profiles(
            summary_file=summary_file,
            output_file=(
                tmp_path / "profiles.json"
            ),
        )


def test_rejects_missing_tier(
    tmp_path: Path,
) -> None:
    """Every tier must have a recorded count."""

    summary_file = write_valid_summary(
        tmp_path
    )
    summary = json.loads(
        summary_file.read_text(
            encoding="utf-8"
        )
    )
    del summary[
        "tier_rating_counts"
    ]["HIGH"]

    summary_file.write_text(
        json.dumps(summary),
        encoding="utf-8",
    )

    with pytest.raises(
        DemandProfileError,
        match="invalid HIGH count",
    ):
        build_demand_profiles(
            summary_file=summary_file,
            output_file=(
                tmp_path / "profiles.json"
            ),
        )