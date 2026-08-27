import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
    Prediction,
)
from backend.app.services.prediction_label_service import (
    LIVE_LABEL_CONTRACT_VERSION,
)


@dataclass(frozen=True)
class LabelCoverageReport:
    """Observed-label coverage for one experiment."""

    experiment_run_id: UUID
    experiment_status: str
    total_prediction_count: int
    mature_prediction_count: int
    eligible_mature_prediction_count: int
    labeled_prediction_count: int
    unlabeled_mature_prediction_count: int
    unlabeled_eligible_mature_prediction_count: int
    positive_label_count: int
    negative_label_count: int
    provenance_complete_count: int
    provenance_invalid_count: int
    ineligible_labeled_prediction_count: int
    label_contract_versions: dict[str, int]
    label_coverage: Decimal
    passed: bool


class LabelCoverageError(Exception):
    """Expected label-coverage verification failure."""


def _validate_current_time(
    current_time: datetime,
) -> None:
    """Require one timezone-aware verification time."""

    if (
        current_time.tzinfo is None
        or current_time.utcoffset() is None
    ):
        raise LabelCoverageError(
            "current_time must be timezone-aware."
        )


def _provenance_is_complete(
    prediction: Prediction,
) -> bool:
    """Check complete and internally valid provenance."""

    assigned_at = (
        prediction.actual_label_assigned_at
    )
    contract_version = (
        prediction.actual_label_contract_version
    )
    evidence = prediction.actual_label_evidence

    return (
        prediction.actual_label is not None
        and assigned_at is not None
        and assigned_at
        >= prediction.target_window_end
        and isinstance(contract_version, str)
        and bool(contract_version.strip())
        and isinstance(evidence, dict)
    )


def _has_any_label_provenance(
    prediction: Prediction,
) -> bool:
    """Check whether any label field is populated."""

    return (
        prediction.actual_label is not None
        or prediction.actual_label_assigned_at
        is not None
        or prediction.actual_label_contract_version
        is not None
        or prediction.actual_label_evidence
        is not None
    )


def build_label_coverage_report(
    session: Session,
    experiment_run_id: UUID,
    *,
    current_time: datetime | None = None,
) -> LabelCoverageReport:
    """Calculate observed-label coverage."""

    verification_time = (
        current_time
        if current_time is not None
        else datetime.now(UTC)
    )

    _validate_current_time(
        verification_time
    )

    try:
        experiment = session.get(
            ExperimentRun,
            experiment_run_id,
        )

        if experiment is None:
            raise LabelCoverageError(
                f"Experiment {experiment_run_id} "
                "was not found."
            )

        predictions = list(
            session.scalars(
                select(Prediction)
                .where(
                    Prediction.experiment_run_id
                    == experiment_run_id
                )
                .order_by(
                    Prediction.target_window_end.asc(),
                    Prediction.id.asc(),
                )
            ).all()
        )

        latest_complete_sampled_at = (
            session.scalar(
                select(
                    func.max(
                        MetricSample.sampled_at
                    )
                ).where(
                    MetricSample.experiment_run_id
                    == experiment_run_id,
                    MetricSample.is_complete.is_(True),
                )
            )
        )

        mature_predictions = [
            prediction
            for prediction in predictions
            if (
                prediction.target_window_end
                <= verification_time
            )
        ]

        eligible_mature_predictions = [
            prediction
            for prediction in mature_predictions
            if (
                latest_complete_sampled_at
                is not None
                and prediction.target_window_end
                <= latest_complete_sampled_at
            )
        ]

        eligible_ids = {
            prediction.id
            for prediction
            in eligible_mature_predictions
        }

        labeled_predictions = [
            prediction
            for prediction
            in eligible_mature_predictions
            if prediction.actual_label is not None
        ]

        unlabeled_mature_predictions = [
            prediction
            for prediction in mature_predictions
            if prediction.actual_label is None
        ]

        unlabeled_eligible_predictions = [
            prediction
            for prediction
            in eligible_mature_predictions
            if prediction.actual_label is None
        ]

        positive_label_count = sum(
            prediction.actual_label is True
            for prediction in labeled_predictions
        )

        negative_label_count = sum(
            prediction.actual_label is False
            for prediction in labeled_predictions
        )

        provenance_complete_count = sum(
            _provenance_is_complete(
                prediction
            )
            for prediction in labeled_predictions
        )

        provenance_invalid_count = sum(
            not _provenance_is_complete(
                prediction
            )
            for prediction in labeled_predictions
        )

        provenance_invalid_count += sum(
            _has_any_label_provenance(
                prediction
            )
            for prediction
            in unlabeled_eligible_predictions
        )

        ineligible_labeled_predictions = [
            prediction
            for prediction in predictions
            if (
                prediction.id not in eligible_ids
                and prediction.actual_label is not None
            )
        ]

        label_contract_versions: dict[
            str,
            int,
        ] = {}

        for prediction in labeled_predictions:
            contract_version = (
                prediction
                .actual_label_contract_version
            )

            if contract_version is None:
                continue

            label_contract_versions[
                contract_version
            ] = (
                label_contract_versions.get(
                    contract_version,
                    0,
                )
                + 1
            )

        eligible_count = len(
            eligible_mature_predictions
        )
        labeled_count = len(
            labeled_predictions
        )

        if eligible_count == 0:
            label_coverage = Decimal("0")
        else:
            label_coverage = (
                Decimal(labeled_count)
                / Decimal(eligible_count)
            )

        expected_contract_versions = {
            LIVE_LABEL_CONTRACT_VERSION: (
                labeled_count
            )
        }

        passed = (
            experiment.status
            == ExperimentStatus.COMPLETED
            and eligible_count > 0
            and labeled_count == eligible_count
            and not unlabeled_eligible_predictions
            and provenance_complete_count
            == labeled_count
            and provenance_invalid_count == 0
            and not ineligible_labeled_predictions
            and label_contract_versions
            == expected_contract_versions
            and label_coverage == Decimal("1")
        )

        return LabelCoverageReport(
            experiment_run_id=experiment.id,
            experiment_status=(
                experiment.status.value
            ),
            total_prediction_count=len(
                predictions
            ),
            mature_prediction_count=len(
                mature_predictions
            ),
            eligible_mature_prediction_count=(
                eligible_count
            ),
            labeled_prediction_count=(
                labeled_count
            ),
            unlabeled_mature_prediction_count=len(
                unlabeled_mature_predictions
            ),
            unlabeled_eligible_mature_prediction_count=len(
                unlabeled_eligible_predictions
            ),
            positive_label_count=(
                positive_label_count
            ),
            negative_label_count=(
                negative_label_count
            ),
            provenance_complete_count=(
                provenance_complete_count
            ),
            provenance_invalid_count=(
                provenance_invalid_count
            ),
            ineligible_labeled_prediction_count=len(
                ineligible_labeled_predictions
            ),
            label_contract_versions=dict(
                sorted(
                    label_contract_versions.items()
                )
            ),
            label_coverage=label_coverage,
            passed=passed,
        )

    except LabelCoverageError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()

        raise LabelCoverageError(
            "Label coverage could not be calculated."
        ) from error


def _print_report(
    report: LabelCoverageReport,
) -> None:
    """Print one concise label-coverage report."""

    print(
        "Experiment:",
        report.experiment_run_id,
    )
    print(
        "Status:",
        report.experiment_status,
    )
    print(
        "Total predictions:",
        report.total_prediction_count,
    )
    print(
        "Mature predictions:",
        report.mature_prediction_count,
    )
    print(
        "Eligible mature predictions:",
        report.eligible_mature_prediction_count,
    )
    print(
        "Labeled predictions:",
        report.labeled_prediction_count,
    )
    print(
        "Unlabeled mature predictions:",
        report.unlabeled_mature_prediction_count,
    )
    print(
        "Unlabeled eligible predictions:",
        report
        .unlabeled_eligible_mature_prediction_count,
    )
    print(
        "Positive labels:",
        report.positive_label_count,
    )
    print(
        "Negative labels:",
        report.negative_label_count,
    )
    print(
        "Complete provenance:",
        report.provenance_complete_count,
    )
    print(
        "Invalid provenance:",
        report.provenance_invalid_count,
    )
    print(
        "Ineligible labeled predictions:",
        report.ineligible_labeled_prediction_count,
    )
    print(
        "Contract versions:",
        report.label_contract_versions,
    )
    print(
        "Label coverage:",
        report.label_coverage,
    )
    print(
        "Passed:",
        report.passed,
    )


def parse_arguments() -> argparse.Namespace:
    """Read label-coverage arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Verify observed-label coverage for "
            "one completed experiment."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the experiment to verify.",
    )

    return parser.parse_args()


def main() -> int:
    """Run label-coverage verification."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            report = build_label_coverage_report(
                session,
                arguments.experiment_run_id,
            )

        _print_report(report)

        if not report.passed:
            print(
                "Label coverage verification failed.",
                file=sys.stderr,
            )
            return 2

        print(
            "Label coverage verification passed."
        )
        return 0

    except LabelCoverageError as error:
        print(
            f"Label coverage error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())