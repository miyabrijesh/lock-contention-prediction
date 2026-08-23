from datetime import UTC, datetime
from decimal import Decimal

import pytest

from monitoring.application_metrics import (
    ApplicationMetricsError,
    application_metrics_from_mapping,
)
from monitoring.collector import _calculate_deadlock_delta
from monitoring.postgres_metrics import (
    PostgreSQLMetricsError,
    metrics_from_mapping,
)


def test_postgresql_metrics_from_valid_mapping() -> None:
    """Convert one valid PostgreSQL result row into metrics."""

    sampled_at = datetime(
        2026,
        8,
        23,
        12,
        0,
        tzinfo=UTC,
    )

    row: dict[str, object] = {
        "sampled_at": sampled_at,
        "active_transactions": 4,
        "blocked_sessions": 2,
        "waiting_locks": 2,
        "total_locks": 15,
        "total_connections": 8,
        "connection_utilization": Decimal("0.125000"),
        "estimated_wait_age_ms": Decimal("250.500"),
        "cumulative_deadlocks": 3,
    }

    metrics = metrics_from_mapping(row)

    assert metrics.sampled_at == sampled_at
    assert metrics.active_transactions == 4
    assert metrics.blocked_sessions == 2
    assert metrics.waiting_locks == 2
    assert metrics.total_locks == 15
    assert metrics.total_connections == 8
    assert metrics.connection_utilization == Decimal("0.125000")
    assert metrics.estimated_wait_age_ms == Decimal("250.500")
    assert metrics.cumulative_deadlocks == 3


def test_postgresql_metrics_reject_invalid_utilization() -> None:
    """Connection utilization cannot be greater than one."""

    row: dict[str, object] = {
        "sampled_at": datetime.now(UTC),
        "active_transactions": 0,
        "blocked_sessions": 0,
        "waiting_locks": 0,
        "total_locks": 0,
        "total_connections": 0,
        "connection_utilization": Decimal("1.100000"),
        "estimated_wait_age_ms": Decimal("0.000"),
        "cumulative_deadlocks": 0,
    }

    with pytest.raises(PostgreSQLMetricsError):
        metrics_from_mapping(row)


def test_application_metrics_calculate_expected_rates() -> None:
    """Calculate workload rates from one populated interval."""

    row: dict[str, object] = {
        "request_count": 100,
        "active_users": 10,
        "mean_latency_ms": Decimal("250.000"),
        "p95_latency_ms": Decimal("450.000"),
        "successful_requests": 80,
        "conflicting_requests": 10,
        "timeout_requests": 5,
        "hotspot_requests": 75,
    }

    metrics = application_metrics_from_mapping(
        row,
        window_duration_seconds=Decimal("2"),
    )

    assert metrics.request_count == 100
    assert metrics.active_users == 10
    assert metrics.requests_per_sec == Decimal("50")
    assert metrics.mean_latency_ms == Decimal("250")
    assert metrics.p95_latency_ms == Decimal("450")
    assert metrics.failure_rate == Decimal("0.20")
    assert metrics.hotspot_ratio == Decimal("0.75")
    assert metrics.booking_success_rate == Decimal("0.80")
    assert metrics.conflict_rate == Decimal("0.10")
    assert metrics.timeout_rate == Decimal("0.05")
    assert metrics.quality_notes == ()


def test_application_metrics_handle_empty_interval() -> None:
    """An interval without requests remains valid but is annotated."""

    row: dict[str, object] = {
        "request_count": 0,
        "active_users": 0,
        "mean_latency_ms": Decimal("0"),
        "p95_latency_ms": Decimal("0"),
        "successful_requests": 0,
        "conflicting_requests": 0,
        "timeout_requests": 0,
        "hotspot_requests": 0,
    }

    metrics = application_metrics_from_mapping(
        row,
        window_duration_seconds=Decimal("2"),
    )

    assert metrics.request_count == 0
    assert metrics.active_users == 0
    assert metrics.requests_per_sec == Decimal("0")
    assert metrics.failure_rate == Decimal("0")
    assert metrics.hotspot_ratio == Decimal("0")
    assert metrics.booking_success_rate == Decimal("0")
    assert metrics.conflict_rate == Decimal("0")
    assert metrics.timeout_rate == Decimal("0")
    assert "NO_REQUESTS_IN_INTERVAL" in metrics.quality_notes


def test_application_metrics_reject_invalid_window() -> None:
    """The metric window must have a positive duration."""

    row: dict[str, object] = {
        "request_count": 0,
        "active_users": 0,
        "mean_latency_ms": Decimal("0"),
        "p95_latency_ms": Decimal("0"),
        "successful_requests": 0,
        "conflicting_requests": 0,
        "timeout_requests": 0,
        "hotspot_requests": 0,
    }

    with pytest.raises(ApplicationMetricsError):
        application_metrics_from_mapping(
            row,
            window_duration_seconds=Decimal("0"),
        )


def test_deadlock_delta_calculation() -> None:
    """Calculate increases and handle a database counter reset."""

    increased_delta, increased_note = _calculate_deadlock_delta(
        current_cumulative_deadlocks=12,
        previous_cumulative_deadlocks=10,
    )

    assert increased_delta == 2
    assert increased_note is None

    reset_delta, reset_note = _calculate_deadlock_delta(
        current_cumulative_deadlocks=4,
        previous_cumulative_deadlocks=9,
    )

    assert reset_delta == 0
    assert reset_note is not None