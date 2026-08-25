# Training Dataset Split Contract

## Purpose

This contract defines the versioned train, validation, and test split used by the lock-contention prediction project. It prevents experimental leakage and preserves enough provenance to reproduce the split.

## Source dataset

- Dataset: `data/processed/contention_training_dataset_v1.csv`
- Dataset SHA-256: `14e759143a5adf77f13d578bfccf2b448176c1abe61d8b32a1937c26c213d58b`
- Total rows: 2,666
- Total experiments: 60
- Negative labels: 1,106
- Positive labels: 1,560
- Target column: `future_high_contention_30s`
- Prediction horizon: 30 seconds

## Split strategy

Rows are grouped by `experiment_run_id`. Every row from one experiment must must remain in exactly one split. Individual rows must never be randomly divided across train, validation, and test data.

The deterministic splitter uses split seed `3201` and assignment SHA-256 `4a24f55d3d1121f50da74ae3ec21cfba4a6f3f9044069edff7113d228c4c8374`.

| Split | Experiments | Rows | Negative | Positive | Purpose |
|---|---:|---:|---:|---:|---|
| Train | 42 | 1,864 | 772 | 1,092 | Fit model parameters |
| Validation | 9 | 400 | 166 | 234 | Select models, thresholds, and hyperparameters |
| Test | 9 | 402 | 168 | 234 | One-time final evaluation |

The experiment sets are pairwise disjoint and their union contains all 60 source experiments.

## Required matrix coverage

Every split contains examples from all marginal experiment dimensions:

- Traffic patterns: `STEADY`, `RAMP`, `BURST`, `FLASH_SALE`, `COOLDOWN`
- Demand profiles: `LOW`, `MEDIUM`, `HIGH`
- Pressure levels: `STANDARD`, `STRESSED`
- Random seeds: `4101`, `4102`

This requirement preserves broad workload coverage, but it does not imply that every possible dimension combination appears in every split.

## Approved model features

Only measurements available at prediction time may be used as model inputs:

- `active_transactions`
- `blocked_sessions`
- `waiting_locks`
- `total_locks`
- `total_connections`
- `connection_utilization`
- `estimated_wait_age_ms`
- `deadlock_delta`
- `configured_users`
- `active_users`
- `requests_per_sec`
- `mean_latency_ms`
- `p95_latency_ms`
- `failure_rate`
- `hotspot_ratio`
- `booking_success_rate`
- `conflict_rate`
- `timeout_rate`

## Columns forbidden as model inputs

The following future-evidence columns directly determine the target and would cause target leakage:

- `future_max_blocked_sessions_30s`
- `future_max_waiting_locks_30s`
- `future_deadlock_delta_sum_30s`
- `future_max_timeout_rate_30s`
- `future_max_p95_latency_ms_30s`

The target `future_high_contention_30s` is used only as `y`, never as an input feature.

Identifiers, timestamps, provenance, and quality-control fields are not baseline model features:

- `schema_version`
- `experiment_run_id`
- `experiment_name`
- `sampled_at`
- `collector_version`
- `code_version`
- `booking_mode`
- `random_seed`
- `sample_interval_sec`
- `simulated_work_ms`
- `is_complete`
- `quality_notes`

## Evaluation rules

1. Fit preprocessing transformations only on the training split.
2. Use validation data for model selection and decision-threshold selection.
3. Do not inspect test metrics while tuning the model.
4. Evaluate the selected model once on the test split.
5. Report confusion matrix, precision, recall, F1, PR-AUC, ROC-AUC, and calibration where applicable.
6. Keep the frozen split assignment unchanged when comparing candidate models.

## Reproduction

The generated CSV files under `data/processed/` are intentionally excluded from Git. Recreate them only after reconstructing and auditing the source dataset:

```bash
python -m data_pipeline.split_training_dataset
```

The tracked file `docs/data/contention_training_dataset_v1_splits.json` freezes the experiment assignment, row counts, label distribution, coverage, and checksums for verification.