import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

import model_training.verify_phase7 as verify_module
from model_training.verify_phase7 import (
    EXPECTED_DATABASE_REVISION,
    Phase7VerificationError,
    build_phase7_completion_report,
    verify_database_revision,
    verify_documentation_evidence,
    write_completion_report,
)


def test_documentation_evidence_is_complete() -> None:
    """Verify every required Phase 7 evidence file."""

    evidence = verify_documentation_evidence()

    assert evidence["required_file_count"] == 5
    assert (
        evidence[
            "automatic_prediction_transaction_boundary"
        ]
        is True
    )
    assert (
        evidence[
            "demonstration_workflow_documented"
        ]
        is True
    )
    assert len(evidence["files"]) == 5

    for file_evidence in evidence[
        "files"
    ].values():
        checksum = file_evidence["sha256"]

        assert isinstance(checksum, str)
        assert len(checksum) == 64


def test_missing_documentation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reject an incomplete Phase 7 evidence set."""

    missing_file = (
        tmp_path
        / "missing-phase7-evidence.md"
    )

    monkeypatch.setattr(
        verify_module,
        "REQUIRED_EVIDENCE_FILES",
        (missing_file,),
    )

    with pytest.raises(
        Phase7VerificationError,
        match="was not found",
    ):
        verify_documentation_evidence()


def test_database_revision_is_current(
    db_session: Session,
) -> None:
    """Require the expected database migration revision."""

    evidence = verify_database_revision(
        db_session
    )

    assert evidence["revision"] == (
        EXPECTED_DATABASE_REVISION
    )
    assert evidence["is_current"] is True
    assert (
        evidence["phase7_migration_required"]
        is False
    )


def test_completion_report_composes_verified_evidence(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose the final report from verified sections."""

    experiment_run_id = uuid4()
    model_version_id = uuid4()

    documentation_evidence: dict[
        str,
        Any,
    ] = {
        "required_file_count": 5,
        "files": {},
        "automatic_prediction_transaction_boundary": (
            True
        ),
        "demonstration_workflow_documented": True,
    }

    database_evidence: dict[
        str,
        Any,
    ] = {
        "revision": EXPECTED_DATABASE_REVISION,
        "expected_revision": (
            EXPECTED_DATABASE_REVISION
        ),
        "is_current": True,
        "phase7_migration_required": False,
    }

    active_model_evidence: dict[
        str,
        Any,
    ] = {
        "model_version_id": str(
            model_version_id
        ),
        "name": "contention-logistic-v1",
        "algorithm": (
            "StandardScaler + LogisticRegression"
        ),
        "is_active": True,
        "artifact_path": (
            "artifacts/models/"
            "contention_candidate_v1.joblib"
        ),
        "artifact_sha256": "a" * 64,
        "target_horizon_seconds": 30,
        "code_version": "pytest",
    }

    experiment_evidence: dict[
        str,
        Any,
    ] = {
        "experiment_run_id": str(
            experiment_run_id
        ),
        "status": "COMPLETED",
        "complete_sample_count": 15,
        "prediction_count": 15,
        "distinct_predicted_sample_count": 15,
        "missing_prediction_count": 0,
        "duplicate_prediction_count": 0,
        "stale_prediction_count": 0,
        "labeled_prediction_count": 0,
        "model_version_count": 1,
        "coverage": "1",
        "collector_versions": [
            "checkpoint-38-v1"
        ],
        "prediction_model_version_ids": [
            str(model_version_id)
        ],
        "passed": True,
    }

    monkeypatch.setattr(
        verify_module,
        "verify_documentation_evidence",
        lambda: documentation_evidence,
    )
    monkeypatch.setattr(
        verify_module,
        "verify_database_revision",
        lambda session: database_evidence,
    )
    monkeypatch.setattr(
        verify_module,
        "verify_active_model",
        lambda session: active_model_evidence,
    )

    def verify_experiment(
        session: Session,
        received_experiment_run_id: object,
        *,
        active_model_id: object,
    ) -> dict[str, Any]:
        assert received_experiment_run_id == (
            experiment_run_id
        )
        assert active_model_id == model_version_id

        return experiment_evidence

    monkeypatch.setattr(
        verify_module,
        "verify_experiment_evidence",
        verify_experiment,
    )

    report = build_phase7_completion_report(
        db_session,
        experiment_run_id,
    )

    assert report["phase"] == 7
    assert report["status"] == "COMPLETED"
    assert report["documentation"] == (
        documentation_evidence
    )
    assert report["database"] == (
        database_evidence
    )
    assert report["active_model"] == (
        active_model_evidence
    )
    assert report["live_experiment"] == (
        experiment_evidence
    )
    assert (
        report["checkpoints"]["39C"]
        == "COMPLETED"
    )
    assert (
        report["checkpoints"]["39D"]
        == "COMPLETED"
    )
    assert (
        report["capabilities"][
            "automatic_prediction_persistence"
        ]
        is True
    )


def test_completion_report_is_written_as_json(
    tmp_path: Path,
) -> None:
    """Write deterministic JSON completion evidence."""

    output_file = (
        tmp_path
        / "phase7-completion.json"
    )

    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "phase": 7,
        "status": "COMPLETED",
    }

    written_file = write_completion_report(
        report=report,
        output_file=output_file,
    )

    assert written_file == output_file
    assert json.loads(
        output_file.read_text(
            encoding="utf-8"
        )
    ) == report
    assert output_file.read_text(
        encoding="utf-8"
    ).endswith("\n")