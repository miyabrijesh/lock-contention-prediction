from datetime import (
    UTC,
    datetime,
    timedelta,
)
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models import (
    ExperimentRun,
    ExperimentStatus,
    MetricSample,
    ModelVersion,
    Prediction,
)
from model_training.register_model import (
    build_registration_spec,
)


def _internal_headers() -> dict[str, str]:
    """Return valid internal authentication."""

    return {
        "X-Internal-Token": (
            settings.internal_api_token
        ),
    }


def _admin_headers() -> dict[str, str]:
    """Return valid administrator authentication."""

    return {
        "X-Admin-Token": settings.admin_api_token,
    }


def _create_experiment(
    session: Session,
) -> ExperimentRun:
    """Create a completed test experiment."""

    now = datetime.now(UTC)

    experiment = ExperimentRun(
        id=uuid4(),
        name=f"Prediction API test {uuid4()}",
        show_id=2,
        status=ExperimentStatus.COMPLETED,
        user_count=8,
        spawn_rate_per_sec=Decimal("4.00"),
        duration_sec=120,
        traffic_pattern="STEADY",
        demand_profile="LOW",
        hotspot_ratio=Decimal("0.2000"),
        hotspot_seat_ids=[101],
        sample_interval_sec=2,
        simulated_work_ms=75,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=4101,
        code_version="pytest",
        started_at=now - timedelta(
            seconds=120
        ),
        ended_at=now,
        created_by="pytest",
    )

    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    return experiment


def _create_metric_sample(
    session: Session,
    *,
    is_complete: bool = True,
) -> MetricSample:
    """Create one stored metric sample."""

    experiment = _create_experiment(
        session
    )

    metric_sample = MetricSample(
        id=uuid4(),
        experiment_run_id=experiment.id,
        sampled_at=(
            datetime.now(UTC)
            - timedelta(minutes=5)
        ),
        active_transactions=5,
        blocked_sessions=2,
        waiting_locks=2,
        total_locks=20,
        total_connections=15,
        connection_utilization=Decimal(
            "0.500000"
        ),
        estimated_wait_age_ms=Decimal(
            "125.000"
        ),
        deadlock_delta=0,
        configured_users=8,
        active_users=7,
        requests_per_sec=Decimal(
            "6.500"
        ),
        mean_latency_ms=Decimal(
            "180.000"
        ),
        p95_latency_ms=Decimal(
            "350.000"
        ),
        failure_rate=Decimal(
            "0.010000"
        ),
        hotspot_ratio=Decimal(
            "0.2000"
        ),
        booking_success_rate=Decimal(
            "0.950000"
        ),
        conflict_rate=Decimal(
            "0.040000"
        ),
        timeout_rate=Decimal(
            "0.010000"
        ),
        is_complete=is_complete,
        quality_notes=[],
        collector_version="pytest",
    )

    session.add(metric_sample)
    session.commit()
    session.refresh(metric_sample)

    return metric_sample


def _create_active_model(
    session: Session,
) -> ModelVersion:
    """Create one valid active model in the test database."""

    specification = build_registration_spec()

    model_version = ModelVersion(
        id=uuid4(),
        name=specification.name,
        algorithm=specification.algorithm,
        feature_schema=dict(
            specification.feature_schema
        ),
        target_horizon_sec=(
            specification.target_horizon_sec
        ),
        training_run_ids=list(
            specification.training_run_ids
        ),
        validation_strategy=(
            specification.validation_strategy
        ),
        risk_thresholds=dict(
            specification.risk_thresholds
        ),
        evaluation_metrics=dict(
            specification.evaluation_metrics
        ),
        artifact_path=specification.artifact_path,
        artifact_sha256=(
            specification.artifact_sha256
        ),
        code_version=specification.code_version,
        is_active=True,
    )

    session.add(model_version)
    session.commit()
    session.refresh(model_version)

    return model_version


def test_prediction_creation_requires_internal_token(
    client: TestClient,
) -> None:
    """Reject creation without internal authentication."""

    response = client.post(
        (
            "/api/v1/predictions/"
            f"metric-samples/{uuid4()}"
        ),
    )

    assert response.status_code == 401

    response_body = response.json()

    assert response_body["detail"]["code"] == (
        "INVALID_INTERNAL_TOKEN"
    )


def test_prediction_reads_require_admin_token(
    client: TestClient,
) -> None:
    """Reject retrieval without administrator authentication."""

    prediction_response = client.get(
        f"/api/v1/predictions/{uuid4()}"
    )

    sample_response = client.get(
        (
            "/api/v1/predictions/"
            f"metric-samples/{uuid4()}"
        )
    )

    assert prediction_response.status_code == 401
    assert sample_response.status_code == 401

    assert (
        prediction_response.json()[
            "detail"
        ]["code"]
        == "INVALID_ADMIN_TOKEN"
    )

    assert (
        sample_response.json()[
            "detail"
        ]["code"]
        == "INVALID_ADMIN_TOKEN"
    )


def test_create_and_read_prediction_through_api(
    client: TestClient,
    db_session: Session,
) -> None:
    """Create and retrieve a persisted prediction."""

    metric_sample = _create_metric_sample(
        db_session
    )

    active_model = _create_active_model(
        db_session
    )

    create_response = client.post(
        (
            "/api/v1/predictions/"
            f"metric-samples/{metric_sample.id}"
        ),
        headers=_internal_headers(),
    )

    assert create_response.status_code == 200

    created_prediction = (
        create_response.json()
    )

    prediction_id = created_prediction["id"]

    assert created_prediction[
        "metric_sample_id"
    ] == str(metric_sample.id)

    assert created_prediction[
        "model_version_id"
    ] == str(active_model.id)

    assert created_prediction[
        "is_stale"
    ] is True

    assert "STALE_TARGET_WINDOW" in (
        created_prediction["quality_flags"]
    )

    assert created_prediction[
        "actual_label"
    ] is None

    assert Decimal(
        str(
            created_prediction[
                "threshold_used"
            ]
        )
    ) == Decimal("0.340000")

    read_by_id_response = client.get(
        (
            "/api/v1/predictions/"
            f"{prediction_id}"
        ),
        headers=_admin_headers(),
    )

    assert read_by_id_response.status_code == 200

    assert (
        read_by_id_response.json()["id"]
        == prediction_id
    )

    read_by_sample_response = client.get(
        (
            "/api/v1/predictions/"
            f"metric-samples/{metric_sample.id}"
        ),
        headers=_admin_headers(),
    )

    assert (
        read_by_sample_response.status_code
        == 200
    )

    assert (
        read_by_sample_response.json()["id"]
        == prediction_id
    )


def test_prediction_creation_is_idempotent(
    client: TestClient,
    db_session: Session,
) -> None:
    """Return one stored row for repeated requests."""

    metric_sample = _create_metric_sample(
        db_session
    )

    _create_active_model(
        db_session
    )

    path = (
        "/api/v1/predictions/"
        f"metric-samples/{metric_sample.id}"
    )

    first_response = client.post(
        path,
        headers=_internal_headers(),
    )

    second_response = client.post(
        path,
        headers=_internal_headers(),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    assert (
        first_response.json()["id"]
        == second_response.json()["id"]
    )

    prediction_count = db_session.scalar(
        select(func.count())
        .select_from(Prediction)
        .where(
            Prediction.metric_sample_id
            == metric_sample.id
        )
    )

    assert prediction_count == 1


def test_missing_metric_sample_returns_structured_error(
    client: TestClient,
) -> None:
    """Return a structured missing-sample error."""

    response = client.post(
        (
            "/api/v1/predictions/"
            f"metric-samples/{uuid4()}"
        ),
        headers=_internal_headers(),
    )

    assert response.status_code == 404

    assert response.json()["detail"]["code"] == (
        "METRIC_SAMPLE_NOT_FOUND"
    )


def test_incomplete_metric_sample_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Reject incomplete telemetry through the API."""

    metric_sample = _create_metric_sample(
        db_session,
        is_complete=False,
    )

    _create_active_model(
        db_session
    )

    response = client.post(
        (
            "/api/v1/predictions/"
            f"metric-samples/{metric_sample.id}"
        ),
        headers=_internal_headers(),
    )

    assert response.status_code == 409

    assert response.json()["detail"]["code"] == (
        "METRIC_SAMPLE_INCOMPLETE"
    )


def test_missing_prediction_returns_structured_error(
    client: TestClient,
) -> None:
    """Return a structured missing-prediction error."""

    response = client.get(
        f"/api/v1/predictions/{uuid4()}",
        headers=_admin_headers(),
    )

    assert response.status_code == 404

    assert response.json()["detail"]["code"] == (
        "PREDICTION_NOT_FOUND"
    )


def test_malformed_prediction_uuid_is_rejected(
    client: TestClient,
) -> None:
    """Let FastAPI reject malformed path UUIDs."""

    response = client.get(
        "/api/v1/predictions/not-a-uuid",
        headers=_admin_headers(),
    )

    assert response.status_code == 422