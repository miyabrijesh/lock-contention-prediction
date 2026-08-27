import argparse
import sys
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    distinct,
    func,
    select,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.models import (
    ExperimentRun,
    MetricSample,
    Prediction,
)


@dataclass(frozen=True)
class PredictionCoverageReport:
    """Prediction-coverage evidence for one experiment."""

    experiment_run_id: UUID
    experiment_status: str
    complete_sample_count: int
    prediction_count: int
    distinct_predicted_sample_count: int
    missing_prediction_count: int
    duplicate_prediction_count: int
    stale_prediction_count: int
    labeled_prediction_count: int
    model_version_count: int
    coverage: Decimal
    passed: bool


class PredictionCoverageError(Exception):
    """Expected prediction-coverage verification failure."""


def build_prediction_coverage_report(
    session: Session,
    experiment_run_id: UUID,
) -> PredictionCoverageReport:
    """Calculate live prediction coverage for one experiment."""

    try:
        experiment = session.get(
            ExperimentRun,
            experiment_run_id,
        )

        if experiment is None:
            raise PredictionCoverageError(
                f"Experiment {experiment_run_id} was not found."
            )

        complete_sample_count = (
            session.scalar(
                select(
                    func.count(MetricSample.id)
                ).where(
                    MetricSample.experiment_run_id
                    == experiment_run_id,
                    MetricSample.is_complete.is_(True),
                )
            )
            or 0
        )

        prediction_count = (
            session.scalar(
                select(
                    func.count(Prediction.id)
                ).where(
                    Prediction.experiment_run_id
                    == experiment_run_id
                )
            )
            or 0
        )

        distinct_predicted_sample_count = (
            session.scalar(
                select(
                    func.count(
                        distinct(
                            Prediction.metric_sample_id
                        )
                    )
                ).where(
                    Prediction.experiment_run_id
                    == experiment_run_id
                )
            )
            or 0
        )

        missing_prediction_count = (
            session.scalar(
                select(
                    func.count(MetricSample.id)
                )
                .outerjoin(
                    Prediction,
                    Prediction.metric_sample_id
                    == MetricSample.id,
                )
                .where(
                    MetricSample.experiment_run_id
                    == experiment_run_id,
                    MetricSample.is_complete.is_(True),
                    Prediction.id.is_(None),
                )
            )
            or 0
        )

        stale_prediction_count = (
            session.scalar(
                select(
                    func.count(Prediction.id)
                ).where(
                    Prediction.experiment_run_id
                    == experiment_run_id,
                    Prediction.is_stale.is_(True),
                )
            )
            or 0
        )

        labeled_prediction_count = (
            session.scalar(
                select(
                    func.count(Prediction.id)
                ).where(
                    Prediction.experiment_run_id
                    == experiment_run_id,
                    Prediction.actual_label.is_not(None),
                )
            )
            or 0
        )

        model_version_count = (
            session.scalar(
                select(
                    func.count(
                        distinct(
                            Prediction.model_version_id
                        )
                    )
                ).where(
                    Prediction.experiment_run_id
                    == experiment_run_id
                )
            )
            or 0
        )

        duplicate_prediction_count = (
            prediction_count
            - distinct_predicted_sample_count
        )

        if complete_sample_count == 0:
            coverage = Decimal("0")
        else:
            coverage = (
                Decimal(prediction_count)
                / Decimal(complete_sample_count)
            )

        passed = (
            experiment.status.value == "COMPLETED"
            and complete_sample_count > 0
            and prediction_count
            == complete_sample_count
            and distinct_predicted_sample_count
            == prediction_count
            and missing_prediction_count == 0
            and duplicate_prediction_count == 0
            and stale_prediction_count == 0
            and labeled_prediction_count == 0
            and model_version_count == 1
            and coverage == Decimal("1")
        )

        return PredictionCoverageReport(
            experiment_run_id=experiment.id,
            experiment_status=(
                experiment.status.value
            ),
            complete_sample_count=(
                complete_sample_count
            ),
            prediction_count=prediction_count,
            distinct_predicted_sample_count=(
                distinct_predicted_sample_count
            ),
            missing_prediction_count=(
                missing_prediction_count
            ),
            duplicate_prediction_count=(
                duplicate_prediction_count
            ),
            stale_prediction_count=(
                stale_prediction_count
            ),
            labeled_prediction_count=(
                labeled_prediction_count
            ),
            model_version_count=model_version_count,
            coverage=coverage,
            passed=passed,
        )

    except PredictionCoverageError:
        session.rollback()
        raise

    except SQLAlchemyError as error:
        session.rollback()

        raise PredictionCoverageError(
            "Prediction coverage could not be calculated."
        ) from error


def _print_report(
    report: PredictionCoverageReport,
) -> None:
    """Print a concise prediction-coverage report."""

    print(
        "Experiment:",
        report.experiment_run_id,
    )
    print(
        "Status:",
        report.experiment_status,
    )
    print(
        "Complete samples:",
        report.complete_sample_count,
    )
    print(
        "Predictions:",
        report.prediction_count,
    )
    print(
        "Distinct predicted samples:",
        report.distinct_predicted_sample_count,
    )
    print(
        "Missing predictions:",
        report.missing_prediction_count,
    )
    print(
        "Duplicate predictions:",
        report.duplicate_prediction_count,
    )
    print(
        "Stale predictions:",
        report.stale_prediction_count,
    )
    print(
        "Labeled predictions:",
        report.labeled_prediction_count,
    )
    print(
        "Model versions used:",
        report.model_version_count,
    )
    print(
        "Coverage:",
        report.coverage,
    )
    print(
        "Passed:",
        report.passed,
    )


def parse_arguments() -> argparse.Namespace:
    """Read the experiment identifier."""

    parser = argparse.ArgumentParser(
        description=(
            "Verify automatic prediction coverage "
            "for one experiment."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the experiment to verify.",
    )

    return parser.parse_args()


def main() -> int:
    """Run prediction-coverage verification."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            report = (
                build_prediction_coverage_report(
                    session,
                    arguments.experiment_run_id,
                )
            )

        _print_report(report)

        if not report.passed:
            print(
                "Prediction coverage verification failed.",
                file=sys.stderr,
            )
            return 2

        print(
            "Prediction coverage verification passed."
        )
        return 0

    except PredictionCoverageError as error:
        print(
            f"Prediction coverage error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())