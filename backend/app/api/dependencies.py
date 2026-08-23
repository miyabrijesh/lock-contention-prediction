from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException, status

from backend.app.core.config import settings


InternalTokenHeader = Annotated[
    str | None,
    Header(
        alias="X-Internal-Token",
        description="Internal API token for workload requests.",
    ),
]

AdminTokenHeader = Annotated[
    str | None,
    Header(
        alias="X-Admin-Token",
        description="Administrator token for experiment management.",
    ),
]


def require_internal_token(
    x_internal_token: InternalTokenHeader = None,
) -> None:
    """Reject requests without the configured internal token."""

    if (
        x_internal_token is None
        or not compare_digest(
            x_internal_token,
            settings.internal_api_token,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_INTERNAL_TOKEN",
                "message": (
                    "A valid internal API token is required."
                ),
            },
            headers={
                "WWW-Authenticate": "InternalToken",
            },
        )


def require_admin_token(
    x_admin_token: AdminTokenHeader = None,
) -> None:
    """Reject experiment management without the admin token."""

    if (
        x_admin_token is None
        or not compare_digest(
            x_admin_token,
            settings.admin_api_token,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "INVALID_ADMIN_TOKEN",
                "message": (
                    "A valid administrator API token is required."
                ),
            },
            headers={
                "WWW-Authenticate": "AdminToken",
            },
        )