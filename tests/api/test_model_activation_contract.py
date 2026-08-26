from pathlib import Path

from backend.app.models import (
    AuditEvent,
    ModelVersion,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "model_activation_contract_v1.md"
)


def test_model_activation_contract_contains_required_rules() -> None:
    """Require the documented activation safety boundaries."""

    contract_text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_content = (
        "# Model Activation Contract v1",
        "At most one model version",
        "X-Admin-Token",
        "MODEL_VERSION_NOT_FOUND",
        "ACTIVE_MODEL_NOT_FOUND",
        "MODEL_ARTIFACT_INVALID",
        "MODEL_PROVENANCE_INVALID",
        "uq_model_versions_single_active",
        "model_activated",
        "resource_type",
        "model_version",
        "Idempotent activation",
        "Immutable provenance",
        "git diff --check",
    )

    missing_content = [
        value
        for value in required_content
        if value not in contract_text
    ]

    assert not missing_content, (
        "The model activation contract is missing: "
        f"{missing_content}"
    )


def test_model_version_has_single_active_database_index() -> None:
    """Keep the database-level single-active-model guard."""

    model_version_table = (
        ModelVersion.metadata.tables["model_versions"]
    )

    matching_indexes = [
        index
        for index in model_version_table.indexes
        if str(index.name)
        == "uq_model_versions_single_active"
    ]

    assert len(matching_indexes) == 1

    active_index = matching_indexes[0]

    assert active_index.unique is True

    indexed_columns = tuple(
        column.name
        for column in active_index.columns
    )

    assert indexed_columns == ("is_active",)


def test_model_activation_changes_only_active_state() -> None:
    """Document the only mutable field used by activation."""

    model_version_table = (
        ModelVersion.metadata.tables["model_versions"]
    )

    model_columns = {
        column.name
        for column in model_version_table.columns
    }

    expected_columns = {
        "id",
        "name",
        "algorithm",
        "feature_schema",
        "target_horizon_sec",
        "training_run_ids",
        "validation_strategy",
        "risk_thresholds",
        "evaluation_metrics",
        "artifact_path",
        "artifact_sha256",
        "code_version",
        "created_at",
        "is_active",
    }

    assert model_columns == expected_columns
    assert "is_active" in model_columns