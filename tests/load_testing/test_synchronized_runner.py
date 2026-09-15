import subprocess
import sys

from load_testing.run_synchronized_experiment import (
    _wait_for_processes,
)


def _start_process(
    *,
    delay_seconds: float,
    exit_code: int,
) -> subprocess.Popen[bytes]:
    """Start a small isolated test subprocess."""

    source = (
        "import sys, time; "
        f"time.sleep({delay_seconds}); "
        f"sys.exit({exit_code})"
    )

    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            source,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_process(
    process: subprocess.Popen[bytes],
) -> None:
    """Ensure a test subprocess cannot remain running."""

    if process.poll() is not None:
        return

    process.terminate()

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def test_wait_for_processes_accepts_successful_children() -> None:
    """Return both successful child exit codes."""

    workload_process = _start_process(
        delay_seconds=0.05,
        exit_code=0,
    )
    collector_process = _start_process(
        delay_seconds=0.05,
        exit_code=0,
    )

    try:
        result = _wait_for_processes(
            workload_process=workload_process,
            collector_process=collector_process,
        )
    finally:
        _stop_process(workload_process)
        _stop_process(collector_process)

    assert result == (
        0,
        0,
        False,
    )


def test_wait_for_processes_stops_collector_after_workload_failure() -> None:
    """Stop the collector when the workload exits unsuccessfully."""

    workload_process = _start_process(
        delay_seconds=0.05,
        exit_code=3,
    )
    collector_process = _start_process(
        delay_seconds=30,
        exit_code=0,
    )

    try:
        result = _wait_for_processes(
            workload_process=workload_process,
            collector_process=collector_process,
        )
    finally:
        _stop_process(workload_process)
        _stop_process(collector_process)

    (
        workload_exit_code,
        collector_exit_code,
        stopped_by_condition,
    ) = result

    assert workload_exit_code == 3
    assert collector_exit_code != 0
    assert stopped_by_condition is False
    assert collector_process.poll() is not None


def test_wait_for_processes_stops_workload_after_collector_failure() -> None:
    """Stop the workload when the collector exits unsuccessfully."""

    workload_process = _start_process(
        delay_seconds=30,
        exit_code=0,
    )
    collector_process = _start_process(
        delay_seconds=0.05,
        exit_code=4,
    )

    try:
        result = _wait_for_processes(
            workload_process=workload_process,
            collector_process=collector_process,
        )
    finally:
        _stop_process(workload_process)
        _stop_process(collector_process)

    (
        workload_exit_code,
        collector_exit_code,
        stopped_by_condition,
    ) = result

    assert workload_exit_code != 0
    assert collector_exit_code == 4
    assert stopped_by_condition is False
    assert workload_process.poll() is not None


def test_wait_for_processes_supports_controlled_completion() -> None:
    """Stop both children after the completion condition is met."""

    workload_process = _start_process(
        delay_seconds=30,
        exit_code=0,
    )
    collector_process = _start_process(
        delay_seconds=30,
        exit_code=0,
    )

    try:
        result = _wait_for_processes(
            workload_process=workload_process,
            collector_process=collector_process,
            stop_condition=lambda: True,
            stop_grace_seconds=0.0,
        )
    finally:
        _stop_process(workload_process)
        _stop_process(collector_process)

    (
        workload_exit_code,
        collector_exit_code,
        stopped_by_condition,
    ) = result

    assert workload_exit_code != 0
    assert collector_exit_code != 0
    assert stopped_by_condition is True
    assert workload_process.poll() is not None
    assert collector_process.poll() is not None