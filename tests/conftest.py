from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Provide a FastAPI test client for one test."""

    with TestClient(app) as test_client:
        yield test_client