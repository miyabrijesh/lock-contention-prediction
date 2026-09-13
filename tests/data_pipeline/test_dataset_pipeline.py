from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    DataExport,
    ExperimentRun,
    MetricSample,
)
from backend.app.models.enums import ExperimentStatus
from data_pipeline.combine_exports import (
    CombinedDatasetError,
    _calculate_label_distribution,
    _combine_rows,
    _validate_header,
    _validate_pilot_dataset,
)
from data_pipeline.export_experiment import (
    CSV_FIELD_NAMES,
    ExperimentExportError,
    _existing_export,
    build_labelled_rows,
)
from load_testing.run_synchronized_experiment import (
    SynchronizedRunnerError,
    _decode_json_response,
    _extract_api_error,
)


def create_completed_experiment(
    *,
    started_at: datetime,
    ended_at: datetime,
) -> ExperimentRun:
    """Create an in-memory completed experiment."""

    return ExperimentRun(
        id=uuid4(),
        name="Dataset Pipeline Test",
        show_id=2,
        status=ExperimentStatus.COMPLETED,
        user_count=10,
        spawn_rate_per_sec=Decimal("5.00"),
        duration_sec=90,
        hotspot_ratio=Decimal("0.9000"),
        hotspot_seat_ids=[
            101,
            102,
            103,
            104,
            105,
        ],
        sample_interval_sec=2,
        simulated_work_ms=200,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=42,
        code_version="test-code-version",
        started_at=started_at,
        ended_at=ended_at,
        created_by="pytest",
    )


def create_metric_sample(
    *,
    experiment_run_id: UUID,
    sampled_at: datetime,
    blocked_sessions: int = 0,
    waiting_locks: int = 0,
    deadlock_delta: int = 0,
    timeout_rate: Decimal = Decimal("0.0000"),
    is_complete: bool = True,
) -> MetricSample:
    """Create one complete in-memory metric sample."""

    return MetricSample(
        id=uuid4(),
        experiment_run_id=experiment_run_id,
        sampled_at=sampled_at,
        active_transactions=2,
        blocked_sessions=blocked_sessions,
        waiting_locks=waiting_locks,
        total_locks=5,
        total_connections=10,
        connection_utilization=Decimal("0.1000"),
        estimated_wait_age_ms=Decimal("0.000"),
        deadlock_delta=deadlock_delta,
        configured_users=10,
        active_users=10,
        requests_per_sec=Decimal("12.500"),
        mean_latency_ms=Decimal("150.000"),
        p95_latency_ms=Decimal("300.000"),
        failure_rate=Decimal("0.0000"),
        hotspot_ratio=Decimal("0.9000"),
        booking_success_rate=Decimal("1.0000"),
        conflict_rate=Decimal("0.0000"),
        timeout_rate=timeout_rate,
        is_complete=is_complete,
        quality_notes=[],
        collector_version="pytest-collector",
    )


def test_build_labelled_rows_uses_future_30_second_window() -> None:
    """A future blocked-session event creates a positive label."""

    base_time = datetime(
        2026,
        8,
        23,
        12,
        0,
        tzinfo=UTC,
    )

    experiment = create_completed_experiment(
        started_at=base_time,
        ended_at=base_time + timedelta(seconds=40),
    )

    samples: list[MetricSample] = []

    for sample_index in range(17):
        blocked_sessions = 0

        # This event occurs at 32 seconds.
        #
        # For the sample at 0 seconds, it is outside:
        #     (0 seconds, 30 seconds]
        #
        # For the sample at 2 seconds, it is inside:
        #     (2 seconds, 32 seconds]
        if sample_index == 16:
            blocked_sessions = 2

        sample = create_metric_sample(
            experiment_run_id=experiment.id,
            sampled_at=(
                base_time
                + timedelta(
                    seconds=sample_index * 2
                )
            ),
            blocked_sessions=blocked_sessions,
        )

        samples.append(sample)

    rows = build_labelled_rows(
        experiment=experiment,
        samples=samples,
    )

    assert len(rows) == 2

    assert (
        rows[0]["future_high_contention_30s"]
        == 0
    )
    assert (
        rows[1]["future_high_contention_30s"]
        == 1
    )

    assert (
        rows[0][
            "future_max_blocked_sessions_30s"
        ]
        == 0
    )
    assert (
        rows[1][
            "future_max_blocked_sessions_30s"
        ]
        == 2
    )


def test_build_labelled_rows_rejects_missing_future_horizon() -> None:
    """An experiment without a full future window cannot be exported."""

    base_time = datetime(
        2026,
        8,
        23,
        12,
        0,
        tzinfo=UTC,
    )

    experiment = create_completed_experiment(
        started_at=base_time,
        ended_at=base_time + timedelta(seconds=28),
    )

    samples = [
        create_metric_sample(
            experiment_run_id=experiment.id,
            sampled_at=(
                base_time
                + timedelta(
                    seconds=sample_index * 2
                )
            ),
        )
        for sample_index in range(15)
    ]

    with pytest.raises(
        ExperimentExportError,
        match=(
            "no samples with a complete "
            "30-second future window"
        ),
    ):
        build_labelled_rows(
            experiment=experiment,
            samples=samples,
        )


def test_build_labelled_rows_rejects_incomplete_sample() -> None:
    """Incomplete telemetry must not enter the ML dataset."""

    base_time = datetime(
        2026,
        8,
        23,
        12,
        0,
        tzinfo=UTC,
    )

    experiment = create_completed_experiment(
        started_at=base_time,
        ended_at=base_time + timedelta(seconds=40),
    )

    samples = [
        create_metric_sample(
            experiment_run_id=experiment.id,
            sampled_at=(
                base_time
                + timedelta(
                    seconds=sample_index * 2
                )
            ),
            is_complete=(
                sample_index != 5
            ),
        )
        for sample_index in range(17)
    ]

    with pytest.raises(ExperimentExportError):
        build_labelled_rows(
            experiment=experiment,
            samples=samples,
        )


def test_validate_header_accepts_exact_schema() -> None:
    """The registered CSV schema is accepted unchanged."""

    _validate_header(
        list(CSV_FIELD_NAMES),
        Path("valid-export.csv"),
    )


def test_validate_header_rejects_missing_column() -> None:
    """A CSV missing a required column is rejected."""

    incomplete_header = list(
        CSV_FIELD_NAMES
    )[:-1]

    with pytest.raises(CombinedDatasetError):
        _validate_header(
            incomplete_header,
            Path("invalid-export.csv"),
        )


def test_combine_rows_sorts_by_experiment_and_time() -> None:
    """Combined rows have a deterministic ordering."""

    rows = _combine_rows([
        [
            {
                "experiment_run_id": (
                    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
                ),
                "sampled_at": (
                    "2026-08-23T12:00:04+00:00"
                ),
                "future_high_contention_30s": "1",
            },
            {
                "experiment_run_id": (
                    "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
                ),
                "sampled_at": (
                    "2026-08-23T12:00:02+00:00"
                ),
                "future_high_contention_30s": "0",
            },
        ],
        [
            {
                "experiment_run_id": (
                    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
                ),
                "sampled_at": (
                    "2026-08-23T12:00:06+00:00"
                ),
                "future_high_contention_30s": "0",
            },
        ],
    ])

    observed_order = [
        (
            row["experiment_run_id"],
            row["sampled_at"],
        )
        for row in rows
    ]

    assert observed_order == [
        (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "2026-08-23T12:00:06+00:00",
        ),
        (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "2026-08-23T12:00:02+00:00",
        ),
        (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "2026-08-23T12:00:04+00:00",
        ),
    ]


def test_combine_rows_rejects_duplicate_sample() -> None:
    """Duplicate experiment timestamps would duplicate training data."""

    duplicate_row = {
        "experiment_run_id": (
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        ),
        "sampled_at": (
            "2026-08-23T12:00:00+00:00"
        ),
        "future_high_contention_30s": "0",
    }

    with pytest.raises(CombinedDatasetError):
        _combine_rows([
            [duplicate_row],
            [duplicate_row.copy()],
        ])


def test_label_distribution_and_pilot_validation() -> None:
    """A three-experiment, two-class, 60-row pilot is valid."""

    rows = [
        {
            "future_high_contention_30s": (
                "0" if index < 30 else "1"
            )
        }
        for index in range(60)
    ]

    (
        negative_count,
        positive_count,
    ) = _calculate_label_distribution(rows)

    assert negative_count == 30
    assert positive_count == 30

    _validate_pilot_dataset(
        experiment_count=3,
        rows=rows,
        negative_label_count=negative_count,
        positive_label_count=positive_count,
    )


def test_pilot_validation_rejects_too_few_rows() -> None:
    """A small dataset is not accepted as the pilot dataset."""

    rows = [
        {
            "future_high_contention_30s": (
                "0" if index < 10 else "1"
            )
        }
        for index in range(20)
    ]

    with pytest.raises(CombinedDatasetError):
        _validate_pilot_dataset(
            experiment_count=3,
            rows=rows,
            negative_label_count=10,
            positive_label_count=10,
        )


def test_decode_json_response_returns_object() -> None:
    """The runner accepts a valid JSON API object."""

    result = _decode_json_response(
        b'{"id":"test-id","status":"DRAFT"}'
    )

    assert result == {
        "id": "test-id",
        "status": "DRAFT",
    }


def test_decode_json_response_rejects_invalid_json() -> None:
    """The runner rejects a non-JSON API response."""

    with pytest.raises(SynchronizedRunnerError):
        _decode_json_response(
            b"not-json"
        )


def test_extract_api_error_reads_structured_detail() -> None:
    """Structured API error codes and messages remain visible."""

    response_body = (
        b'{"detail":{'
        b'"code":"EXPERIMENT_NOT_FOUND",'
        b'"message":"The experiment was not found."'
        b"}}"
    )

    message = _extract_api_error(
        response_body
    )

    assert "EXPERIMENT_NOT_FOUND" in message
    assert "The experiment was not found." in message

def test_existing_export_is_scoped_to_schema_version(
    db_session: Session,
) -> None:
    """Allow one registered export for each schema version."""
    base_time = datetime(
        2026,
        9,
        13,
        12,
        0,
        tzinfo=UTC,
    )

    experiment = create_completed_experiment(
        started_at=base_time,
        ended_at=base_time + timedelta(seconds=120),
    )

    db_session.add(experiment)
    db_session.flush()

    version_1_export = DataExport(
        experiment_run_id=experiment.id,
        file_path="/tmp/version-1.csv",
        file_sha256="a" * 64,
        schema_version="1.0.0",
        row_count=44,
        created_by="pytest",
    )
    version_2_export = DataExport(
        experiment_run_id=experiment.id,
        file_path="/tmp/version-2.csv",
        file_sha256="b" * 64,
        schema_version="2.0.0",
        row_count=43,
        created_by="pytest",
    )

    db_session.add_all([
        version_1_export,
        version_2_export,
    ])
    db_session.flush()

    observed_v1 = _existing_export(
        db_session,
        experiment.id,
    )
    observed_v2 = _existing_export(
        db_session,
        experiment.id,
        schema_version="2.0.0",
    )

    assert observed_v1 is not None
    assert observed_v1.id == version_1_export.id

    assert observed_v2 is not None
    assert observed_v2.id == version_2_export.id