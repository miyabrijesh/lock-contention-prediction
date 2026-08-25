# Checkpoint 31 Experiment Matrix Contract

**Status:** Implementation complete
**Matrix version:** `1.0.0`

## Objective

Generate a reproducible training dataset containing multiple demand concentrations, time-varying traffic patterns, system-pressure levels, and random seeds.

This replaces reliance on the 89-row pilot dataset, which was useful for pipeline verification but too small for credible model evaluation.

## Matrix Dimensions

| Dimension | Values | Count |
|---|---|---:|
| Traffic pattern | `STEADY`, `RAMP`, `BURST`, `FLASH_SALE`, `COOLDOWN` | 5 |
| Demand profile | `LOW`, `MEDIUM`, `HIGH` | 3 |
| Pressure level | `STANDARD`, `STRESSED` | 2 |
| Random seed | `4101`, `4102` | 2 |

The total number of planned experiment runs is:

```text
5 traffic patterns × 3 demand profiles × 2 pressure levels × 2 random seeds = 60 runs
```

## Fixed Settings

| Setting | Value |
|---|---|
| Experiment show | `2` |
| Duration | 120 seconds |
| Sampling interval | 2 seconds |
| Prediction horizon | 30 seconds |
| Booking mode | `EXPERIMENT_RECYCLE` |

A 120-second run sampled every two seconds produces approximately 60 metric samples.

Samples without a complete 30-second future window are excluded from the labelled dataset. This leaves approximately 44–45 labelled rows per experiment.

The initially estimated combined dataset size was approximately 2,700 rows.

## Demand-Profile Settings

| Profile | Hotspot ratio | Hotspot seats |
|---|---:|---:|
| `LOW` | 0.20 | 20 |
| `MEDIUM` | 0.60 | 8 |
| `HIGH` | 0.95 | 4 |

All values are within the tracked MovieLens-calibrated ranges.

The profiles represent demand concentration only. They do not provide contention labels.

## Pressure Levels

| Level | Users | Spawn rate | Simulated lock work |
|---|---:|---:|---:|
| `STANDARD` | 8 | 4 users/second | 75 ms |
| `STRESSED` | 18 | 8 users/second | 250 ms |

Pressure is varied independently from demand concentration.

Using only one pressure level for each demand profile would confound demand concentration with contention outcome. The independent pressure dimension allows the effect of demand concentration to be studied under different system loads.

## Hotspot-Seat Selection

The matrix runner must:

1. Load available seats for experiment show `2`.
2. Sort the seats by database ID.
3. Select the first required number of seats for the chosen demand profile.

The runner must not assume that seat IDs always begin at `101`.

## Scenario Identity

A scenario is uniquely identified by the combination of:

- Matrix version
- Traffic pattern
- Demand profile
- Pressure level
- Random seed

No duplicate scenario identity may appear in one matrix manifest.

## Run Naming

Each experiment name must include:

- Matrix version
- Demand profile
- Traffic pattern
- Pressure level
- Random seed

Example:

```text
M31-v1.0.0-HIGH-BURST-STRESSED-s4101
```

The matrix manifest may use a longer scenario identity such as:

```text
1.0.0-HIGH-BURST-STRESSED-seed4101
```

## Lifecycle Requirements

For every run, the automation must:

1. Verify that no experiment is currently `RUNNING`.
2. Create a `DRAFT` experiment through the API.
3. Start the synchronized workload and metrics collector.
4. Require successful completion of both child processes.
5. Verify that the final experiment status is `COMPLETED`.
6. Export the labelled experiment CSV.
7. Record the result in the matrix execution manifest.

Each recorded result must include:

- Scenario configuration
- Experiment run ID
- Database status
- Export path
- Export row count
- Export checksum
- Attempt status
- Start and end timestamps
- Failure reason, when applicable

The matrix must stop on an unexpected infrastructure or validation error unless an explicit resume operation is requested.

## Resume and Idempotency Requirements

The runner must maintain an incremental manifest so interrupted data collection can resume without silently duplicating completed scenarios.

A resume operation must:

- Skip scenarios already marked `COMPLETED`.
- Reuse a safe existing `DRAFT` or `COMPLETED` experiment when appropriate.
- Reject an experiment that is unexpectedly still `RUNNING`.
- Preserve previous attempt records.
- Reject a changed matrix configuration when its checksum no longer matches the manifest.

The automation must never delete previous experiments or exports.

Rebuilding a dataset must create a new versioned combined file and manifest rather than silently overwriting an existing version.

## Dataset-Quality Requirements

Before the matrix dataset is accepted:

- Every planned scenario must have a terminal status.
- Successful runs must contain metric samples.
- Successful runs must have a registered data export.
- Source-export checksums must match their files.
- Both target labels must exist in the combined dataset.
- Incomplete telemetry rows must be excluded.
- Duplicate experiment and sample-timestamp pairs must be rejected.
- Duplicate scenario identities must be rejected.
- Experiment IDs must remain in the combined dataset.
- The combined dataset checksum must match its manifest.
- Label values must agree with their recorded future evidence.

## Evaluation Leakage Rule

Rows from one experiment are temporally related.

Rows belonging to the same `experiment_run_id` must never be divided across training, validation, and test sets.

Model evaluation must group by complete experiments rather than randomly splitting individual rows.

Future-evidence columns used to create the target must never be supplied as model features.

## Observed Checkpoint 31 Outcome

The completed matrix produced:

| Result | Observed value |
|---|---:|
| Planned scenarios | 60 |
| Completed scenarios | 60 |
| Running scenarios remaining | 0 |
| Traffic patterns represented | 5 |
| Demand profiles represented | 3 |
| Metric samples collected | 3,600 |
| Registered experiment exports | 60 |
| Labelled rows produced | 2,666 |
| Minimum rows per experiment | 44 |
| Maximum rows per experiment | 45 |
| Negative labels | 1,106 |
| Positive labels | 1,560 |

The final combined dataset contains approximately 41.5% negative labels and 58.5% positive labels.

## Scientific Limitations

- Reservations remain controlled synthetic transactions.
- MovieLens supplies real-world popularity concentration, not ticket purchases or database-lock measurements.
- Contention labels come from measured PostgreSQL and booking telemetry.
- Results initially describe the local PostgreSQL instance and Mac hardware used for collection.
- The dataset contains two random seeds per matrix combination, which is sufficient for the baseline but not for broad statistical generalisation.
- Additional seeds, pressure levels, hardware environments, database configurations, and real operational traces may be introduced later as new versioned datasets.