from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from backend.app.models import Prediction


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CONTRACT_FILE = (
    PROJECT_ROOT
    / "docs"
    / "model_training"
    / "prediction_persistence_contract_v1.md"
)


def test_prediction_persistence_contract_content() -> None:
    """Require the documented persistence boundaries."""

    contract_text = CONTRACT_FILE.read_text(
        encoding="utf-8"
    )

    required_content = (
        "# Prediction Persistence Contract v1",
        "18 ordered features",
        "STALE_TARGET_WINDOW",
        "predictions.metric_sample_id",
        "METRIC_SAMPLE_NOT_FOUND",
        "METRIC_SAMPLE_INCOMPLETE",
        "ACTIVE_MODEL_NOT_FOUND",
        "MODEL_ARTIFACT_INVALID",
        "MODEL_PROVENANCE_INVALID",
        "PREDICTION_INPUT_INVALID",
        "PREDICTION_CONFLICT",
        "actual_label",
        "Checkpoint 37",
        "git diff --check",
    )

    missing_content = [
        value
        for value in required_content
        if value not in contract_text
    ]

    assert not missing_content, (
        "The prediction persistence contract "
        f"is missing: {missing_content}"
    )


def test_metric_sample_allows_only_one_prediction() -> None:
    """Keep database-level prediction idempotency."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    matching_constraints = [
        constraint
        for constraint
        in prediction_table.constraints
        if (
            isinstance(
                constraint,
                UniqueConstraint,
            )
            and tuple(
                column.name
                for column in constraint.columns
            )
            == ("metric_sample_id",)
        )
    ]

    assert len(matching_constraints) == 1


def test_prediction_probability_and_window_constraints() -> None:
    """Keep probability, threshold, and window checks."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    check_constraint_names = {
        str(constraint.name)
        for constraint
        in prediction_table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    required_constraints = {
        "ck_predictions_probability",
        "ck_predictions_threshold",
        "ck_predictions_target_window",
    }

    assert required_constraints <= (
        check_constraint_names
    )


def test_prediction_provenance_foreign_keys() -> None:
    """Require experiment, sample, and model provenance."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    foreign_key_columns = {
        tuple(
            column.name
            for column in constraint.columns
        )
        for constraint
        in prediction_table.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
    }

    assert (
        "experiment_run_id",
    ) in foreign_key_columns

    assert (
        "metric_sample_id",
    ) in foreign_key_columns

    assert (
        "model_version_id",
    ) in foreign_key_columns