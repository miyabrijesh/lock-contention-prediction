from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import Seat, Show


def _admin_headers() -> dict[str, str]:
    """Return the configured local admin header."""

    return {
        "X-Admin-Token": settings.admin_api_token,
    }


def _experiment_payload(
    session: Session,
    *,
    demand_profile: str,
    hotspot_ratio: Decimal,
    hotspot_seat_count: int,
) -> dict[str, object]:
    """Build one experiment request using seeded test records."""

    show = session.scalar(
        select(Show)
        .where(Show.is_experiment_show.is_(True))
        .order_by(Show.id)
    )

    assert show is not None

    hotspot_seat_ids = list(
        session.scalars(
            select(Seat.id)
            .where(Seat.show_id == show.id)
            .order_by(Seat.id)
            .limit(hotspot_seat_count)
        ).all()
    )

    assert len(hotspot_seat_ids) == hotspot_seat_count

    return {
        "name": f"Demand profile test {uuid4()}",
        "show_id": show.id,
        "user_count": 10,
        "spawn_rate_per_sec": "5.00",
        "duration_sec": 90,
        "traffic_pattern": "STEADY",
        "demand_profile": demand_profile,
        "hotspot_ratio": str(hotspot_ratio),
        "hotspot_seat_ids": hotspot_seat_ids,
        "sample_interval_sec": 2,
        "simulated_work_ms": 100,
        "booking_mode": "EXPERIMENT_RECYCLE",
        "random_seed": 42,
        "code_version": "pytest",
        "created_by": "pytest",
    }


def test_custom_demand_profile_is_accepted(
    client: TestClient,
    db_session: Session,
) -> None:
    """Allow explicitly controlled experiments to use CUSTOM."""

    response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            demand_profile="CUSTOM",
            hotspot_ratio=Decimal("0.80"),
            hotspot_seat_count=2,
        ),
    )

    assert response.status_code == 201
    assert response.json()["demand_profile"] == "CUSTOM"


def test_unknown_demand_profile_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject a profile name outside the API contract."""

    response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            demand_profile="UNKNOWN",
            hotspot_ratio=Decimal("0.80"),
            hotspot_seat_count=2,
        ),
    )

    assert response.status_code == 422


def test_mislabeled_high_profile_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Prevent a low ratio from being labelled as HIGH demand."""

    response = client.post(
        "/api/v1/experiments",
        headers=_admin_headers(),
        json=_experiment_payload(
            db_session,
            demand_profile="HIGH",
            hotspot_ratio=Decimal("0.20"),
            hotspot_seat_count=3,
        ),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == (
        "DEMAND_PROFILE_MISMATCH"
    )