from pathlib import Path

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB

from backend.app.models import Prediction


PROJECT_ROOT = Path(__file__).resolve().parents[2]

MIGRATION_FILE = (
    PROJECT_ROOT
    / "database"
    / "migrations"
    / "versions"
    / (
        "b7e4c2d9f801_"
        "add_prediction_label_provenance.py"
    )
)


def test_prediction_model_defines_label_provenance() -> None:
    """Require nullable live-label provenance fields."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    required_columns = {
        "actual_label",
        "actual_label_assigned_at",
        "actual_label_contract_version",
        "actual_label_evidence",
    }

    assert required_columns <= set(
        prediction_table.columns.keys()
    )

    assert (
        prediction_table.c.actual_label_assigned_at
        .nullable
        is True
    )

    assert (
        prediction_table.c.actual_label_contract_version
        .nullable
        is True
    )

    assert (
        prediction_table.c.actual_label_evidence
        .nullable
        is True
    )

    assert isinstance(
        prediction_table.c.actual_label_evidence.type,
        JSONB,
    )


def test_prediction_model_defines_label_constraints() -> None:
    """Require database-level label invariants."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    constraint_names = {
        str(constraint.name)
        for constraint in prediction_table.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    required_constraints = {
        "ck_predictions_label_evidence_object",
        "ck_predictions_label_contract_nonblank",
        "ck_predictions_label_assignment_time",
        "ck_predictions_label_provenance_complete",
    }

    assert required_constraints <= constraint_names


def test_prediction_model_defines_unlabeled_index() -> None:
    """Require efficient mature-label candidate lookup."""

    prediction_table = (
        Prediction.metadata.tables["predictions"]
    )

    matching_indexes = [
        index
        for index in prediction_table.indexes
        if index.name
        == (
            "ix_predictions_"
            "unlabeled_target_window_end"
        )
    ]

    assert len(matching_indexes) == 1

    where_clause = matching_indexes[
        0
    ].dialect_options[
        "postgresql"
    ][
        "where"
    ]

    assert where_clause is not None
    assert "actual_label IS NULL" in str(
        where_clause
    )


def test_label_provenance_migration_is_complete() -> None:
    """Require upgrade and downgrade migration coverage."""

    assert MIGRATION_FILE.is_file()

    migration_text = MIGRATION_FILE.read_text(
        encoding="utf-8"
    )

    required_content = (
        'revision: str = "b7e4c2d9f801"',
        (
            'down_revision: str | None = '
            '"a91f6c8d2e47"'
        ),
        '"actual_label_assigned_at"',
        '"actual_label_contract_version"',
        '"actual_label_evidence"',
        (
            '"ck_predictions_'
            'label_provenance_complete"'
        ),
        (
            '"ix_predictions_'
            'unlabeled_target_window_end"'
        ),
        "op.add_column",
        "op.drop_column",
    )

    for expected_text in required_content:
        assert expected_text in migration_text