from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Mapping, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


APPLICATION_METRICS_QUERY = text(
    """
    SELECT
        COUNT(*)::integer AS request_count,
        COUNT(DISTINCT user_id)::integer AS active_users,
        COALESCE(
            ROUND(
                AVG(latency_ms),
                3
            ),
            0
        ) AS mean_latency_ms,
        COALESCE(
            ROUND(
                (
                    percentile_cont(0.95)
                    WITHIN GROUP (
                        ORDER BY latency_ms
                    )
                )::numeric,
                3
            ),
            0
        ) AS p95_latency_ms,
        COUNT(*) FILTER (
            WHERE outcome = 'SUCCESS'
        )::integer AS successful_requests,
        COUNT(*) FILTER (
            WHERE outcome = 'SEAT_UNAVAILABLE'
        )::integer AS conflicting_requests,
        COUNT(*) FILTER (
            WHERE outcome = 'LOCK_TIMEOUT'
        )::integer AS timeout_requests,
        COUNT(*) FILTER (
            WHERE seat_id = ANY(
                CAST(
                    :hotspot_seat_ids
                    AS BIGINT[]
                )
            )
        )::integer AS hotspot_requests
    FROM booking_attempts
    WHERE experiment_run_id = :experiment_run_id
      AND requested_at >= :window_started_at
      AND requested_at < :window_ended_at
    """
)


@dataclass(frozen=True)
class ApplicationMetrics:
    """Booking workload measurements for one sample window."""

    request_count: int
    active_users: int
    requests_per_sec: Decimal
    mean_latency_ms: Decimal
    p95_latency_ms: Decimal
    failure_rate: Decimal
    hotspot_ratio: Decimal
    booking_success_rate: Decimal
    conflict_rate: Decimal
    timeout_rate: Decimal
    quality_notes: tuple[str, ...]


class ApplicationMetricsError(Exception):
    """Raised when application telemetry cannot be interpreted."""


def _required_value(
    row: Mapping[str, object],
    field_name: str,
) -> object:
    """Return one required query value."""

    value = row.get(field_name)

    if value is None:
        raise ApplicationMetricsError(
            f"Application telemetry did not return {field_name}."
        )

    return value


def _to_integer(
    row: Mapping[str, object],
    field_name: str,
) -> int:
    """Validate one application count."""

    value = _required_value(
        row,
        field_name,
    )

    if isinstance(value, bool) or not isinstance(value, int):
        raise ApplicationMetricsError(
            f"Application telemetry returned an invalid "
            f"{field_name} value."
        )

    if value < 0:
        raise ApplicationMetricsError(
            f"Application telemetry returned a negative "
            f"{field_name} value."
        )

    return value


def _to_decimal(
    row: Mapping[str, object],
    field_name: str,
) -> Decimal:
    """Convert one application measurement to Decimal."""

    value = _required_value(
        row,
        field_name,
    )

    try:
        converted_value = Decimal(str(value))
    except Exception as error:
        raise ApplicationMetricsError(
            f"Application telemetry returned an invalid "
            f"{field_name} value."
        ) from error

    if converted_value < 0:
        raise ApplicationMetricsError(
            f"Application telemetry returned a negative "
            f"{field_name} value."
        )

    return converted_value


def _calculate_rate(
    numerator: int,
    denominator: int,
    *,
    decimal_places: int,
) -> Decimal:
    """Calculate a bounded ratio using a fixed decimal scale."""

    if denominator == 0:
        return Decimal("0")

    rate = (
        Decimal(numerator)
        / Decimal(denominator)
    )

    quantizer = Decimal("1").scaleb(
        -decimal_places
    )

    return rate.quantize(
        quantizer,
        rounding=ROUND_HALF_UP,
    )


def application_metrics_from_mapping(
    row: Mapping[str, object],
    *,
    window_duration_seconds: Decimal,
) -> ApplicationMetrics:
    """Convert an aggregate query result into typed metrics."""

    if window_duration_seconds <= 0:
        raise ApplicationMetricsError(
            "The collection window duration must be positive."
        )

    request_count = _to_integer(
        row,
        "request_count",
    )
    active_users = _to_integer(
        row,
        "active_users",
    )
    successful_requests = _to_integer(
        row,
        "successful_requests",
    )
    conflicting_requests = _to_integer(
        row,
        "conflicting_requests",
    )
    timeout_requests = _to_integer(
        row,
        "timeout_requests",
    )
    hotspot_requests = _to_integer(
        row,
        "hotspot_requests",
    )

    mean_latency_ms = _to_decimal(
        row,
        "mean_latency_ms",
    )
    p95_latency_ms = _to_decimal(
        row,
        "p95_latency_ms",
    )

    if successful_requests > request_count:
        raise ApplicationMetricsError(
            "Successful requests exceed total requests."
        )

    if conflicting_requests > request_count:
        raise ApplicationMetricsError(
            "Conflicting requests exceed total requests."
        )

    if timeout_requests > request_count:
        raise ApplicationMetricsError(
            "Timeout requests exceed total requests."
        )

    if hotspot_requests > request_count:
        raise ApplicationMetricsError(
            "Hotspot requests exceed total requests."
        )

    failed_requests = (
        request_count - successful_requests
    )

    requests_per_sec = (
        Decimal(request_count)
        / window_duration_seconds
    ).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )

    quality_notes: list[str] = []

    if request_count == 0:
        quality_notes.append(
            "NO_REQUESTS_IN_INTERVAL"
        )

    return ApplicationMetrics(
        request_count=request_count,
        active_users=active_users,
        requests_per_sec=requests_per_sec,
        mean_latency_ms=mean_latency_ms,
        p95_latency_ms=p95_latency_ms,
        failure_rate=_calculate_rate(
            failed_requests,
            request_count,
            decimal_places=6,
        ),
        hotspot_ratio=_calculate_rate(
            hotspot_requests,
            request_count,
            decimal_places=4,
        ),
        booking_success_rate=_calculate_rate(
            successful_requests,
            request_count,
            decimal_places=6,
        ),
        conflict_rate=_calculate_rate(
            conflicting_requests,
            request_count,
            decimal_places=6,
        ),
        timeout_rate=_calculate_rate(
            timeout_requests,
            request_count,
            decimal_places=6,
        ),
        quality_notes=tuple(quality_notes),
    )


def collect_application_metrics(
    session: Session,
    *,
    experiment_run_id: UUID,
    hotspot_seat_ids: tuple[int, ...],
    window_started_at: datetime,
    window_ended_at: datetime,
) -> ApplicationMetrics:
    """Collect booking telemetry for one experiment window."""

    window_duration_seconds = Decimal(
        str(
            (
                window_ended_at
                - window_started_at
            ).total_seconds()
        )
    )

    if window_duration_seconds <= 0:
        raise ApplicationMetricsError(
            "The application metrics window must be positive."
        )

    result = session.execute(
        APPLICATION_METRICS_QUERY,
        {
            "experiment_run_id": experiment_run_id,
            "hotspot_seat_ids": list(
                hotspot_seat_ids
            ),
            "window_started_at": window_started_at,
            "window_ended_at": window_ended_at,
        },
    )

    row = result.mappings().one()

    return application_metrics_from_mapping(
        cast(
            Mapping[str, object],
            row,
        ),
        window_duration_seconds=(
            window_duration_seconds
        ),
    )