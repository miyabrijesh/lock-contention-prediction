"""Add traffic pattern to experiment runs.

Revision ID: 64e1b9a7c205
Revises: 03be09fc7e31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "64e1b9a7c205"
down_revision: str | None = "03be09fc7e31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the traffic-pattern experiment configuration."""

    op.add_column(
        "experiment_runs",
        sa.Column(
            "traffic_pattern",
            sa.String(length=20),
            nullable=False,
            server_default="STEADY",
        ),
    )

    op.create_check_constraint(
        "ck_experiment_runs_traffic_pattern",
        "experiment_runs",
        "traffic_pattern IN "
        "('STEADY', 'RAMP', 'BURST', "
        "'FLASH_SALE', 'COOLDOWN')",
    )


def downgrade() -> None:
    """Remove the traffic-pattern experiment configuration."""

    op.drop_constraint(
        "ck_experiment_runs_traffic_pattern",
        "experiment_runs",
        type_="check",
    )

    op.drop_column(
        "experiment_runs",
        "traffic_pattern",
    )