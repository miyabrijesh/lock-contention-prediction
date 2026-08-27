"""Add prediction label provenance.

Revision ID: b7e4c2d9f801
Revises: a91f6c8d2e47
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b7e4c2d9f801"
down_revision: str | None = "a91f6c8d2e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add auditable live-label provenance."""

    op.add_column(
        "predictions",
        sa.Column(
            "actual_label_assigned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "predictions",
        sa.Column(
            "actual_label_contract_version",
            sa.String(length=32),
            nullable=True,
        ),
    )

    op.add_column(
        "predictions",
        sa.Column(
            "actual_label_evidence",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            nullable=True,
        ),
    )

    op.create_check_constraint(
        "ck_predictions_label_evidence_object",
        "predictions",
        "actual_label_evidence IS NULL "
        "OR jsonb_typeof(actual_label_evidence) = 'object'",
    )

    op.create_check_constraint(
        "ck_predictions_label_contract_nonblank",
        "predictions",
        "actual_label_contract_version IS NULL "
        "OR btrim(actual_label_contract_version) <> ''",
    )

    op.create_check_constraint(
        "ck_predictions_label_assignment_time",
        "predictions",
        "actual_label_assigned_at IS NULL "
        "OR actual_label_assigned_at >= target_window_end",
    )

    op.create_check_constraint(
        "ck_predictions_label_provenance_complete",
        "predictions",
        "("
        "actual_label IS NULL "
        "AND actual_label_assigned_at IS NULL "
        "AND actual_label_contract_version IS NULL "
        "AND actual_label_evidence IS NULL"
        ") OR ("
        "actual_label IS NOT NULL "
        "AND actual_label_assigned_at IS NOT NULL "
        "AND actual_label_contract_version IS NOT NULL "
        "AND actual_label_evidence IS NOT NULL"
        ")",
    )

    op.create_index(
        "ix_predictions_unlabeled_target_window_end",
        "predictions",
        ["target_window_end"],
        unique=False,
        postgresql_where=sa.text(
            "actual_label IS NULL"
        ),
    )


def downgrade() -> None:
    """Remove live-label provenance."""

    op.drop_index(
        "ix_predictions_unlabeled_target_window_end",
        table_name="predictions",
    )

    op.drop_constraint(
        "ck_predictions_label_provenance_complete",
        "predictions",
        type_="check",
    )

    op.drop_constraint(
        "ck_predictions_label_assignment_time",
        "predictions",
        type_="check",
    )

    op.drop_constraint(
        "ck_predictions_label_contract_nonblank",
        "predictions",
        type_="check",
    )

    op.drop_constraint(
        "ck_predictions_label_evidence_object",
        "predictions",
        type_="check",
    )

    op.drop_column(
        "predictions",
        "actual_label_evidence",
    )

    op.drop_column(
        "predictions",
        "actual_label_contract_version",
    )

    op.drop_column(
        "predictions",
        "actual_label_assigned_at",
    )
