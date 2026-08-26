# Prediction Persistence Contract v1

## Status

Checkpoint 36A implementation baseline.

## Purpose

This contract defines how one complete telemetry sample is converted into a
future lock-contention prediction and safely stored in PostgreSQL.

A persisted prediction records:

- the source experiment;
- the exact metric sample;
- the active model version;
- the prediction probability;
- the derived risk level;
- the classification threshold;
- the future target window;
- staleness and quality information.

Prediction persistence must preserve enough provenance to reproduce and audit
every result.

## Preconditions

A prediction may be created only when all of the following are true:

1. The requested metric sample exists.
2. The metric sample has `is_complete = true`.
3. Every required inference feature is present and valid.
4. Exactly one registered model has `is_active = true`.
5. The active model artifact exists.
6. The artifact checksum matches the registered SHA-256.
7. The artifact passes the inference contract.
8. The registered feature schema matches the artifact.
9. The registered threshold matches the artifact.
10. The registered target horizon is 30 seconds.

The service must not select the newest inactive model when no model is active.

## Source metric sample

The model consumes these 18 ordered features:

1. `active_transactions`
2. `blocked_sessions`
3. `waiting_locks`
4. `total_locks`
5. `total_connections`
6. `connection_utilization`
7. `estimated_wait_age_ms`
8. `deadlock_delta`
9. `configured_users`
10. `active_users`
11. `requests_per_sec`
12. `mean_latency_ms`
13. `p95_latency_ms`
14. `failure_rate`
15. `hotspot_ratio`
16. `booking_success_rate`
17. `conflict_rate`
18. `timeout_rate`

The existing inference engine remains responsible for exact feature
validation, ordering, numeric conversion, and range checking.

Incomplete samples and samples containing missing required values must not
produce prediction rows.

## Active model selection

The prediction service must query `model_versions` using:

```text
is_active = true
```

The service must fail when no active model exists.

The service must never activate a model as a side effect of prediction.

The active model row must provide:

- model version ID;
- artifact path;
- artifact SHA-256;
- feature schema;
- target horizon;
- risk threshold;
- model name;
- code version.

## Artifact verification

Before inference, the service must:

1. Resolve the registered project-relative artifact path.
2. Reject absolute artifact paths.
3. Reject paths that escape the project root.
4. Require an existing regular file.
5. Verify the file SHA-256 before deserialization.
6. Load the artifact through `ContentionPredictor.load()`.
7. Validate registered provenance against the loaded predictor.

A failure must not create a prediction row.

## Inference result

The current model emits:

| Field | Value |
|---|---|
| Model | `contention-logistic-v1` |
| Target horizon | 30 seconds |
| High-risk threshold | 0.34 |
| Probability range | 0–1 |
| Emitted risk levels | `LOW`, `HIGH` |

Risk classification is:

```text
LOW  when probability < 0.34
HIGH when probability >= 0.34
```

`MEDIUM` and `UNKNOWN` remain reserved values and are not emitted by a
successful version 1 prediction.

## Persistence mapping

One successful inference produces one row in `predictions`.

| Prediction column | Source |
|---|---|
| `experiment_run_id` | `MetricSample.experiment_run_id` |
| `metric_sample_id` | `MetricSample.id` |
| `model_version_id` | Active `ModelVersion.id` |
| `predicted_at` | Database creation time |
| `target_window_start` | `MetricSample.sampled_at` |
| `target_window_end` | `sampled_at + target_horizon_sec` |
| `probability` | Inference probability rounded to six decimal places |
| `risk_level` | Inference risk level |
| `threshold_used` | Inference threshold rounded to six decimal places |
| `is_stale` | Whether the future target window already ended |
| `quality_flags` | Normalized sample and prediction quality flags |
| `actual_label` | `null` until an observed label is attached |

The service must not populate `actual_label` during inference.

## Target window

For a sample taken at time `T` and a 30-second horizon:

```text
target_window_start = T
target_window_end = T + 30 seconds
```

The database requires:

```text
target_window_end > target_window_start
```

The prediction describes future contention inside this exact interval.

## Staleness

A prediction is stale when it is created after the target window has already
ended.

Formally:

```text
is_stale = predicted_at >= target_window_end
```

A stale prediction may be persisted for reproducibility, testing, or historical
backfilling, but it must be explicitly marked.

A stale prediction receives this quality flag:

```text
STALE_TARGET_WINDOW
```

A live prediction created before the target window ends has:

```text
is_stale = false
```

## Quality flags

`quality_flags` is a JSON array of unique strings.

It contains:

1. Existing nonempty strings from `MetricSample.quality_notes`.
2. `STALE_TARGET_WINDOW` when the target window has ended.

Flags must:

- be stripped of surrounding whitespace;
- exclude empty strings;
- contain no duplicate values;
- preserve deterministic ordering.

An otherwise complete live sample with no quality notes uses:

```json
[]
```

## Probability storage

The database stores probability and threshold as:

```text
NUMERIC(7, 6)
```

The service converts floating-point inference output through decimal text and
rounds it to six decimal places.

It must not convert directly from the binary float representation.

Stored values must remain between zero and one.

## Idempotency

`predictions.metric_sample_id` has a unique database constraint.

Therefore, one metric sample can have at most one persisted prediction.

Before running inference, the service must check whether a prediction already
exists for the metric sample.

If one exists:

- return the existing prediction;
- do not run inference again;
- do not create another row;
- do not change its model version;
- do not overwrite its probability;
- do not overwrite its risk level;
- do not overwrite its target window;
- do not overwrite its actual label.

This makes retries safe.

The existing prediction retains the model version that originally produced it,
even if another model later becomes active.

## Transaction sequence

Prediction creation must execute as one transaction:

1. Load and lock the requested metric sample.
2. Reject a missing sample.
3. Return an existing prediction when one is already stored.
4. Reject an incomplete metric sample.
5. Load the active model.
6. Reject the operation when no model is active.
7. Resolve and verify the model artifact.
8. Validate registered model provenance.
9. Run inference from the metric sample.
10. Calculate the target window.
11. Determine staleness.
12. Normalize quality flags.
13. Create the prediction row.
14. Flush the row and enforce database constraints.
15. Commit the transaction.
16. Refresh and return the prediction.

Any failure must roll back the transaction.

## Concurrency

The metric-sample row must be locked before checking for an existing
prediction.

The unique constraint on `metric_sample_id` remains the final concurrency
protection.

If two requests race for the same metric sample:

- at most one prediction row may be stored;
- a retry must return the stored prediction;
- duplicate predictions must not be exposed as successful independent rows.

## Expected service errors

| Error code | Meaning |
|---|---|
| `METRIC_SAMPLE_NOT_FOUND` | The requested metric sample does not exist |
| `METRIC_SAMPLE_INCOMPLETE` | The sample is not valid for inference |
| `ACTIVE_MODEL_NOT_FOUND` | No registered model is active |
| `MODEL_ARTIFACT_INVALID` | Artifact verification or loading failed |
| `MODEL_PROVENANCE_INVALID` | Registered metadata does not match the artifact |
| `PREDICTION_INPUT_INVALID` | Required inference features are invalid |
| `PREDICTION_CONFLICT` | Concurrent persistence could not be safely resolved |
| `INTERNAL_ERROR` | Unexpected database or application failure |

Expected errors must not expose:

- API tokens;
- absolute local paths;
- database connection details;
- raw SQL errors;
- serialized model contents;
- internal exception traces.

## Prediction retrieval

Checkpoint 36 implements internal repository and service operations.

Public prediction endpoints are introduced in Checkpoint 37.

Checkpoint 36 must not expose an unauthenticated prediction endpoint.

## Actual-label boundary

`actual_label` remains `null` when the prediction is created.

The actual label can be determined only after the complete target window has
passed and corresponding observed telemetry exists.

Prediction creation must not use future observations. Doing so would introduce
data leakage.

A later evaluation workflow may attach the observed label without modifying
the original probability, model version, threshold, or target window.

## Security boundary

- Only registered artifacts may be loaded.
- Artifact paths come from the database, not request bodies.
- Checksums are verified before Joblib deserialization.
- Client-supplied feature dictionaries are not accepted by the persistence
  service.
- Features come from a stored `MetricSample`.
- Prediction creation must use database transactions.
- Failures must roll back partial writes.

## Checkpoint 36 completion boundary

Checkpoint 36 is complete when:

- this persistence contract is versioned;
- prediction schemas are implemented;
- prediction repository operations are implemented;
- prediction service operations are implemented;
- complete metric samples can produce stored predictions;
- incomplete samples are rejected;
- missing samples are rejected;
- absence of an active model is rejected;
- invalid artifacts and provenance are rejected;
- retries return the existing prediction;
- stale historical predictions are marked;
- probability and threshold use six-decimal storage;
- target windows use the registered horizon;
- actual labels remain `null`;
- service tests pass;
- the complete project test suite passes;
- `git diff --check` reports no whitespace errors.