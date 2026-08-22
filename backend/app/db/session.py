from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_min,
    max_overflow=max(
        0,
        settings.db_pool_max - settings.db_pool_min,
    ),
    connect_args={
        "options": (
            f"-c lock_timeout={settings.db_lock_timeout_ms}ms "
            f"-c statement_timeout={settings.db_statement_timeout_ms}ms"
        )
    },
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """Provide a database session and always close it afterward."""

    database_session = SessionLocal()

    try:
        yield database_session
    finally:
        database_session.close()