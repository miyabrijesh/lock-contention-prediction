# Phase 6 Experiment Campaign

Status: Checkpoint 28 planning baseline

## Purpose

This campaign will generate a larger and more varied dataset for predicting
whether PostgreSQL will experience high lock contention within the next
30 seconds.

The campaign will include very low, low, borderline, medium, high and extreme
booking pressure.

## Important distinction

The workload level describes the pressure that we intend to create.

It is not the machine-learning label.

The final label must be calculated from measurements collected from PostgreSQL
during the experiment.

For example, a workload configured as `BORDERLINE` may produce both negative
and positive rows. This is useful because the prediction problem should not be
artificially easy.

## Common experiment configuration

Unless a later campaign section overrides a value, all Phase 6 experiments
will use:

| Setting | Value |
| --- | --- |
| Show ID | `2` |
| Booking mode | `EXPERIMENT_RECYCLE` |
| Duration | `90` seconds |
| Sample interval | `2` seconds |
| Prediction horizon | `30` seconds |
| Experiment mode | Enabled only while experiments are running |
| Created by | `local-admin` |

The experiment show contains seat IDs beginning at `101`.

## Parameters used to control pressure

### User count

The number of simulated customers operating at the same time.

More users generally create more database transactions.

### Spawn rate

The number of simulated customers started per second.

A higher spawn rate creates pressure more quickly.

### Hotspot ratio

The probability that a customer chooses one of the popular hotspot seats.

For example, a hotspot ratio of `0.80` means approximately 80 percent of
booking attempts target the hotspot seat group.

### Hotspot seat count

The number of popular seats receiving concentrated demand.

Fewer hotspot seats cause more users to compete for the same database rows.

### Simulated work

The time for which a transaction deliberately holds the seat lock.

Longer simulated work gives competing transactions more time to form a queue.

## Workload level summary

| Level | Users | Spawn rate | Hotspot ratio | Hotspot seats | Simulated work |
| --- | ---: | ---: | ---: | ---: | ---: |
| VERY_LOW | 3 | 1/sec | 0.10 | 25 | 10 ms |
| LOW | 5 | 2/sec | 0.20 | 20 | 25 ms |
| BORDERLINE | 8 | 4/sec | 0.50 | 10 | 75 ms |
| MEDIUM | 12 | 5/sec | 0.75 | 5 | 150 ms |
| HIGH | 18 | 8/sec | 0.95 | 4 | 250 ms |
| EXTREME | 20 | 10/sec | 0.98 | 3 | 350 ms |

These are starting configurations. A configuration may be adjusted after a
smoke test if it does not create the intended pressure on the development
machine.

## Level 1: VERY_LOW

### Configuration

- `user_count`: `3`
- `spawn_rate_per_sec`: `1`
- `hotspot_ratio`: `0.10`
- `hotspot_seat_ids`: seat IDs `101` through `125`
- `simulated_work_ms`: `10`

### Intended behaviour

This level represents quiet booking activity.

Customers are spread across many seats, transactions finish quickly and
database queues should be uncommon.

### Expected observations

- zero or nearly zero blocked sessions
- zero or nearly zero waiting locks
- low response latency
- high booking throughput
- mostly negative target labels

## Level 2: LOW

### Configuration

- `user_count`: `5`
- `spawn_rate_per_sec`: `2`
- `hotspot_ratio`: `0.20`
- `hotspot_seat_ids`: seat IDs `101` through `120`
- `simulated_work_ms`: `25`

### Intended behaviour

This level represents ordinary booking activity with a small amount of
customer interest concentrated on popular seats.

### Expected observations

- few blocked sessions
- few waiting locks
- stable response latency
- high booking throughput
- mostly negative target labels

This configuration is based on the successful Phase 5 low-contention pilot.

## Level 3: BORDERLINE

### Configuration

- `user_count`: `8`
- `spawn_rate_per_sec`: `4`
- `hotspot_ratio`: `0.50`
- `hotspot_seat_ids`: seat IDs `101` through `110`
- `simulated_work_ms`: `75`

### Intended behaviour

This level represents the boundary between healthy database activity and
harmful lock contention.

Small changes in customer timing or seat selection may cause contention to
appear and disappear.

### Expected observations

- some intervals with no blocked sessions
- some intervals with blocked sessions
- changing response latency
- a mixture of negative and positive target labels

This is one of the most important levels for machine-learning training.

## Level 4: MEDIUM

### Configuration

- `user_count`: `12`
- `spawn_rate_per_sec`: `5`
- `hotspot_ratio`: `0.75`
- `hotspot_seat_ids`: seat IDs `101` through `105`
- `simulated_work_ms`: `150`

### Intended behaviour

This level represents clear competition for a small group of popular seats.

### Expected observations

- several blocked sessions
- several waiting locks
- reduced throughput
- increased response latency
- mostly positive target labels

The Phase 5 medium pilot reached a maximum of seven blocked sessions and seven
waiting locks. Therefore, this level already represents meaningful contention.

## Level 5: HIGH

### Configuration

- `user_count`: `18`
- `spawn_rate_per_sec`: `8`
- `hotspot_ratio`: `0.95`
- `hotspot_seat_ids`: seat IDs `101` through `104`
- `simulated_work_ms`: `250`

### Intended behaviour

This level represents heavy flash-sale-style competition for a very small
number of popular seats.

### Expected observations

- persistent blocked sessions
- persistent waiting locks
- low booking throughput
- high response latency
- mostly positive target labels

This configuration is based on the successful Phase 5 high-contention pilot.

## Level 6: EXTREME

### Configuration

- `user_count`: `20`
- `spawn_rate_per_sec`: `10`
- `hotspot_ratio`: `0.98`
- `hotspot_seat_ids`: seat IDs `101` through `103`
- `simulated_work_ms`: `350`

### Intended behaviour

This level represents an intentionally overloaded booking system.

It will be used carefully and only after a successful short smoke test.

### Expected observations

- long lock queues
- high response latency
- reduced throughput
- possible lock timeouts
- possible database statement timeouts
- positive target labels

A deadlock is not required for this experiment to demonstrate high
contention.

## Safety rules

1. Run only one experiment at a time.
2. Use only the designated experiment show.
3. Use `EXPERIMENT_RECYCLE` booking mode.
4. Confirm that all experiment seats are `AVAILABLE` before starting.
5. Start with a short smoke test before running the extreme configuration.
6. Stop the campaign if the API or database becomes unresponsive.
7. Return `EXPERIMENT_MODE_ENABLED` to `false` after experimentation.
8. Do not use the development computer for unrelated heavy tasks during a run.

## Measurement-based target

The target column remains:

`future_high_contention_30s`

A row receives label `1` when at least one sample during the following
30 seconds contains:

- at least two blocked sessions, or
- at least two waiting locks, or
- at least one new deadlock, or
- a timeout rate greater than zero.

Otherwise, the row receives label `0`.

## Checkpoint 28A completion condition

Checkpoint 28A is complete when:

- all six workload levels are documented
- every level has an initial configuration
- expected behaviour is documented
- workload names are separated from measured ML labels
- safety rules are documented

# Checkpoint 28B: Traffic Arrival Patterns

## Purpose of traffic patterns

A workload level controls the maximum pressure placed on the booking system.

A traffic pattern controls how that pressure changes during the experiment.

Two experiments can have the same maximum number of users but behave
differently.

For example:

- ten customers arriving gradually may produce manageable pressure
- ten customers arriving together may produce a sudden lock queue

Both the workload level and the arrival pattern must therefore be recorded.

## Experiment duration

The standard Phase 6 experiment duration is `90` seconds.

Traffic patterns divide these 90 seconds into stages.

Each stage specifies the percentage of the configured user count that should
be active.

## User-count calculation

The number of users for a stage will be calculated using:

`maximum of 1 and the configured user count multiplied by the stage percentage`

Fractional results will be rounded upward.

For example, if the experiment is configured for 12 users:

| Percentage | Active users |
| ---: | ---: |
| 25% | 3 |
| 50% | 6 |
| 75% | 9 |
| 100% | 12 |

## Pattern 1: STEADY

### Description

The experiment gradually starts its configured users and then maintains
approximately the same pressure for the remainder of the run.

This represents ordinary continuous booking activity.

### Schedule

| Time | Target users |
| --- | ---: |
| 0–10 seconds | Gradually increase from 1 user to 100% |
| 10–90 seconds | Maintain 100% |

### Expected behaviour

- relatively stable request rate
- stable latency under low pressure
- persistent queues under high pressure
- useful as the baseline for comparing other patterns

## Pattern 2: RAMP

### Description

Traffic increases in stages throughout the experiment.

This represents demand gradually increasing as an event becomes more popular
or as more customers discover that booking has opened.

### Schedule

| Time | Target users |
| --- | ---: |
| 0–30 seconds | 25% |
| 30–60 seconds | 50% |
| 60–75 seconds | 75% |
| 75–90 seconds | 100% |

### Expected behaviour

- low pressure near the beginning
- increasing request rate over time
- gradually increasing latency
- contention may appear near the end
- useful for learning early warning signals

## Pattern 3: BURST

### Description

The experiment begins with light traffic, suddenly increases to maximum
traffic and then returns to light traffic.

This represents a temporary surge caused by an advertisement, notification or
social-media post.

### Schedule

| Time | Target users |
| --- | ---: |
| 0–20 seconds | 25% |
| 20–50 seconds | 100% |
| 50–90 seconds | 25% |

### Expected behaviour

- stable activity before the burst
- sudden increase in database pressure
- increased blocked sessions during the burst
- recovery after traffic decreases
- useful for testing whether the model detects an approaching surge

## Pattern 4: FLASH_SALE

### Description

A small number of customers wait before sales open. Demand then immediately
jumps to its maximum level.

This represents ticket sales opening for a highly popular movie, concert or
sporting event.

### Schedule

| Time | Target users |
| --- | ---: |
| 0–10 seconds | 10% |
| 10–45 seconds | 100% |
| 45–70 seconds | 60% |
| 70–90 seconds | 30% |

### Expected behaviour

- sudden lock competition
- rapid growth in response latency
- possible blocked sessions and waiting locks
- gradual reduction after the initial rush
- possible timeouts for high or extreme workloads

## Pattern 5: COOLDOWN

### Description

The experiment starts under maximum pressure and then gradually reduces the
number of active customers.

This represents the period after an initial ticket-booking rush.

### Schedule

| Time | Target users |
| --- | ---: |
| 0–30 seconds | 100% |
| 30–60 seconds | 50% |
| 60–90 seconds | 20% |

### Expected behaviour

- high pressure at the beginning
- decreasing blocked sessions
- decreasing waiting locks
- improving latency
- useful for observing database recovery

## Traffic-pattern summary

| Pattern | Main behaviour | Main research purpose |
| --- | --- | --- |
| STEADY | Stable customer activity | Establish a baseline |
| RAMP | Gradually increasing activity | Observe warning signals |
| BURST | Temporary sudden increase | Observe contention and recovery |
| FLASH_SALE | Immediate heavy demand | Reproduce ticket-sale pressure |
| COOLDOWN | Pressure decreases over time | Observe recovery behaviour |

## Relationship between levels and patterns

The workload level and traffic pattern are separate settings.

Examples:

- `LOW + STEADY` represents ordinary low booking traffic
- `BORDERLINE + RAMP` represents pressure gradually approaching contention
- `MEDIUM + BURST` represents a temporary busy period
- `HIGH + FLASH_SALE` represents a popular ticket launch
- `EXTREME + COOLDOWN` represents recovery after severe overload

Not every workload level must be combined with every traffic pattern.

Checkpoint 28C will define the exact combinations and random seeds used in the
campaign.

## Reproducibility requirements

Every traffic-pattern experiment must record:

- experiment run ID
- workload level
- traffic pattern
- configured maximum users
- stage schedule
- random seed
- code version
- experiment start and end timestamps

Using the same configuration and random seed should produce a comparable
workload.

Exact request timing may still vary because operating-system and database
scheduling cannot be perfectly identical.

## Normal booking protection

Traffic patterns may only control experimental workloads.

They must not change the behaviour of:

- normal customer bookings
- normal shows
- non-experiment seats
- the normal booking API contract

Experiment seat recycling remains permitted only when the experiment is
running in `EXPERIMENT_RECYCLE` mode.

## Checkpoint 28B completion condition

Checkpoint 28B is complete when:

- five traffic patterns are documented
- every pattern has an exact 90-second schedule
- every pattern has an expected behaviour
- workload level and traffic pattern are defined separately
- reproducibility requirements are documented
- normal booking behaviour remains protected


# Checkpoint 28C: Production Experiment Matrix

## Purpose

The production experiment matrix defines the workload combinations that will
be used to generate the Phase 6 machine-learning dataset.

The campaign contains `36` production experiments.

These experiments vary:

- workload pressure
- traffic arrival pattern
- random seed
- customer timing
- seat-selection order

The experiments will use the workload configurations from Checkpoint 28A and
the arrival schedules from Checkpoint 28B.

## Why experiments are repeated

One experiment is not enough to represent a workload condition.

Operating-system scheduling, network timing, PostgreSQL scheduling and random
seat selection can produce different measurements even when two experiments
have similar configurations.

Repeated experiments use different random seeds to measure this variation.

## Campaign distribution

| Workload level | Number of experiments | Main expected result |
| --- | ---: | --- |
| VERY_LOW | 4 | Mostly negative labels |
| LOW | 5 | Mostly negative labels |
| BORDERLINE | 10 | Mixture of negative and positive labels |
| MEDIUM | 7 | Mostly positive labels |
| HIGH | 6 | Positive labels |
| EXTREME | 4 | Positive labels and possible timeouts |
| Total | 36 | Mixed contention conditions |

The `BORDERLINE` level receives the largest number of experiments because the
prediction model must learn difficult transitions, not only obvious low and
high conditions.

## Production experiment matrix

| Campaign code | Workload level | Traffic pattern | Random seed | Main purpose |
| --- | --- | --- | ---: | --- |
| P6-001 | VERY_LOW | STEADY | 1101 | Quiet baseline |
| P6-002 | VERY_LOW | STEADY | 1102 | Repeat quiet baseline |
| P6-003 | VERY_LOW | RAMP | 1103 | Gradual quiet demand |
| P6-004 | VERY_LOW | BURST | 1104 | Small harmless burst |
| P6-005 | LOW | STEADY | 1201 | Normal low traffic |
| P6-006 | LOW | RAMP | 1202 | Gradually increasing low traffic |
| P6-007 | LOW | BURST | 1203 | Temporary low-level surge |
| P6-008 | LOW | FLASH_SALE | 1204 | Small sudden booking launch |
| P6-009 | LOW | COOLDOWN | 1205 | Recovery from low pressure |
| P6-010 | BORDERLINE | STEADY | 1301 | Stable boundary condition |
| P6-011 | BORDERLINE | STEADY | 1302 | Repeat stable boundary |
| P6-012 | BORDERLINE | RAMP | 1303 | Gradual approach to contention |
| P6-013 | BORDERLINE | RAMP | 1304 | Repeat gradual approach |
| P6-014 | BORDERLINE | BURST | 1305 | Temporary boundary crossing |
| P6-015 | BORDERLINE | BURST | 1306 | Repeat temporary crossing |
| P6-016 | BORDERLINE | FLASH_SALE | 1307 | Sudden boundary crossing |
| P6-017 | BORDERLINE | FLASH_SALE | 1308 | Repeat sudden crossing |
| P6-018 | BORDERLINE | COOLDOWN | 1309 | Recovery near the boundary |
| P6-019 | BORDERLINE | COOLDOWN | 1310 | Repeat boundary recovery |
| P6-020 | MEDIUM | STEADY | 1401 | Sustained clear contention |
| P6-021 | MEDIUM | RAMP | 1402 | Gradual growth into contention |
| P6-022 | MEDIUM | RAMP | 1403 | Repeat gradual growth |
| P6-023 | MEDIUM | BURST | 1404 | Temporary medium contention |
| P6-024 | MEDIUM | BURST | 1405 | Repeat medium burst |
| P6-025 | MEDIUM | FLASH_SALE | 1406 | Medium ticket-launch pressure |
| P6-026 | MEDIUM | COOLDOWN | 1407 | Recovery from medium contention |
| P6-027 | HIGH | STEADY | 1501 | Sustained high contention |
| P6-028 | HIGH | RAMP | 1502 | Gradual growth into high contention |
| P6-029 | HIGH | BURST | 1503 | Temporary high-contention burst |
| P6-030 | HIGH | FLASH_SALE | 1504 | Heavy ticket-launch pressure |
| P6-031 | HIGH | FLASH_SALE | 1505 | Repeat heavy launch |
| P6-032 | HIGH | COOLDOWN | 1506 | Recovery from high contention |
| P6-033 | EXTREME | STEADY | 1601 | Sustained overload |
| P6-034 | EXTREME | BURST | 1602 | Sudden temporary overload |
| P6-035 | EXTREME | FLASH_SALE | 1603 | Maximum launch pressure |
| P6-036 | EXTREME | COOLDOWN | 1604 | Recovery from overload |

## Campaign code

Every planned experiment has a unique campaign code such as:

`P6-014`

The campaign code identifies the planned experiment configuration.

The PostgreSQL `experiment_run_id` identifies the actual executed experiment.

Both values must be retained so that a dataset row can be traced back to:

1. its campaign configuration
2. its experiment database record
3. its collected metric samples
4. its exported CSV file

## Random-seed ranges

Random seeds are grouped by workload level:

| Workload level | Seed range |
| --- | --- |
| VERY_LOW | 1101–1104 |
| LOW | 1201–1205 |
| BORDERLINE | 1301–1310 |
| MEDIUM | 1401–1407 |
| HIGH | 1501–1506 |
| EXTREME | 1601–1604 |

A random seed must not be changed after an experiment begins.

If an experiment must be repeated because of a technical failure, the repeated
run must retain the original seed and record that it replaced the failed run.

## Smoke experiments

Short smoke experiments are separate from the 36 production experiments.

Smoke experiments are used to verify that:

- the API is running
- experiment mode is enabled
- the workload generator supports the selected traffic pattern
- the metrics collector stores samples
- the lifecycle finishes correctly
- experiment seats return to `AVAILABLE`

Smoke-test rows must not be included in the final machine-learning dataset.

Smoke experiments should use campaign codes beginning with:

`SMOKE-`

Examples:

- `SMOKE-STEADY`
- `SMOKE-RAMP`
- `SMOKE-BURST`
- `SMOKE-FLASH`
- `SMOKE-COOLDOWN`

## Execution order

The experiments must not automatically run from `P6-001` through `P6-036` in
increasing pressure order.

Running all low experiments first and all extreme experiments last could allow
machine temperature, background activity or database state to become
associated with the workload level.

The execution order should mix workload levels.

The final execution order will be generated and recorded before data
collection begins.

Only one experiment may run at a time.

## Expected dataset size

A 90-second experiment sampled every 2 seconds produces approximately 45
metric samples.

Rows near the end cannot be labelled because they do not have a complete
30-second future window.

Each experiment should therefore produce approximately 29 or 30 eligible
labelled rows.

The estimated Phase 6 dataset size is:

`36 experiments multiplied by approximately 30 rows`

This gives approximately:

`1,080 labelled rows`

The exact row count may be slightly lower because real sampling timestamps can
drift.

## Expected label behaviour

The intended label behaviour is:

| Workload level | Expected label behaviour |
| --- | --- |
| VERY_LOW | Almost entirely `0` |
| LOW | Mostly `0` |
| BORDERLINE | Mixture of `0` and `1` |
| MEDIUM | Mostly `1` |
| HIGH | Almost entirely `1` |
| EXTREME | Almost entirely `1` |

These expectations are not allowed to create the labels.

Labels must always be calculated from future measured telemetry.

If the `BORDERLINE` experiments produce only one class, their parameters must
be adjusted and additional boundary experiments must be performed.

## Real-world calibration placeholder

Checkpoint 29 will add an external demand-calibration reference to the
campaign.

The external dataset may influence:

- relative item popularity
- hotspot probability
- burst strength
- demand concentration
- arrival timing

It will not supply PostgreSQL contention labels.

The contention labels must continue to come from measured PostgreSQL
telemetry.

## Dataset-splitting warning

Rows from one experiment are closely related because they were collected from
the same continuous workload.

Rows from the same `experiment_run_id` must not be divided between training
and testing.

The final dataset will be split by complete experiments.

The final split will be created after the campaign has been collected and its
actual label distribution is known.

## Checkpoint 28C completion condition

Checkpoint 28C is complete when:

- exactly 36 production experiments are documented
- every production experiment has a unique campaign code
- every production experiment has a random seed
- workload levels and traffic patterns are represented
- borderline conditions receive additional repetitions
- smoke experiments are excluded from the production matrix
- expected dataset size is documented
- experiment-level dataset splitting is required


# Checkpoint 28D: Acceptance and Data-Quality Rules

## Purpose

These rules determine whether an executed experiment may enter the final
Phase 6 machine-learning dataset.

An experiment must be accepted or rejected using technical evidence.

An experiment must not be removed merely because its measured result differs
from the expected workload level.

## Experiment review outcomes

Every production experiment will receive one of these review outcomes:

| Review outcome | Meaning |
| --- | --- |
| ACCEPTED | The experiment and its data satisfy all required checks |
| ACCEPTED_WITH_NOTE | Required checks pass, but a non-critical observation must be documented |
| REPEAT_REQUIRED | A technical or data-quality failure makes the run unsuitable |
| NOT_RUN | The planned experiment has not yet been executed |

Only `ACCEPTED` and `ACCEPTED_WITH_NOTE` experiments may enter the combined
dataset.

## Required lifecycle checks

An experiment can be accepted only when:

1. The experiment began in `DRAFT`.
2. The experiment successfully transitioned to `RUNNING`.
3. The workload and collector started after the experiment became `RUNNING`.
4. The workload process finished with exit code `0`.
5. The collector process finished with exit code `0`.
6. The experiment transitioned to `COMPLETED`.
7. `started_at` is present.
8. `ended_at` is present.
9. `ended_at` is later than `started_at`.
10. The audit trail contains:
    - `experiment_created`
    - `experiment_started`
    - `experiment_completed`

Experiments ending in `FAILED` or `CANCELLED` cannot enter the production
dataset.

## Required traceability checks

Every accepted experiment must retain:

- campaign code
- experiment run ID
- experiment name
- workload level
- traffic pattern
- random seed
- code version
- schema version
- experiment start time
- experiment end time
- workload exit code
- collector exit code
- export file path
- export SHA-256 checksum
- review outcome
- review notes

Every exported row must contain the same `experiment_run_id` as its source
experiment.

## Metric-sample acceptance rules

A standard 90-second experiment sampled every 2 seconds should normally
produce approximately 45 metric samples.

An experiment may be accepted only when:

- at least 42 metric samples exist
- sample timestamps are in increasing order
- duplicate sample timestamps do not exist
- all samples reference the correct experiment
- all required measurements are present
- every accepted sample has `is_complete` set to true
- the sampling period covers at least 82 seconds
- no gap between consecutive samples exceeds 6 seconds

The label calculation must use timestamps rather than assuming that every
sample occurs at an exact 2-second boundary.

## Required measurement validation

The following counts must be zero or greater:

- active transactions
- blocked sessions
- waiting locks
- total locks
- total connections
- deadlock delta
- configured users
- active users

The following measurements must be zero or greater:

- requests per second
- mean latency
- p95 latency
- estimated wait age

The following rates must remain between zero and one:

- connection utilization
- failure rate
- hotspot ratio
- booking success rate
- conflict rate
- timeout rate

A sample containing a negative count, impossible rate or missing required
measurement is invalid.

## Workload validation

An accepted experiment must contain:

- at least one booking attempt
- at least one request measured during the experiment
- booking attempts linked to the correct experiment run
- no normal-show bookings incorrectly linked to the experiment
- no experiment bookings incorrectly linked to another experiment

Initial metric samples may contain zero requests while users are starting.

An experiment must be repeated if every sample reports zero requests.

## Seat-recycling validation

After an `EXPERIMENT_RECYCLE` workload finishes:

- all experiment-show seats must be `AVAILABLE`
- no experiment seat may remain `HELD`
- no experiment seat may remain `BOOKED`
- seat versions may increase
- successful experimental bookings may be stored as `CANCELLED`
- booking-attempt telemetry must remain preserved

A seat remaining unavailable after the experiment is a technical failure and
requires investigation before another production experiment is run.

## Export validation

Every accepted experiment must produce one immutable CSV export.

The export must satisfy these rules:

- schema version is `1.0.0`
- the file exists
- the file contains a header
- the file contains at least 25 labelled rows
- all rows have a complete 30-second future horizon
- every row references the same source experiment
- every row has a target value of either `0` or `1`
- incomplete telemetry does not enter the export
- the calculated SHA-256 matches the value stored in `data_exports`
- the export file path is registered in `data_exports`

A difference between the stored checksum and the calculated checksum means
that the file was changed after export. That export must not be used.

## Unexpected-result rule

Workload names describe intended pressure, not guaranteed results.

Examples of unexpected results include:

- a LOW experiment producing positive labels
- a BORDERLINE experiment producing only negative labels
- a HIGH experiment producing some negative labels
- an EXTREME experiment producing no timeouts
- an experiment producing no deadlocks

An unexpected result is not automatically a bad experiment.

The review process must first determine whether:

- the workload executed correctly
- the collector executed correctly
- the database measurements are complete
- the experiment is traceable
- the export is valid

If all technical checks pass, the unexpected result must remain in the
dataset.

Removing correctly measured unexpected results would bias the dataset.

## Deadlock interpretation

A high-contention experiment does not need to produce a deadlock.

Lock contention can create:

- blocked sessions
- waiting locks
- high latency
- reduced throughput
- timeouts

without creating a circular deadlock.

Zero deadlocks must therefore not be used as a reason to reject an
experiment.

## Repetition rules

An experiment must be repeated when:

- the workload exits with a non-zero code
- the collector exits with a non-zero code
- the experiment does not reach `COMPLETED`
- fewer than 42 metric samples are recorded
- the sampling period covers less than 82 seconds
- a sample gap exceeds 6 seconds
- required telemetry is missing
- the workload produces no requests
- seats do not return to `AVAILABLE`
- the export checksum is invalid
- the export contains fewer than 25 labelled rows
- traceability information is missing

A repeated experiment must:

- use the same campaign code
- use the same workload configuration
- use the same traffic pattern
- use the same random seed
- receive a new experiment run ID
- record the failed experiment run ID in its notes

The failed run must remain in the database for audit purposes, but its rows
must not enter the production dataset.

## Campaign-level acceptance targets

The complete Phase 6 campaign should satisfy:

| Requirement | Target |
| --- | ---: |
| Planned production experiments | 36 |
| Preferred accepted experiments | 36 |
| Minimum accepted experiments | 30 |
| Minimum accepted borderline experiments | 8 |
| Minimum labelled dataset rows | 800 |
| Preferred labelled dataset rows | Approximately 1,080 |
| Minimum traffic patterns represented | 5 |
| Minimum workload levels represented | 6 |
| Minimum experiments containing negative labels | 10 |
| Minimum experiments containing positive labels | 10 |
| Preferred mixed-label experiments | At least 3 |

The minimum of 30 experiments is an emergency lower bound, not the normal
goal.

A campaign containing fewer than 30 accepted experiments is not ready for
model training.

## Label-balance targets

The preferred target distribution is:

- 35% to 65% negative rows
- 35% to 65% positive rows

The acceptable outer limit is:

- no class below 25%
- no class above 75%

If the label balance falls outside the acceptable range:

1. Do not delete valid rows from the majority class.
2. Identify workload conditions likely to create the minority class.
3. Run additional experiments.
4. Export and validate the new experiments.
5. Rebuild the combined dataset.

Class balance must be improved by collecting evidence, not by manipulating
labels.

## Borderline-data target

The final dataset must not contain only obviously healthy and obviously
overloaded conditions.

Borderline experiments should provide:

- intervals with zero or one waiting session
- intervals with at least two waiting sessions
- rising latency before contention
- recovery after contention
- both positive and negative future labels where possible

If all borderline experiments produce the same label, additional intermediate
configurations must be designed.

Possible adjustments include:

- changing user count by one or two users
- changing simulated work by 10 to 25 milliseconds
- adding or removing one hotspot seat
- changing hotspot ratio by 0.05
- changing the timing of a burst

## Data-leakage prevention

The model must predict the future using information available at the current
sample time.

The following future-evidence columns must not be used as model inputs:

- `future_max_blocked_sessions_30s`
- `future_max_waiting_locks_30s`
- `future_deadlock_delta_sum_30s`
- `future_max_timeout_rate_30s`
- `future_max_p95_latency_ms_30s`
- `future_high_contention_30s`

The final column in this list is the target.

The other future columns may be retained for auditing and explanation, but
they must be excluded from model features.

The following identifiers must also be excluded from model features:

- experiment run ID
- experiment name
- campaign code
- export ID
- file path
- SHA-256 checksum

Identifiers help trace data but do not describe database behaviour.

## Dataset-split acceptance rule

Training, validation and testing data must be separated by complete
experiments.

All rows from one `experiment_run_id` must belong to only one split.

The same experiment must never appear in both:

- training and validation
- training and testing
- validation and testing

The final split should approximately follow:

- 60% training experiments
- 20% validation experiments
- 20% testing experiments

Each split should contain both positive and negative labels.

If a split contains only one class, experiment groups must be reassigned
without splitting individual experiments.

## Dataset versioning

The Phase 5 pilot dataset remains version `1.0.0`.

The Phase 6 production dataset must receive a new dataset version.

The final version number will be assigned when the dataset schema and campaign
results are reviewed.

Existing exports must not be silently overwritten.

A changed dataset must receive:

- a new output file
- a new manifest
- a new SHA-256 checksum
- documented source experiment IDs
- a documented creation timestamp

## Final campaign decision

The complete Phase 6 dataset may be marked `ML_READY` only when:

- campaign-level minimums pass
- label-balance limits pass
- required traffic patterns are represented
- required workload levels are represented
- all included experiments are accepted
- all checksums are valid
- no incomplete samples are included
- future-evidence columns are excluded from model inputs
- experiment-level data splitting is possible
- limitations are documented

If any required condition fails, the dataset remains `NOT_READY`.

## Checkpoint 28D completion condition

Checkpoint 28D is complete when:

- experiment acceptance rules are documented
- experiment rejection and repetition rules are documented
- unexpected results are protected from selective removal
- metric and export quality thresholds are documented
- campaign size and class-balance targets are documented
- data-leakage prevention is documented
- experiment-level splitting is required
- the `ML_READY` decision is defined