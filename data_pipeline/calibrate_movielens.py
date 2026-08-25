from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final


SCHEMA_VERSION: Final = "1.0.0"

DEFAULT_SOURCE_DIRECTORY: Final = Path(
    "data/external/movielens/ml-1m"
)

DEFAULT_OUTPUT_FILE: Final = Path(
    "data/external/movielens/derived/movie_popularity.csv"
)

DEFAULT_SUMMARY_FILE: Final = Path(
    "docs/data/movielens_calibration_summary.json"
)

DEFAULT_ACQUISITION_FILE: Final = Path(
    "docs/data/movielens_1m_acquisition.json"
)

CSV_FIELD_NAMES: Final = (
    "movie_id",
    "title",
    "genres",
    "rating_count",
    "average_rating",
    "popularity_score",
    "popularity_percentile",
    "popularity_tier",
    "selection_weight",
)


@dataclass(frozen=True)
class MovieRecord:
    """One movie loaded from the MovieLens source file."""

    movie_id: int
    title: str
    genres: str


@dataclass(frozen=True)
class RatingStatistics:
    """Aggregated rating activity for all movies."""

    rating_counts: dict[int, int]
    rating_sums: dict[int, int]
    rating_count: int
    user_count: int


@dataclass(frozen=True)
class PopularityRecord:
    """One processed movie-popularity record."""

    movie_id: int
    title: str
    genres: str
    rating_count: int
    average_rating: Decimal
    popularity_score: Decimal
    popularity_percentile: Decimal
    popularity_tier: str
    selection_weight: Decimal


@dataclass(frozen=True)
class MovieLensCalibrationResult:
    """Summary returned after calibration completes."""

    output_file: Path
    summary_file: Path
    movie_count: int
    rating_count: int
    user_count: int
    low_movie_count: int
    medium_movie_count: int
    high_movie_count: int
    output_sha256: str


class MovieLensCalibrationError(Exception):
    """Expected MovieLens parsing or calibration failure."""


def _calculate_sha256(file_path: Path) -> str:
    """Calculate the SHA-256 checksum of one file."""

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        for block in iter(
            lambda: input_file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _parse_positive_integer(
    value: str,
    *,
    field_name: str,
    file_path: Path,
    line_number: int,
) -> int:
    """Parse and validate a positive integer field."""

    try:
        parsed_value = int(value)
    except ValueError as error:
        raise MovieLensCalibrationError(
            f"{file_path} line {line_number} contains "
            f"an invalid {field_name}."
        ) from error

    if parsed_value <= 0:
        raise MovieLensCalibrationError(
            f"{file_path} line {line_number} contains "
            f"a non-positive {field_name}."
        )

    return parsed_value


def _load_movies(
    source_directory: Path,
) -> dict[int, MovieRecord]:
    """Load MovieLens movie metadata."""

    movies_file = source_directory / "movies.dat"

    if not movies_file.is_file():
        raise MovieLensCalibrationError(
            f"MovieLens movies file was not found: {movies_file}"
        )

    movies: dict[int, MovieRecord] = {}

    with movies_file.open(
        "r",
        encoding="latin-1",
        newline="",
    ) as input_file:
        for line_number, raw_line in enumerate(
            input_file,
            start=1,
        ):
            line = raw_line.rstrip("\r\n")
            fields = line.split("::", 2)

            if len(fields) != 3:
                raise MovieLensCalibrationError(
                    f"{movies_file} line {line_number} does not "
                    "contain exactly three fields."
                )

            movie_id = _parse_positive_integer(
                fields[0],
                field_name="movie ID",
                file_path=movies_file,
                line_number=line_number,
            )
            title = fields[1].strip()
            genres = fields[2].strip()

            if not title:
                raise MovieLensCalibrationError(
                    f"{movies_file} line {line_number} has "
                    "an empty movie title."
                )

            if movie_id in movies:
                raise MovieLensCalibrationError(
                    f"{movies_file} contains duplicate movie "
                    f"ID {movie_id}."
                )

            movies[movie_id] = MovieRecord(
                movie_id=movie_id,
                title=title,
                genres=genres,
            )

    if not movies:
        raise MovieLensCalibrationError(
            "MovieLens movies.dat contains no movies."
        )

    return movies


def _aggregate_ratings(
    source_directory: Path,
    movies: dict[int, MovieRecord],
) -> RatingStatistics:
    """Count ratings and rating totals for every movie."""

    ratings_file = source_directory / "ratings.dat"

    if not ratings_file.is_file():
        raise MovieLensCalibrationError(
            f"MovieLens ratings file was not found: {ratings_file}"
        )

    rating_counts = {
        movie_id: 0
        for movie_id in movies
    }
    rating_sums = {
        movie_id: 0
        for movie_id in movies
    }

    user_ids: set[int] = set()
    total_rating_count = 0

    with ratings_file.open(
        "r",
        encoding="latin-1",
        newline="",
    ) as input_file:
        for line_number, raw_line in enumerate(
            input_file,
            start=1,
        ):
            line = raw_line.rstrip("\r\n")
            fields = line.split("::")

            if len(fields) != 4:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} does not "
                    "contain exactly four fields."
                )

            user_id = _parse_positive_integer(
                fields[0],
                field_name="user ID",
                file_path=ratings_file,
                line_number=line_number,
            )
            movie_id = _parse_positive_integer(
                fields[1],
                field_name="movie ID",
                file_path=ratings_file,
                line_number=line_number,
            )

            try:
                rating = int(fields[2])
            except ValueError as error:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} contains "
                    "an invalid rating."
                ) from error

            if rating < 1 or rating > 5:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} contains "
                    f"rating {rating}; expected 1 through 5."
                )

            try:
                timestamp = int(fields[3])
            except ValueError as error:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} contains "
                    "an invalid timestamp."
                ) from error

            if timestamp < 0:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} contains "
                    "a negative timestamp."
                )

            if movie_id not in movies:
                raise MovieLensCalibrationError(
                    f"{ratings_file} line {line_number} references "
                    f"unknown movie ID {movie_id}."
                )

            rating_counts[movie_id] += 1
            rating_sums[movie_id] += rating
            user_ids.add(user_id)
            total_rating_count += 1

    if total_rating_count == 0:
        raise MovieLensCalibrationError(
            "MovieLens ratings.dat contains no ratings."
        )

    return RatingStatistics(
        rating_counts=rating_counts,
        rating_sums=rating_sums,
        rating_count=total_rating_count,
        user_count=len(user_ids),
    )


def _nearest_rank_value(
    sorted_values: list[int],
    percentile: Decimal,
) -> int:
    """Return one nearest-rank percentile value."""

    if not sorted_values:
        raise MovieLensCalibrationError(
            "Cannot calculate a percentile without values."
        )

    rank = math.ceil(
        float(percentile) * len(sorted_values)
    )
    index = max(0, rank - 1)

    return sorted_values[index]


def _tier_for_percentile(
    popularity_percentile: Decimal,
) -> str:
    """Map a popularity percentile to a named tier."""

    if popularity_percentile <= Decimal("0.50"):
        return "LOW"

    if popularity_percentile <= Decimal("0.90"):
        return "MEDIUM"

    return "HIGH"


def _build_popularity_records(
    movies: dict[int, MovieRecord],
    statistics: RatingStatistics,
) -> list[PopularityRecord]:
    """Calculate popularity measurements for every movie."""

    sorted_rating_counts = sorted(
        statistics.rating_counts.values()
    )
    movie_count = len(movies)
    maximum_rating_count = max(sorted_rating_counts)

    records: list[PopularityRecord] = []

    for movie_id in sorted(movies):
        movie = movies[movie_id]
        rating_count = statistics.rating_counts[movie_id]
        rating_sum = statistics.rating_sums[movie_id]

        if rating_count == 0:
            average_rating = Decimal("0.0000")
        else:
            average_rating = (
                Decimal(rating_sum)
                / Decimal(rating_count)
            ).quantize(Decimal("0.0001"))

        percentile_position = bisect_right(
            sorted_rating_counts,
            rating_count,
        )

        popularity_percentile = (
            Decimal(percentile_position)
            / Decimal(movie_count)
        ).quantize(Decimal("0.000001"))

        if maximum_rating_count == 0:
            popularity_score = Decimal("0.000000")
        else:
            popularity_score = Decimal(
                str(
                    math.log1p(rating_count)
                    / math.log1p(maximum_rating_count)
                )
            ).quantize(Decimal("0.000001"))

        selection_weight = (
            Decimal(rating_count)
            / Decimal(statistics.rating_count)
        ).quantize(Decimal("0.000000000001"))

        records.append(
            PopularityRecord(
                movie_id=movie.movie_id,
                title=movie.title,
                genres=movie.genres,
                rating_count=rating_count,
                average_rating=average_rating,
                popularity_score=popularity_score,
                popularity_percentile=(
                    popularity_percentile
                ),
                popularity_tier=_tier_for_percentile(
                    popularity_percentile
                ),
                selection_weight=selection_weight,
            )
        )

    return records


def _write_popularity_csv(
    output_file: Path,
    records: list[PopularityRecord],
) -> None:
    """Write the row-level popularity output locally."""

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = output_file.with_suffix(
        output_file.suffix + ".tmp"
    )

    with temporary_file.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=CSV_FIELD_NAMES,
            lineterminator="\n",
        )
        writer.writeheader()

        for record in records:
            writer.writerow({
                "movie_id": record.movie_id,
                "title": record.title,
                "genres": record.genres,
                "rating_count": record.rating_count,
                "average_rating": record.average_rating,
                "popularity_score": record.popularity_score,
                "popularity_percentile": (
                    record.popularity_percentile
                ),
                "popularity_tier": (
                    record.popularity_tier
                ),
                "selection_weight": (
                    record.selection_weight
                ),
            })

    temporary_file.replace(output_file)


def _load_source_archive_sha256(
    acquisition_file: Path | None,
) -> str | None:
    """Load the recorded source checksum when available."""

    if acquisition_file is None:
        return None

    if not acquisition_file.is_file():
        raise MovieLensCalibrationError(
            f"Acquisition record was not found: "
            f"{acquisition_file}"
        )

    try:
        acquisition = json.loads(
            acquisition_file.read_text(
                encoding="utf-8"
            )
        )
    except (
        json.JSONDecodeError,
        OSError,
    ) as error:
        raise MovieLensCalibrationError(
            "The MovieLens acquisition record is invalid."
        ) from error

    checksum = acquisition.get("archive_sha256")

    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
    ):
        raise MovieLensCalibrationError(
            "The acquisition record does not contain "
            "a valid SHA-256 checksum."
        )

    return checksum


def _write_summary(
    *,
    summary_file: Path,
    source_directory: Path,
    output_file: Path,
    output_sha256: str,
    source_archive_sha256: str | None,
    records: list[PopularityRecord],
    statistics: RatingStatistics,
) -> None:
    """Write a non-row-level calibration summary."""

    rating_counts = sorted(
        record.rating_count
        for record in records
    )

    tier_movie_counts = Counter(
        record.popularity_tier
        for record in records
    )

    tier_rating_counts = Counter({
        "LOW": 0,
        "MEDIUM": 0,
        "HIGH": 0,
    })

    for record in records:
        tier_rating_counts[
            record.popularity_tier
        ] += record.rating_count

    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_name": "MovieLens 1M",
        "source_directory": str(source_directory),
        "source_archive_sha256": (
            source_archive_sha256
        ),
        "calibration_output_file": str(output_file),
        "calibration_output_sha256": output_sha256,
        "movie_count": len(records),
        "rating_count": statistics.rating_count,
        "user_count": statistics.user_count,
        "zero_rating_movie_count": sum(
            1
            for record in records
            if record.rating_count == 0
        ),
        "maximum_rating_count": max(rating_counts),
        "percentile_rating_count_thresholds": {
            "p50": _nearest_rank_value(
                rating_counts,
                Decimal("0.50"),
            ),
            "p90": _nearest_rank_value(
                rating_counts,
                Decimal("0.90"),
            ),
            "p95": _nearest_rank_value(
                rating_counts,
                Decimal("0.95"),
            ),
            "p99": _nearest_rank_value(
                rating_counts,
                Decimal("0.99"),
            ),
        },
        "popularity_tier_definition": {
            "LOW": "popularity percentile <= 0.50",
            "MEDIUM": (
                "popularity percentile > 0.50 "
                "and <= 0.90"
            ),
            "HIGH": "popularity percentile > 0.90",
        },
        "tier_movie_counts": {
            "LOW": tier_movie_counts["LOW"],
            "MEDIUM": tier_movie_counts["MEDIUM"],
            "HIGH": tier_movie_counts["HIGH"],
        },
        "tier_rating_counts": {
            "LOW": tier_rating_counts["LOW"],
            "MEDIUM": tier_rating_counts["MEDIUM"],
            "HIGH": tier_rating_counts["HIGH"],
        },
        "popularity_score_method": (
            "log1p(rating_count) divided by "
            "log1p(maximum_rating_count)"
        ),
        "selection_weight_method": (
            "movie rating_count divided by "
            "total rating_count"
        ),
        "contention_label_source": (
            "Measured PostgreSQL experiment telemetry"
        ),
        "raw_data_redistributed": False,
    }

    summary_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_file.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def calibrate_movielens(
    *,
    source_directory: Path = (
        DEFAULT_SOURCE_DIRECTORY
    ),
    output_file: Path = DEFAULT_OUTPUT_FILE,
    summary_file: Path = DEFAULT_SUMMARY_FILE,
    acquisition_file: Path | None = (
        DEFAULT_ACQUISITION_FILE
    ),
) -> MovieLensCalibrationResult:
    """Build a MovieLens popularity calibration."""

    movies = _load_movies(source_directory)

    statistics = _aggregate_ratings(
        source_directory,
        movies,
    )

    records = _build_popularity_records(
        movies,
        statistics,
    )

    if len(records) != len(movies):
        raise MovieLensCalibrationError(
            "Not every movie received a popularity record."
        )

    _write_popularity_csv(
        output_file,
        records,
    )

    output_sha256 = _calculate_sha256(
        output_file
    )

    source_archive_sha256 = (
        _load_source_archive_sha256(
            acquisition_file
        )
    )

    _write_summary(
        summary_file=summary_file,
        source_directory=source_directory,
        output_file=output_file,
        output_sha256=output_sha256,
        source_archive_sha256=(
            source_archive_sha256
        ),
        records=records,
        statistics=statistics,
    )

    tier_counts = Counter(
        record.popularity_tier
        for record in records
    )

    return MovieLensCalibrationResult(
        output_file=output_file.resolve(),
        summary_file=summary_file.resolve(),
        movie_count=len(records),
        rating_count=statistics.rating_count,
        user_count=statistics.user_count,
        low_movie_count=tier_counts["LOW"],
        medium_movie_count=tier_counts["MEDIUM"],
        high_movie_count=tier_counts["HIGH"],
        output_sha256=output_sha256,
    )


def parse_arguments() -> argparse.Namespace:
    """Read command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Calculate MovieLens 1M popularity "
            "calibration values."
        )
    )

    parser.add_argument(
        "--source-directory",
        type=Path,
        default=DEFAULT_SOURCE_DIRECTORY,
        help=(
            "Directory containing movies.dat and "
            "ratings.dat."
        ),
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=(
            "Local row-level popularity CSV. "
            "This file must remain ignored by Git."
        ),
    )
    parser.add_argument(
        "--summary-file",
        type=Path,
        default=DEFAULT_SUMMARY_FILE,
        help=(
            "Tracked non-row-level calibration "
            "summary JSON."
        ),
    )
    parser.add_argument(
        "--acquisition-file",
        type=Path,
        default=DEFAULT_ACQUISITION_FILE,
        help=(
            "MovieLens archive acquisition record."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Execute the MovieLens calibration command."""

    arguments = parse_arguments()

    try:
        result = calibrate_movielens(
            source_directory=(
                arguments.source_directory
            ),
            output_file=arguments.output_file,
            summary_file=arguments.summary_file,
            acquisition_file=(
                arguments.acquisition_file
            ),
        )
    except MovieLensCalibrationError as error:
        print(
            f"MovieLens calibration error: {error}",
            file=sys.stderr,
        )
        return 2

    print("MovieLens calibration completed:")
    print(f"  Movies: {result.movie_count}")
    print(f"  Ratings: {result.rating_count}")
    print(f"  Users: {result.user_count}")
    print(f"  LOW movies: {result.low_movie_count}")
    print(
        f"  MEDIUM movies: "
        f"{result.medium_movie_count}"
    )
    print(f"  HIGH movies: {result.high_movie_count}")
    print(f"  Output: {result.output_file}")
    print(f"  Summary: {result.summary_file}")
    print(f"  SHA-256: {result.output_sha256}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())