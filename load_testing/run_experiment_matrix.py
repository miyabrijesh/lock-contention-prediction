import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.models import ExperimentRun, Seat
from backend.app.models.enums import ExperimentStatus
from data_pipeline.export_experiment import (
    ExperimentExportResult,
    export_completed_experiment,
)
from load_testing.experiment_matrix import (
    DEFAULT_MATRIX_FILE,
    ExperimentMatrix,
    ExperimentMatrixError,
    ExperimentScenario,
    load_experiment_matrix,
)
from load_testing.run_synchronized_experiment import (
    _api_request,
    run_synchronized_experiment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MANIFEST_FILE = Path(
    "data/raw/experiment_matrix/"
    "checkpoint_31_training_matrix_v1.manifest.json"
)
DEFAULT_LOCUST_OUTPUT_DIRECTORY = Path("data/raw/locust")
DEFAULT_EXPORT_DIRECTORY = Path("data/exports/experiments")


class ExperimentMatrixRunnerError(Exception):
    """Expected safe failure while executing an experiment matrix."""


def _utc_now_text() -> str:
    """Return the current UTC time in an ISO-8601 representation."""

    return datetime.now(UTC).isoformat()


def _resolve_project_path(path: Path) -> Path:
    """Resolve a relative path against the project root."""

    if path.is_absolute():
        return path

    return PROJECT_ROOT / path


def _calculate_sha256(file_path: Path) -> str:
    """Calculate a file checksum without loading the whole file."""

    digest = hashlib.sha256()

    with file_path.open("rb") as input_file:
        for block in iter(
            lambda: input_file.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert supported application values to JSON-safe values."""

    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _json_safe(item)
            for item in value
        ]

    if isinstance(value, (UUID, Path, Decimal)):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Enum):
        return value.value

    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
        return value

    raise ExperimentMatrixRunnerError(
        "Cannot serialize manifest value of type "
        f"{type(value).__name__}."
    )


def _scenario_dictionary(
    scenario: ExperimentScenario,
) -> dict[str, object]:
    """Convert one scenario to a stable manifest dictionary."""

    value = _json_safe(asdict(scenario))

    if not isinstance(value, dict):
        raise ExperimentMatrixRunnerError(
            "Scenario serialization did not produce an object."
        )

    return value


def _export_dictionary(
    result: ExperimentExportResult,
) -> dict[str, object]:
    """Convert an export result to a manifest dictionary."""

    value = _json_safe(asdict(result))

    if not isinstance(value, dict):
        raise ExperimentMatrixRunnerError(
            "Export serialization did not produce an object."
        )

    return value


def _write_manifest(
    manifest_file: Path,
    manifest: dict[str, object],
) -> None:
    """Atomically write the latest matrix execution state."""

    manifest["updated_at"] = _utc_now_text()

    manifest_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_file = manifest_file.with_suffix(
        f"{manifest_file.suffix}.tmp"
    )
    temporary_file.write_text(
        json.dumps(
            _json_safe(manifest),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(manifest_file)


def _build_new_manifest(
    *,
    matrix: ExperimentMatrix,
    matrix_file: Path,
    matrix_sha256: str,
    base_url: str,
    locust_output_directory: Path,
    export_directory: Path,
) -> dict[str, object]:
    """Create the initial resumable manifest."""

    scenarios: dict[str, object] = {}

    for scenario in matrix.scenarios:
        scenarios[scenario.scenario_id] = {
            "configuration": _scenario_dictionary(
                scenario
            ),
            "status": "PLANNED",
            "attempts": [],
        }

    created_at = _utc_now_text()

    return {
        "manifest_schema_version": "1.0.0",
        "matrix_name": matrix.matrix_name,
        "matrix_schema_version": matrix.schema_version,
        "matrix_file": str(matrix_file),
        "matrix_sha256": matrix_sha256,
        "base_url": base_url,
        "locust_output_directory": str(
            locust_output_directory
        ),
        "export_directory": str(export_directory),
        "expected_run_count": matrix.expected_run_count,
        "created_at": created_at,
        "updated_at": created_at,
        "scenarios": scenarios,
    }


def _load_existing_manifest(
    manifest_file: Path,
    *,
    matrix: ExperimentMatrix,
    matrix_sha256: str,
) -> dict[str, object]:
    """Load a compatible manifest for an explicit resume."""

    try:
        raw_value = json.loads(
            manifest_file.read_text(encoding="utf-8")
        )
    except OSError as error:
        raise ExperimentMatrixRunnerError(
            f"Could not read manifest {manifest_file}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ExperimentMatrixRunnerError(
            f"Manifest {manifest_file} is not valid JSON."
        ) from error

    if not isinstance(raw_value, dict):
        raise ExperimentMatrixRunnerError(
            "The execution manifest root must be an object."
        )

    if raw_value.get("matrix_name") != matrix.matrix_name:
        raise ExperimentMatrixRunnerError(
            "The manifest belongs to a different matrix."
        )

    if raw_value.get("matrix_sha256") != matrix_sha256:
        raise ExperimentMatrixRunnerError(
            "The matrix file changed after the manifest was created. "
            "Use a new versioned matrix and manifest."
        )

    if raw_value.get("expected_run_count") != (
        matrix.expected_run_count
    ):
        raise ExperimentMatrixRunnerError(
            "The manifest run count does not match the matrix."
        )

    scenarios = raw_value.get("scenarios")

    if not isinstance(scenarios, dict):
        raise ExperimentMatrixRunnerError(
            "The manifest does not contain a scenarios object."
        )

    expected_scenario_ids = {
        scenario.scenario_id
        for scenario in matrix.scenarios
    }

    if set(scenarios) != expected_scenario_ids:
        raise ExperimentMatrixRunnerError(
            "The manifest scenarios do not match the matrix."
        )

    return raw_value


def _get_scenario_entry(
    manifest: dict[str, object],
    scenario_id: str,
) -> dict[str, object]:
    """Return one mutable scenario manifest entry."""

    scenarios = manifest.get("scenarios")

    if not isinstance(scenarios, dict):
        raise ExperimentMatrixRunnerError(
            "The manifest scenarios value is invalid."
        )

    entry = scenarios.get(scenario_id)

    if not isinstance(entry, dict):
        raise ExperimentMatrixRunnerError(
            f"Manifest scenario {scenario_id} is invalid."
        )

    return entry


def _get_attempts(
    entry: dict[str, object],
) -> list[object]:
    """Return the mutable attempt list for one scenario."""

    attempts = entry.get("attempts")

    if not isinstance(attempts, list):
        raise ExperimentMatrixRunnerError(
            "Scenario attempts must be a JSON array."
        )

    return attempts


def _git_code_version() -> str:
    """Return the Git commit used to execute the matrix."""

    completed_process = subprocess.run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    if completed_process.returncode != 0:
        raise ExperimentMatrixRunnerError(
            "Could not determine the current Git code version."
        )

    code_version = completed_process.stdout.strip()

    if not code_version:
        raise ExperimentMatrixRunnerError(
            "Git returned an empty code version."
        )

    return code_version


def _load_hotspot_seat_ids(
    scenario: ExperimentScenario,
) -> list[int]:
    """Load deterministic hotspot seats from the configured show."""

    with SessionLocal() as session:
        seat_ids = list(
            session.scalars(
                select(Seat.id)
                .where(Seat.show_id == scenario.show_id)
                .order_by(Seat.id)
                .limit(scenario.hotspot_seat_count)
            ).all()
        )

    if len(seat_ids) != scenario.hotspot_seat_count:
        raise ExperimentMatrixRunnerError(
            f"Scenario {scenario.scenario_id} requires "
            f"{scenario.hotspot_seat_count} hotspot seats, "
            f"but only {len(seat_ids)} were found."
        )

    return seat_ids


def _find_running_experiment() -> ExperimentRun | None:
    """Return the currently running experiment, if any."""

    with SessionLocal() as session:
        return session.scalar(
            select(ExperimentRun)
            .where(
                ExperimentRun.status
                == ExperimentStatus.RUNNING
            )
            .order_by(ExperimentRun.created_at)
            .limit(1)
        )


def _read_experiment_status(
    experiment_run_id: UUID,
) -> ExperimentStatus | None:
    """Read the latest database status for a prior attempt."""

    with SessionLocal() as session:
        experiment = session.get(
            ExperimentRun,
            experiment_run_id,
        )

        if experiment is None:
            return None

        return experiment.status


def _create_experiment(
    *,
    scenario: ExperimentScenario,
    hotspot_seat_ids: list[int],
    base_url: str,
    code_version: str,
) -> UUID:
    """Create one DRAFT experiment through the admin API."""

    response = _api_request(
        base_url=base_url,
        method="POST",
        path="/api/v1/experiments",
        payload={
            "name": scenario.experiment_name,
            "show_id": scenario.show_id,
            "user_count": scenario.user_count,
            "spawn_rate_per_sec": str(
                scenario.spawn_rate_per_sec
            ),
            "duration_sec": scenario.duration_sec,
            "traffic_pattern": (
                scenario.traffic_pattern.value
            ),
            "demand_profile": (
                scenario.demand_profile.value
            ),
            "hotspot_ratio": str(
                scenario.hotspot_ratio
            ),
            "hotspot_seat_ids": hotspot_seat_ids,
            "sample_interval_sec": (
                scenario.sample_interval_sec
            ),
            "simulated_work_ms": (
                scenario.simulated_work_ms
            ),
            "booking_mode": scenario.booking_mode,
            "random_seed": scenario.random_seed,
            "code_version": code_version,
            "created_by": scenario.created_by,
        },
    )

    raw_experiment_id = response.get("id")

    if not isinstance(raw_experiment_id, str):
        raise ExperimentMatrixRunnerError(
            "Experiment API response did not contain an ID."
        )

    try:
        return UUID(raw_experiment_id)
    except ValueError as error:
        raise ExperimentMatrixRunnerError(
            "Experiment API returned an invalid UUID."
        ) from error


def _export_experiment(
    *,
    experiment_run_id: UUID,
    export_directory: Path,
    created_by: str,
) -> ExperimentExportResult:
    """Export one completed experiment using the data pipeline."""

    with SessionLocal() as session:
        return export_completed_experiment(
            session,
            experiment_run_id=experiment_run_id,
            output_directory=export_directory,
            created_by=created_by,
        )


def _latest_attempt(
    entry: dict[str, object],
) -> dict[str, object] | None:
    """Return the most recent attempt dictionary."""

    attempts = _get_attempts(entry)

    if not attempts:
        return None

    attempt = attempts[-1]

    if not isinstance(attempt, dict):
        raise ExperimentMatrixRunnerError(
            "The latest scenario attempt is invalid."
        )

    return attempt


def _reusable_experiment_id(
    entry: dict[str, object],
) -> tuple[UUID | None, ExperimentStatus | None]:
    """Find a safe DRAFT or COMPLETED attempt to resume."""

    attempt = _latest_attempt(entry)

    if attempt is None:
        return None, None

    raw_experiment_id = attempt.get("experiment_run_id")

    if not isinstance(raw_experiment_id, str):
        return None, None

    try:
        experiment_run_id = UUID(raw_experiment_id)
    except ValueError as error:
        raise ExperimentMatrixRunnerError(
            "Manifest contains an invalid experiment UUID."
        ) from error

    status = _read_experiment_status(experiment_run_id)

    if status == ExperimentStatus.RUNNING:
        raise ExperimentMatrixRunnerError(
            f"Experiment {experiment_run_id} is still RUNNING. "
            "Resolve it manually before resuming the matrix."
        )

    if status in {
        ExperimentStatus.DRAFT,
        ExperimentStatus.COMPLETED,
    }:
        return experiment_run_id, status

    return None, status


def _start_attempt(
    entry: dict[str, object],
) -> dict[str, object]:
    """Append and return a new scenario attempt."""

    attempts = _get_attempts(entry)
    attempt: dict[str, object] = {
        "attempt_number": len(attempts) + 1,
        "status": "STARTING",
        "started_at": _utc_now_text(),
        "ended_at": None,
        "experiment_run_id": None,
        "database_status": None,
        "export": None,
        "error": None,
    }
    attempts.append(attempt)
    entry["status"] = "RUNNING"

    return attempt


def _mark_failure(
    *,
    entry: dict[str, object],
    attempt: dict[str, object],
    error: BaseException,
) -> None:
    """Record one failed or interrupted attempt."""

    attempt["status"] = (
        "INTERRUPTED"
        if isinstance(error, KeyboardInterrupt)
        else "FAILED"
    )
    attempt["ended_at"] = _utc_now_text()
    attempt["error"] = f"{type(error).__name__}: {error}"
    entry["status"] = attempt["status"]


def _execute_scenario(
    *,
    scenario: ExperimentScenario,
    entry: dict[str, object],
    manifest: dict[str, object],
    manifest_file: Path,
    base_url: str,
    locust_output_directory: Path,
    export_directory: Path,
    code_version: str,
) -> None:
    """Create, execute, export, and record one scenario."""

    reusable_id, reusable_status = (
        _reusable_experiment_id(entry)
    )

    if reusable_id is None:
        running_experiment = _find_running_experiment()

        if running_experiment is not None:
            raise ExperimentMatrixRunnerError(
                "Another experiment is RUNNING: "
                f"{running_experiment.id} "
                f"({running_experiment.name})."
            )

        attempt = _start_attempt(entry)
        _write_manifest(manifest_file, manifest)

        try:
            hotspot_seat_ids = _load_hotspot_seat_ids(
                scenario
            )
            experiment_run_id = _create_experiment(
                scenario=scenario,
                hotspot_seat_ids=hotspot_seat_ids,
                base_url=base_url,
                code_version=code_version,
            )
        except BaseException as error:
            _mark_failure(
                entry=entry,
                attempt=attempt,
                error=error,
            )
            _write_manifest(manifest_file, manifest)
            raise

        attempt["experiment_run_id"] = str(
            experiment_run_id
        )
        attempt["status"] = "DRAFT_CREATED"
        attempt["database_status"] = "DRAFT"
        _write_manifest(manifest_file, manifest)
        database_status = ExperimentStatus.DRAFT

    else:
        experiment_run_id = reusable_id
        database_status = reusable_status
        attempt = _latest_attempt(entry)

        if attempt is None:
            raise ExperimentMatrixRunnerError(
                "Reusable experiment has no manifest attempt."
            )

        entry["status"] = "RUNNING"
        attempt["status"] = "RESUMING"
        attempt["error"] = None
        _write_manifest(manifest_file, manifest)

    try:
        if database_status == ExperimentStatus.DRAFT:
            attempt["status"] = "WORKLOAD_RUNNING"
            _write_manifest(manifest_file, manifest)

            run_synchronized_experiment(
                experiment_run_id=experiment_run_id,
                base_url=base_url,
                actor=scenario.created_by,
                output_directory=(
                    locust_output_directory
                ),
            )

            database_status = _read_experiment_status(
                experiment_run_id
            )

        if database_status != ExperimentStatus.COMPLETED:
            status_text = (
                database_status.value
                if database_status is not None
                else "MISSING"
            )
            raise ExperimentMatrixRunnerError(
                f"Experiment {experiment_run_id} ended with "
                f"database status {status_text}."
            )

        attempt["database_status"] = "COMPLETED"
        attempt["status"] = "EXPORTING"
        _write_manifest(manifest_file, manifest)

        export_result = _export_experiment(
            experiment_run_id=experiment_run_id,
            export_directory=export_directory,
            created_by=scenario.created_by,
        )

        attempt["export"] = _export_dictionary(
            export_result
        )
        attempt["status"] = "COMPLETED"
        attempt["ended_at"] = _utc_now_text()
        attempt["error"] = None
        entry["status"] = "COMPLETED"
        _write_manifest(manifest_file, manifest)

    except BaseException as error:
        _mark_failure(
            entry=entry,
            attempt=attempt,
            error=error,
        )
        _write_manifest(manifest_file, manifest)
        raise


def _selected_scenarios(
    *,
    matrix: ExperimentMatrix,
    manifest: dict[str, object],
    limit: int | None,
) -> list[ExperimentScenario]:
    """Select incomplete scenarios in deterministic order."""

    scenarios = [
        scenario
        for scenario in matrix.scenarios
        if _get_scenario_entry(
            manifest,
            scenario.scenario_id,
        ).get("status") != "COMPLETED"
    ]

    if limit is not None:
        return scenarios[:limit]

    return scenarios


def _count_manifest_statuses(
    manifest: dict[str, object],
) -> dict[str, int]:
    """Count scenario states for the execution summary."""

    scenarios = manifest.get("scenarios")

    if not isinstance(scenarios, dict):
        raise ExperimentMatrixRunnerError(
            "The manifest scenarios value is invalid."
        )

    counts: dict[str, int] = {}

    for entry in scenarios.values():
        if not isinstance(entry, dict):
            raise ExperimentMatrixRunnerError(
                "A manifest scenario entry is invalid."
            )

        status = entry.get("status")

        if not isinstance(status, str):
            raise ExperimentMatrixRunnerError(
                "A manifest scenario status is invalid."
            )

        counts[status] = counts.get(status, 0) + 1

    return counts


def run_matrix(
    *,
    matrix: ExperimentMatrix,
    matrix_file: Path,
    manifest_file: Path,
    base_url: str,
    locust_output_directory: Path,
    export_directory: Path,
    resume: bool,
    limit: int | None,
) -> None:
    """Execute incomplete matrix scenarios with resume protection."""

    matrix_sha256 = _calculate_sha256(matrix_file)

    if manifest_file.exists():
        if not resume:
            raise ExperimentMatrixRunnerError(
                f"Manifest {manifest_file} already exists. "
                "Use --resume to continue it."
            )

        manifest = _load_existing_manifest(
            manifest_file,
            matrix=matrix,
            matrix_sha256=matrix_sha256,
        )

    else:
        if resume:
            raise ExperimentMatrixRunnerError(
                f"Cannot resume because {manifest_file} "
                "does not exist."
            )

        manifest = _build_new_manifest(
            matrix=matrix,
            matrix_file=matrix_file,
            matrix_sha256=matrix_sha256,
            base_url=base_url,
            locust_output_directory=(
                locust_output_directory
            ),
            export_directory=export_directory,
        )
        _write_manifest(manifest_file, manifest)

    selected = _selected_scenarios(
        matrix=matrix,
        manifest=manifest,
        limit=limit,
    )

    if not selected:
        print("All matrix scenarios are already completed.")
        return

    code_version = _git_code_version()

    print("Matrix execution starting:")
    print(f"  Matrix: {matrix.matrix_name}")
    print(f"  Scenarios selected: {len(selected)}")
    print(f"  Manifest: {manifest_file}")
    print(f"  Code version: {code_version}")

    for scenario_index, scenario in enumerate(
        selected,
        start=1,
    ):
        entry = _get_scenario_entry(
            manifest,
            scenario.scenario_id,
        )

        print(
            f"[{scenario_index}/{len(selected)}] "
            f"{scenario.scenario_id}"
        )

        try:
            _execute_scenario(
                scenario=scenario,
                entry=entry,
                manifest=manifest,
                manifest_file=manifest_file,
                base_url=base_url,
                locust_output_directory=(
                    locust_output_directory
                ),
                export_directory=export_directory,
                code_version=code_version,
            )
        except KeyboardInterrupt:
            print(
                "Matrix execution interrupted. Resume with "
                "--execute --resume.",
                file=sys.stderr,
            )
            raise
        except Exception as error:
            raise ExperimentMatrixRunnerError(
                f"Scenario {scenario.scenario_id} failed: {error}"
            ) from error

        print(f"  Completed: {scenario.scenario_id}")

    counts = _count_manifest_statuses(manifest)

    print("Matrix execution batch finished:")

    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")


def parse_arguments() -> argparse.Namespace:
    """Read safe matrix-runner command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute the resumable Checkpoint 31 "
            "experiment matrix. Execution requires --execute."
        )
    )
    parser.add_argument(
        "--matrix-file",
        type=Path,
        default=DEFAULT_MATRIX_FILE,
        help=(
            "Matrix JSON file. Default: "
            "load_testing/config/experiment_matrix_v1.json"
        ),
    )
    parser.add_argument(
        "--manifest-file",
        type=Path,
        default=DEFAULT_MANIFEST_FILE,
        help=(
            "Resumable execution manifest. Default: "
            "data/raw/experiment_matrix/"
            "checkpoint_31_training_matrix_v1.manifest.json"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "Running reservation API address. Default: "
            f"{DEFAULT_BASE_URL}"
        ),
    )
    parser.add_argument(
        "--locust-output-directory",
        type=Path,
        default=DEFAULT_LOCUST_OUTPUT_DIRECTORY,
        help="Base directory for Locust CSV output.",
    )
    parser.add_argument(
        "--export-directory",
        type=Path,
        default=DEFAULT_EXPORT_DIRECTORY,
        help="Directory for labelled experiment exports.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Execute at most this many incomplete scenarios. "
            "Useful for the one-run pilot."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create and execute experiments.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an existing compatible manifest.",
    )

    return parser.parse_args()


def main() -> int:
    """Validate the matrix and optionally execute it."""

    arguments = parse_arguments()

    try:
        matrix_file = _resolve_project_path(
            arguments.matrix_file
        )
        manifest_file = _resolve_project_path(
            arguments.manifest_file
        )
        locust_output_directory = _resolve_project_path(
            arguments.locust_output_directory
        )
        export_directory = _resolve_project_path(
            arguments.export_directory
        )

        matrix = load_experiment_matrix(matrix_file)

        if arguments.limit is not None:
            if arguments.limit <= 0:
                raise ExperimentMatrixRunnerError(
                    "--limit must be greater than zero."
                )

            if arguments.limit > matrix.expected_run_count:
                raise ExperimentMatrixRunnerError(
                    "--limit must not exceed the matrix run count."
                )

        if arguments.resume and not arguments.execute:
            raise ExperimentMatrixRunnerError(
                "--resume can only be used with --execute."
            )

        print("Experiment matrix runner validation passed:")
        print(f"  Matrix: {matrix.matrix_name}")
        print(f"  Planned runs: {len(matrix.scenarios)}")
        print(f"  Matrix file: {matrix_file}")
        print(f"  Manifest file: {manifest_file}")

        if not arguments.execute:
            print(
                "Dry run only. No experiments were created. "
                "Use --execute to run the matrix."
            )
            return 0

        run_matrix(
            matrix=matrix,
            matrix_file=matrix_file,
            manifest_file=manifest_file,
            base_url=arguments.base_url.rstrip("/"),
            locust_output_directory=(
                locust_output_directory
            ),
            export_directory=export_directory,
            resume=arguments.resume,
            limit=arguments.limit,
        )

        return 0

    except KeyboardInterrupt:
        return 130
    except (
        ExperimentMatrixError,
        ExperimentMatrixRunnerError,
    ) as error:
        print(
            f"Experiment matrix runner error: {error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())