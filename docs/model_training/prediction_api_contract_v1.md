# Prediction API Contract v1

## Status

Checkpoint 37A implementation baseline.

## Purpose

This contract defines authenticated API access to persisted future-contention
predictions.

The API permits internal application processes to request prediction creation
for an existing metric sample. It permits administrators to retrieve stored
prediction records.

API clients cannot submit arbitrary model features or artifact paths.

## Endpoint summary

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| `POST` | `/api/v1/predictions/metric-samples/{metric_sample_id}` | Internal token | Create or return a prediction |
| `GET` | `/api/v1/predictions/metric-samples/{metric_sample_id}` | Admin token | Read prediction for a sample |
| `GET` | `/api/v1/predictions/{prediction_id}` | Admin token | Read one prediction |

## Create or return a prediction

```http
POST /api/v1/predictions/metric-samples/{metric_sample_id}
X-Internal-Token: configured-internal-token
```

The endpoint accepts no request body.

The metric-sample UUID is the only client-supplied prediction input.

The service loads all model features from the stored `MetricSample`.

A successful first request creates and returns one persisted prediction.

A successful repeated request returns the existing prediction.

Both successful cases return:

```text
200 OK
```

The endpoint does not expose whether a retry ran inference because idempotency
guarantees the same stored result.

## Read prediction for a metric sample

```http
GET /api/v1/predictions/metric-samples/{metric_sample_id}
X-Admin-Token: configured-admin-token
```

A successful request returns the prediction associated with the metric sample.

If no prediction exists, the endpoint returns:

```text
404 PREDICTION_NOT_FOUND
```

## Read one prediction

```http
GET /api/v1/predictions/{prediction_id}
X-Admin-Token: configured-admin-token
```

A successful request returns one persisted prediction.

If the prediction does not exist, the endpoint returns:

```text
404 PREDICTION_NOT_FOUND
```

## Route ordering

The metric-sample route must be declared before:

```text
/{prediction_id}
```

This prevents the literal path segment `metric-samples` from being interpreted
as a prediction UUID.

## Prediction response

A successful response contains:

```json
{
  "id": "377ae405-0d10-492c-aabe-9408fc2d33c8",
  "experiment_run_id": "68f73d8f-874f-40ef-807b-9aecd97eeb04",
  "metric_sample_id": "2b8bcd35-0ec7-4bfa-8c9c-b23aa8ba8fa6",
  "model_version_id": "ce84e90c-61f3-4247-bc7f-3737711674d3",
  "predicted_at": "2026-08-26T11:24:14.262955+05:30",
  "target_window_start": "2026-08-25T15:02:58.346369+05:30",
  "target_window_end": "2026-08-25T15:03:28.346369+05:30",
  "probability": "0.961174",
  "risk_level": "HIGH",
  "threshold_used": "0.340000",
  "is_stale": true,
  "quality_flags": [
    "STALE_TARGET_WINDOW"
  ],
  "actual_label": null
}
```

Decimal fields may be represented as JSON strings by the API serialization
layer to preserve exact six-decimal values.

## Authentication

### Prediction creation

Prediction creation requires:

```text
X-Internal-Token
```

Missing or invalid authentication returns:

```text
401 INVALID_INTERNAL_TOKEN
```

This token is intended for trusted internal processes such as the metrics
collector or synchronized experiment runner.

### Prediction retrieval

Prediction retrieval requires:

```text
X-Admin-Token
```

Missing or invalid authentication returns:

```text
401 INVALID_ADMIN_TOKEN
```

Administrative retrieval is intentionally separate from internal automated
creation.

## Error responses

Expected errors use:

```json
{
  "detail": {
    "code": "METRIC_SAMPLE_NOT_FOUND",
    "message": "The requested metric sample was not found."
  }
}
```

Supported errors include:

| HTTP status | Code | Meaning |
|---:|---|---|
| 401 | `INVALID_INTERNAL_TOKEN` | Prediction creation authentication failed |
| 401 | `INVALID_ADMIN_TOKEN` | Prediction retrieval authentication failed |
| 404 | `METRIC_SAMPLE_NOT_FOUND` | Source sample does not exist |
| 404 | `ACTIVE_MODEL_NOT_FOUND` | No active model exists |
| 404 | `PREDICTION_NOT_FOUND` | Stored prediction does not exist |
| 409 | `METRIC_SAMPLE_INCOMPLETE` | Source sample is incomplete |
| 409 | `MODEL_ARTIFACT_INVALID` | Active artifact failed validation |
| 409 | `MODEL_PROVENANCE_INVALID` | Registered metadata is inconsistent |
| 409 | `PREDICTION_INPUT_INVALID` | Stored features failed validation |
| 409 | `PREDICTION_CONFLICT` | Concurrent persistence conflict |
| 422 | FastAPI validation response | Path UUID is malformed |
| 500 | `INTERNAL_ERROR` | Unexpected internal failure |

Responses must not expose raw exception details, SQL, tokens, absolute
filesystem paths, or serialized model objects.

## Idempotency

The create endpoint is idempotent by `metric_sample_id`.

Repeated successful requests must return the same:

- prediction ID;
- metric-sample ID;
- model-version ID;
- probability;
- risk level;
- threshold;
- target window;
- actual label.

A repeated request must not create a second database row.

## Data-leakage boundary

Prediction creation uses only telemetry available in the source metric sample.

The endpoint must not:

- read future metric samples;
- calculate the future label;
- populate `actual_label`;
- accept a client-provided outcome;
- modify historical model outputs.

`actual_label` remains null until a separate post-window evaluation process is
performed.

## Staleness disclosure

Historical predictions may be created for reproducibility.

When the target window has already ended:

```text
is_stale = true
```

And:

```text
STALE_TARGET_WINDOW
```

must appear in `quality_flags`.

API consumers must not present stale historical predictions as current alerts.

## Security requirements

- Tokens are never included in response bodies.
- Tokens are never written to prediction records.
- Tokens are never written to quality flags.
- Artifact paths are not accepted from clients.
- Feature mappings are not accepted from clients.
- Model IDs are selected from the active database record.
- Prediction creation is transactional.
- Retrieval endpoints are read-only.
- Error responses use safe machine-readable messages.

## Checkpoint 37 completion boundary

Checkpoint 37 is complete when:

- this API contract is versioned;
- authenticated prediction routes are implemented;
- creation requires the internal token;
- retrieval requires the admin token;
- valid stored samples produce API predictions;
- repeated creation is idempotent;
- malformed UUIDs are rejected;
- missing predictions return structured errors;
- incomplete samples are rejected;
- stale status and quality flags are returned;
- API tests pass;
- the complete project test suite passes;
- `git diff --check` reports no whitespace errors.