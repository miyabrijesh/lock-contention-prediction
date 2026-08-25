from datetime import UTC, datetime
from uuid import UUID

import pytest

from data_pipeline.audit_training_dataset import (
    TrainingDatasetAuditError,
    _validate_row,
)
from data_pipeline.export_experiment import (
    COUNT_FEATURE_NAMES,
    CSV_FIELD_NAMES,
    DECIMAL_FEATURE_NAMES,
)


EXPERIMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _valid_row(*, positive: bool = False) -> dict[str, str]:
    """Create one complete row using the versioned CSV schema."""

    row = {field: "0" for field in CSV_FIELD_NAMES}
    row.update(
        {
            "schema_version": "1.0.0",
            "experiment_run_id": str(EXPERIMENT_ID),
            "experiment_name": "Audit test experiment",
            "sampled_at": datetime(2026, 8, 25, tzinfo=UTC).isoformat(),
            "collector_version": "pytest",
            "code_version": "pytest",
            "booking_mode": "EXPERIMENT_RECYCLE",
            "random_seed": "4101",
            "sample_interval_sec": "2",
            "simulated_work_ms": "75",
            "is_complete": "true",
            "quality_notes": "{}",
            "connection_utilization": "0.25",
            "failure_rate": "0",
            "hotspot_ratio": "0.20",
            "booking_success_rate": "1",
            "conflict_rate": "0",
            "timeout_rate": "0",
            "future_max_timeout_rate_30s": "0",
            "future_max_p95_latency_ms_30s": "100",
            "future_high_contention_30s": "1" if positive else "0",
            "future_max_blocked_sessions_30s": "2" if positive else "0",
        }
    )

    for field in COUNT_FEATURE_NAMES:
        row.setdefault(field, "0")
    for field in DECIMAL_FEATURE_NAMES:
        row.setdefault(field, "0")
    return row


def test_validate_negative_training_row() -> None:
    """Accept a complete row with negative future evidence."""

    experiment_id, _, label = _validate_row(_valid_row(), 2)
    assert experiment_id == str(EXPERIMENT_ID)
    assert label == 0


def test_validate_positive_training_row() -> None:
    """Accept a label supported by future blocked sessions."""

    _, _, label = _validate_row(_valid_row(positive=True), 2)
    assert label == 1


def test_reject_inconsistent_future_label() -> None:
    """Reject a positive label without positive future evidence."""

    row = _valid_row()
    row["future_high_contention_30s"] = "1"

    with pytest.raises(
        TrainingDatasetAuditError,
        match="inconsistent with future evidence",
    ):
        _validate_row(row, 2)


def test_reject_rate_above_one() -> None:
    """Reject impossible bounded rate values."""

    row = _valid_row()
    row["timeout_rate"] = "1.2"

    with pytest.raises(
        TrainingDatasetAuditError,
        match="must not exceed 1",
    ):
        _validate_row(row, 2)


def test_reject_incomplete_row() -> None:
    """Reject telemetry that was not marked complete."""

    row = _valid_row()
    row["is_complete"] = "false"

    with pytest.raises(
        TrainingDatasetAuditError,
        match="not marked complete",
    ):
        _validate_row(row, 2)

def test_hotspot_ratio_is_runtime_metadata() -> None:
    """Allow measured hotspot ratio to vary between samples."""

    from data_pipeline.audit_training_dataset import (
        CONSISTENT_FIELDS,
    )

    assert "hotspot_ratio" not in CONSISTENT_FIELDS