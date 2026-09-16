from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import (
    JSONB,
    UUID as PostgresUUID,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.models.enums import (
    DemoScenarioName,
    DemoSessionStatus,
)


class DemoSession(Base):
    """Coordinate terminal and browser demo activity."""

    __tablename__ = "demo_sessions"

    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(selected_seat_ids) = 'array'",
            name="ck_demo_sessions_selected_seats_array",
        ),
        CheckConstraint(
            "jsonb_array_length(selected_seat_ids) "
            "BETWEEN 0 AND 5",
            name="ck_demo_sessions_selected_seat_count",
        ),
        CheckConstraint(
            "status NOT IN "
            "('READY', 'RUNNING', 'COMPLETED') "
            "OR jsonb_array_length(selected_seat_ids) "
            "BETWEEN 1 AND 5",
            name="ck_demo_sessions_active_seats_required",
        ),
        UniqueConstraint(
            "experiment_run_id",
            name="uq_demo_sessions_experiment_run",
        ),
        Index(
            "ix_demo_sessions_show_created",
            "show_id",
            "created_at",
        ),
        Index(
            "ix_demo_sessions_status_created",
            "status",
            "created_at",
        ),
        Index(
            "uq_demo_sessions_show_open",
            "show_id",
            unique=True,
            postgresql_where=text(
                "status IN "
                "('WAITING_FOR_SELECTION', "
                "'READY', 'RUNNING')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    show_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "shows.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    status: Mapped[DemoSessionStatus] = mapped_column(
        SqlEnum(
            DemoSessionStatus,
            name="demo_session_status",
        ),
        nullable=False,
        default=(
            DemoSessionStatus.WAITING_FOR_SELECTION
        ),
        server_default=(
            DemoSessionStatus.WAITING_FOR_SELECTION.value
        ),
    )

    scenario: Mapped[DemoScenarioName] = mapped_column(
        SqlEnum(
            DemoScenarioName,
            name="demo_scenario_name",
        ),
        nullable=False,
    )

    selected_seat_ids: Mapped[list[int]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )

    experiment_run_id: Mapped[UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey(
            "experiment_runs.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    selection_submitted_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )