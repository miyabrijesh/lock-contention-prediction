import argparse
import json
import subprocess
import sys
from pathlib import Path
from time import sleep
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from backend.app.core.config import settings


class SynchronizedRunnerError(Exception):
    """Expected synchronized-runner failure."""


def _decode_json_response(
    response_body: bytes,
) -> dict[str, object]:
    """Decode one API JSON response."""

    try:
        decoded_response = json.loads(
            response_body.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise SynchronizedRunnerError(
            "The API returned an invalid JSON response."
        ) from error

    if not isinstance(decoded_response, dict):
        raise SynchronizedRunnerError(
            "The API returned an unexpected response structure."
        )

    return cast(
        dict[str, object],
        decoded_response,
    )


def _extract_api_error(
    response_body: bytes,
) -> str:
    """Extract a useful message from an API error response."""

    try:
        decoded_response = json.loads(
            response_body.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return response_body.decode(
            "utf-8",
            errors="replace",
        )

    if not isinstance(decoded_response, dict):
        return str(decoded_response)

    detail = decoded_response.get("detail")

    if isinstance(detail, dict):
        code = detail.get("code")
        message = detail.get("message")

        if code is not None and message is not None:
            return f"{code}: {message}"

    return str(detail or decoded_response)


def _api_request(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Send one authenticated experiment API request."""

    request_headers = {
        "Accept": "application/json",
        "X-Admin-Token": settings.admin_api_token,
    }

    request_data: bytes | None = None

    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        request_data = json.dumps(payload).encode("utf-8")

    request = Request(
        url=(
            f"{base_url.rstrip('/')}"
            f"{path}"
        ),
        data=request_data,
        headers=request_headers,
        method=method,
    )

    try:
        with urlopen(
            request,
            timeout=10,
        ) as response:
            return _decode_json_response(
                response.read()
            )
    except HTTPError as error:
        error_body = error.read()

        raise SynchronizedRunnerError(
            f"API request {method} {path} failed "
            f"with HTTP {error.code}: "
            f"{_extract_api_error(error_body)}"
        ) from error
    except URLError as error:
        raise SynchronizedRunnerError(
            f"Unable to reach the API at "
            f"{base_url}: {error.reason}"
        ) from error


def _read_experiment(
    *,
    base_url: str,
    experiment_run_id: UUID,
) -> dict[str, object]:
    """Read one experiment from the API."""

    return _api_request(
        base_url=base_url,
        method="GET",
        path=(
            "/api/v1/experiments/"
            f"{experiment_run_id}"
        ),
    )


def _transition_experiment(
    *,
    base_url: str,
    experiment_run_id: UUID,
    action: str,
    actor: str,
    reason: str | None = None,
) -> dict[str, object]:
    """Apply one experiment lifecycle transition."""

    transition_payload: dict[str, object] = {
        "actor": actor,
    }

    if reason is not None:
        transition_payload["reason"] = reason

    return _api_request(
        base_url=base_url,
        method="POST",
        path=(
            "/api/v1/experiments/"
            f"{experiment_run_id}/"
            f"{action}"
        ),
        payload=transition_payload,
    )


def _validate_preconditions(
    *,
    base_url: str,
    experiment_run_id: UUID,
) -> dict[str, object]:
    """Validate configuration before starting any process."""

    if not settings.experiment_mode_enabled:
        raise SynchronizedRunnerError(
            "Experiment mode is disabled. Set "
            "EXPERIMENT_MODE_ENABLED=true and restart "
            "the API before running an experiment."
        )

    if not settings.admin_api_token.strip():
        raise SynchronizedRunnerError(
            "The admin API token is empty."
        )

    _api_request(
        base_url=base_url,
        method="GET",
        path="/health/ready",
    )

    experiment = _read_experiment(
        base_url=base_url,
        experiment_run_id=experiment_run_id,
    )

    experiment_status = experiment.get("status")

    if experiment_status != "DRAFT":
        raise SynchronizedRunnerError(
            "The synchronized runner requires a DRAFT "
            f"experiment, but its current status is "
            f"{experiment_status!r}."
        )

    duration_value = experiment.get("duration_sec")

    if not isinstance(duration_value, int):
        raise SynchronizedRunnerError(
            "The experiment response does not contain "
            "a valid duration."
        )

    if duration_value < 60:
        print(
            "Warning: experiments shorter than 60 seconds "
            "produce very few complete 30-second label "
            "windows.",
            file=sys.stderr,
        )

    return experiment


def _terminate_process(
    process: subprocess.Popen[bytes] | None,
    *,
    process_name: str,
) -> None:
    """Terminate one child process if it is still running."""

    if process is None or process.poll() is not None:
        return

    print(
        f"Stopping {process_name} process..."
    )

    process.terminate()

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(
            f"{process_name} did not stop cleanly; "
            "forcing termination.",
            file=sys.stderr,
        )
        process.kill()
        process.wait(timeout=5)


def _wait_for_processes(
    *,
    workload_process: subprocess.Popen[bytes],
    collector_process: subprocess.Popen[bytes],
) -> tuple[int, int]:
    """Wait for both child processes and detect early failures."""

    while True:
        workload_return_code = workload_process.poll()
        collector_return_code = collector_process.poll()

        if (
            workload_return_code is not None
            and workload_return_code != 0
            and collector_return_code is None
        ):
            _terminate_process(
                collector_process,
                process_name="collector",
            )

        if (
            collector_return_code is not None
            and collector_return_code != 0
            and workload_return_code is None
        ):
            _terminate_process(
                workload_process,
                process_name="workload",
            )

        if (
            workload_process.poll() is not None
            and collector_process.poll() is not None
        ):
            break

        sleep(0.25)

    return (
        workload_process.wait(),
        collector_process.wait(),
    )


def run_synchronized_experiment(
    *,
    experiment_run_id: UUID,
    base_url: str,
    actor: str,
    output_directory: Path,
) -> None:
    """Run the workload and collector under one lifecycle."""

    workload_process: subprocess.Popen[bytes] | None = None
    collector_process: subprocess.Popen[bytes] | None = None
    experiment_started = False

    _validate_preconditions(
        base_url=base_url,
        experiment_run_id=experiment_run_id,
    )

    print("Starting experiment lifecycle...")

    started_experiment = _transition_experiment(
        base_url=base_url,
        experiment_run_id=experiment_run_id,
        action="start",
        actor=actor,
        reason=(
            "Started by the synchronized "
            "Checkpoint 27 runner."
        ),
    )

    experiment_started = True

    print(
        "Experiment status: "
        f"{started_experiment.get('status')}"
    )

    collector_command = [
        sys.executable,
        "-m",
        "monitoring.run_collector",
        str(experiment_run_id),
    ]

    workload_command = [
        sys.executable,
        "-m",
        "load_testing.run_experiment",
        str(experiment_run_id),
        "--host",
        base_url,
        "--output-directory",
        str(output_directory),
    ]

    try:
        print("Starting metrics collector...")

        collector_process = subprocess.Popen(
            collector_command,
        )

        print("Starting Locust workload...")

        workload_process = subprocess.Popen(
            workload_command,
        )

        (
            workload_return_code,
            collector_return_code,
        ) = _wait_for_processes(
            workload_process=workload_process,
            collector_process=collector_process,
        )

        print(
            "Workload exit code: "
            f"{workload_return_code}"
        )
        print(
            "Collector exit code: "
            f"{collector_return_code}"
        )

        if workload_return_code != 0:
            raise SynchronizedRunnerError(
                "The Locust workload process failed with "
                f"exit code {workload_return_code}."
            )

        if collector_return_code != 0:
            raise SynchronizedRunnerError(
                "The metrics collector process failed with "
                f"exit code {collector_return_code}."
            )

        completed_experiment = _transition_experiment(
            base_url=base_url,
            experiment_run_id=experiment_run_id,
            action="complete",
            actor=actor,
            reason=(
                "Workload and metrics collection "
                "completed successfully."
            ),
        )

        experiment_started = False

        print(
            "Experiment completed successfully."
        )
        print(
            "Final experiment status: "
            f"{completed_experiment.get('status')}"
        )

    except KeyboardInterrupt:
        _terminate_process(
            workload_process,
            process_name="workload",
        )
        _terminate_process(
            collector_process,
            process_name="collector",
        )

        if experiment_started:
            try:
                _transition_experiment(
                    base_url=base_url,
                    experiment_run_id=experiment_run_id,
                    action="cancel",
                    actor=actor,
                    reason=(
                        "The synchronized experiment "
                        "was interrupted by the operator."
                    ),
                )
                experiment_started = False
            except SynchronizedRunnerError as error:
                print(
                    "Unable to cancel the interrupted "
                    f"experiment: {error}",
                    file=sys.stderr,
                )

        raise

    except Exception as error:
        _terminate_process(
            workload_process,
            process_name="workload",
        )
        _terminate_process(
            collector_process,
            process_name="collector",
        )

        if experiment_started:
            try:
                _transition_experiment(
                    base_url=base_url,
                    experiment_run_id=experiment_run_id,
                    action="fail",
                    actor=actor,
                    reason=str(error)[:500],
                )
                experiment_started = False
            except SynchronizedRunnerError as transition_error:
                print(
                    "Unable to mark the experiment as "
                    f"FAILED: {transition_error}",
                    file=sys.stderr,
                )

        if isinstance(error, SynchronizedRunnerError):
            raise

        raise SynchronizedRunnerError(
            f"Unexpected synchronized-runner error: {error}"
        ) from error

    finally:
        _terminate_process(
            workload_process,
            process_name="workload",
        )
        _terminate_process(
            collector_process,
            process_name="collector",
        )


def parse_arguments() -> argparse.Namespace:
    """Read synchronized-runner command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Start a DRAFT experiment, run Locust and "
            "the metrics collector together, and apply "
            "the final experiment lifecycle transition."
        )
    )

    parser.add_argument(
        "experiment_run_id",
        type=UUID,
        help="UUID of the DRAFT experiment.",
    )

    parser.add_argument(
        "--base-url",
        default=(
            f"http://{settings.app_host}:"
            f"{settings.app_port}"
        ),
        help=(
            "Running reservation API address. "
            "Default is calculated from application settings."
        ),
    )

    parser.add_argument(
        "--actor",
        default="synchronized-runner",
        help=(
            "Actor stored in experiment audit events."
        ),
    )

    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("data/raw/locust"),
        help=(
            "Base directory for Locust CSV output. "
            "Default: data/raw/locust"
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run one synchronized experiment."""

    arguments = parse_arguments()

    try:
        run_synchronized_experiment(
            experiment_run_id=(
                arguments.experiment_run_id
            ),
            base_url=arguments.base_url,
            actor=arguments.actor,
            output_directory=(
                arguments.output_directory
            ),
        )
    except SynchronizedRunnerError as error:
        print(
            f"Synchronized runner error: {error}",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            "\nSynchronized experiment interrupted.",
            file=sys.stderr,
        )
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())