from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

import monitoring.prediction_labeling as labeling_module
from backend.app.models import (
    ExperimentStatus,
)
from backend.app.repositories.prediction_repository import (
    get_eligible_unlabeled_prediction_ids,
)
from backend.app.services.prediction_label_service import (
    PredictionLabelResult,
    PredictionLabelServiceError,
    PredictionLabelStatus,
)
from monitoring.prediction_labeling import (
    MAXIMUM_BATCH_LIMIT,
    PredictionLabelBatchError,
    run_prediction_label_batch,
)
from tests.model_validation.test_prediction_label_service import (
    _create_complete_future_window,
    _create_experiment,
    _create_metric_sample,
    _create_model_version,
    _create_prediction,
    _historical_window_start,
)


def test_candidate_query_enforces_eligibility(
    db_session: Session,
) -> None:
    """Select only completed, mature, unlabeled rows."""

    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    eligible_experiment = _create_experiment(
        db_session
    )
    eligible_prediction = _create_prediction(
        db_session,
        experiment=eligible_experiment,
        model_version=model_version,
        window_start=window_start,
    )
    _create_complete_future_window(
        db_session,
        experiment=eligible_experiment,
        window_start=window_start,
    )

    no_coverage_experiment = _create_experiment(
        db_session
    )
    _create_prediction(
        db_session,
        experiment=no_coverage_experiment,
        model_version=model_version,
        window_start=window_start,
    )

    draft_experiment = _create_experiment(
        db_session,
        status=ExperimentStatus.DRAFT,
    )
    _create_prediction(
        db_session,
        experiment=draft_experiment,
        model_version=model_version,
        window_start=window_start,
    )
    _create_complete_future_window(
        db_session,
        experiment=draft_experiment,
        window_start=window_start,
    )

    labeled_experiment = _create_experiment(
        db_session
    )
    _create_prediction(
        db_session,
        experiment=labeled_experiment,
        model_version=model_version,
        window_start=window_start,
        actual_label=False,
    )
    _create_complete_future_window(
        db_session,
        experiment=labeled_experiment,
        window_start=window_start,
    )

    candidate_ids = (
        get_eligible_unlabeled_prediction_ids(
            db_session,
            current_time=(
                window_start
                + timedelta(minutes=2)
            ),
            limit=100,
        )
    )

    assert candidate_ids == [
        eligible_prediction.id
    ]


def test_candidate_query_applies_filter_and_limit(
    db_session: Session,
) -> None:
    """Apply deterministic ordering, filtering, and limit."""

    model_version = _create_model_version(
        db_session
    )
    first_start = _historical_window_start()
    second_start = (
        first_start
        + timedelta(seconds=40)
    )

    first_experiment = _create_experiment(
        db_session
    )
    first_prediction = _create_prediction(
        db_session,
        experiment=first_experiment,
        model_version=model_version,
        window_start=first_start,
    )
    _create_complete_future_window(
        db_session,
        experiment=first_experiment,
        window_start=first_start,
    )

    second_experiment = _create_experiment(
        db_session
    )
    second_prediction = _create_prediction(
        db_session,
        experiment=second_experiment,
        model_version=model_version,
        window_start=second_start,
    )
    _create_complete_future_window(
        db_session,
        experiment=second_experiment,
        window_start=second_start,
    )

    limited_ids = (
        get_eligible_unlabeled_prediction_ids(
            db_session,
            current_time=(
                second_start
                + timedelta(minutes=2)
            ),
            limit=1,
        )
    )

    assert limited_ids == [
        first_prediction.id
    ]

    filtered_ids = (
        get_eligible_unlabeled_prediction_ids(
            db_session,
            current_time=(
                second_start
                + timedelta(minutes=2)
            ),
            experiment_run_id=(
                second_experiment.id
            ),
            limit=100,
        )
    )

    assert filtered_ids == [
        second_prediction.id
    ]


def test_batch_aggregates_label_results(
    db_session: Session,
) -> None:
    """Count successful and insufficient outcomes."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    first_start = _historical_window_start()
    second_start = (
        first_start
        + timedelta(seconds=40)
    )

    first_prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=first_start,
    )

    first_future_samples = (
        _create_complete_future_window(
            db_session,
            experiment=experiment,
            window_start=first_start,
        )
    )

    first_future_samples[4].blocked_sessions = 2
    db_session.commit()

    second_prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=second_start,
    )

    for index in range(1, 15):
        _create_metric_sample(
            db_session,
            experiment=experiment,
            sampled_at=(
                second_start
                + timedelta(seconds=index * 2)
            ),
        )

    # This makes the second prediction eligible for
    # inspection while its internal window remains sparse.
    _create_metric_sample(
        db_session,
        experiment=experiment,
        sampled_at=(
            second_start
            + timedelta(seconds=32)
        ),
    )

    batch_time = (
        second_prediction.target_window_end
        + timedelta(seconds=5)
    )

    report = run_prediction_label_batch(
        db_session,
        experiment_run_id=experiment.id,
        current_time=batch_time,
        limit=100,
    )

    assert report.candidate_count == 2
    assert report.attempted_count == 2
    assert report.completed_attempt_count == 2
    assert report.error_count == 0
    assert report.passed is True
    assert (
        report.status_counts[
            PredictionLabelStatus
            .LABELED_POSITIVE
            .value
        ]
        == 1
    )
    assert (
        report.status_counts[
            PredictionLabelStatus
            .INSUFFICIENT_FUTURE_COVERAGE
            .value
        ]
        == 1
    )

    db_session.refresh(first_prediction)
    db_session.refresh(second_prediction)

    assert first_prediction.actual_label is True
    assert second_prediction.actual_label is None

    retry_report = run_prediction_label_batch(
        db_session,
        experiment_run_id=experiment.id,
        current_time=batch_time,
        limit=100,
    )

    assert retry_report.candidate_count == 1
    assert (
        retry_report.status_counts[
            PredictionLabelStatus
            .INSUFFICIENT_FUTURE_COVERAGE
            .value
        ]
        == 1
    )


def test_batch_continues_after_service_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Record one error and continue to the next candidate."""

    failed_prediction_id = uuid4()
    successful_prediction_id = uuid4()

    def candidate_query(
        session: Session,
        *,
        current_time: datetime,
        experiment_run_id: UUID | None,
        limit: int,
    ) -> list[UUID]:
        assert session is db_session
        assert current_time.tzinfo is not None
        assert experiment_run_id is None
        assert limit == 10

        return [
            failed_prediction_id,
            successful_prediction_id,
        ]

    def assign_label(
        session: Session,
        prediction_id: UUID,
        *,
        current_time: datetime,
    ) -> PredictionLabelResult:
        assert session is db_session
        assert current_time.tzinfo is not None

        if prediction_id == failed_prediction_id:
            raise PredictionLabelServiceError(
                "Expected test error."
            )

        return PredictionLabelResult(
            prediction_id=prediction_id,
            status=(
                PredictionLabelStatus
                .LABELED_NEGATIVE
            ),
            actual_label=False,
            future_sample_count=15,
            expected_future_sample_count=15,
        )

    monkeypatch.setattr(
        labeling_module,
        "get_eligible_unlabeled_prediction_ids",
        candidate_query,
    )
    monkeypatch.setattr(
        labeling_module,
        "assign_actual_label",
        assign_label,
    )

    report = run_prediction_label_batch(
        db_session,
        current_time=datetime.now(UTC),
        limit=10,
    )

    assert report.candidate_count == 2
    assert report.attempted_count == 2
    assert report.completed_attempt_count == 1
    assert report.error_count == 1
    assert report.error_prediction_ids == (
        failed_prediction_id,
    )
    assert report.passed is False
    assert (
        report.status_counts[
            PredictionLabelStatus
            .LABELED_NEGATIVE
            .value
        ]
        == 1
    )


def test_empty_batch_passes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat an empty candidate batch as successful."""

    monkeypatch.setattr(
        labeling_module,
        "get_eligible_unlabeled_prediction_ids",
        lambda *args, **kwargs: [],
    )

    report = run_prediction_label_batch(
        db_session,
        current_time=datetime.now(UTC),
    )

    assert report.candidate_count == 0
    assert report.attempted_count == 0
    assert report.error_count == 0
    assert report.passed is True


@pytest.mark.parametrize(
    "invalid_limit",
    [
        0,
        -1,
        MAXIMUM_BATCH_LIMIT + 1,
        True,
    ],
)
def test_invalid_batch_limit_is_rejected(
    db_session: Session,
    invalid_limit: int,
) -> None:
    """Reject unsafe batch limits."""

    with pytest.raises(
        PredictionLabelBatchError,
        match="batch limit",
    ):
        run_prediction_label_batch(
            db_session,
            current_time=datetime.now(UTC),
            limit=invalid_limit,
        )


def test_naive_batch_time_is_rejected(
    db_session: Session,
) -> None:
    """Require a timezone-aware batch timestamp."""

    with pytest.raises(
        PredictionLabelBatchError,
        match="timezone-aware",
    ):
        run_prediction_label_batch(
            db_session,
            current_time=datetime.now(),
        )