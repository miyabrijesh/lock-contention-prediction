from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.session import engine


router = APIRouter(
    prefix="/health",
    tags=["Health"],
)


def get_application_migration_head() -> str | None:
    """Return the newest migration revision included in the code."""

    alembic_config = Config("alembic.ini")
    migration_scripts = ScriptDirectory.from_config(alembic_config)

    return migration_scripts.get_current_head()


@router.get(
    "/live",
    response_model=None,
    summary="Check whether the API process is running",
)
def liveness_check() -> JSONResponse:
    """Return success when the FastAPI process can respond."""

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ok",
            "service": "lock-contention-api",
        },
    )


@router.get(
    "/ready",
    response_model=None,
    summary="Check database and migration readiness",
)
def readiness_check() -> JSONResponse:
    """Verify database connectivity and migration compatibility."""

    try:
        with engine.connect() as connection:
            database_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

        application_revision = get_application_migration_head()

    except (SQLAlchemyError, CommandError, OSError):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "service": "lock-contention-api",
                "database": "unavailable",
                "migration": "unknown",
            },
        )

    if (
        database_revision is None
        or application_revision is None
        or database_revision != application_revision
    ):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "service": "lock-contention-api",
                "database": "available",
                "migration": "mismatch",
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ok",
            "service": "lock-contention-api",
            "database": "available",
            "migration": "current",
        },
    )