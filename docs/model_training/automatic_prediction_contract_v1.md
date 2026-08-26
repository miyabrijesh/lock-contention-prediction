# Automatic Prediction Contract v1

## Status

Checkpoint 38A implementation baseline.

## Purpose

This contract defines how the metrics collector automatically creates one
future-contention prediction after persisting each complete metric sample.

The automatic integration connects live experiment telemetry to the active
Phase 7 prediction model.

## Runtime sequence

For every collection interval, the collector must:

1. Confirm that the experiment is still `RUNNING`.
2. Collect PostgreSQL metrics.
3. Collect application and workload metrics.
4. Build one complete `MetricSample`.
5. Commit the metric sample.
6. Refresh the committed metric sample.
7. Start a separate prediction transaction.
8. Load the single active model.
9. Verify the registered model artifact.
10. Run inference using the committed metric sample.
11. Persist one prediction.
12. Commit the prediction.
13. Return both the metric sample and prediction.
14. Print a concise prediction summary.

The metric sample must exist before prediction creation begins.

## Transaction boundary

Metric persistence and prediction persistence use separate transactions.

The required boundary is:

```text
Transaction 1: collect and commit MetricSample
Transaction 2: verify model, infer, and commit Prediction
```

This separation is intentional.

If prediction creation fails after the metric sample was committed:

- the metric sample remains stored;
- no partial prediction row remains;
- the error is reported;
- the collector must not claim complete success.

The collector must never roll back an already-committed metric sample because
a later prediction failed.

## Failure policy

Automatic prediction is required during a Phase 7 synchronized experiment.

A prediction failure must raise a safe `MetricCollectorError`.

The collector process then exits with code:

```text
2
```

The synchronized experiment runner detects the nonzero collector exit code and
marks the experiment as failed.

This strict policy prevents an experiment from being reported as successfully
completed when prediction coverage is incomplete.

## Safe error message

The collector may report a safe message such as:

```text
The metric sample was persisted, but automatic prediction failed:
ACTIVE_MODEL_NOT_FOUND.
```

The message may contain the structured prediction error code.

It must not contain:

- API tokens;
- database URLs;
- absolute artifact paths;
- raw SQL;
- serialized models;
- internal exception traces.

## Internal service integration

The collector and prediction service run inside the same trusted Python
application environment.

The collector calls:

```text
create_prediction_for_metric_sample()
```

directly after committing the metric sample.

It does not make an HTTP request to its own API.

Therefore:

- no token is passed between internal Python functions;
- no localhost HTTP round trip is required;
- the same validated prediction service remains responsible for persistence;
- public or external creation still requires `X-Internal-Token`.

Direct service use does not weaken the API authentication boundary.

## Collection result

`MetricCollectionResult` contains:

```text
sample
prediction
cumulative_deadlocks
```

Where:

- `sample` is the committed `MetricSample`;
- `prediction` is the committed `Prediction`;
- `cumulative_deadlocks` is the PostgreSQL cumulative deadlock counter.

Returning both records allows the runtime loop to print and verify prediction
information without querying the database again.

## Runtime output

After a successful collection interval, the collector prints the existing
metric summary and a concise prediction summary.

The prediction summary includes:

- probability;
- risk level;
- threshold;
- stale status.

Example:

```text
Collected sample 1:
requests_per_sec=6.5,
blocked_sessions=2,
waiting_locks=2,
p95_latency_ms=350.0,
prediction_probability=0.961174,
prediction_risk=HIGH,
prediction_threshold=0.340000,
prediction_stale=false
```

The collector must not print:

- model artifact contents;
- tokens;
- database credentials;
- complete feature mappings.

## Active-model requirement

Automatic prediction requires exactly one active model version.

When no active model exists, prediction creation fails with:

```text
ACTIVE_MODEL_NOT_FOUND
```

The collector then reports a safe `MetricCollectorError`.

The collector must not:

- select an inactive model;
- activate a model automatically;
- silently skip prediction;
- continue while claiming full Phase 7 success.

## Metric-sample requirement

Only a committed sample with:

```text
is_complete = true
```

may produce a prediction.

The current collector builds complete samples. If required feature values are
missing or invalid, the prediction service returns:

```text
PREDICTION_INPUT_INVALID
```

The sample remains stored for diagnosis, but the collector reports failure.

## Idempotency

Automatic prediction remains idempotent by:

```text
metric_sample_id
```

If prediction creation is retried for a committed sample:

- the existing prediction is returned;
- inference is not repeated;
- no duplicate prediction is inserted;
- the original model version and probability remain unchanged.

The database unique constraint on `predictions.metric_sample_id` remains the
final concurrency protection.

## Live staleness expectation

During a normal synchronized experiment, prediction should occur immediately
after metric collection.

For a 30-second target horizon, the normal result is:

```text
is_stale = false
```

A stale result is still valid as historical evidence but indicates that the
prediction was generated after its target window ended.

Repeated stale predictions during a live experiment indicate excessive
collector or inference delay and must be investigated.

## Prediction coverage

For a successfully completed Phase 7 experiment:

```text
complete metric samples = persisted predictions
```

Coverage is calculated as:

```text
prediction coverage =
predictions / complete metric samples
```

A successful integrated run requires:

```text
prediction coverage = 1.0
```

Duplicate predictions are forbidden.

## Model provenance

Every automatic prediction records:

- active model-version ID;
- metric-sample ID;
- experiment-run ID;
- artifact-backed probability;
- risk level;
- threshold;
- target window;
- prediction timestamp;
- quality flags.

The original model provenance remains stored in `model_versions`.

## Actual-label boundary

Automatic prediction creation sets:

```text
actual_label = null
```

The collector must not read future samples to calculate a label during
prediction.

Observed labels may be attached only after the target window has completed.

This prevents future-data leakage into live inference.

## Performance boundary

Prediction runs after metric collection and sample persistence.

Inference must not delay or alter the telemetry values already collected for
the interval.

The current model artifact is small, but prediction duration should still be
observed during the integrated run.

If prediction repeatedly takes longer than the configured sample interval, the
collector schedule and deployment design must be reviewed.

## Database migration

Checkpoint 38 requires no database migration.

The existing tables already provide:

- `metric_samples`;
- `model_versions`;
- `predictions`;
- model and sample foreign keys;
- prediction uniqueness by metric sample;
- probability and threshold checks;
- target-window checks.

## Required tests

Checkpoint 38 tests must prove:

1. A committed complete sample creates one prediction.
2. The collection result returns both records.
3. Prediction uses the active model.
4. Prediction failure does not erase the metric sample.
5. Prediction failure becomes `MetricCollectorError`.
6. Missing active model is not silently ignored.
7. Repeated prediction remains idempotent.
8. Existing metric-collection calculations remain unchanged.
9. Development data is not modified by tests.
10. The complete project suite still passes.

## Checkpoint 38 completion boundary

Checkpoint 38 is complete when:

- this contract is versioned;
- the collector invokes prediction after sample commit;
- the result contains both sample and prediction;
- runtime output contains prediction probability and risk;
- prediction errors safely stop the collector;
- committed samples survive prediction failure;
- integration tests pass;
- one real synchronized experiment completes;
- the real experiment has one prediction per complete sample;
- live predictions are not unexpectedly stale;
- actual labels remain null during live inference;
- the complete project test suite passes;
- `git diff --check` reports no whitespace errors.