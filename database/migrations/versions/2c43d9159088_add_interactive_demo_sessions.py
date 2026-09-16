"""add interactive demo sessions

Revision ID: 2c43d9159088
Revises: f6f8012bfccd
Create Date: 2026-09-16 09:46:59.297278

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "2c43d9159088"
down_revision: Union[
    str,
    Sequence[str],
    None,
] = "f6f8012bfccd"
branch_labels: Union[
    str,
    Sequence[str],
    None,
] = None
depends_on: Union[
    str,
    Sequence[str],
    None,
] = None


demo_session_status_enum = postgresql.ENUM(
    "WAITING_FOR_SELECTION",
    "READY",
    "RUNNING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    name="demo_session_status",
    create_type=False,
)

demo_scenario_name_enum = postgresql.ENUM(
    "BASELINE",
    "CONTENTION",
    name="demo_scenario_name",
    create_type=False,
)


def upgrade() -> None:
    """Create interactive demo-session coordination state."""

    connection = op.get_bind()

    demo_session_status_enum.create(
        connection,
        checkfirst=True,
    )
    demo_scenario_name_enum.create(
        connection,
        checkfirst=True,
    )

    op.create_table(
        "demo_sessions",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "show_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "status",
            demo_session_status_enum,
            server_default="WAITING_FOR_SELECTION",
            nullable=False,
        ),
        sa.Column(
            "scenario",
            demo_scenario_name_enum,
            nullable=False,
        ),
        sa.Column(
            "selected_seat_ids",
            postgresql.JSONB(
                astext_type=sa.Text()
            ),
            server_default=sa.text(
                "'[]'::jsonb"
            ),
            nullable=False,
        ),
        sa.Column(
            "experiment_run_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "selection_submitted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_seat_ids) = 'array'",
            name=(
                "ck_demo_sessions_"
                "selected_seats_array"
            ),
        ),
        sa.CheckConstraint(
            "jsonb_array_length(selected_seat_ids) "
            "BETWEEN 0 AND 5",
            name=(
                "ck_demo_sessions_"
                "selected_seat_count"
            ),
        ),
        sa.CheckConstraint(
            "status NOT IN "
            "('READY', 'RUNNING', 'COMPLETED') "
            "OR jsonb_array_length(selected_seat_ids) "
            "BETWEEN 1 AND 5",
            name=(
                "ck_demo_sessions_"
                "active_seats_required"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_run_id"],
            ["experiment_runs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["show_id"],
            ["shows.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_run_id",
            name=(
                "uq_demo_sessions_"
                "experiment_run"
            ),
        ),
    )

    op.create_index(
        "ix_demo_sessions_show_created",
        "demo_sessions",
        [
            "show_id",
            "created_at",
        ],
        unique=False,
    )

    op.create_index(
        "ix_demo_sessions_status_created",
        "demo_sessions",
        [
            "status",
            "created_at",
        ],
        unique=False,
    )

    op.create_index(
        "uq_demo_sessions_show_open",
        "demo_sessions",
        ["show_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN "
            "('WAITING_FOR_SELECTION', "
            "'READY', 'RUNNING')"
        ),
    )


def downgrade() -> None:
    """Remove interactive demo-session coordination state."""

    op.drop_index(
        "uq_demo_sessions_show_open",
        table_name="demo_sessions",
        postgresql_where=sa.text(
            "status IN "
            "('WAITING_FOR_SELECTION', "
            "'READY', 'RUNNING')"
        ),
    )

    op.drop_index(
        "ix_demo_sessions_status_created",
        table_name="demo_sessions",
    )

    op.drop_index(
        "ix_demo_sessions_show_created",
        table_name="demo_sessions",
    )

    op.drop_table("demo_sessions")

    connection = op.get_bind()

    demo_scenario_name_enum.drop(
        connection,
        checkfirst=True,
    )
    demo_session_status_enum.drop(
        connection,
        checkfirst=True,
    )