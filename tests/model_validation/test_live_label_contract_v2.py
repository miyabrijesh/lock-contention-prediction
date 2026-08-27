from pathlib import Path

from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
    _minimum_future_sample_count,
)


PROJECT_ROOT = (
    Path(__file__).resolve().parents[2]
)

V1_CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_validation"
    / "live_label_contract_v1.md"
)

V2_CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_validation"
    / "live_label_contract_v2.md"
)


def test_v2_is_the_active_runtime_contract() -> None:
    """Require new assignments to use contract v2."""

    assert (
        LIVE_LABEL_CONTRACT_VERSION
        == "live-label-v2"
    )


def test_v1_contract_remains_preserved() -> None:
    """Keep the historical v1 contract immutable."""

    assert V1_CONTRACT_FILE.is_file()

    document = V1_CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    assert "live-label-v1" in document
    assert "exact future-sample count" in document


def test_v2_contract_documents_the_correction() -> None:
    """Require an auditable jitter correction."""

    assert V2_CONTRACT_FILE.is_file()

    document = V2_CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_content = (
        "live-label-v2",
        "minimum_future_sample_count",
        "first_boundary_gap_ms",
        "final_boundary_gap_ms",
        "maximum_allowed_gap_ms",
        "Existing `live-label-v1` rows are immutable",
        "A fresh experiment must be used",
    )

    for required_text in required_content:
        assert required_text in document


def test_v2_allows_one_nominal_boundary_sample() -> None:
    """Permit one count of boundary scheduling jitter."""

    assert _minimum_future_sample_count(15) == 14
    assert _minimum_future_sample_count(2) == 1
    assert _minimum_future_sample_count(1) == 1
