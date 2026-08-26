from pathlib import Path


CONTRACT_FILE = Path(
    "docs/model_training/"
    "automatic_prediction_contract_v1.md"
)


def _contract_text() -> str:
    """Load the automatic prediction contract."""

    assert CONTRACT_FILE.is_file()

    return CONTRACT_FILE.read_text(
        encoding="utf-8"
    )


def test_contract_defines_automatic_prediction_flow() -> None:
    """Require the sample-to-prediction workflow."""

    contract = _contract_text()

    required_content = (
        "# Automatic Prediction Contract v1",
        "Transaction 1",
        "Transaction 2",
        "create_prediction_for_metric_sample()",
        "MetricCollectionResult",
    )

    for expected_text in required_content:
        assert expected_text in contract


def test_contract_defines_failure_behavior() -> None:
    """Require explicit and safe prediction failure handling."""

    contract = _contract_text()

    required_content = (
        "MetricCollectorError",
        "ACTIVE_MODEL_NOT_FOUND",
        "PREDICTION_INPUT_INVALID",
        "Transaction 1",
        "Transaction 2",
    )

    for expected_text in required_content:
        assert expected_text in contract


def test_contract_defines_completion_invariants() -> None:
    """Require measurable integration completion rules."""

    contract = _contract_text()

    required_content = (
        "prediction coverage = 1.0",
        "actual_label = null",
        "no database migration",
        "git diff --check",
    )

    for expected_text in required_content:
        assert expected_text in contract