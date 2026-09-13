"""allow versioned experiment exports

Revision ID: f6f8012bfccd
Revises: b7e4c2d9f801
Create Date: 2026-09-13 11:42:52.652190

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6f8012bfccd'
down_revision: Union[str, Sequence[str], None] = 'b7e4c2d9f801'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Allow one export per experiment and schema version."""
    op.drop_constraint(
        "data_exports_experiment_run_id_key",
        "data_exports",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_data_exports_run_schema_version",
        "data_exports",
        [
            "experiment_run_id",
            "schema_version",
        ],
    )


def downgrade() -> None:
    """Restore the original one-export-per-run schema safely."""
    connection = op.get_bind()

    duplicate = connection.execute(
        sa.text(
            """
            SELECT experiment_run_id
            FROM data_exports
            GROUP BY experiment_run_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()

    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade: data_exports contains multiple "
            "schema versions for at least one experiment."
        )

    op.drop_constraint(
        "uq_data_exports_run_schema_version",
        "data_exports",
        type_="unique",
    )
    op.create_unique_constraint(
        "data_exports_experiment_run_id_key",
        "data_exports",
        ["experiment_run_id"],
    )