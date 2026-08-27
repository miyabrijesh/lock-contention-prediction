from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Final
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
)
from backend.app.repositories.prediction_repository import (
    get_future_metric_samples,
    get_latest_complete_sampled_at,
    get_prediction_for_labeling,
)


LIVE_LABEL_CONTRACT_VERSION: Final[str] = (
    "live-label-v1"
)

PREDICTION_HORIZON_SECONDS: Final[int] = 30

MAXIMUM_SAMPLE_GAP_MULTIPLIER: Final[
    Decimal
] = Decimal("1.75")


class PredictionLabelStatus(str, Enum):
    """Possible results from live label assignment."""

    LABELED_POSITIVE = "LABELED_POSITIVE"
    LABELED_NEGATIVE = "LABELED_NEGATIVE"
    ALREADY_LABELED = "ALREADY_LABELED"
    WINDOW_NOT_MATURE = "WINDOW_NOT_MATURE"
    EXPERIMENT_NOT_COMPLETED = (
        "EXPERIMENT_NOT_COMPLETED"
    )
    INSUFFICIENT_FUTURE_COVERAGE = (
        "INSUFFICIENT_FUTURE_COVERAGE"
    )
    INVALID_FUTURE_EVIDENCE = (
        "INVALID_FUTURE_EVIDENCE"
    )
    PREDICTION_NOT_FOUND = "PREDICTION_NOT_FOUND"


@dataclass(frozen=True)
class PredictionLabelResult:
    """Structured result from one labeling attempt."""

    prediction_id: UUID
    status: PredictionLabelStatus
    actual_label: bool | None
    future_sample_count: int
    expected_future_sample_count: int


class PredictionLabelServiceError(Exception):
    """Unexpected failure during label assignment."""


class _InvalidFutureEvidence(ValueError):
    """Internal invalid-evidence signal."""


def _validated_count(
    value: int | None,
    *,
    field_name: str,
) -> int:
    """Return one valid nonnegative count."""

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise _InvalidFutureEvidence(
            f"{field_name} is invalid."
        )

    return value


def _validated_decimal(
    value: Decimal | None,
    *,
    field_name: str,
    maximum: Decimal | None = None,
) -> Decimal:
    """Return one valid nonnegative decimal value."""

    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
    ):
        raise _InvalidFutureEvidence(
            f"{field_name} is invalid."
        )

    if (
        maximum is not None
        and value > maximum
    ):
        raise _InvalidFutureEvidence(
            f"{field_name} is out of range."
        )

    return value


def _validate_future_samples(
    samples: list[MetricSample],
) -> None:
    """Validate every future evidence sample."""

    for sample in samples:
        if not sample.is_complete:
            raise _InvalidFutureEvidence(
                "A future metric sample is incomplete."
            )

        _validated_count(
            sample.blocked_sessions,
            field_name="blocked_sessions",
        )
        _validated_count(
            sample.waiting_locks,
            field_name="waiting_locks",
        )
        _validated_count(
            sample.deadlock_delta,
            field_name="deadlock_delta",
        )
        _validated_decimal(
            sample.timeout_rate,
            field_name="timeout_rate",
            maximum=Decimal("1"),
        )
        _validated_decimal(
            sample.p95_latency_ms,
            field_name="p95_latency_ms",
        )


def _maximum_sample_gap_ms(
    samples: list[MetricSample],
) -> int:
    """Return the largest adjacent sample gap."""

    gaps = [
        round(
            (
                current.sampled_at
                - previous.sampled_at
            ).total_seconds()
            * 1000
        )
        for previous, current in zip(
            samples,
            samples[1:],
        )
    ]

    if not gaps:
        return 0

    return max(gaps)



def _expected_future_sample_count(
    sample_interval_sec: int,
) -> int:
    """Calculate the expected count for 30 seconds."""

    if sample_interval_sec <= 0:
        raise _InvalidFutureEvidence(
            "The sample interval must be positive."
        )

    expected_count = (
        PREDICTION_HORIZON_SECONDS
        // sample_interval_sec
    )

    if expected_count <= 0:
        raise _InvalidFutureEvidence(
            "The sample interval exceeds the horizon."
        )

    return expected_count


def _maximum_allowed_gap_ms(
    sample_interval_sec: int,
) -> int:
    """Calculate the frozen timestamp-gap tolerance."""

    interval_ms = Decimal(
        sample_interval_sec * 1000
    )

    return int(
        interval_ms
        * MAXIMUM_SAMPLE_GAP_MULTIPLIER
    )


def _finish_without_label(
    session: Session,
    *,
    prediction_id: UUID,
    status: PredictionLabelStatus,
    actual_label: bool | None,
    future_sample_count: int,
    expected_future_sample_count: int,
) -> PredictionLabelResult:
    """Release the transaction without assigning a label."""

    session.commit()

    return PredictionLabelResult(
        prediction_id=prediction_id,
        status=status,
        actual_label=actual_label,
        future_sample_count=future_sample_count,
        expected_future_sample_count=(
            expected_future_sample_count
        ),
    )


def _calculate_evidence(
    samples: list[MetricSample],
) -> tuple[
    int,
    int,
    int,
    Decimal,
    Decimal,
]:
    """Calculate the frozen future-window evidence."""

    maximum_blocked_sessions = max(
        _validated_count(
            sample.blocked_sessions,
            field_name="blocked_sessions",
        )
        for sample in samples
    )

    maximum_waiting_locks = max(
        _validated_count(
            sample.waiting_locks,
            field_name="waiting_locks",
        )
        for sample in samples
    )

    deadlock_delta_sum = sum(
        _validated_count(
            sample.deadlock_delta,
            field_name="deadlock_delta",
        )
        for sample in samples
    )

    maximum_timeout_rate = max(
        _validated_decimal(
            sample.timeout_rate,
            field_name="timeout_rate",
            maximum=Decimal("1"),
        )
        for sample in samples
    )

    maximum_p95_latency_ms = max(
        _validated_decimal(
            sample.p95_latency_ms,
            field_name="p95_latency_ms",
        )
        for sample in samples
    )

    return (
        maximum_blocked_sessions,
        maximum_waiting_locks,
        deadlock_delta_sum,
        maximum_timeout_rate,
        maximum_p95_latency_ms,
    )


def assign_actual_label(
    session: Session,
    prediction_id: UUID,
    *,
    current_time: datetime | None = None,
) -> PredictionLabelResult:
    """Assign an observed label to one mature prediction."""

    assignment_time = (
        current_time
        if current_time is not None
        else datetime.now(UTC)
    )

    if (
        assignment_time.tzinfo is None
        or assignment_time.utcoffset() is None
    ):
        raise ValueError(
            "current_time must be timezone-aware."
        )

    try:
        prediction = get_prediction_for_labeling(
            session,
            prediction_id,
        )

        if prediction is None:
            return _finish_without_label(
                session,
                prediction_id=prediction_id,
                status=(
                    PredictionLabelStatus
                    .PREDICTION_NOT_FOUND
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=0,
            )

        if prediction.actual_label is not None:
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .ALREADY_LABELED
                ),
                actual_label=(
                    prediction.actual_label
                ),
                future_sample_count=0,
                expected_future_sample_count=0,
            )

        experiment = session.get(
            ExperimentRun,
            prediction.experiment_run_id,
        )

        if experiment is None:
            raise PredictionLabelServiceError(
                "The prediction references a missing "
                "experiment."
            )

        try:
            expected_sample_count = (
                _expected_future_sample_count(
                    experiment.sample_interval_sec
                )
            )
        except _InvalidFutureEvidence:
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INVALID_FUTURE_EVIDENCE
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=0,
            )

        if (
            experiment.status
            != ExperimentStatus.COMPLETED
        ):
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .EXPERIMENT_NOT_COMPLETED
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        if (
            assignment_time
            < prediction.target_window_end
        ):
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .WINDOW_NOT_MATURE
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        expected_window_end = (
            prediction.target_window_start
            + timedelta(
                seconds=PREDICTION_HORIZON_SECONDS
            )
        )

        if (
            prediction.target_window_end
            != expected_window_end
        ):
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INVALID_FUTURE_EVIDENCE
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        future_samples = get_future_metric_samples(
            session,
            experiment_run_id=(
                prediction.experiment_run_id
            ),
            target_window_start=(
                prediction.target_window_start
            ),
            target_window_end=(
                prediction.target_window_end
            ),
        )

        future_sample_count = len(
            future_samples
        )

        if not future_samples:
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INSUFFICIENT_FUTURE_COVERAGE
                ),
                actual_label=None,
                future_sample_count=0,
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        try:
            _validate_future_samples(
                future_samples
            )
        except _InvalidFutureEvidence:
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INVALID_FUTURE_EVIDENCE
                ),
                actual_label=None,
                future_sample_count=(
                    future_sample_count
                ),
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        latest_complete_sampled_at = (
            get_latest_complete_sampled_at(
                session,
                prediction.experiment_run_id,
            )
        )

        if (
            latest_complete_sampled_at is None
            or latest_complete_sampled_at
            < prediction.target_window_end
        ):
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INSUFFICIENT_FUTURE_COVERAGE
                ),
                actual_label=None,
                future_sample_count=(
                    future_sample_count
                ),
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        if (
            future_sample_count
            != expected_sample_count
        ):
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INSUFFICIENT_FUTURE_COVERAGE
                ),
                actual_label=None,
                future_sample_count=(
                    future_sample_count
                ),
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        maximum_sample_gap_ms = (
            _maximum_sample_gap_ms(
                future_samples
            )
        )

        allowed_gap_ms = (
            _maximum_allowed_gap_ms(
                experiment.sample_interval_sec
            )
        )

        if maximum_sample_gap_ms > allowed_gap_ms:
            return _finish_without_label(
                session,
                prediction_id=prediction.id,
                status=(
                    PredictionLabelStatus
                    .INSUFFICIENT_FUTURE_COVERAGE
                ),
                actual_label=None,
                future_sample_count=(
                    future_sample_count
                ),
                expected_future_sample_count=(
                    expected_sample_count
                ),
            )

        (
            maximum_blocked_sessions,
            maximum_waiting_locks,
            deadlock_delta_sum,
            maximum_timeout_rate,
            maximum_p95_latency_ms,
        ) = _calculate_evidence(
            future_samples
        )

        actual_label = (
            maximum_blocked_sessions >= 2
            or maximum_waiting_locks >= 2
            or deadlock_delta_sum >= 1
            or maximum_timeout_rate > 0
        )

        evidence: dict[str, object] = {
            "future_sample_count": (
                future_sample_count
            ),
            "expected_future_sample_count": (
                expected_sample_count
            ),
            "future_max_blocked_sessions_30s": (
                maximum_blocked_sessions
            ),
            "future_max_waiting_locks_30s": (
                maximum_waiting_locks
            ),
            "future_deadlock_delta_sum_30s": (
                deadlock_delta_sum
            ),
            "future_max_timeout_rate_30s": str(
                maximum_timeout_rate
            ),
            "future_max_p95_latency_ms_30s": str(
                maximum_p95_latency_ms
            ),
            "first_future_sampled_at": (
                future_samples[0]
                .sampled_at
                .isoformat()
            ),
            "last_future_sampled_at": (
                future_samples[-1]
                .sampled_at
                .isoformat()
            ),
            "maximum_sample_gap_ms": (
                maximum_sample_gap_ms
            ),
        }

        prediction.actual_label = actual_label
        prediction.actual_label_assigned_at = (
            assignment_time
        )
        prediction.actual_label_contract_version = (
            LIVE_LABEL_CONTRACT_VERSION
        )
        prediction.actual_label_evidence = evidence

        session.flush()
        session.commit()

        status = (
            PredictionLabelStatus.LABELED_POSITIVE
            if actual_label
            else PredictionLabelStatus.LABELED_NEGATIVE
        )

        return PredictionLabelResult(
            prediction_id=prediction.id,
            status=status,
            actual_label=actual_label,
            future_sample_count=(
                future_sample_count
            ),
            expected_future_sample_count=(
                expected_sample_count
            ),
        )

    except PredictionLabelServiceError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()

        raise PredictionLabelServiceError(
            "Prediction label assignment failed."
        ) from error