# Experiment Lifecycle API Contract

Status: Implementation baseline for the controlled-workload phase

## Purpose

An experiment run defines one reproducible workload configuration used to
generate PostgreSQL lock-contention telemetry and machine-learning data.

An experiment is not a customer-facing ticket-booking event. It controls how
synthetic clients create reservation pressure against the designated
experiment show.

## Authentication

All experiment-management endpoints require:

`X-Admin-Token: <configured admin API token>`

A missing or invalid token returns `401 Unauthorized`.

The admin token must be stored only in `.env`.

## Experimental integrity rule

[RECOMMENDED DEFAULT]

Only one experiment may have `RUNNING` status across the complete local
PostgreSQL instance.

Reason: PostgreSQL lock, connection and transaction metrics are collected at
database level. Allowing multiple experiments to run simultaneously would mix
their measurements and make experiment attribution unreliable.

This rule must be enforced by both application validation and a database
constraint.

## Experiment statuses

Allowed statuses:

- `DRAFT`
- `RUNNING`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

## State transitions

Allowed transitions:

- `DRAFT -> RUNNING`
- `DRAFT -> CANCELLED`
- `RUNNING -> COMPLETED`
- `RUNNING -> FAILED`
- `RUNNING -> CANCELLED`

The following statuses are terminal:

- `COMPLETED`
- `FAILED`
- `CANCELLED`

A terminal experiment cannot be restarted.

Repeating a transition that has already completed returns the current
experiment without creating another audit event.

Any other invalid transition returns `409 Conflict`.

## Configuration immutability

The workload configuration becomes immutable when an experiment starts.

The following values cannot change after creation:

- show
- user count
- spawn rate
- duration
- hotspot ratio
- hotspot seat IDs
- sampling interval
- simulated work duration
- booking mode
- random seed
- code version

A new configuration requires a new experiment run.

Reason: changing configuration during a run would make the resulting dataset
irreproducible.

## API-EXP-001: Create an experiment

### Endpoint

`POST /api/v1/experiments`

### Request body

```json
{
  "name": "Hotspot contention baseline",
  "show_id": 2,
  "user_count": 100,
  "spawn_rate_per_sec": 20.0,
  "duration_sec": 300,
  "hotspot_ratio": 0.8,
  "hotspot_seat_ids": [101, 102, 103, 104, 105],
  "sample_interval_sec": 2,
  "simulated_work_ms": 200,
  "booking_mode": "EXPERIMENT_RECYCLE",
  "random_seed": 42,
  "code_version": "git-commit-hash",
  "created_by": "local-admin"
}
```

### Validation

- `name` must contain 1 to 100 characters.
- `show_id` must identify an existing experiment show.
- `user_count` must be between 1 and 2000.
- `spawn_rate_per_sec` must be greater than zero.
- `duration_sec` must be between 30 and 3600.
- `hotspot_ratio` must be between 0 and 1.
- `hotspot_seat_ids` must contain at least one unique seat ID.
- Every hotspot seat must belong to the selected experiment show.
- `sample_interval_sec` must be between 1 and 10.
- `simulated_work_ms` must be between 0 and 1000.
- `booking_mode` must be `NORMAL` or `EXPERIMENT_RECYCLE`.
- `created_by` must contain 1 to 100 characters.
- `code_version` is optional and limited to 64 characters.

### Result

A successful request creates a `DRAFT` experiment and returns
`201 Created`.

Creation does not start the workload.

## API-EXP-002: List experiments

### Endpoint

`GET /api/v1/experiments`

### Behaviour

Return experiments ordered by `created_at` descending.

Optional status filter:

`GET /api/v1/experiments?status=COMPLETED`

## API-EXP-003: Get one experiment

### Endpoint

`GET /api/v1/experiments/{experiment_run_id}`

Return `404 Not Found` when the experiment does not exist.

## API-EXP-004: Start an experiment

### Endpoint

`POST /api/v1/experiments/{experiment_run_id}/start`

### Request body

```json
{
  "actor": "local-admin"
}
```

### Validation

- Experiment mode must be enabled.
- The experiment must currently be `DRAFT`.
- No other experiment may currently be `RUNNING`.
- The selected show must still be an experiment show.
- Every configured hotspot seat must still exist and belong to the show.
- All seats belonging to the experiment show must be `AVAILABLE`.
- No collector or workload process is started by this API call itself.

### Database changes

- Change status to `RUNNING`.
- Set `started_at` to the current UTC timestamp.
- Keep `ended_at` null.
- Create one `experiment_started` audit event.
- Commit all changes in one transaction.

## API-EXP-005: Complete an experiment

### Endpoint

`POST /api/v1/experiments/{experiment_run_id}/complete`

### Request body

```json
{
  "actor": "experiment-runner"
}
```

### Validation

The experiment must currently be `RUNNING`.

### Database changes

- Change status to `COMPLETED`.
- Set `ended_at` to the current UTC timestamp.
- Create one `experiment_completed` audit event.
- Commit all changes in one transaction.

## API-EXP-006: Fail an experiment

### Endpoint

`POST /api/v1/experiments/{experiment_run_id}/fail`

### Request body

```json
{
  "actor": "experiment-runner",
  "reason": "Metrics collector stopped unexpectedly."
}
```

### Validation

The experiment must currently be `RUNNING`.

### Database changes

- Change status to `FAILED`.
- Set `ended_at` to the current UTC timestamp.
- Store the failure reason in the audit-event details.
- Create one `experiment_failed` audit event.
- Commit all changes in one transaction.

## API-EXP-007: Cancel an experiment

### Endpoint

`POST /api/v1/experiments/{experiment_run_id}/cancel`

### Request body

```json
{
  "actor": "local-admin",
  "reason": "Experiment cancelled manually."
}
```

### Validation

The experiment must currently be `DRAFT` or `RUNNING`.

### Database changes

- Change status to `CANCELLED`.
- Set `ended_at` when cancelling a running experiment.
- Create one `experiment_cancelled` audit event.
- Commit all changes in one transaction.

## Experiment booking modes

### NORMAL

Normal reservation behaviour:

- A successful booking leaves the seat `BOOKED`.
- The seat cannot be successfully booked again.
- This mode is useful for correctness demonstrations and short experiments.
- It is unsuitable for long sustained workloads with a small number of seats.

### EXPERIMENT_RECYCLE

[RECOMMENDED DEFAULT]

Controlled experimental behaviour:

1. Lock the selected seat using `SELECT FOR UPDATE`.
2. Hold the lock for `simulated_work_ms`.
3. Record the successful or failed attempt.
4. Return the seat to `AVAILABLE` before committing.
5. Increment the seat version.
6. Preserve attempt telemetry for the experiment.

Experiment recycling is permitted only when:

- Experiment mode is enabled.
- `experiment_run_id` is supplied.
- The experiment is `RUNNING`.
- `booking_mode` is `EXPERIMENT_RECYCLE`.
- The show is marked as an experiment show.

The normal customer-booking path must never recycle a seat.

Reason: recycling allows repeated transactions to compete for the same
hotspot seats throughout a controlled experiment without permanently
exhausting the experiment inventory.

## Audit events

The lifecycle must create these actions:

- `experiment_created`
- `experiment_started`
- `experiment_completed`
- `experiment_failed`
- `experiment_cancelled`

Each event records:

- experiment run ID
- actor
- resource type
- resource ID
- structured details
- creation timestamp

## Error responses

Errors use:

```json
{
  "detail": {
    "code": "ERROR_CODE",
    "message": "Human-readable explanation."
  }
}
```

Required errors:

| HTTP status | Error code | Meaning |
| --- | --- | --- |
| 401 | `INVALID_ADMIN_TOKEN` | Missing or invalid admin token |
| 404 | `EXPERIMENT_NOT_FOUND` | Experiment does not exist |
| 404 | `SHOW_NOT_FOUND` | Show does not exist |
| 404 | `HOTSPOT_SEAT_NOT_FOUND` | One or more hotspot seats do not exist |
| 409 | `SHOW_NOT_EXPERIMENTAL` | Selected show is not an experiment show |
| 409 | `HOTSPOT_SEAT_SHOW_MISMATCH` | Hotspot seat belongs to another show |
| 409 | `EXPERIMENT_ALREADY_RUNNING` | Another experiment is running |
| 409 | `INVALID_EXPERIMENT_TRANSITION` | Requested status transition is invalid |
| 409 | `EXPERIMENT_SEATS_NOT_READY` | Experiment-show seats are not available |
| 403 | `EXPERIMENT_MODE_DISABLED` | Experiment execution is disabled |
| 422 | FastAPI validation response | Request schema is invalid |
| 500 | `INTERNAL_ERROR` | Unexpected database or server failure |

## Transaction requirements

Every lifecycle operation must:

1. Validate the current database state.
2. Apply the state change.
3. Insert the audit event.
4. Commit both operations together.
5. Roll back both operations if either fails.

## Traceability requirements

Every `booking_attempt`, `metric_sample`, `prediction`, audit event and dataset
export produced by an experiment must reference the same
`experiment_run_id`.

This ID is the link between workload configuration, measured data, model
inputs, predictions and exported results.
