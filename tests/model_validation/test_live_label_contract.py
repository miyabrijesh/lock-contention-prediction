from pathlib import Path


CONTRACT_FILE = Path(
    "docs/model_validation/"
    "live_label_contract_v1.md"
)


def _contract_text() -> str:
    """Load the live-label contract."""

    assert CONTRACT_FILE.is_file()

    return CONTRACT_FILE.read_text(
        encoding="utf-8"
    )


def test_contract_matches_training_target() -> None:
    """Require the frozen training target definition."""

    contract = _contract_text()

    required_content = (
        "future_high_contention_30s",
        "(t, t + 30 seconds]",
        "blocked_sessions_30s >= 2",
        "waiting_locks_30s >= 2",
        "deadlock_delta_sum_30s >= 1",
        "timeout_rate_30s > 0",
    )

    for expected_text in required_content:
        assert expected_text in contract


def test_contract_prevents_false_negative_labels() -> None:
    """Require strict maturity and coverage rules."""

    contract = _contract_text()

    required_content = (
        "actual_label = null",
        "WINDOW_NOT_MATURE",
        "INSUFFICIENT_FUTURE_COVERAGE",
        "latest_sampled_at >= t + 30 seconds",
        "expected_future_sample_count = 15",
        "must never be converted",
    )

    for expected_text in required_content:
        assert expected_text in contract


def test_contract_defines_provenance() -> None:
    """Require auditable label provenance."""

    contract = _contract_text()

    required_content = (
        "actual_label_assigned_at",
        "actual_label_contract_version",
        "actual_label_evidence",
        "live-label-v1",
        "maximum_sample_gap_ms",
        "controlled Alembic migration",
    )

    for expected_text in required_content:
        assert expected_text in contract


def test_contract_prevents_leakage() -> None:
    """Require independence between prediction and outcome."""

    contract = _contract_text()

    required_content = (
        "Leakage prohibitions",
        "prediction probability",
        "prediction risk level",
        "model threshold",
        "p95 latency alone",
        "another experiment",
        "improve model metrics",
    )

    for expected_text in required_content:
        assert expected_text in contract