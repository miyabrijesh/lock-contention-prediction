from pathlib import Path


DEMO_FILE = Path(
    "docs/demo/phase7_end_to_end_demo.md"
)


def _demo_text() -> str:
    """Load the Phase 7 demonstration guide."""

    assert DEMO_FILE.is_file()

    return DEMO_FILE.read_text(
        encoding="utf-8"
    )


def test_demo_defines_complete_runtime_flow() -> None:
    """Require every major runtime component."""

    demo = _demo_text()

    required_content = (
        "# Phase 7 End-to-End Prediction Demonstration",
        "EXPERIMENT_MODE_ENABLED=true",
        "backend.app.main:app",
        "load_testing.run_synchronized_experiment",
        "monitoring.prediction_coverage",
        "Final experiment status: COMPLETED",
    )

    for expected_text in required_content:
        assert expected_text in demo


def test_demo_defines_prediction_evidence() -> None:
    """Require measurable live-prediction evidence."""

    demo = _demo_text()

    required_content = (
        "prediction_probability=",
        "risk_level=",
        "threshold=",
        "stale=False",
        "prediction coverage = 1.0",
        "Missing predictions: 0",
        "Duplicate predictions: 0",
        "Labeled predictions: 0",
    )

    for expected_text in required_content:
        assert expected_text in demo


def test_demo_defines_safety_and_limitations() -> None:
    """Require failure handling and honest limitations."""

    demo = _demo_text()

    required_content = (
        "Failure recovery",
        "Active model not found",
        "Do not manually insert predictions",
        "Known limitation",
        "commercial ticketing platform",
        "production ticketing service",
        "does not prove",
    )

    for expected_text in required_content:
        assert expected_text in demo