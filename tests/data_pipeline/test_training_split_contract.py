import hashlib
import json
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPLIT_MANIFEST = (
    PROJECT_ROOT
    / "docs/data/contention_training_dataset_v1_splits.json"
)


def _load_manifest() -> dict[str, object]:
    """Load the frozen, Git-tracked split assignment."""

    value = json.loads(SPLIT_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_frozen_split_counts_are_complete_and_disjoint() -> None:
    """All 60 experiments and 2,666 rows appear exactly once."""

    manifest = _load_manifest()
    splits = manifest["splits"]
    assert isinstance(splits, dict)

    expected = {
        "train": (42, 1864, 772, 1092),
        "validation": (9, 400, 166, 234),
        "test": (9, 402, 168, 234),
    }
    experiment_sets: dict[str, set[str]] = {}

    for name, counts in expected.items():
        value = splits[name]
        assert isinstance(value, dict)
        experiment_ids = value["experiment_ids"]
        assert isinstance(experiment_ids, list)
        experiment_sets[name] = set(experiment_ids)
        assert value["experiment_count"] == counts[0]
        assert value["row_count"] == counts[1]
        distribution = value["label_distribution"]
        assert isinstance(distribution, dict)
        assert distribution["negative"] == counts[2]
        assert distribution["positive"] == counts[3]

    assert experiment_sets["train"].isdisjoint(
        experiment_sets["validation"]
    )
    assert experiment_sets["train"].isdisjoint(
        experiment_sets["test"]
    )
    assert experiment_sets["validation"].isdisjoint(
        experiment_sets["test"]
    )
    assert len(set().union(*experiment_sets.values())) == 60
    assert manifest["total_experiment_count"] == 60
    assert manifest["total_row_count"] == 2666


def test_frozen_assignment_checksum_is_reproducible() -> None:
    """Detect any modification to the experiment assignment."""

    manifest = _load_manifest()
    splits = manifest["splits"]
    assert isinstance(splits, dict)
    assignment = {
        name: splits[name]["experiment_ids"]
        for name in ("train", "validation", "test")
    }
    encoded = json.dumps(
        assignment,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual = hashlib.sha256(encoded).hexdigest()
    assert actual == manifest["assignment_sha256"]
    assert actual == (
        "4a24f55d3d1121f50da74ae3ec21cfb"
        "a4a6f3f9044069edff7113d228c4c8374"
    )


def test_every_split_has_matrix_coverage_and_usable_balance() -> None:
    """Require all marginal conditions and both target classes."""

    manifest = _load_manifest()
    splits = manifest["splits"]
    assert isinstance(splits, dict)

    expected_coverage = {
        "traffic_patterns": {
            "STEADY",
            "RAMP",
            "BURST",
            "FLASH_SALE",
            "COOLDOWN",
        },
        "demand_profiles": {"LOW", "MEDIUM", "HIGH"},
        "pressure_levels": {"STANDARD", "STRESSED"},
        "random_seeds": {4101, 4102},
    }

    for name in ("train", "validation", "test"):
        value = splits[name]
        assert isinstance(value, dict)
        coverage = value["matrix_coverage"]
        distribution = value["label_distribution"]
        assert isinstance(coverage, dict)
        assert isinstance(distribution, dict)

        for dimension, expected_values in expected_coverage.items():
            assert set(coverage[dimension]) == expected_values

        negative = distribution["negative"]
        positive = distribution["positive"]
        assert isinstance(negative, int)
        assert isinstance(positive, int)
        total = negative + positive
        minority_fraction = Decimal(min(negative, positive)) / Decimal(
            total
        )
        assert minority_fraction >= Decimal("0.20")