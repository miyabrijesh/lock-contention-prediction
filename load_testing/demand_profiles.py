import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DEMAND_PROFILE_FILE = (
    PROJECT_ROOT
    / "load_testing"
    / "config"
    / "demand_profiles.json"
)


class DemandProfileName(str, Enum):
    """Supported workload demand calibrations."""

    CUSTOM = "CUSTOM"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DemandProfileError(ValueError):
    """Raised when demand-profile data is missing or invalid."""


@dataclass(frozen=True)
class DemandProfile:
    """One validated MovieLens-derived demand profile."""

    name: DemandProfileName
    profile_version: str
    source_dataset: str
    source_movie_count: int
    source_rating_count: int
    movie_share: Decimal
    interaction_share: Decimal
    interaction_density_index: Decimal
    relative_density_to_medium: Decimal
    hotspot_ratio_minimum: Decimal
    hotspot_ratio_maximum: Decimal
    hotspot_seat_count_minimum: int
    hotspot_seat_count_maximum: int
    workload_levels: tuple[str, ...]
    description: str


def parse_demand_profile_name(
    value: DemandProfileName | str,
) -> DemandProfileName:
    """Normalize one demand-profile name."""

    if isinstance(value, DemandProfileName):
        return value

    normalized_value = value.strip().upper()

    try:
        return DemandProfileName(normalized_value)
    except ValueError as error:
        supported_names = ", ".join(
            profile.value
            for profile in DemandProfileName
        )

        raise DemandProfileError(
            "Unsupported demand profile "
            f"{value!r}. Supported values: "
            f"{supported_names}."
        ) from error


def _required_mapping(
    mapping: dict[str, object],
    field_name: str,
) -> dict[str, object]:
    """Read one required JSON object."""

    value = mapping.get(field_name)

    if not isinstance(value, dict):
        raise DemandProfileError(
            f"Demand profiles contain an invalid {field_name}."
        )

    return value


def _required_text(
    mapping: dict[str, object],
    field_name: str,
) -> str:
    """Read one required nonempty text value."""

    value = mapping.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise DemandProfileError(
            f"Demand profiles contain an invalid {field_name}."
        )

    return value.strip()


def _required_decimal(
    mapping: dict[str, object],
    field_name: str,
) -> Decimal:
    """Read one required finite decimal value."""

    value = mapping.get(field_name)

    try:
        decimal_value = Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as error:
        raise DemandProfileError(
            f"Demand profiles contain an invalid {field_name}."
        ) from error

    if not decimal_value.is_finite():
        raise DemandProfileError(
            f"Demand profiles contain an invalid {field_name}."
        )

    return decimal_value


def _required_nonnegative_integer(
    mapping: dict[str, object],
    field_name: str,
) -> int:
    """Read one required nonnegative integer."""

    value = mapping.get(field_name)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise DemandProfileError(
            f"Demand profiles contain an invalid {field_name}."
        )

    return value


def _required_workload_levels(
    mapping: dict[str, object],
) -> tuple[str, ...]:
    """Read the workload levels assigned to a profile."""

    value = mapping.get("workload_levels")

    if (
        not isinstance(value, list)
        or not value
        or any(
            not isinstance(item, str)
            or not item.strip()
            for item in value
        )
    ):
        raise DemandProfileError(
            "Demand profiles contain invalid workload_levels."
        )

    return tuple(
        item.strip()
        for item in value
        if isinstance(item, str)
    )


def load_demand_profiles(
    profile_file: Path = DEFAULT_DEMAND_PROFILE_FILE,
) -> dict[DemandProfileName, DemandProfile]:
    """Load and validate the tracked demand-profile file."""

    if not profile_file.is_file():
        raise DemandProfileError(
            f"Demand-profile file was not found: {profile_file}"
        )

    try:
        loaded_value = json.loads(
            profile_file.read_text(encoding="utf-8")
        )
    except (
        json.JSONDecodeError,
        OSError,
    ) as error:
        raise DemandProfileError(
            "Demand-profile file is not valid JSON."
        ) from error

    if not isinstance(loaded_value, dict):
        raise DemandProfileError(
            "Demand-profile file must contain a JSON object."
        )

    profile_version = _required_text(
        loaded_value,
        "profile_version",
    )
    source = _required_mapping(
        loaded_value,
        "source",
    )
    source_dataset = _required_text(
        source,
        "dataset_name",
    )
    profiles_mapping = _required_mapping(
        loaded_value,
        "profiles",
    )

    profiles: dict[
        DemandProfileName,
        DemandProfile,
    ] = {}

    for profile_name in (
        DemandProfileName.LOW,
        DemandProfileName.MEDIUM,
        DemandProfileName.HIGH,
    ):
        raw_profile = _required_mapping(
            profiles_mapping,
            profile_name.value,
        )
        ratio_range = _required_mapping(
            raw_profile,
            "hotspot_ratio_range",
        )
        seat_count_range = _required_mapping(
            raw_profile,
            "hotspot_seat_count_range",
        )

        profile = DemandProfile(
            name=profile_name,
            profile_version=profile_version,
            source_dataset=source_dataset,
            source_movie_count=(
                _required_nonnegative_integer(
                    raw_profile,
                    "source_movie_count",
                )
            ),
            source_rating_count=(
                _required_nonnegative_integer(
                    raw_profile,
                    "source_rating_count",
                )
            ),
            movie_share=_required_decimal(
                raw_profile,
                "movie_share",
            ),
            interaction_share=_required_decimal(
                raw_profile,
                "interaction_share",
            ),
            interaction_density_index=(
                _required_decimal(
                    raw_profile,
                    "interaction_density_index",
                )
            ),
            relative_density_to_medium=(
                _required_decimal(
                    raw_profile,
                    "relative_density_to_medium",
                )
            ),
            hotspot_ratio_minimum=_required_decimal(
                ratio_range,
                "minimum",
            ),
            hotspot_ratio_maximum=_required_decimal(
                ratio_range,
                "maximum",
            ),
            hotspot_seat_count_minimum=(
                _required_nonnegative_integer(
                    seat_count_range,
                    "minimum",
                )
            ),
            hotspot_seat_count_maximum=(
                _required_nonnegative_integer(
                    seat_count_range,
                    "maximum",
                )
            ),
            workload_levels=(
                _required_workload_levels(raw_profile)
            ),
            description=_required_text(
                raw_profile,
                "description",
            ),
        )

        if (
            profile.hotspot_ratio_minimum < 0
            or profile.hotspot_ratio_maximum > 1
            or profile.hotspot_ratio_minimum
            > profile.hotspot_ratio_maximum
        ):
            raise DemandProfileError(
                f"{profile_name.value} contains an invalid "
                "hotspot-ratio range."
            )

        if (
            profile.hotspot_seat_count_minimum <= 0
            or profile.hotspot_seat_count_minimum
            > profile.hotspot_seat_count_maximum
        ):
            raise DemandProfileError(
                f"{profile_name.value} contains an invalid "
                "hotspot-seat-count range."
            )

        profiles[profile_name] = profile

    return profiles


def validate_demand_configuration(
    *,
    profile_name: DemandProfileName | str,
    hotspot_ratio: Decimal,
    hotspot_seat_count: int,
    profile_file: Path = DEFAULT_DEMAND_PROFILE_FILE,
) -> DemandProfile | None:
    """Validate an experiment against its calibrated profile."""

    parsed_name = parse_demand_profile_name(
        profile_name
    )

    if parsed_name == DemandProfileName.CUSTOM:
        return None

    profiles = load_demand_profiles(profile_file)
    profile = profiles[parsed_name]

    if not (
        profile.hotspot_ratio_minimum
        <= hotspot_ratio
        <= profile.hotspot_ratio_maximum
    ):
        raise DemandProfileError(
            f"{parsed_name.value} demand requires hotspot_ratio "
            f"between {profile.hotspot_ratio_minimum} and "
            f"{profile.hotspot_ratio_maximum}."
        )

    if not (
        profile.hotspot_seat_count_minimum
        <= hotspot_seat_count
        <= profile.hotspot_seat_count_maximum
    ):
        raise DemandProfileError(
            f"{parsed_name.value} demand requires between "
            f"{profile.hotspot_seat_count_minimum} and "
            f"{profile.hotspot_seat_count_maximum} "
            "hotspot seats."
        )

    return profile
