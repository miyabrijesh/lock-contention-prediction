from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final


PROFILE_VERSION: Final = "1.0.0"

DEFAULT_SUMMARY_FILE: Final = Path(
    "docs/data/movielens_calibration_summary.json"
)

DEFAULT_OUTPUT_FILE: Final = Path(
    "load_testing/config/demand_profiles.json"
)

TIER_NAMES: Final = (
    "LOW",
    "MEDIUM",
    "HIGH",
)

PROFILE_MAPPINGS: Final[
    dict[str, dict[str, object]]
] = {
    "LOW": {
        "workload_levels": [
            "VERY_LOW",
            "LOW",
        ],
        "hotspot_ratio_min": "0.10",
        "hotspot_ratio_max": "0.30",
        "hotspot_seat_count_min": 15,
        "hotspot_seat_count_max": 25,
        "description": (
            "Demand is distributed across many seats "
            "with limited hotspot concentration."
        ),
    },
    "MEDIUM": {
        "workload_levels": [
            "BORDERLINE",
            "MEDIUM",
        ],
        "hotspot_ratio_min": "0.40",
        "hotspot_ratio_max": "0.80",
        "hotspot_seat_count_min": 5,
        "hotspot_seat_count_max": 12,
        "description": (
            "Demand is moderately concentrated and may "
            "cross the contention boundary."
        ),
    },
    "HIGH": {
        "workload_levels": [
            "HIGH",
            "EXTREME",
        ],
        "hotspot_ratio_min": "0.85",
        "hotspot_ratio_max": "1.00",
        "hotspot_seat_count_min": 3,
        "hotspot_seat_count_max": 5,
        "description": (
            "Demand is strongly concentrated on a very "
            "small seat group."
        ),
    },
}


@dataclass(frozen=True)
class DemandProfileResult:
    """Result of creating calibrated demand profiles."""

    output_file: Path
    profile_count: int
    source_movie_count: int
    source_rating_count: int
    output_sha256: str


class DemandProfileError(Exception):
    """Expected demand-profile validation failure."""


def _calculate_sha256(
    file_path: Path,
) -> str:
    """Calculate the SHA-256 of one file."""

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        for block in iter(
            lambda: input_file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _required_positive_integer(
    mapping: dict[str, object],
    field_name: str,
) -> int:
    """Read one required positive integer."""

    value = mapping.get(field_name)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise DemandProfileError(
            f"Calibration summary contains an invalid "
            f"{field_name}."
        )

    return value


def _required_checksum(
    mapping: dict[str, object],
    field_name: str,
) -> str:
    """Read one required SHA-256 checksum."""

    value = mapping.get(field_name)

    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(
            character not in "0123456789abcdef"
            for character in value.lower()
        )
    ):
        raise DemandProfileError(
            f"Calibration summary contains an invalid "
            f"{field_name}."
        )

    return value.lower()


def _required_tier_counts(
    summary: dict[str, object],
    field_name: str,
) -> dict[str, int]:
    """Read LOW, MEDIUM and HIGH counts."""

    raw_counts = summary.get(field_name)

    if not isinstance(raw_counts, dict):
        raise DemandProfileError(
            f"Calibration summary is missing "
            f"{field_name}."
        )

    counts: dict[str, int] = {}

    for tier_name in TIER_NAMES:
        value = raw_counts.get(tier_name)

        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise DemandProfileError(
                f"{field_name} contains an invalid "
                f"{tier_name} count."
            )

        counts[tier_name] = value

    return counts


def _load_calibration_summary(
    summary_file: Path,
) -> dict[str, object]:
    """Load the MovieLens calibration summary."""

    if not summary_file.is_file():
        raise DemandProfileError(
            f"Calibration summary was not found: "
            f"{summary_file}"
        )

    try:
        loaded_value = json.loads(
            summary_file.read_text(
                encoding="utf-8"
            )
        )
    except (
        json.JSONDecodeError,
        OSError,
    ) as error:
        raise DemandProfileError(
            "The calibration summary is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise DemandProfileError(
            "The calibration summary must be "
            "a JSON object."
        )

    return loaded_value


def build_demand_profiles(
    *,
    summary_file: Path = DEFAULT_SUMMARY_FILE,
    output_file: Path = DEFAULT_OUTPUT_FILE,
) -> DemandProfileResult:
    """Build demand profiles from MovieLens aggregates."""

    summary = _load_calibration_summary(
        summary_file
    )

    movie_count = _required_positive_integer(
        summary,
        "movie_count",
    )
    rating_count = _required_positive_integer(
        summary,
        "rating_count",
    )

    source_archive_sha256 = _required_checksum(
        summary,
        "source_archive_sha256",
    )
    calibration_output_sha256 = (
        _required_checksum(
            summary,
            "calibration_output_sha256",
        )
    )

    tier_movie_counts = _required_tier_counts(
        summary,
        "tier_movie_counts",
    )
    tier_rating_counts = _required_tier_counts(
        summary,
        "tier_rating_counts",
    )

    if sum(tier_movie_counts.values()) != movie_count:
        raise DemandProfileError(
            "Tier movie counts do not equal "
            "the total movie count."
        )

    if sum(tier_rating_counts.values()) != rating_count:
        raise DemandProfileError(
            "Tier rating counts do not equal "
            "the total rating count."
        )

    movie_shares: dict[str, Decimal] = {}
    interaction_shares: dict[str, Decimal] = {}
    interaction_densities: dict[str, Decimal] = {}

    for tier_name in TIER_NAMES:
        movie_share = (
            Decimal(tier_movie_counts[tier_name])
            / Decimal(movie_count)
        )
        interaction_share = (
            Decimal(tier_rating_counts[tier_name])
            / Decimal(rating_count)
        )

        movie_shares[tier_name] = movie_share
        interaction_shares[tier_name] = (
            interaction_share
        )

        if movie_share == 0:
            raise DemandProfileError(
                f"{tier_name} has no movies."
            )

        interaction_densities[tier_name] = (
            interaction_share / movie_share
        )

    medium_density = interaction_densities[
        "MEDIUM"
    ]

    if medium_density == 0:
        raise DemandProfileError(
            "MEDIUM interaction density is zero."
        )

    profiles: dict[str, dict[str, object]] = {}

    for tier_name in TIER_NAMES:
        mapping = PROFILE_MAPPINGS[tier_name]

        profiles[tier_name] = {
            "source_movie_count": (
                tier_movie_counts[tier_name]
            ),
            "source_rating_count": (
                tier_rating_counts[tier_name]
            ),
            "movie_share": str(
                movie_shares[tier_name].quantize(
                    Decimal("0.000001")
                )
            ),
            "interaction_share": str(
                interaction_shares[
                    tier_name
                ].quantize(
                    Decimal("0.000001")
                )
            ),
            "interaction_density_index": str(
                interaction_densities[
                    tier_name
                ].quantize(
                    Decimal("0.000001")
                )
            ),
            "relative_density_to_medium": str(
                (
                    interaction_densities[
                        tier_name
                    ]
                    / medium_density
                ).quantize(
                    Decimal("0.000001")
                )
            ),
            "workload_levels": (
                mapping["workload_levels"]
            ),
            "hotspot_ratio_range": {
                "minimum": (
                    mapping[
                        "hotspot_ratio_min"
                    ]
                ),
                "maximum": (
                    mapping[
                        "hotspot_ratio_max"
                    ]
                ),
            },
            "hotspot_seat_count_range": {
                "minimum": (
                    mapping[
                        "hotspot_seat_count_min"
                    ]
                ),
                "maximum": (
                    mapping[
                        "hotspot_seat_count_max"
                    ]
                ),
            },
            "description": mapping["description"],
        }

    output_document = {
        "profile_version": PROFILE_VERSION,
        "created_at": datetime.now(
            UTC
        ).isoformat(),
        "source": {
            "dataset_name": "MovieLens 1M",
            "calibration_summary": str(
                summary_file
            ),
            "archive_sha256": (
                source_archive_sha256
            ),
            "calibration_output_sha256": (
                calibration_output_sha256
            ),
            "movie_count": movie_count,
            "rating_count": rating_count,
        },
        "profiles": profiles,
        "mapping_methodology": {
            "real_world_component": (
                "Movie and interaction shares are "
                "calculated from MovieLens rating counts."
            ),
            "experimental_component": (
                "Hotspot-ratio and seat-count ranges "
                "are controlled reservation-system "
                "parameters."
            ),
            "simulated_work_rule": (
                "Lock-hold duration remains an "
                "independent experimental variable and "
                "is not derived from MovieLens."
            ),
            "label_rule": (
                "Contention labels come only from future "
                "PostgreSQL telemetry."
            ),
        },
    }

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(
        output_file.suffix + ".tmp"
    )

    temporary_file.write_text(
        json.dumps(
            output_document,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary_file.replace(output_file)

    output_sha256 = _calculate_sha256(
        output_file
    )

    return DemandProfileResult(
        output_file=output_file.resolve(),
        profile_count=len(profiles),
        source_movie_count=movie_count,
        source_rating_count=rating_count,
        output_sha256=output_sha256,
    )


def parse_arguments() -> argparse.Namespace:
    """Read command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Build reservation demand profiles "
            "from MovieLens calibration statistics."
        )
    )

    parser.add_argument(
        "--summary-file",
        type=Path,
        default=DEFAULT_SUMMARY_FILE,
        help="MovieLens calibration summary JSON.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help="Demand-profile JSON output.",
    )

    return parser.parse_args()


def main() -> int:
    """Build the calibrated profile file."""

    arguments = parse_arguments()

    try:
        result = build_demand_profiles(
            summary_file=arguments.summary_file,
            output_file=arguments.output_file,
        )
    except DemandProfileError as error:
        print(
            f"Demand-profile error: {error}",
            file=sys.stderr,
        )
        return 2

    print("Demand profiles created:")
    print(f"  Profiles: {result.profile_count}")
    print(
        f"  Source movies: "
        f"{result.source_movie_count}"
    )
    print(
        f"  Source ratings: "
        f"{result.source_rating_count}"
    )
    print(f"  Output: {result.output_file}")
    print(f"  SHA-256: {result.output_sha256}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())