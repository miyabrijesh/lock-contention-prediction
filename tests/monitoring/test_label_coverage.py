from datetime import (
    UTC,
    datetime,
    timedelta,
)
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.models import (
    ExperimentStatus,
)
from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
    assign_actual_label,
)
from monitoring.label_coverage import (
    LabelCoverageError,
    build_label_coverage_report,
)
from tests.model_validation.test_prediction_label_service import (
    _create_complete_future_window,
    _create_experiment,
    _create_model_version,
    _create_prediction,
    _historical_window_start,
)


def test_complete_label_coverage_passes(
    db_session: Session,
) -> None:
    """Accept complete eligible observed labels."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    verification_time = (
        prediction.target_window_end
        + timedelta(seconds=5)
    )

    assign_actual_label(
        db_session,
        prediction.id,
        current_time=verification_time,
    )

    report = build_label_coverage_report(
        db_session,
        experiment.id,
        current_time=verification_time,
    )

    assert report.total_prediction_count == 1
    assert report.mature_prediction_count == 1
    assert (
        report.eligible_mature_prediction_count
        == 1
    )
    assert report.labeled_prediction_count == 1
    assert (
        report
        .unlabeled_eligible_mature_prediction_count
        == 0
    )
    assert report.positive_label_count == 0
    assert report.negative_label_count == 1
    assert report.provenance_complete_count == 1
    assert report.provenance_invalid_count == 0
    assert (
        report.ineligible_labeled_prediction_count
        == 0
    )
    assert report.label_contract_versions == {
        LIVE_LABEL_CONTRACT_VERSION: 1,
    }
    assert str(report.label_coverage) == "1"
    assert report.passed is True


def test_eligible_unlabeled_prediction_fails(
    db_session: Session,
) -> None:
    """Reject missing labels for eligible predictions."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    report = build_label_coverage_report(
        db_session,
        experiment.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=5)
        ),
    )

    assert (
        report.eligible_mature_prediction_count
        == 1
    )
    assert report.labeled_prediction_count == 0
    assert (
        report
        .unlabeled_eligible_mature_prediction_count
        == 1
    )
    assert str(report.label_coverage) == "0"
    assert report.passed is False


def test_final_window_without_telemetry_is_excluded(
    db_session: Session,
) -> None:
    """Exclude rows whose horizon lacks telemetry."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    first_start = _historical_window_start()
    final_start = (
        first_start
        + timedelta(seconds=40)
    )

    first_prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=first_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=first_start,
    )

    final_prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=final_start,
    )

    verification_time = (
        final_prediction.target_window_end
        + timedelta(seconds=5)
    )

    assign_actual_label(
        db_session,
        first_prediction.id,
        current_time=verification_time,
    )

    report = build_label_coverage_report(
        db_session,
        experiment.id,
        current_time=verification_time,
    )

    assert report.total_prediction_count == 2
    assert report.mature_prediction_count == 2
    assert (
        report.eligible_mature_prediction_count
        == 1
    )
    assert report.labeled_prediction_count == 1
    assert (
        report.unlabeled_mature_prediction_count
        == 1
    )
    assert (
        report
        .unlabeled_eligible_mature_prediction_count
        == 0
    )
    assert str(report.label_coverage) == "1"
    assert report.passed is True


def test_ineligible_manual_label_fails(
    db_session: Session,
) -> None:
    """Reject a label without telemetry eligibility."""

    experiment = _create_experiment(
        db_session
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
        actual_label=False,
    )

    report = build_label_coverage_report(
        db_session,
        experiment.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=5)
        ),
    )

    assert (
        report.eligible_mature_prediction_count
        == 0
    )
    assert (
        report.ineligible_labeled_prediction_count
        == 1
    )
    assert report.passed is False


def test_noncompleted_experiment_fails(
    db_session: Session,
) -> None:
    """Reject validation for a non-completed run."""

    experiment = _create_experiment(
        db_session,
        status=ExperimentStatus.DRAFT,
    )
    model_version = _create_model_version(
        db_session
    )
    window_start = _historical_window_start()

    prediction = _create_prediction(
        db_session,
        experiment=experiment,
        model_version=model_version,
        window_start=window_start,
    )

    _create_complete_future_window(
        db_session,
        experiment=experiment,
        window_start=window_start,
    )

    report = build_label_coverage_report(
        db_session,
        experiment.id,
        current_time=(
            prediction.target_window_end
            + timedelta(seconds=5)
        ),
    )

    assert report.experiment_status == "DRAFT"
    assert report.passed is False


def test_unknown_experiment_is_rejected(
    db_session: Session,
) -> None:
    """Reject an unknown experiment identifier."""

    experiment_run_id = uuid4()

    with pytest.raises(
        LabelCoverageError,
        match=str(experiment_run_id),
    ):
        build_label_coverage_report(
            db_session,
            experiment_run_id,
            current_time=datetime.now(UTC),
        )


def test_naive_verification_time_is_rejected(
    db_session: Session,
) -> None:
    """Require timezone-aware report generation."""

    with pytest.raises(
        LabelCoverageError,
        match="timezone-aware",
    ):
        build_label_coverage_report(
            db_session,
            uuid4(),
            current_time=datetime.now(),
        )