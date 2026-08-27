import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, sleep
from uuid import UUID

from backend.app.db.session import SessionLocal
from backend.app.models import ExperimentRun
from backend.app.models.enums import ExperimentStatus
from monitoring.collector import (
    MetricCollectorError,
    collect_and_persist_metric_sample,
)
from monitoring.postgres_metrics import (
    collect_postgresql_metrics,
)


@dataclass(frozen=True)
class CollectionConfiguration:
    """Runtime settings loaded from one experiment."""

    experiment_run_id: UUID
    sample_interval_sec: int
    duration_sec: int


class CollectorRunnerError(Exception):
    """Expected collector-runner configuration failure."""


def load_collection_configuration(
    experiment_run_id: UUID,
) -> CollectionConfiguration:
    """Load and validate collector runtime settings."""

    with SessionLocal() as session:
        experiment = session.get(
            ExperimentRun,
            experiment_run_id,
        )

        if experiment is None:
            raise CollectorRunnerError(
                f"Experiment {experiment_run_id} was not found."
            )

        if experiment.status != ExperimentStatus.RUNNING:
            raise CollectorRunnerError(
                "The metrics collector requires a "
                "RUNNING experiment."
            )

        return CollectionConfiguration(
            experiment_run_id=experiment.id,
            sample_interval_sec=(
                experiment.sample_interval_sec
            ),
            duration_sec=experiment.duration_sec,
        )


def _experiment_is_running(
    experiment_run_id: UUID,
) -> bool:
    """Check whether collection should continue."""

    with SessionLocal() as session:
        experiment = session.get(
            ExperimentRun,
            experiment_run_id,
        )

        return (
            experiment is not None
            and experiment.status
            == ExperimentStatus.RUNNING
        )


def _collect_deadlock_baseline() -> tuple[
    datetime,
    int,
]:
    """Collect the initial timestamp and deadlock counter."""

    with SessionLocal() as session:
        snapshot = collect_postgresql_metrics(
            session
        )

        session.rollback()

        return (
            snapshot.sampled_at,
            snapshot.cumulative_deadlocks,
        )


def run_collector(
    configuration: CollectionConfiguration,
) -> int:
    """Collect samples at the configured interval."""

    (
        window_started_at,
        previous_cumulative_deadlocks,
    ) = _collect_deadlock_baseline()

    collection_started_at = monotonic()

    collection_deadline = (
        collection_started_at
        + configuration.duration_sec
    )

    next_sample_at = (
        collection_started_at
        + configuration.sample_interval_sec
    )

    collected_sample_count = 0

    print("Metrics collector started:")

    print(
        "  Experiment: "
        f"{configuration.experiment_run_id}"
    )

    print(
        "  Sample interval: "
        f"{configuration.sample_interval_sec} seconds"
    )

    print(
        "  Maximum duration: "
        f"{configuration.duration_sec} seconds"
    )

    while next_sample_at <= collection_deadline:
        remaining_wait = (
            next_sample_at - monotonic()
        )

        if remaining_wait > 0:
            sleep(remaining_wait)

        if not _experiment_is_running(
            configuration.experiment_run_id
        ):
            print(
                "Experiment is no longer RUNNING. "
                "Stopping collection."
            )
            break

        with SessionLocal() as session:
            result = collect_and_persist_metric_sample(
                session,
                experiment_run_id=(
                    configuration.experiment_run_id
                ),
                window_started_at=window_started_at,
                previous_cumulative_deadlocks=(
                    previous_cumulative_deadlocks
                ),
            )

        collected_sample_count += 1

        window_started_at = result.sample.sampled_at

        previous_cumulative_deadlocks = (
            result.cumulative_deadlocks
        )

        print(
            "Collected sample "
            f"{collected_sample_count}: "
            "requests_per_sec="
            f"{result.sample.requests_per_sec}, "
            "blocked_sessions="
            f"{result.sample.blocked_sessions}, "
            "waiting_locks="
            f"{result.sample.waiting_locks}, "
            "p95_latency_ms="
            f"{result.sample.p95_latency_ms}, "
            "prediction_probability="
            f"{result.prediction.probability}, "
            "risk_level="
            f"{result.prediction.risk_level.value}, "
            "threshold="
            f"{result.prediction.threshold_used}, "
            "stale="
            f"{result.prediction.is_stale}"
        )

        next_sample_at += (
            configuration.sample_interval_sec
        )

    print(
        "Metrics collector finished. "
        f"Samples collected: {collected_sample_count}"
    )

    return collected_sample_count


def parse_arguments() -> argparse.Namespace:
    """Read collector command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Collect PostgreSQL and booking metrics "
            "for one RUNNING experiment."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the RUNNING experiment.",
    )

    return parser.parse_args()


def main() -> int:
    """Run the timed metrics collector."""

    arguments = parse_arguments()

    try:
        configuration = load_collection_configuration(
            arguments.experiment_run_id
        )

        sample_count = run_collector(
            configuration
        )

        if sample_count == 0:
            print(
                "No metric samples were collected.",
                file=sys.stderr,
            )
            return 2

        return 0

    except (
        CollectorRunnerError,
        MetricCollectorError,
    ) as error:
        print(
            f"Metrics collector error: {error}",
            file=sys.stderr,
        )
        return 2

    except KeyboardInterrupt:
        print(
            "\nMetrics collection interrupted.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())