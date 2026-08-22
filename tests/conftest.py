from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import backend.app.api.routes.health as health_routes
from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.main import app


DEVELOPMENT_DATABASE_URL = make_url(
    settings.database_url
)

TEST_DATABASE_URL = DEVELOPMENT_DATABASE_URL.set(
    database="lock_contention_test_db"
)

if (
    TEST_DATABASE_URL.database
    == DEVELOPMENT_DATABASE_URL.database
):
    raise RuntimeError(
        "The test database must not be the development database."
    )


test_engine = create_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
)


@pytest.fixture(scope="session", autouse=True)
def dispose_test_engine() -> Generator[None, None, None]:
    """Dispose the test connection pool after the test session."""

    yield

    test_engine.dispose()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide a session inside a rollback-only test transaction."""

    connection = test_engine.connect()
    transaction = connection.begin()

    test_session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
    )

    try:
        yield test_session
    finally:
        test_session.close()

        if transaction.is_active:
            transaction.rollback()

        connection.close()


@pytest.fixture
def client(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    """Run API requests using the rollback-only test session."""

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    monkeypatch.setattr(
        health_routes,
        "engine",
        test_engine,
    )

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()