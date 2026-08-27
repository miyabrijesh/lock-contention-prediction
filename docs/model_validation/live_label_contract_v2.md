# Live Prediction Label Contract v2

## Status

- Contract status: Active
- Contract document version: `2.0.0`
- Runtime provenance value: `live-label-v2`
- Prediction horizon: 30 seconds
- Supersedes: the future-coverage adequacy rule in `live-label-v1`
- Target definition: unchanged from `live-label-v1`

## Purpose

This contract defines how a persisted live prediction receives an observed
binary contention label after its 30-second target window has matured.

Version 2 corrects the exact-sample-count requirement in version 1. A valid
periodically collected window can contain one fewer sample than the nominal
count when ordinary timestamp jitter places a boundary sample a few
milliseconds outside the exact target window.

The correction changes only the evidence-coverage rule. It does not change:

- the contention target;
- the target thresholds;
- the 30-second prediction horizon;
- the feature schema;
- the target-window boundaries;
- the positive and negative label definitions.

## Empirical reason for the correction

A controlled 120-second RAMP experiment produced:

- 60 complete metric samples;
- 60 persisted predictions;
- 100% prediction coverage;
- zero missing predictions;
- zero duplicate predictions;
- zero stale predictions;
- 59 adjacent collection intervals;
- an average interval of approximately 1.995 seconds;
- a maximum interval of approximately 2.352 seconds;
- no interval above the allowed 3.5-second gap.

There were 44 predictions whose 30-second target windows were covered by later
telemetry.

Under `live-label-v1`:

- 26 windows contained 15 future samples and were labeled;
- 18 windows contained 14 future samples and were rejected;
- every rejected window had valid start coverage;
- every rejected window had valid end coverage;
- every rejected window had adjacent gaps below 3.5 seconds.

The rejected windows were not missing telemetry. They differed only because of
normal sampling jitter around the exact window boundary. Exact sample equality
therefore measured timestamp phase rather than evidence completeness.

## Prediction target

For a prediction created from a metric sample at time `t`, the target asks:

> Does high lock contention occur during the following 30 seconds?

The exact target window is:

```text
(t, t + 30 seconds]
```

Therefore:

- the source sample at exactly `t` is excluded;
- samples after `t` are eligible;
- a sample at exactly `t + 30 seconds` is included;
- samples after `t + 30 seconds` are excluded.

Future evidence must belong to the same experiment as the prediction.

## Target calculation

For all valid samples inside the target window, calculate:

```text
future_max_blocked_sessions_30s
future_max_waiting_locks_30s
future_deadlock_delta_sum_30s
future_max_timeout_rate_30s
future_max_p95_latency_ms_30s
```

The observed label is positive when any of the following conditions is true:

```text
future_max_blocked_sessions_30s >= 2
OR future_max_waiting_locks_30s >= 2
OR future_deadlock_delta_sum_30s >= 1
OR future_max_timeout_rate_30s > 0
```

The future maximum p95 latency is retained as supporting evidence. It does not
independently make the target positive.

If none of the positive conditions is satisfied, the observed label is
negative.

## Sample validity

Every evidence sample must:

- belong to the prediction's experiment;
- fall inside `(t, t + 30 seconds]`;
- be marked complete;
- contain valid nonnegative contention counts;
- contain a timeout rate between zero and one;
- contain a valid nonnegative p95 latency;
- have a valid timezone-aware timestamp.

Invalid or incomplete evidence must never silently produce a negative label.

## Nominal sample count

The nominal future-sample count is:

```text
expected_future_sample_count =
    prediction_horizon_seconds // sample_interval_sec
```

For a 30-second horizon and a two-second collection interval:

```text
expected_future_sample_count = 15
```

This is the nominal number of samples expected under an ideal schedule. It is
retained in the provenance evidence for comparison and auditing.

## Minimum sample count

Version 2 permits one nominal boundary sample of scheduling jitter:

```text
minimum_future_sample_count =
    max(expected_future_sample_count - 1, 1)
```

For a two-second collection interval:

```text
minimum_future_sample_count = 14
```

A count below the minimum is insufficient future coverage.

A count above the nominal value is permitted when every sample is valid and
falls inside the exact target window. Additional valid observations are not
missing evidence and must not be rejected.

## Maximum permitted gap

The maximum permitted evidence gap is:

```text
maximum_allowed_gap =
    sample_interval_sec * 1.75
```

For a two-second collection interval:

```text
maximum_allowed_gap = 3.5 seconds
```

The same tolerance applies to:

- the gap between the target-window start and the first future sample;
- every adjacent pair of future samples;
- the gap between the last future sample and the target-window end.

## Future-coverage requirements

A target window has sufficient future coverage only when every condition below
is satisfied:

1. The experiment has status `COMPLETED`.
2. The prediction target window has matured.
3. The latest complete experiment sample reaches or passes the target-window
   end.
4. At least one valid sample exists inside the target window.
5. The number of valid future samples is at least
   `minimum_future_sample_count`.
6. The first future sample occurs no more than `maximum_allowed_gap` after the
   target-window start.
7. The last future sample occurs no more than `maximum_allowed_gap` before the
   target-window end.
8. No adjacent future-sample gap exceeds `maximum_allowed_gap`.
9. Every evidence sample belongs to the prediction's experiment.
10. Every evidence sample is complete and valid.

This rule tolerates ordinary scheduling jitter while continuing to reject:

- genuinely sparse windows;
- missing leading evidence;
- missing trailing evidence;
- missing interior evidence;
- excessive collection gaps;
- incomplete samples;
- invalid metric values;
- cross-experiment evidence.

## Insufficient coverage

If any future-coverage requirement fails, the assignment result is:

```text
INSUFFICIENT_FUTURE_COVERAGE
```

The prediction remains unlabeled.

Insufficient coverage must not be interpreted as a negative contention label.

## Invalid evidence

If samples exist but contain invalid or incomplete metric values, the assignment
result is:

```text
INVALID_FUTURE_EVIDENCE
```

The prediction remains unlabeled.

## Assignment timing

A label may be assigned only when:

```text
actual_label_assigned_at >= target_window_end
```

Before the target window matures, the assignment result is:

```text
WINDOW_NOT_MATURE
```

## Idempotency

A prediction that already has an observed label must not be recalculated,
overwritten, or assigned a different contract version.

Repeated assignment returns:

```text
ALREADY_LABELED
```

Concurrent assignment must lock the prediction row before checking or writing
its label provenance.

## Required provenance

A v2 label must persist:

```text
actual_label
actual_label_assigned_at
actual_label_contract_version
actual_label_evidence
```

The contract version must be:

```text
actual_label_contract_version = "live-label-v2"
```

The evidence object must contain at least:

```text
future_sample_count
expected_future_sample_count
minimum_future_sample_count
future_max_blocked_sessions_30s
future_max_waiting_locks_30s
future_deadlock_delta_sum_30s
future_max_timeout_rate_30s
future_max_p95_latency_ms_30s
first_future_sampled_at
last_future_sampled_at
maximum_sample_gap_ms
first_boundary_gap_ms
final_boundary_gap_ms
maximum_allowed_gap_ms
```

## Version-1 compatibility

Existing `live-label-v1` rows are immutable historical evidence.

They must not be:

- overwritten;
- silently converted to v2;
- deleted to improve label coverage;
- represented as if they were assigned under v2.

A historical experiment containing both v1 and v2 labels does not satisfy the
single-active-contract coverage requirement. It must not be presented as a
homogeneous final evaluation run.

The experiment that exposed the v1 limitation remains an integration and
contract-diagnostic artifact.

A fresh experiment must be used for formal v2 validation.

## Terminal predictions

Predictions near the end of an experiment may mature after collection stops but
lack a complete following 30-second telemetry window.

These predictions remain intentionally unlabeled and are excluded from the
eligible-label denominator.

They are not treated as negative examples.

## Label coverage

For one completed experiment:

```text
label_coverage =
    labeled_eligible_predictions
    / eligible_predictions
```

A formal v2 validation run passes label coverage only when:

- the experiment is completed;
- at least one prediction is eligible;
- every eligible prediction is labeled;
- every label has complete provenance;
- no ineligible prediction is labeled;
- every label uses `live-label-v2`;
- label coverage equals one.

## Evaluation limitation

A run containing only observed positive labels cannot establish:

- specificity;
- false-positive rate;
- balanced accuracy;
- negative-class recall;
- general discrimination performance.

Such a run can demonstrate integration, prediction coverage, label provenance,
and positive-event behavior.

Additional lower-pressure experiments are required to collect valid negative
evidence.

## Acceptance requirements

The v2 implementation is accepted only when:

1. A valid 15-sample window is labeled.
2. A valid jitter-covered 14-sample window is labeled.
3. A window containing fewer than 14 samples is rejected.
4. Missing leading-boundary evidence is rejected.
5. Missing trailing-boundary evidence is rejected.
6. An excessive interior sample gap is rejected.
7. Incomplete samples are rejected.
8. Invalid metric values are rejected.
9. Cross-experiment samples are excluded.
10. Exact target-window boundaries are enforced.
11. Assignment before target-window maturity is rejected.
12. Existing labels remain immutable.
13. Provenance records `live-label-v2`.
14. Contract and service tests pass.
15. A fresh controlled live experiment achieves complete v2 label coverage.
