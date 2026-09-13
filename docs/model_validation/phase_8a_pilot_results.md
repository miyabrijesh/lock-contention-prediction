# Phase 8A Pilot Results

## Status

- Campaign: `phase_8a_pilot_matrix_v2`
- Pilot status: In progress
- Planned experiments: 15
- Completed experiments: 5
- Remaining experiments: 10
- Active prediction model: `contention-logistic-v1`
- Active label contract: `live-label-v2`
- Verified export schema: `2.0.0`

## First Experiment

| Field | Value |
|---|---|
| Experiment ID | `2588b50c-5114-4670-8511-870a1f28928c` |
| Traffic pattern | `STEADY` |
| Demand profile | `LOW` |
| Configured users | 30 |
| Spawn rate | 10 users per second |
| Simulated transaction work | 150 ms |
| Duration | 120 seconds |
| Random seed | 8101 |
| Final status | `COMPLETED` |

## Workload Results

The first experiment produced:

- 5,066 successful booking requests;
- zero HTTP request failures;
- approximately 41.88 requests per second;
- median response time of approximately 390 ms;
- p95 response time of approximately 1,300 ms;
- maximum observed response time of approximately 15 seconds;
- 60 complete metric samples;
- 60 persisted predictions.

The maximum response time was an outlier and does not represent the response
time of every request.

## Prediction Coverage

Prediction coverage passed:

- complete metric samples: 60;
- predictions: 60;
- distinct predicted samples: 60;
- missing predictions: 0;
- duplicate predictions: 0;
- stale predictions: 0;
- prediction coverage: 1.

## Observed Labels

Of the 60 predictions:

- 44 had target windows that could be inspected;
- 43 received valid `live-label-v2` labels;
- 26 were positive;
- 17 were negative;
- one eligible prediction remained unlabelled because of insufficient
  time coverage.

The valid labelled rows contained both target classes. This indicates that the
intermediate-pressure configuration may provide useful evidence near the
contention decision boundary.

## Coverage Exception

The rejected prediction was:

```text
97b98aa0-5ca7-49d8-a0b3-ce6bfc49f3bf
```

Its future window contained 15 samples, which met the nominal count. However,
the first future sample occurred approximately 3,767 ms after the target-window
start.

The maximum permitted coverage gap under `live-label-v2` is 3,500 ms.
Therefore, the label was correctly rejected as:

```text
INSUFFICIENT_FUTURE_COVERAGE
```

The experiment-level sampling statistics were:

- samples: 60;
- adjacent intervals: 59;
- average interval: approximately 1.999 seconds;
- maximum interval: approximately 3.767 seconds;
- intervals above 3.5 seconds: 1.

The excessive gap occurred during the early startup period. The largest
internal gap inside the rejected prediction's selected future samples was only
approximately 2.059 seconds.

The label contract will not be weakened to force complete coverage. The
rejected row will remain excluded from valid labelled evidence.

## Preliminary Model Behaviour

For the 43 valid labelled predictions:

| Outcome | Count |
|---|---:|
| True negative | 1 |
| False positive | 16 |
| False negative | 1 |
| True positive | 25 |

Approximate derived metrics are:

| Metric | Value |
|---|---:|
| Accuracy | 60.47% |
| Precision | 60.98% |
| Recall | 96.15% |
| F1-score | 74.63% |

The active model detected 25 of the 26 observed contention events, producing
high recall. However, it also generated 16 false-positive warnings.

This single experiment does not justify changing the model or its frozen
threshold. It suggests that the new intermediate workload differs from the
conditions represented in the original training dataset. The expanded
campaign and experiment-level evaluation will determine whether retraining is
necessary.

## Pilot Decision

The first run provides useful class variation, but it contains one isolated
coverage failure during startup.

The next action is to execute one additional scenario and determine whether
the startup-gap failure repeats. The remaining pilot matrix will be resumed
only after the second scenario is inspected.

No production-performance claim is made from this single run.

## Five-Run Interim Analysis

Five of the fifteen Phase 8A pilot scenarios were completed. These runs covered STEADY and RAMP traffic under LOW, MEDIUM, and HIGH demand at the INTERMEDIATE pressure level.

Across the five experiments:

- Metric samples collected: 300
- Predictions generated: 300
- Predictions with valid `live-label-v2` labels: 220
- Actual positive labels: 185
- Actual negative labels: 35
- True positives: 184
- True negatives: 2
- False positives: 33
- False negatives: 1
- Accuracy: 84.55%
- Precision: 84.79%
- Recall: 99.46%
- F1-score: 91.54%

These are interim controlled-experiment results, not final or production-performance results.

The model detected nearly all future contention cases, missing only one of the 185 positive cases. However, it incorrectly warned about contention for 33 of the 35 negative cases. Therefore, the current model has very high recall but poor ability to recognize safe or non-contention conditions.

The high F1-score must be interpreted carefully because the pilot data is strongly dominated by positive labels. Additional negative and borderline scenarios are required before retraining or making broader performance claims.

## Data-Quality Finding

Two predictions were rejected by the `live-label-v2` contract because their 30-second future windows contained sampling gaps greater than the permitted 3.5 seconds:

- LOW–STEADY maximum gap: 3.767 seconds
- HIGH–STEADY maximum gap: 3.621 seconds

The rejection is correct and prevents incomplete future evidence from becoming a potentially misleading training label.

A separate audit found that the existing version-1 CSV exporter applies a weaker future-window check than `live-label-v2`. Consequently, two exported version-1 rows did not qualify as verified live-label-v2 rows.

The existing version-1 exports remain preserved and unchanged for reproducibility. A version-2 export process has now been implemented and verified. It exports only non-stale predictions carrying complete `live-label-v2` provenance and uses the persisted observed label rather than recalculating the target.

## Current Phase 8A Status

- Planned pilot scenarios: 15
- Completed scenarios: 5
- Remaining scenarios: 10
- Current status: Partially completed
- Dataset version 2: Generated and verified for five completed experiments
- Verified version-2 rows: 220
- Negative labels: 35
- Positive labels: 185
- Model retraining: Not yet performed
- Production deployment: Not started

All five version-2 CSV files were checked against their registered database
records. Their row counts and SHA-256 hashes matched, and every accepted row
used the `live-label-v2` contract.

The dataset remains strongly weighted toward positive labels. The remaining
ten experiments are still needed before retraining decisions or final model
evaluation.

No production-performance claim is made from these five interim experiments.