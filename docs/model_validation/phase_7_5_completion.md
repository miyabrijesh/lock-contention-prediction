# Phase 7.5 Live Prediction Validation Completion

## Status

- Phase: 7.5
- Status: Complete
- Completion date: 27 August 2026
- Active label contract: `live-label-v2`
- Prediction horizon: 30 seconds
- Active model: `contention-logistic-v1`

## Purpose

Phase 7.5 established an auditable connection between a live prediction and
the contention outcome observed during its future target window.

Before this phase, the system could create and persist predictions, but
`actual_label` remained null. The system therefore had no validated process for
determining whether a live prediction was correct after its 30-second target
window had passed.

This phase implemented, tested, and validated that process.

## Scope

Phase 7.5 covered:

- definition of the live observed-label contract;
- prediction label-provenance persistence;
- database constraints for complete provenance;
- safe and idempotent label assignment;
- exact future-window telemetry selection;
- future-evidence quality validation;
- batch label processing;
- prediction coverage reporting;
- observed-label coverage reporting;
- controlled LOW and HIGH live experiments;
- diagnosis of timestamp sampling jitter;
- introduction and validation of `live-label-v2`;
- controlled comparison of predicted and observed classes.

This phase did not retrain or replace the deployed model.

## Implemented components

The implementation added or extended:

- `backend/app/models/prediction.py`;
- `backend/app/schemas/prediction.py`;
- `backend/app/repositories/prediction_repository.py`;
- `backend/app/services/prediction_label_service.py`;
- `monitoring/prediction_labeling.py`;
- `monitoring/label_coverage.py`;
- prediction label-provenance database migration;
- live-label contract documentation;
- service-level label tests;
- batch-label tests;
- coverage-report tests;
- contract-version tests.

## Prediction label provenance

The prediction record now supports:

```text
actual_label
actual_label_assigned_at
actual_label_contract_version
actual_label_evidence
```

A label is valid only when all provenance fields are populated together.

An unlabeled prediction must have all four provenance fields set to null.

The database enforces:

- complete or completely absent label provenance;
- timezone-aware assignment after target-window maturity;
- nonblank contract versions;
- JSON-object evidence;
- efficient lookup of mature unlabeled predictions.

## Live prediction target

For a prediction created from a metric sample at time `t`, the target asks:

> Does high lock contention occur during the following 30 seconds?

The exact future window is:

```text
(t, t + 30 seconds]
```

Therefore:

- the source metric sample at exactly `t` is excluded;
- metric samples after `t` are eligible;
- a sample at exactly `t + 30 seconds` is included;
- samples after `t + 30 seconds` are excluded;
- evidence from another experiment is excluded.

## Observed-label calculation

For valid future samples inside the target window, the system calculates:

```text
future_max_blocked_sessions_30s
future_max_waiting_locks_30s
future_deadlock_delta_sum_30s
future_max_timeout_rate_30s
future_max_p95_latency_ms_30s
```

The observed label is positive when at least one of the following conditions is
true:

```text
future_max_blocked_sessions_30s >= 2
OR future_max_waiting_locks_30s >= 2
OR future_deadlock_delta_sum_30s >= 1
OR future_max_timeout_rate_30s > 0
```

The future maximum p95 latency is retained as supporting evidence. It does not
independently make the target positive.

When none of the positive conditions is satisfied, the observed label is
negative.

## Assignment safeguards

The observed-label service requires:

1. An existing prediction.
2. An existing associated experiment.
3. A completed experiment.
4. A mature target window.
5. A target window of exactly 30 seconds.
6. Future samples from the same experiment.
7. Complete future samples.
8. Valid future metric values.
9. Sufficient future-window coverage.
10. Assignment at or after the target-window end.

The prediction row is locked during assignment.

Repeated assignment does not overwrite an existing label. It returns the
existing result as `ALREADY_LABELED`.

## Label-assignment results

The service reports one of the following states:

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

An insufficient or invalid window remains unlabeled. It is never silently
converted into a negative example.

## Version-1 contract

The first contract implementation used:

```text
actual_label_contract_version = "live-label-v1"
```

For a 30-second target window and two-second sample interval, v1 required
exactly:

```text
expected_future_sample_count = 15
```

It also required adjacent sampling gaps to remain below:

```text
2 seconds * 1.75 = 3.5 seconds
```

## Version-1 diagnostic experiment

The v1 diagnostic experiment was:

```text
a70ed804-e5d6-4928-b192-1f38b062223d
```

Its configuration used:

- traffic pattern: `RAMP`;
- demand profile: `MEDIUM`;
- users: 40;
- spawn rate: 15 users per second;
- duration: 120 seconds;
- hotspot ratio: 0.65;
- hotspot seats: 12;
- sample interval: 2 seconds;
- simulated work: 180 milliseconds;
- random seed: 4103.

The experiment produced:

- 60 complete metric samples;
- 60 persisted predictions;
- 60 distinct predicted samples;
- zero missing predictions;
- zero duplicate predictions;
- zero stale predictions;
- one model version;
- prediction coverage of 1.00.

There were 44 prediction windows with later telemetry extending through their
target-window ends.

Under `live-label-v1`:

- 26 windows contained 15 future samples and received labels;
- 18 windows contained 14 future samples and remained unlabeled;
- all 26 assigned labels were positive;
- all assigned labels had complete v1 provenance.

The resulting v1 label coverage was:

```text
26 / 44 = 0.5909090909
```

## Timestamp-jitter diagnosis

The 18 rejected windows were inspected individually.

The diagnostic results were:

```text
Future-sample count distribution: {14: 18, 15: 26}
Labeled/count distribution: {(False, 14): 18, (True, 15): 26}
```

Across the complete experiment:

- total samples: 60;
- adjacent intervals: 59;
- minimum interval: approximately 1.610 seconds;
- average interval: approximately 1.995 seconds;
- maximum interval: approximately 2.352 seconds;
- intervals above 2 seconds: 27;
- intervals above 3.5 seconds: 0.

Every rejected window had:

- valid leading coverage;
- valid trailing coverage;
- valid internal gaps;
- 14 complete future samples.

The rejection was caused only by normal millisecond scheduling jitter around
the exact target-window boundary.

The exact-count requirement was therefore measuring timestamp phase rather
than evidence completeness.

## Version-2 correction

The corrected contract uses:

```text
actual_label_contract_version = "live-label-v2"
```

Version 2 preserves:

- the 30-second horizon;
- the exact future-window boundaries;
- the contention thresholds;
- the target definition;
- the evidence metrics;
- the assignment timing;
- the provenance requirements.

Version 2 changes only the evidence-coverage adequacy rule.

For a two-second interval:

```text
expected_future_sample_count = 15
minimum_future_sample_count = 14
maximum_allowed_gap = 3.5 seconds
```

A v2 window must satisfy:

1. At least 14 valid future samples are present.
2. The first future sample is within 3.5 seconds of the window start.
3. The last future sample is within 3.5 seconds of the window end.
4. Every adjacent sample gap is at most 3.5 seconds.
5. Every sample is complete and valid.
6. Every sample belongs to the prediction's experiment.
7. Every sample falls inside the exact future window.

A valid sample count greater than 15 is permitted when all samples remain
inside the exact future window.

## Version preservation

Existing `live-label-v1` rows remain immutable.

They were not:

- overwritten;
- converted to v2;
- deleted;
- represented as v2 evidence.

The v1 diagnostic experiment is retained as historical evidence explaining why
the versioned correction was necessary.

It is not treated as a homogeneous v2 evaluation experiment.

## LOW v2 validation experiment

The LOW experiment was:

```text
6b5b036a-3581-4edc-94cd-df9c13c593fc
```

Its configuration used:

- traffic pattern: `STEADY`;
- demand profile: `LOW`;
- users: 5;
- spawn rate: 2 users per second;
- duration: 120 seconds;
- hotspot ratio: 0.10;
- hotspot seats: 20;
- sample interval: 2 seconds;
- simulated work: 20 milliseconds;
- random seed: 4104.

The configuration was valid under the MovieLens-calibrated LOW profile:

```text
hotspot ratio range: 0.10 to 0.30
hotspot seat-count range: 15 to 25
```

### LOW workload result

The synchronized workload and collector both completed successfully.

The workload produced:

- 7,127 booking requests;
- zero failed booking requests;
- average response time of approximately 48 milliseconds;
- median response time of approximately 38 milliseconds.

### LOW prediction coverage

The LOW experiment produced:

- complete metric samples: 60;
- predictions: 60;
- distinct predicted samples: 60;
- missing predictions: 0;
- duplicate predictions: 0;
- stale predictions: 0;
- labeled predictions before assignment: 0;
- model versions used: 1;
- prediction coverage: 1.00;
- prediction coverage result: passed.

### LOW observed-label result

The batch label operation reported:

- candidates: 44;
- attempts: 44;
- completed attempts: 44;
- unexpected errors: 0;
- labeled negative: 43;
- labeled positive: 0;
- insufficient future coverage: 1;
- invalid future evidence: 0;
- contract version: `live-label-v2`.

The label-coverage report showed:

- total predictions: 60;
- mature predictions: 60;
- temporally eligible predictions: 44;
- labeled predictions: 43;
- unlabeled mature predictions: 17;
- unlabeled temporally eligible predictions: 1;
- negative labels: 43;
- positive labels: 0;
- complete provenance records: 43;
- invalid provenance records: 0;
- ineligible labeled predictions: 0;
- reported temporal label coverage: approximately 0.977273.

### LOW telemetry exclusion

The single unlabeled candidate was:

```text
171e932d-b001-498d-ac41-2ca9828aaafa
```

Its future evidence contained:

- future samples: 15;
- minimum required samples: 14;
- first boundary gap: 9.135 seconds;
- final boundary gap: approximately 0.017 seconds;
- maximum internal gap: approximately 2.044 seconds;
- maximum allowed gap: 3.5 seconds.

The complete LOW sampling series showed:

- samples: 60;
- minimum adjacent gap: approximately 0.023 seconds;
- average adjacent gap: approximately 2.000 seconds;
- maximum adjacent gap: approximately 9.135 seconds;
- gaps above 3.5 seconds: one.

Several rapid catch-up observations followed the nine-second gap.

The window therefore had a genuine leading telemetry gap. Version 2 correctly
rejected it.

The contract was not weakened and the window was not converted into a negative
label.

### LOW model behavior

Every valid LOW label was negative.

Every valid LOW prediction was below the decision threshold.

For valid LOW evidence:

```text
true negatives = 43
false positives = 0
```

## HIGH v2 validation experiment

The HIGH experiment was:

```text
e20a2514-b53d-448b-b818-3f33a7df7ac0
```

Its configuration used:

- traffic pattern: `RAMP`;
- demand profile: `HIGH`;
- users: 40;
- spawn rate: 15 users per second;
- duration: 120 seconds;
- hotspot ratio: 0.90;
- hotspot seats: 3;
- sample interval: 2 seconds;
- simulated work: 180 milliseconds;
- random seed: 4105.

The configuration was valid under the MovieLens-calibrated HIGH profile:

```text
hotspot ratio range: 0.85 to 1.00
hotspot seat-count range: 3 to 5
```

### HIGH prediction coverage

The HIGH experiment produced:

- complete metric samples: 60;
- predictions: 60;
- distinct predicted samples: 60;
- missing predictions: 0;
- duplicate predictions: 0;
- stale predictions: 0;
- labeled predictions before assignment: 0;
- model versions used: 1;
- prediction coverage: 1.00;
- prediction coverage result: passed.

### HIGH observed-label result

The batch label operation reported:

- candidates: 44;
- attempts: 44;
- completed attempts: 44;
- unexpected errors: 0;
- labeled positive: 44;
- labeled negative: 0;
- insufficient future coverage: 0;
- invalid future evidence: 0;
- contract version: `live-label-v2`.

The label-coverage report showed:

- total predictions: 60;
- mature predictions: 60;
- eligible predictions: 44;
- labeled predictions: 44;
- unlabeled mature predictions: 16;
- unlabeled eligible predictions: 0;
- positive labels: 44;
- negative labels: 0;
- complete provenance records: 44;
- invalid provenance records: 0;
- ineligible labeled predictions: 0;
- label coverage: 1.00;
- label coverage result: passed.

### HIGH model behavior

Every valid HIGH label was positive.

Every valid HIGH prediction was above the decision threshold.

For valid HIGH evidence:

```text
true positives = 44
false negatives = 0
```

## Combined controlled validation

The combined v2 validation contained:

```text
LOW valid labels = 43
HIGH valid labels = 44
Total valid labels = 87
```

The combined confusion matrix was:

| Observed class | Predicted negative | Predicted positive |
|---|---:|---:|
| Negative | 43 | 0 |
| Positive | 0 | 44 |

Equivalently:

| Outcome | Count |
|---|---:|
| True negative | 43 |
| False positive | 0 |
| False negative | 0 |
| True positive | 44 |
| Total valid labels | 87 |

Metrics calculated across these valid controlled observations were:

| Metric | Value |
|---|---:|
| Accuracy | 1.00 |
| Precision | 1.00 |
| Recall | 1.00 |
| Specificity | 1.00 |
| F1 | 1.00 |

## Interpretation of the combined metrics

The combined results demonstrate that the deployed prediction and labeling
pipeline behaved correctly at the two deliberately separated workload
extremes:

- valid LOW observations were predicted negative;
- valid HIGH observations were predicted positive;
- no valid LOW observation was predicted positive;
- no valid HIGH observation was predicted negative;
- all assigned labels used `live-label-v2`;
- incomplete telemetry remained unlabeled;
- prediction coverage remained complete.

The perfect controlled metrics must not be interpreted as universal production
performance.

They describe two deliberately separated controlled workloads, not a random
sample of all possible database workloads.

## Prediction coverage and label coverage

Prediction coverage and observed-label coverage measure different properties.

Prediction coverage is:

```text
predictions / complete metric samples
```

Both v2 experiments achieved:

```text
60 / 60 = 1.00
```

Observed-label coverage depends on future evidence quality.

The HIGH experiment achieved:

```text
44 / 44 = 1.00
```

The LOW experiment produced:

```text
43 valid labels from 44 temporal candidates
```

The remaining LOW candidate was correctly excluded because of a genuine
nine-second telemetry gap.

This exclusion demonstrates evidence-quality enforcement rather than a model
classification failure.

## Automated verification

Before the final completion document was added, the complete automated test
suite reported:

```text
257 passed
1 non-blocking deprecation warning
```

The warning concerns the existing Starlette test-client compatibility layer. It
does not represent a failed project test or a Phase 7.5 validation failure.

The test suite covers:

- prediction persistence;
- model activation;
- inference;
- prediction API contracts;
- label-provenance constraints;
- target-window boundaries;
- positive and negative label calculation;
- assignment timing;
- idempotency;
- invalid evidence;
- incomplete evidence;
- cross-experiment isolation;
- v1 contract preservation;
- v2 jitter tolerance;
- leading-boundary gaps;
- trailing-boundary gaps;
- excessive internal gaps;
- batch labeling;
- prediction coverage;
- label coverage.

## Academic significance

Phase 7.5 provides evidence for three separate claims.

### Claim 1: The deployed prediction pipeline operates end to end

Both v2 experiments produced one prediction for every complete collected
metric sample.

### Claim 2: Live predictions can receive auditable future outcomes

Valid mature predictions received labels derived only from later telemetry
inside their exact target windows.

Each assigned label stored its assignment time, contract version, and evidence.

### Claim 3: Evidence quality is enforced

The system rejected:

- the v1 exact-count assumption after empirical diagnosis;
- a LOW prediction window with a genuine nine-second leading gap;
- invalid and incomplete evidence in automated tests.

The system did not manufacture negative labels from missing telemetry.

## Limitations

The controlled live validation has the following limitations:

1. Only one random seed was used for each v2 demand condition.
2. The workloads intentionally represented separated LOW and HIGH conditions.
3. No MEDIUM or BORDERLINE v2 run was included in the combined metrics.
4. The 87 valid labels are correlated time-series observations from two
   experiments.
5. The labels must not be treated as 87 independent experimental replications.
6. One LOW temporal candidate was excluded because of a genuine collection
   gap.
7. The controlled metrics do not estimate general production accuracy.
8. The validation does not replace the frozen experiment-grouped test-set
   evaluation.
9. Workloads from external production database systems were not evaluated.
10. Longer-duration drift and calibration behavior were not evaluated.
11. Broader multi-seed validation remains future work.
12. The experiment was performed on the project's local controlled
    environment.

## Future work

Future validation may include:

- multiple random seeds per demand profile;
- MEDIUM and BORDERLINE workloads;
- longer-duration experiments;
- confidence calibration using accumulated live labels;
- analysis across different database hardware;
- drift detection;
- periodic observed-label assignment;
- automated exclusion reporting for telemetry-quality gaps;
- comparison across future model versions.

These items are extensions. They are not required to complete Phase 7.5.

## Phase acceptance criteria

Phase 7.5 is accepted because:

1. The live prediction target is explicitly documented.
2. The target window is fixed at 30 seconds.
3. Label evidence is restricted to `(t, t + 30 seconds]`.
4. Cross-experiment evidence is excluded.
5. Incomplete and invalid evidence remains unlabeled.
6. Label provenance is persisted and database-constrained.
7. Assignment occurs only after target-window maturity.
8. Assignment is idempotent and concurrency-safe.
9. Batch label processing is implemented.
10. Prediction coverage reporting is implemented.
11. Label coverage reporting is implemented.
12. Historical v1 evidence remains immutable.
13. The v1 exact-count limitation was empirically diagnosed.
14. The correction was introduced as `live-label-v2`.
15. Valid 14-sample jitter windows are accepted.
16. Genuine boundary and internal gaps are rejected.
17. Both v2 experiments achieved 100% prediction coverage.
18. The HIGH experiment achieved 100% v2 label coverage.
19. Valid negative evidence was collected under LOW demand.
20. Valid positive evidence was collected under HIGH demand.
21. The model correctly separated the valid controlled LOW and HIGH evidence.
22. Automated tests pass.
23. Known limitations are explicitly documented.

## Final conclusion

The project now has an auditable path from a persisted live prediction to its
future observed contention outcome.

The implemented process preserves:

- exact target semantics;
- evidence quality;
- contract versioning;
- assignment timing;
- provenance completeness;
- historical label immutability;
- explicit handling of missing telemetry.

Controlled LOW and HIGH validation demonstrated correct end-to-end behavior for
both observed target classes.

The results support the completion of Phase 7.5 while remaining explicitly
limited to the controlled experimental conditions documented above.

Phase 7.5 is complete.
