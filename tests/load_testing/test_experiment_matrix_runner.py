import sys
from pathlib import Path

import pytest

from load_testing.experiment_matrix import (
    ExperimentMatrix,
    load_experiment_matrix,
)
from load_testing.run_experiment_matrix import (
    ExperimentMatrixRunnerError,
    _build_new_manifest,
    _get_scenario_entry,
    _load_existing_manifest,
    _selected_scenarios,
    _write_manifest,
    main,
)


MATRIX_FILE = Path(
    "load_testing/config/experiment_matrix_v1.json"
).resolve()


def _new_manifest() -> tuple[
    ExperimentMatrix,
    dict[str, object],
]:
    """Build one in-memory manifest for selection tests."""

    matrix = load_experiment_matrix(MATRIX_FILE)
    manifest = _build_new_manifest(
        matrix=matrix,
        matrix_file=MATRIX_FILE,
        matrix_sha256="a" * 64,
        base_url="http://127.0.0.1:8000",
        locust_output_directory=Path("data/raw/locust"),
        export_directory=Path("data/exports/experiments"),
    )

    return matrix, manifest


def test_default_runner_is_dry_run_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default invocation must not create an execution manifest."""

    manifest_file = tmp_path / "manifest.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_experiment_matrix.py",
            "--matrix-file",
            str(MATRIX_FILE),
            "--manifest-file",
            str(manifest_file),
        ],
    )

    exit_code = main()

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run only" in captured.out
    assert not manifest_file.exists()


def test_resume_requires_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resume must never imply permission to execute workloads."""

    manifest_file = tmp_path / "manifest.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_experiment_matrix.py",
            "--matrix-file",
            str(MATRIX_FILE),
            "--manifest-file",
            str(manifest_file),
            "--resume",
        ],
    )

    exit_code = main()

    captured = capsys.readouterr()

    assert exit_code == 2
    assert "--resume can only be used" in captured.err
    assert not manifest_file.exists()


def test_completed_scenario_is_skipped() -> None:
    """Resume selection must begin with the next incomplete run."""

    matrix, manifest = _new_manifest()

    first_scenario = matrix.scenarios[0]
    second_scenario = matrix.scenarios[1]

    first_entry = _get_scenario_entry(
        manifest,
        first_scenario.scenario_id,
    )
    first_entry["status"] = "COMPLETED"

    selected = _selected_scenarios(
        matrix=matrix,
        manifest=manifest,
        limit=1,
    )

    assert len(selected) == 1
    assert selected[0].scenario_id == (
        second_scenario.scenario_id
    )


def test_changed_matrix_cannot_reuse_manifest(
    tmp_path: Path,
) -> None:
    """Checksum protection prevents mixing two matrix versions."""

    matrix, manifest = _new_manifest()
    manifest_file = tmp_path / "manifest.json"

    _write_manifest(
        manifest_file,
        manifest,
    )

    with pytest.raises(
        ExperimentMatrixRunnerError,
        match="matrix file changed",
    ):
        _load_existing_manifest(
            manifest_file,
            matrix=matrix,
            matrix_sha256="b" * 64,
        )