from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.enums import SeatState


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    venue: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    show_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    is_experiment_show: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
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

    seats: Mapped[list[Seat]] = relationship(
        back_populates="show",
    )


class Seat(Base):
    __tablename__ = "seats"

    __table_args__ = (
        UniqueConstraint(
            "show_id",
            "seat_number",
            name="uq_seats_show_seat_number",
        ),
        CheckConstraint(
            "version >= 0",
            name="ck_seats_version_nonnegative",
        ),
        CheckConstraint(
            "(state = 'HELD' AND hold_expires_at IS NOT NULL) OR "
            "(state IN ('AVAILABLE', 'BOOKED') "
            "AND hold_expires_at IS NULL)",
            name="ck_seats_hold_expiry_matches_state",
        ),
        Index(
            "ix_seats_show_state",
            "show_id",
            "state",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    show_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seat_number: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    state: Mapped[SeatState] = mapped_column(
        SqlEnum(SeatState, name="seat_state"),
        nullable=False,
        default=SeatState.AVAILABLE,
        server_default=SeatState.AVAILABLE.value,
    )
    hold_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
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

    show: Mapped[Show] = relationship(
        back_populates="seats",
    )


class SyntheticUser(Base):
    __tablename__ = "synthetic_users"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    external_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )