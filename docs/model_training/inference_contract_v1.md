# Contention Model Inference Contract

## Status

Checkpoint 34A inference contract.

Contract version: `1.0.0`

Model name: `contention-logistic-v1`

## Purpose

This contract defines how the frozen lock-contention model must be loaded, validated, and used for inference.

The model predicts whether high PostgreSQL lock contention will occur within the next 30 seconds.

This contract does not create an API endpoint. Live API integration belongs to Phase 7.

## Frozen model artifact

| Property | Value |
|---|---|
| Artifact file | `artifacts/models/contention_candidate_v1.joblib` |
| Artifact format | Joblib |
| Model | Logistic regression |
| Pipeline | `StandardScaler` followed by `LogisticRegression` |
| Artifact SHA-256 | `e255db6ee5d3a59c075b6249fc7d355e5107dd56b2ed7ddab695bb029db24704` |
| Classification threshold | `0.34` |
| Prediction horizon | 30 seconds |
| Target | `future_high_contention_30s` |
| Feature count | 18 |

The artifact checksum must be verified before deserialization. Inference must stop if the checksum does not match.

## Dataset provenance

| Property | Value |
|---|---|
| Dataset | `contention_training_dataset_v1` |
| Total experiments | 60 |
| Total labelled rows | 2,666 |
| Training experiments | 42 |
| Validation experiments | 9 |
| Test experiments | 9 |
| Split assignment SHA-256 | `4a24f55d3d1121f50da74ae3ec21cfba4a6f3f9044069edff7113d228c4c8374` |

Experiments were assigned as complete groups. Rows from one experiment were never divided across train, validation, and test splits.

## Required feature order

Inference must supply the following 18 features in exactly this order:

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

The feature order stored inside the artifact must exactly match this contract.

## Input source

Version-1 inference accepts one complete `MetricSample`.

The following metadata identifies the prediction but must not be supplied as model features:

- `experiment_run_id`
- `metric_sample_id`
- `sampled_at`
- `collector_version`
- `code_version`
- `booking_mode`
- `random_seed`
- `sample_interval_sec`
- `simulated_work_ms`
- `is_complete`
- `quality_notes`

## Input validation

Inference must be rejected when:

- a required feature is missing;
- an unexpected feature is supplied to the low-level inference function;
- a feature value is `null`;
- a feature value is not numeric;
- a feature value is `NaN` or infinite;
- a count, rate, ratio, latency, or duration value is negative;
- `connection_utilization` is outside `0` to `1`;
- `failure_rate` is outside `0` to `1`;
- `hotspot_ratio` is outside `0` to `1`;
- `booking_success_rate` is outside `0` to `1`;
- `conflict_rate` is outside `0` to `1`;
- `timeout_rate` is outside `0` to `1`;
- the source metric sample has `is_complete = false`;
- the model artifact checksum is invalid;
- the artifact feature order differs from this contract;
- the artifact threshold differs from `0.34`;
- the artifact model name differs from `logistic_regression`.

Boolean values must not be accepted as numeric feature values.

A failed inference must not create a `Prediction` database record.

## Output contract

A successful inference result contains:

| Field | Type | Meaning |
|---|---|---|
| `probability` | Decimal-compatible number | Probability of high contention within 30 seconds |
| `risk_level` | `RiskLevel` | `LOW` or `HIGH` |
| `threshold_used` | Decimal-compatible number | Frozen threshold `0.34` |
| `model_name` | String | `contention-logistic-v1` |
| `model_artifact_sha256` | String | SHA-256 of the loaded artifact |
| `feature_schema_version` | String | `1.0.0` |
| `target_horizon_sec` | Integer | `30` |

The probability must be finite and within `0` to `1`.

## Risk-level mapping

| Condition | Risk level |
|---|---|
| `probability < 0.34` | `LOW` |
| `probability >= 0.34` | `HIGH` |

The boundary value `0.34` is classified as `HIGH`.

`MEDIUM` is not emitted by this binary version-1 model.

`UNKNOWN` is not emitted by successful inference. Invalid inputs or invalid artifacts must produce a controlled error instead of an `UNKNOWN` prediction.

## Prediction database mapping

A successful Phase 7 prediction maps to the `predictions` table as follows:

| Database field | Source |
|---|---|
| `experiment_run_id` | Source metric sample experiment |
| `metric_sample_id` | Source metric sample |
| `model_version_id` | Active registered `ModelVersion` |
| `predicted_at` | Database creation time |
| `target_window_start` | Source `sampled_at` |
| `target_window_end` | Source `sampled_at` plus 30 seconds |
| `probability` | Model probability rounded for `Numeric(7,6)` storage |
| `risk_level` | Contract risk mapping |
| `threshold_used` | `0.340000` |
| `is_stale` | `false` when initially created |
| `quality_flags` | Empty list for valid inference |
| `actual_label` | `null` until the future target window can be evaluated |

The existing unique constraint on `metric_sample_id` prevents duplicate predictions for one sample.

## Model-version registration mapping

Checkpoint 34C must register the model using:

| `ModelVersion` field | Required value |
|---|---|
| `name` | `contention-logistic-v1` |
| `algorithm` | `StandardScaler + LogisticRegression` |
| `feature_schema` | Ordered 18-feature contract with schema version `1.0.0` |
| `target_horizon_sec` | `30` |
| `training_run_ids` | The 42 frozen training experiment IDs |
| `validation_strategy` | Grouped train, validation, and test split by `experiment_run_id` |
| `risk_thresholds` | High contention threshold `0.34` |
| `evaluation_metrics` | Frozen Checkpoint 33D test metrics |
| `artifact_path` | `artifacts/models/contention_candidate_v1.joblib` |
| `artifact_sha256` | `e255db6ee5d3a59c075b6249fc7d355e5107dd56b2ed7ddab695bb029db24704` |
| `code_version` | Git commit used for registration |
| `is_active` | `false` during initial registration |

Activation must be a separate controlled operation. The database permits only one active model version.

## Frozen test evaluation

| Metric | Result |
|---|---:|
| Test rows | 402 |
| Test experiments | 9 |
| PR-AUC | 0.9822 |
| ROC-AUC | 0.9708 |
| F2 | 0.9367 |
| Precision | 0.8114 |
| Recall | 0.9744 |
| False positives | 53 |
| False negatives | 6 |

The threshold favors recall. It catches most upcoming high-contention cases but produces additional false warnings.

The test set must not be reused for further model or threshold selection.

## Determinism requirements

For the same:

- verified model artifact;
- ordered feature vector;
- feature schema;
- classification threshold;
- scikit-learn environment;

the inference probability and risk result must be repeatable.

Checkpoint 34B tests must verify deterministic inference using repeated identical inputs.

## Security requirements

- Do not accept a model path directly from an untrusted API request.
- Use only the configured registered artifact path.
- Verify SHA-256 before deserializing the Joblib artifact.
- Do not log model feature values when logs could expose operational data.
- Do not silently replace an invalid or missing value with zero.
- Do not silently reorder unknown input fields.
- Return controlled errors without exposing filesystem paths to API clients.

Joblib files can execute Python code during loading. Only the tracked, checksum-verified project artifact may be loaded.

## Scientific limitations

- The model was trained using controlled synthetic reservation transactions.
- MovieLens supplied popularity concentration, not ticket purchases or database-lock measurements.
- Lock-contention labels came from measured PostgreSQL experiment telemetry.
- The current evidence describes the local PostgreSQL configuration and Mac hardware used for collection.
- Performance may change with different database settings, hardware, connection pools, transaction logic, traffic, or workloads.
- A high test score does not prove equivalent production performance.
- Operational monitoring and future retraining are still required.

## Checkpoint 34A completion condition

Checkpoint 34A is complete when:

- this contract is tracked;
- the artifact path and SHA-256 match the frozen candidate;
- all 18 features and their exact order match the artifact;
- threshold `0.34` matches the artifact and selection report;
- test metrics match the frozen Checkpoint 33D report;
- risk mapping is unambiguous at the threshold boundary;
- invalid-input behavior is defined;
- database mappings match the existing `ModelVersion` and `Prediction` models;
- the inference-contract validation tests pass;
- the complete project test suite passes;
- `git diff --check` reports no whitespace errors.