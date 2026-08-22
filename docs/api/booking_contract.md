# Booking API Contract

Status: Implementation baseline for Phase 3

## API-BOOK-001: Create a booking

### Endpoint

`POST /api/v1/bookings`

### Authentication headers

- `X-Internal-Token`: required internal API token
- `Idempotency-Key`: required UUID identifying one logical request

A missing or invalid internal token returns `401 Unauthorized`.

### Request body

    {
      "user_id": 1,
      "show_id": 1,
      "seat_id": 1,
      "experiment_run_id": null
    }

| Field | Type | Required | Validation |
|---|---|---:|---|
| `user_id` | Positive integer | Yes | Must identify an existing synthetic user |
| `show_id` | Positive integer | Yes | Must identify an existing show |
| `seat_id` | Positive integer | Yes | Must identify a seat belonging to the show |
| `experiment_run_id` | UUID or null | No | Must identify a running experiment when supplied |

### Successful creation

A newly created booking returns `201 Created`.

The response contains:

    {
      "id": "booking-uuid",
      "idempotency_key": "request-uuid",
      "user_id": 1,
      "show_id": 1,
      "seat_id": 1,
      "experiment_run_id": null,
      "status": "CONFIRMED",
      "booked_at": "2030-01-01T10:00:00Z",
      "cancelled_at": null,
      "created_at": "2030-01-01T10:00:00Z",
      "updated_at": "2030-01-01T10:00:00Z"
    }

### Idempotency behaviour

- A new idempotency key creates a booking.
- Repeating the same key with the same request returns the existing booking.
- An identical replay returns `200 OK`.
- An identical replay includes `Idempotent-Replayed: true`.
- Reusing the key with different request data returns `409 Conflict`.

### Validation sequence

1. Validate the internal API token.
2. Validate the idempotency-key format.
3. Validate the request-body types.
4. Search for an existing booking with the idempotency key.
5. Validate the synthetic user.
6. Validate the show.
7. Validate the optional experiment run.
8. Lock the seat using `SELECT FOR UPDATE`.
9. Confirm that the seat belongs to the show.
10. Recheck the seat state after obtaining the lock.
11. Update the seat and insert the booking.
12. Record the booking attempt.
13. Commit the transaction.
14. Return the response.

### Normal booking rules

When `experiment_run_id` is null:

- The show must not be an experiment show.
- The seat must belong to the requested show.
- The seat must be available.
- The seat state becomes `BOOKED`.
- The seat version increases by one.
- The seat hold-expiry value becomes null.

### Experiment booking rules

When `experiment_run_id` is supplied:

- `experiment_mode_enabled` must be true.
- The experiment must exist.
- The experiment status must be `RUNNING`.
- The experiment must belong to the requested show.
- The show must be marked as an experiment show.
- Experiment recycling behaviour will be added during the workload phase.

### Seat-state rules

- `AVAILABLE`: the seat may be booked.
- `BOOKED`: return `409 SEAT_UNAVAILABLE`.
- `HELD` with a future expiry: return `409 SEAT_UNAVAILABLE`.
- `HELD` with an expired expiry: release it while holding the row lock and continue.

### Transaction requirements

The following operations must happen in one database transaction:

1. Lock the seat row.
2. Revalidate the seat.
3. Change the seat state to `BOOKED`.
4. Clear `hold_expires_at`.
5. Increment the seat version.
6. Insert one confirmed booking.
7. Insert one successful booking attempt.
8. Commit once.

If any operation fails:

- Roll back the booking transaction.
- Do not leave a partially created booking.
- Do not leave an incorrectly modified seat.
- Record the failed attempt in a separate transaction.

### Error response format

    {
      "detail": {
        "code": "SEAT_UNAVAILABLE",
        "message": "The requested seat is not available."
      }
    }

### Error mapping

| HTTP status | Error code | Meaning |
|---:|---|---|
| 401 | `INVALID_INTERNAL_TOKEN` | Internal token is missing or invalid |
| 404 | `USER_NOT_FOUND` | Synthetic user does not exist |
| 404 | `SHOW_NOT_FOUND` | Show does not exist |
| 404 | `SEAT_NOT_FOUND` | Seat does not exist |
| 404 | `EXPERIMENT_NOT_FOUND` | Experiment does not exist |
| 403 | `EXPERIMENT_MODE_DISABLED` | Experiment booking is disabled |
| 409 | `SEAT_SHOW_MISMATCH` | Seat does not belong to the show |
| 409 | `EXPERIMENT_SHOW_MISMATCH` | Experiment does not belong to the show |
| 409 | `EXPERIMENT_NOT_RUNNING` | Experiment is not running |
| 409 | `SEAT_UNAVAILABLE` | Seat is held or booked |
| 409 | `IDEMPOTENCY_KEY_REUSED` | Key was used with different request data |
| 503 | `LOCK_TIMEOUT` | PostgreSQL row lock was not acquired in time |
| 503 | `DATABASE_TIMEOUT` | PostgreSQL statement timed out |
| 500 | `INTERNAL_ERROR` | Unexpected backend failure |

### Booking-attempt outcome mapping

| Situation | Attempt outcome |
|---|---|
| Booking created | `SUCCESS` |
| Identical idempotent replay | `SUCCESS` |
| Seat held or booked | `SEAT_UNAVAILABLE` |
| Lock acquisition timeout | `LOCK_TIMEOUT` |
| Invalid request or missing entity | `VALIDATION_ERROR` |
| Unexpected backend failure | `INTERNAL_ERROR` |

## Recommended assumptions

These rules are implementation defaults and are not externally confirmed business requirements:

- The booking endpoint is an internal workload-simulation endpoint.
- Normal booking requests cannot target the experiment show.
- Only an available or expired-held seat can be booked.
- Identical idempotent requests return the original booking.
- The service validates that the seat belongs to the show.
- PostgreSQL lock timeout is 2 seconds.
- PostgreSQL statement timeout is 5 seconds.

## Data-integrity notes

The database has independent foreign keys for `bookings.show_id` and
`bookings.seat_id`, but it does not directly guarantee that the selected seat
belongs to the selected show. The service must enforce this rule.

`booking_attempts.user_id`, `show_id`, and `seat_id` intentionally do not use
foreign keys. This allows invalid requests to be retained as experimental
telemetry.