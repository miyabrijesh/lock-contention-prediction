from fastapi.testclient import TestClient


def test_list_shows(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/shows")

    assert response.status_code == 200

    shows = response.json()

    assert len(shows) >= 2
    assert any(
        show["name"] == "Normal Booking Demonstration"
        for show in shows
    )
    assert any(
        show["name"] == "Lock Contention Experiment"
        for show in shows
    )


def test_get_existing_show(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/shows/1")

    assert response.status_code == 200

    show = response.json()

    assert show["id"] == 1
    assert show["name"] == "Normal Booking Demonstration"
    assert show["venue"] == "College Demo Theatre"
    assert show["is_experiment_show"] is False
    assert "show_time" in show
    assert "created_at" in show
    assert "updated_at" in show


def test_list_seats_for_show(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/shows/1/seats")

    assert response.status_code == 200

    seats = response.json()

    assert len(seats) == 100
    assert seats[0]["show_id"] == 1
    assert seats[0]["seat_number"] == "A1"
    assert seats[0]["state"] == "AVAILABLE"
    assert seats[0]["version"] == 0


def test_filter_available_seats(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/shows/1/seats",
        params={"state": "AVAILABLE"},
    )

    assert response.status_code == 200

    seats = response.json()

    assert len(seats) == 100
    assert all(
        seat["state"] == "AVAILABLE"
        for seat in seats
    )


def test_missing_show_returns_404(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/shows/999999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Show 999999 was not found.",
    }


def test_invalid_state_returns_422(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/shows/1/seats",
        params={"state": "INVALID"},
    )

    assert response.status_code == 422

    error = response.json()

    assert error["detail"][0]["loc"] == [
        "query",
        "state",
    ]