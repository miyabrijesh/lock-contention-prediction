from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, cast

from sqlalchemy import text
from sqlalchemy.orm import Session


POSTGRESQL_METRICS_QUERY = text(
    """
    WITH activity AS (
        SELECT
            pid,
            state,
            wait_event_type,
            xact_start,
            query_start
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
    ),
    activity_summary AS (
        SELECT
            COUNT(*) FILTER (
                WHERE xact_start IS NOT NULL
            )::integer AS active_transactions,
            COUNT(*) FILTER (
                WHERE wait_event_type = 'Lock'
            )::integer AS blocked_sessions,
            COUNT(*)::integer AS total_connections,
            COALESCE(
                ROUND(
                    (
                        MAX(
                            EXTRACT(
                                EPOCH FROM (
                                    clock_timestamp()
                                    - COALESCE(
                                        xact_start,
                                        query_start
                                    )
                                )
                            ) * 1000
                        ) FILTER (
                            WHERE wait_event_type = 'Lock'
                        )
                    )::numeric,
                    3
                ),
                0
            ) AS estimated_wait_age_ms
        FROM activity
    ),
    lock_summary AS (
        SELECT
            COUNT(*)::integer AS total_locks,
            COUNT(*) FILTER (
                WHERE NOT database_lock.granted
            )::integer AS waiting_locks
        FROM pg_locks AS database_lock
        JOIN activity
          ON activity.pid = database_lock.pid
    ),
    database_stats AS (
        SELECT deadlocks
        FROM pg_stat_database
        WHERE datname = current_database()
    )
    SELECT
        clock_timestamp() AS sampled_at,
        activity_summary.active_transactions,
        activity_summary.blocked_sessions,
        lock_summary.waiting_locks,
        lock_summary.total_locks,
        activity_summary.total_connections,
        ROUND(
            activity_summary.total_connections::numeric
            / current_setting('max_connections')::numeric,
            6
        ) AS connection_utilization,
        activity_summary.estimated_wait_age_ms,
        database_stats.deadlocks AS cumulative_deadlocks
    FROM activity_summary
    CROSS JOIN lock_summary
    CROSS JOIN database_stats
    """
)


@dataclass(frozen=True)
class PostgreSQLMetrics:
    """One instantaneous PostgreSQL lock-contention snapshot."""

    sampled_at: datetime
    active_transactions: int
    blocked_sessions: int
    waiting_locks: int
    total_locks: int
    total_connections: int
    connection_utilization: Decimal
    estimated_wait_age_ms: Decimal
    cumulative_deadlocks: int


class PostgreSQLMetricsError(Exception):
    """Raised when a PostgreSQL snapshot cannot be interpreted."""


def _required_value(
    row: Mapping[str, object],
    field_name: str,
) -> object:
    """Return one required query value."""

    value = row.get(field_name)

    if value is None:
        raise PostgreSQLMetricsError(
            f"PostgreSQL did not return {field_name}."
        )

    return value


def _to_integer(
    row: Mapping[str, object],
    field_name: str,
) -> int:
    """Validate one required PostgreSQL integer value."""

    value = _required_value(
        row,
        field_name,
    )

    if isinstance(value, bool) or not isinstance(value, int):
        raise PostgreSQLMetricsError(
            f"PostgreSQL returned an invalid {field_name} value."
        )

    return value


def _to_decimal(
    row: Mapping[str, object],
    field_name: str,
) -> Decimal:
    """Convert one required PostgreSQL value to Decimal."""

    value = _required_value(
        row,
        field_name,
    )

    try:
        return Decimal(str(value))
    except Exception as error:
        raise PostgreSQLMetricsError(
            f"PostgreSQL returned an invalid {field_name} value."
        ) from error


def _to_datetime(
    row: Mapping[str, object],
    field_name: str,
) -> datetime:
    """Validate one required timestamp."""

    value = _required_value(
        row,
        field_name,
    )

    if not isinstance(value, datetime):
        raise PostgreSQLMetricsError(
            f"PostgreSQL returned an invalid {field_name} timestamp."
        )

    return value


def metrics_from_mapping(
    row: Mapping[str, object],
) -> PostgreSQLMetrics:
    """Convert a SQL result mapping into a typed snapshot."""

    metrics = PostgreSQLMetrics(
        sampled_at=_to_datetime(
            row,
            "sampled_at",
        ),
        active_transactions=_to_integer(
            row,
            "active_transactions",
        ),
        blocked_sessions=_to_integer(
            row,
            "blocked_sessions",
        ),
        waiting_locks=_to_integer(
            row,
            "waiting_locks",
        ),
        total_locks=_to_integer(
            row,
            "total_locks",
        ),
        total_connections=_to_integer(
            row,
            "total_connections",
        ),
        connection_utilization=_to_decimal(
            row,
            "connection_utilization",
        ),
        estimated_wait_age_ms=_to_decimal(
            row,
            "estimated_wait_age_ms",
        ),
        cumulative_deadlocks=_to_integer(
            row,
            "cumulative_deadlocks",
        ),
    )

    integer_measurements = (
        metrics.active_transactions,
        metrics.blocked_sessions,
        metrics.waiting_locks,
        metrics.total_locks,
        metrics.total_connections,
        metrics.cumulative_deadlocks,
    )

    if any(value < 0 for value in integer_measurements):
        raise PostgreSQLMetricsError(
            "PostgreSQL returned a negative count."
        )

    if metrics.estimated_wait_age_ms < 0:
        raise PostgreSQLMetricsError(
            "PostgreSQL returned a negative wait age."
        )

    if not (
        Decimal("0")
        <= metrics.connection_utilization
        <= Decimal("1")
    ):
        raise PostgreSQLMetricsError(
            "Connection utilization must be between zero and one."
        )

    return metrics


def collect_postgresql_metrics(
    session: Session,
) -> PostgreSQLMetrics:
    """Collect one live PostgreSQL metrics snapshot."""

    result = session.execute(
        POSTGRESQL_METRICS_QUERY
    )

    row = result.mappings().one()

    return metrics_from_mapping(
    cast(
        Mapping[str, object],
        row,
    )
)