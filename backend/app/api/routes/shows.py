from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.enums import SeatState
from backend.app.repositories.show_repository import (
    get_show_by_id,
    list_seats_for_show,
    list_shows,
)
from backend.app.schemas.seat import SeatRead
from backend.app.schemas.show import ShowRead


router = APIRouter(
    prefix="/api/v1/shows",
    tags=["Shows"],
)

DatabaseSession = Annotated[Session, Depends(get_db)]
ShowId = Annotated[int, Path(ge=1)]


@router.get(
    "",
    response_model=list[ShowRead],
    summary="List all reservation shows",
)
def get_shows(
    session: DatabaseSession,
) -> list[ShowRead]:
    """Return every configured reservation show."""

    shows = list_shows(session)

    return [
        ShowRead.model_validate(show)
        for show in shows
    ]


@router.get(
    "/{show_id}",
    response_model=ShowRead,
    summary="Get one reservation show",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Show not found",
        }
    },
)
def get_show(
    show_id: ShowId,
    session: DatabaseSession,
) -> ShowRead:
    """Return one show using its database identifier."""

    show = get_show_by_id(session, show_id)

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Show {show_id} was not found.",
        )

    return ShowRead.model_validate(show)


@router.get(
    "/{show_id}/seats",
    response_model=list[SeatRead],
    summary="List seats for one show",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Show not found",
        }
    },
)
def get_show_seats(
    show_id: ShowId,
    session: DatabaseSession,
    seat_state: Annotated[
        SeatState | None,
        Query(
            alias="state",
            description="Optionally filter seats by their current state.",
        ),
    ] = None,
) -> list[SeatRead]:
    """Return all seats for a show, optionally filtered by state."""

    show = get_show_by_id(session, show_id)

    if show is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Show {show_id} was not found.",
        )

    seats = list_seats_for_show(
        session=session,
        show_id=show_id,
        seat_state=seat_state,
    )

    return [
        SeatRead.model_validate(seat)
        for seat in seats
    ]