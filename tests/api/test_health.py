from fastapi.testclient import TestClient


def test_liveness_endpoint(
    client: TestClient,
) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "lock-contention-api",
    }


def test_readiness_endpoint(
    client: TestClient,
) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "lock-contention-api",
        "database": "available",
        "migration": "current",
    }