# Live Prediction Label Contract v1

## Status

Checkpoint 40A implementation baseline.

## Purpose

This contract defines how persisted live predictions receive observed labels
after their future target windows have completed.

The live label must remain compatible with the target used to train and test
`contention-logistic-v1`:

```text
future_high_contention_30s
```

The labeler must not redefine high contention merely to improve evaluation
results.

## Target identity

| Property | Value |
|---|---|
| Target name | `future_high_contention_30s` |
| Prediction horizon | 30 seconds |
| Label type | Boolean |
| Positive database value | `actual_label = true` |
| Negative database value | `actual_label = false` |
| Not yet eligible or unreliable | `actual_label = null` |
| Training contract version | `1.0.0` |
| Live label contract version | `1.0.0` |

## Authoritative compatibility sources

The live target is derived from:

```text
docs/data/dataset_contract.md
data_pipeline/export_experiment.py
model_training/training_data.py
```

The authoritative training implementation uses:

```python
PREDICTION_HORIZON_SECONDS = 30
```

and the target:

```text
future_high_contention_30s
```

## Prediction timestamp

For one prediction, define:

```text
t = prediction.target_window_start
```

The prediction service currently sets:

```text
prediction.target_window_start =
metric_sample.sampled_at
```

The target window ends at:

```text
t + 30 seconds
```

and must equal:

```text
prediction.target_window_end
```

## Exact future-window boundary

Future evidence is selected from:

```text
(t, t + 30 seconds]
```

Therefore:

- the metric sample at exactly `t` is excluded;
- samples after `t` are eligible;
- a sample at exactly `t + 30 seconds` is included;
- samples after `t + 30 seconds` are excluded.

Equivalent condition:

```text
sample.sampled_at > prediction.target_window_start
AND
sample.sampled_at <= prediction.target_window_end
```

## Same-experiment rule

Every future evidence sample must satisfy:

```text
sample.experiment_run_id =
prediction.experiment_run_id
```

A prediction must never use:

- samples from another experiment;
- global aggregates across experiments;
- samples selected only by timestamp;
- samples from a later repeated run;
- samples from the training dataset.

## Complete-sample rule

Only metric samples with:

```text
is_complete = true
```

may be used as label evidence.

Every required contention metric must be present and valid:

- `blocked_sessions`
- `waiting_locks`
- `deadlock_delta`
- `timeout_rate`
- `p95_latency_ms`

Counts must be nonnegative.

Rates must be between zero and one.

Latency must be nonnegative.

An incomplete or invalid future sample must not silently create a negative
label.

## Exact evidence calculations

For eligible future samples inside the target window, calculate:

```text
future_max_blocked_sessions_30s =
maximum blocked_sessions
```

```text
future_max_waiting_locks_30s =
maximum waiting_locks
```

```text
future_deadlock_delta_sum_30s =
sum of deadlock_delta
```

```text
future_max_timeout_rate_30s =
maximum timeout_rate
```

```text
future_max_p95_latency_ms_30s =
maximum p95_latency_ms
```

The p95 value is supporting evidence only.

## Exact positive-label rule

The observed label is positive when at least one condition is true:

```text
future_max_blocked_sessions_30s >= 2
OR
future_max_waiting_locks_30s >= 2
OR
future_deadlock_delta_sum_30s >= 1
OR
future_max_timeout_rate_30s > 0
```

Then:

```text
actual_label = true
```

The conditions use inclusive thresholds for blocked sessions, waiting locks,
and deadlocks.

The timeout rule is strictly greater than zero.

## Exact negative-label rule

The observed label is negative only when all conditions are true:

```text
future_max_blocked_sessions_30s < 2
AND
future_max_waiting_locks_30s < 2
AND
future_deadlock_delta_sum_30s < 1
AND
future_max_timeout_rate_30s = 0
```

Then:

```text
actual_label = false
```

A missing, immature, sparse, or invalid future window must never be converted
to `false`.

## Latency boundary

High latency alone does not define lock contention.

The following condition is forbidden:

```text
future_max_p95_latency_ms_30s >= threshold
therefore actual_label = true
```

Latency remains useful for:

- descriptive analysis;
- event severity;
- operational impact;
- baseline comparison;
- demonstration charts.

It does not determine the v1 target.

## Maturity rule

A prediction is immature while:

```text
current_time < prediction.target_window_end
```

An immature prediction must remain:

```text
actual_label = null
```

The labeler must not estimate, extrapolate, or partially calculate its future
outcome.

The label assignment time must satisfy:

```text
actual_label_assigned_at
>= prediction.target_window_end
```

This timestamp is required evidence that labeling occurred after the target
window completed.

## Experiment-status rule

The v1 validation labeler labels predictions only after the experiment has
reached:

```text
COMPLETED
```

Predictions belonging to these experiment states remain unlabeled:

- `DRAFT`
- `RUNNING`
- `FAILED`
- `CANCELLED`

This prevents partial or failed experiment data from becoming final validation
evidence.

## Future-coverage rule

The original training exporter considers a horizon eligible when:

```text
latest_sampled_at >= t + 30 seconds
```

That rule proves that telemetry reached the horizon boundary, but it does not
prove that expected intermediate intervals were collected.

Live validation therefore adds an evidence-quality gate without changing the
target definition.

For an experiment with configured interval `s` seconds:

```text
expected_future_sample_count =
30 / s
```

For the current two-second interval:

```text
expected_future_sample_count = 15
```

A live label is reliable only when:

1. the latest complete sample reaches or passes the target-window end;
2. at least one complete sample exists inside the future window;
3. the number of eligible future samples matches the expected interval count;
4. no adjacent future-sample gap materially exceeds the configured interval
   after normal timestamp jitter is considered;
5. every evidence sample belongs to the same experiment;
6. every evidence sample is complete and valid.

The exact timestamp-jitter tolerance must be frozen in the implementation
contract before code is written.

If future coverage is insufficient:

```text
actual_label = null
```

The labeler must report:

```text
LABEL_INSUFFICIENT_FUTURE_COVERAGE
```

This stricter rule does not redefine positive or negative contention. It
refuses to manufacture a label from unreliable evidence.

## Existing training-label limitation

The frozen training dataset used eligibility based on the latest sample
reaching the horizon end.

It did not enforce:

- an exact future-sample count;
- a maximum inter-sample gap;
- a label-assignment timestamp.

This limitation must be disclosed in Phase 7.5 evaluation.

Live validation uses stricter evidence eligibility to avoid false negative
labels caused by missing telemetry.

## Idempotency

Label assignment is idempotent by:

```text
prediction.id
```

If `actual_label` is already non-null:

- the existing value is returned;
- evidence is not recalculated;
- the label is not overwritten;
- the original assignment timestamp remains unchanged;
- the original label-contract version remains unchanged.

The labeler must never change `true` to `false` or `false` to `true` during an
ordinary retry.

Any correction requires an explicit future versioned correction workflow.

## Concurrency

Two labeler processes may inspect the same eligible prediction.

The implementation must ensure:

- only one final label assignment is committed;
- both callers observe the same stored label;
- no partial evidence is stored;
- database errors roll back the label transaction;
- existing prediction and metric rows remain intact.

## Required label provenance

A trustworthy live label requires more than one Boolean value.

Checkpoint 40B must add or otherwise persist:

```text
actual_label_assigned_at
actual_label_contract_version
actual_label_evidence
```

Recommended meanings:

| Field | Meaning |
|---|---|
| `actual_label_assigned_at` | Time the mature label was committed |
| `actual_label_contract_version` | `live-label-v1` |
| `actual_label_evidence` | Frozen evidence values and coverage metadata |

The evidence should contain:

```text
future_sample_count
expected_future_sample_count
future_max_blocked_sessions_30s
future_max_waiting_locks_30s
future_deadlock_delta_sum_30s
future_max_timeout_rate_30s
future_max_p95_latency_ms_30s
first_future_sampled_at
last_future_sampled_at
maximum_sample_gap_ms
```

No credentials, SQL text, model artifact data, or unrelated user information
may be stored in evidence.

## Database migration boundary

The current `predictions` table already contains:

```text
actual_label
```

It does not contain sufficient label-provenance fields to prove when and how a
label was assigned.

Checkpoint 40B is therefore expected to require a controlled Alembic migration
for label provenance.

The migration must preserve every existing prediction.

Existing predictions remain:

```text
actual_label = null
```

until they become eligible under the live-label contract.

## Leakage prohibitions

The labeler must never:

- assign a label before `target_window_end`;
- use the prediction probability to determine the label;
- use the prediction risk level to determine the label;
- use the model threshold to determine the label;
- use workload profile names as labels;
- use manually assigned LOW, MEDIUM, or HIGH scenarios as labels;
- use future samples from another experiment;
- use p95 latency alone as a positive condition;
- label a missing future window as negative;
- change the label to improve model metrics;
- tune the label definition after seeing validation performance.

The model output and actual outcome must remain independent.

## Label-service result states

The label service should distinguish:

```text
LABELED_POSITIVE
LABELED_NEGATIVE
ALREADY_LABELED
WINDOW_NOT_MATURE
EXPERIMENT_NOT_COMPLETED
INSUFFICIENT_FUTURE_COVERAGE
INVALID_FUTURE_EVIDENCE
PREDICTION_NOT_FOUND
```

Only these states modify `actual_label`:

```text
LABELED_POSITIVE
LABELED_NEGATIVE
```

## Transaction boundary

Label lookup, evidence validation, provenance creation, and final label
assignment occur in one transaction.

If any step fails:

- `actual_label` remains unchanged;
- no partial label evidence remains;
- the prediction row remains available;
- metric samples remain unchanged;
- the caller receives a safe structured error.

## Relationship to model evaluation

A prediction is eligible for live performance metrics only when:

```text
actual_label IS NOT NULL
```

Evaluation must report:

```text
label coverage =
labeled predictions / total predictions
```

Prediction coverage and label coverage are different:

```text
prediction coverage =
predictions / complete metric samples
```

```text
label coverage =
labeled mature predictions / eligible mature predictions
```

A run can have 100% prediction coverage but incomplete label coverage.

## Required tests

Checkpoint 40 implementation must prove:

1. The window is exactly `(t, t + 30 seconds]`.
2. The current sample at `t` is excluded.
3. A sample at `t + 30 seconds` is included.
4. Each positive condition independently produces `true`.
5. All-negative evidence produces `false`.
6. High latency alone produces `false`.
7. Immature predictions remain null.
8. Non-completed experiments remain unlabeled.
9. Missing future evidence remains null.
10. Sparse future evidence remains null.
11. Incomplete samples remain invalid.
12. Cross-experiment samples are excluded.
13. Existing labels are not overwritten.
14. Repeated labeling is idempotent.
15. Label assignment occurs after the target-window end.
16. Label provenance is persisted.
17. Concurrent labeling does not produce conflicting results.
18. Test data remains isolated from development data.

## Checkpoint 40A completion boundary

Checkpoint 40A is complete when:

- this contract is versioned;
- its target matches the frozen training target;
- window boundaries are explicit;
- positive and negative rules are explicit;
- incomplete-horizon behavior is explicit;
- training-label limitations are disclosed;
- provenance requirements are defined;
- leakage prohibitions are defined;
- contract tests pass;
- the complete project suite passes;
- `git diff --check` reports no errors.