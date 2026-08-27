import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.db.session import SessionLocal
from backend.app.repositories.prediction_repository import (
    get_eligible_unlabeled_prediction_ids,
)
from backend.app.services.prediction_label_service import (
    PredictionLabelServiceError,
    PredictionLabelStatus,
    assign_actual_label,
)


DEFAULT_BATCH_LIMIT = 100
MAXIMUM_BATCH_LIMIT = 1000


@dataclass(frozen=True)
class PredictionLabelBatchReport:
    """Outcome counts from one controlled label batch."""

    experiment_run_id: UUID | None
    current_time: datetime
    requested_limit: int
    candidate_count: int
    attempted_count: int
    completed_attempt_count: int
    error_count: int
    status_counts: dict[str, int]
    error_prediction_ids: tuple[UUID, ...]
    passed: bool


class PredictionLabelBatchError(Exception):
    """Expected batch-labeling configuration failure."""


def _validate_current_time(
    current_time: datetime,
) -> None:
    """Require one timezone-aware batch timestamp."""

    if (
        current_time.tzinfo is None
        or current_time.utcoffset() is None
    ):
        raise PredictionLabelBatchError(
            "current_time must be timezone-aware."
        )


def _validate_limit(
    limit: int,
) -> None:
    """Require a controlled positive batch limit."""

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAXIMUM_BATCH_LIMIT
    ):
        raise PredictionLabelBatchError(
            "The batch limit must be between "
            f"1 and {MAXIMUM_BATCH_LIMIT}."
        )


def run_prediction_label_batch(
    session: Session,
    *,
    experiment_run_id: UUID | None = None,
    current_time: datetime | None = None,
    limit: int = DEFAULT_BATCH_LIMIT,
) -> PredictionLabelBatchReport:
    """Label one deterministic batch of predictions."""

    batch_time = (
        current_time
        if current_time is not None
        else datetime.now(UTC)
    )

    _validate_current_time(batch_time)
    _validate_limit(limit)

    status_counts = {
        status.value: 0
        for status in PredictionLabelStatus
    }

    error_prediction_ids: list[UUID] = []

    try:
        candidate_ids = (
            get_eligible_unlabeled_prediction_ids(
                session,
                current_time=batch_time,
                experiment_run_id=(
                    experiment_run_id
                ),
                limit=limit,
            )
        )

        # End the candidate-selection transaction before
        # each prediction receives its own row lock.
        session.commit()

    except SQLAlchemyError as error:
        session.rollback()

        raise PredictionLabelBatchError(
            "Prediction-label candidates could not "
            "be selected."
        ) from error

    for prediction_id in candidate_ids:
        try:
            result = assign_actual_label(
                session,
                prediction_id,
                current_time=batch_time,
            )

            status_counts[
                result.status.value
            ] += 1

        except PredictionLabelServiceError:
            session.rollback()
            error_prediction_ids.append(
                prediction_id
            )

    attempted_count = len(candidate_ids)
    error_count = len(error_prediction_ids)

    return PredictionLabelBatchReport(
        experiment_run_id=experiment_run_id,
        current_time=batch_time,
        requested_limit=limit,
        candidate_count=len(candidate_ids),
        attempted_count=attempted_count,
        completed_attempt_count=(
            attempted_count - error_count
        ),
        error_count=error_count,
        status_counts=status_counts,
        error_prediction_ids=tuple(
            error_prediction_ids
        ),
        passed=(error_count == 0),
    )


def _print_report(
    report: PredictionLabelBatchReport,
) -> None:
    """Print one concise batch-labeling report."""

    print(
        "Experiment:",
        (
            report.experiment_run_id
            if report.experiment_run_id is not None
            else "ALL_COMPLETED_EXPERIMENTS"
        ),
    )
    print(
        "Batch time:",
        report.current_time.isoformat(),
    )
    print(
        "Requested limit:",
        report.requested_limit,
    )
    print(
        "Candidates:",
        report.candidate_count,
    )
    print(
        "Attempts:",
        report.attempted_count,
    )
    print(
        "Completed attempts:",
        report.completed_attempt_count,
    )
    print(
        "Errors:",
        report.error_count,
    )

    print("Result states:")

    for status in PredictionLabelStatus:
        print(
            f"  {status.value}: "
            f"{report.status_counts[status.value]}"
        )

    if report.error_prediction_ids:
        print("Error prediction IDs:")

        for prediction_id in (
            report.error_prediction_ids
        ):
            print(
                f"  {prediction_id}"
            )

    print(
        "Passed:",
        report.passed,
    )


def parse_arguments() -> argparse.Namespace:
    """Read batch-labeling arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Assign observed labels to mature "
            "predictions with complete future coverage."
        )
    )

    parser.add_argument(
        "--experiment-run-id",
        type=UUID,
        default=None,
        help=(
            "Optionally restrict labeling to one "
            "completed experiment."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
        help=(
            "Maximum number of predictions processed "
            f"per batch. Maximum: {MAXIMUM_BATCH_LIMIT}."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run one controlled label batch."""

    arguments = parse_arguments()

    try:
        with SessionLocal() as session:
            report = run_prediction_label_batch(
                session,
                experiment_run_id=(
                    arguments.experiment_run_id
                ),
                limit=arguments.limit,
            )

        _print_report(report)

        if not report.passed:
            print(
                "Prediction labeling batch failed.",
                file=sys.stderr,
            )
            return 2

        print(
            "Prediction labeling batch completed."
        )
        return 0

    except PredictionLabelBatchError as error:
        print(
            f"Prediction labeling error: {error}",
            file=sys.stderr,
        )
        return 2

    except KeyboardInterrupt:
        print(
            "\nPrediction labeling interrupted.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())