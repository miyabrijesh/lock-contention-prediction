"""Add demand profile to experiment runs.

Revision ID: a91f6c8d2e47
Revises: 64e1b9a7c205
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a91f6c8d2e47"
down_revision: str | None = "64e1b9a7c205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add traceable demand-profile configuration."""

    op.add_column(
        "experiment_runs",
        sa.Column(
            "demand_profile",
            sa.String(length=20),
            nullable=False,
            server_default="CUSTOM",
        ),
    )

    op.create_check_constraint(
        "ck_experiment_runs_demand_profile",
        "experiment_runs",
        "demand_profile IN "
        "('CUSTOM', 'LOW', 'MEDIUM', 'HIGH')",
    )


def downgrade() -> None:
    """Remove demand-profile configuration."""

    op.drop_constraint(
        "ck_experiment_runs_demand_profile",
        "experiment_runs",
        type_="check",
    )

    op.drop_column(
        "experiment_runs",
        "demand_profile",
    )