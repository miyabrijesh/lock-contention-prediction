# Phase 8A Expanded Experiment Plan

## Status

- Phase: 8A
- Status: Pilot planning
- Previous systematic campaign: 60 experiments
- Total completed experiments currently stored: 70
- Previous dataset: `contention-training-v1`
- Intended expanded dataset: `contention-training-v2`
- Active label contract: `live-label-v2`
- Prediction horizon: 30 seconds
- Sampling interval: 2 seconds
- Database: PostgreSQL 16.15

## Purpose

Phase 8A expands the controlled reservation workload so that the prediction
model can be evaluated across a broader range of database-pressure conditions.

The original version-1 dataset and its results will remain unchanged. New
experiments will be used to create a separately versioned dataset.

The purpose is not simply to produce more rows. The new campaign must add
meaningfully different workload conditions, improve repetition and reduce
confounding between experimental variables.

## Existing Evidence

The original systematic campaign contains 60 experiments produced from:

- five traffic patterns;
- three demand profiles;
- two user-count levels;
- two random seeds.

The matrix calculation is:

```text
5 traffic patterns
× 3 demand profiles
× 2 user levels
× 2 random seeds
= 60 experiments
```

The five traffic patterns are:

- `STEADY`
- `RAMP`
- `BURST`
- `FLASH_SALE`
- `COOLDOWN`

The three demand profiles are:

- `LOW`
- `MEDIUM`
- `HIGH`

The original systematic campaign mainly used:

- 8 and 18 configured users;
- random seeds 4101 and 4102;
- 75 ms and 250 ms of simulated transaction work;
- 120-second experiment duration;
- two-second sampling intervals.

The local development database currently contains 70 completed experiments.
The additional experiments include setup, diagnostic, live-validation and
demonstration runs. They must not automatically be treated as members of the
original systematic training campaign.

## Meaning of the Experimental Variables

### Traffic pattern

The traffic pattern describes how the number of active users changes during an
experiment.

- `STEADY` represents relatively stable traffic.
- `RAMP` gradually increases the number of users.
- `BURST` introduces a sudden temporary increase.
- `FLASH_SALE` represents intense demand concentrated into a short period.
- `COOLDOWN` begins with pressure and then reduces it.

Testing multiple patterns is necessary because two experiments can contain the
same maximum number of users while producing very different database
behaviour.

### Demand profile

The demand profile controls how user choices are distributed across available
seats.

- `LOW` spreads demand across many seats.
- `MEDIUM` produces moderate concentration.
- `HIGH` directs most demand towards a small group of hotspot seats.

Demand profiles were calibrated using popularity concentration derived from
MovieLens 1M. MovieLens does not provide the contention labels and is not used
as the classifier-training dataset.

### Configured users

Configured users represent the maximum number of virtual Locust users active
under the traffic plan. A virtual user sends genuine HTTP booking requests to
the reservation API.

### Spawn rate

Spawn rate controls how quickly virtual users are introduced. A high spawn rate
creates a faster increase in application and database pressure.

### Hotspot ratio

The hotspot ratio is the probability that a booking attempt selects a seat
from the hotspot group.

For example, a hotspot ratio of `0.95` means that approximately 95% of seat
selections are directed towards the small hotspot-seat group.

### Hotspot-seat count

The hotspot-seat count determines the number of highly demanded seats. A small
hotspot group combined with a high hotspot ratio increases the probability that
multiple users compete for the same database records.

### Simulated transaction work

Simulated transaction work is the controlled amount of time spent inside the
booking transaction. Increasing this value keeps database resources occupied
for longer and can increase lock contention.

This setting is an experimental database-pressure variable. It is not derived
from MovieLens.

### Random seed

The random seed makes pseudo-random user decisions reproducible. Repeating a
configuration with different seeds tests whether its result is stable rather
than being caused by one accidental sequence of seat selections.

## Identified Limitations of the Existing Campaign

The original campaign provides a controlled baseline, but it has several
limitations:

1. Only two main user-count levels were systematically tested.
2. Only two random seeds were used for each main configuration.
3. User count and simulated transaction work were often changed together.
4. Higher-load conditions were not systematically represented.
5. Experiments were executed on one local machine.
6. Real production booking-platform measurements were unavailable.
7. Later validation and demonstration runs were not part of the original
   systematic 60-run matrix.

The relationship between user count and simulated work is particularly
important. If user count and work duration always increase together, it becomes
difficult to determine which variable caused the observed increase in
contention.

## Expansion Strategy

Phase 8A will add controlled variation without changing every setting at once.

The expansion will examine:

- additional user-count levels;
- additional transaction-work durations;
- additional random seeds;
- all five traffic patterns;
- all three calibrated demand profiles.

The objective is to distinguish the influence of:

- crowd size;
- demand concentration;
- traffic shape;
- request-arrival speed;
- transaction duration.

The expansion must improve experimental coverage rather than merely duplicate
the existing configurations.

## Demand Configuration

The MovieLens-calibrated profiles remain:

| Profile | Hotspot-ratio range | Hotspot-seat-count range | Initial value |
|---|---:|---:|---|
| `LOW` | 0.10–0.30 | 15–25 | Ratio 0.20, 20 seats |
| `MEDIUM` | 0.40–0.80 | 5–12 | Ratio 0.60, 8 seats |
| `HIGH` | 0.85–1.00 | 3–5 | Ratio 0.95, 4 seats |

MovieLens 1M contains 1,000,209 ratings across 3,883 movies. Rating
concentration was used to define realistic differences between distributed and
highly concentrated demand.

Contention labels continue to come exclusively from future PostgreSQL
measurements.

## Fixed Technical Contract

Unless a pilot result requires a documented correction, Phase 8A retains the
following settings:

- prediction horizon: 30 seconds;
- source sample at time `t`: excluded from its own future outcome;
- target window: `(t, t + 30 seconds]`;
- sampling interval: 2 seconds;
- booking mode: `EXPERIMENT_RECYCLE`;
- database: PostgreSQL 16.15;
- label contract: `live-label-v2`;
- experiment duration: 120 seconds;
- experiment show: seeded PostgreSQL experiment show.

The target remains:

> Will a defined high-contention event occur during the following 30 seconds?

Phase 8A expands the evidence base. It does not redefine the prediction target.

## Pilot Campaign

The first Phase 8A pilot will contain 15 experiments:

```text
5 traffic patterns
× 3 demand profiles
× 1 intermediate pressure configuration
= 15 experiments
```

The initial pilot configuration will use:

- configured users: 30;
- spawn rate: 10 users per second;
- simulated transaction work: 150 ms;
- duration: 120 seconds;
- sampling interval: 2 seconds;
- booking mode: `EXPERIMENT_RECYCLE`;
- one recorded pilot seed for every scenario.

This intermediate setting was selected because the original systematic campaign
mainly used 8 and 18 users. The pilot therefore tests a new pressure level
without immediately jumping to an extreme workload.

The pilot is not the final expanded campaign. Its purpose is to verify that the
configuration produces usable measurements and meaningful class variation.

## Pilot Matrix

| Scenario | Traffic pattern | Demand profile | Users | Spawn rate | Work | Seed |
|---:|---|---|---:|---:|---:|---:|
| 1 | `STEADY` | `LOW` | 30 | 10/s | 150 ms | 8101 |
| 2 | `STEADY` | `MEDIUM` | 30 | 10/s | 150 ms | 8102 |
| 3 | `STEADY` | `HIGH` | 30 | 10/s | 150 ms | 8103 |
| 4 | `RAMP` | `LOW` | 30 | 10/s | 150 ms | 8104 |
| 5 | `RAMP` | `MEDIUM` | 30 | 10/s | 150 ms | 8105 |
| 6 | `RAMP` | `HIGH` | 30 | 10/s | 150 ms | 8106 |
| 7 | `BURST` | `LOW` | 30 | 10/s | 150 ms | 8107 |
| 8 | `BURST` | `MEDIUM` | 30 | 10/s | 150 ms | 8108 |
| 9 | `BURST` | `HIGH` | 30 | 10/s | 150 ms | 8109 |
| 10 | `FLASH_SALE` | `LOW` | 30 | 10/s | 150 ms | 8110 |
| 11 | `FLASH_SALE` | `MEDIUM` | 30 | 10/s | 150 ms | 8111 |
| 12 | `FLASH_SALE` | `HIGH` | 30 | 10/s | 150 ms | 8112 |
| 13 | `COOLDOWN` | `LOW` | 30 | 10/s | 150 ms | 8113 |
| 14 | `COOLDOWN` | `MEDIUM` | 30 | 10/s | 150 ms | 8114 |
| 15 | `COOLDOWN` | `HIGH` | 30 | 10/s | 150 ms | 8115 |

Different seeds are recorded to make each run reproducible. However, this
15-run pilot is not sufficient for statistical repetition because each
traffic-and-demand combination appears only once. Multiple seeds will be added
to the full campaign after the pilot is validated.

## Pilot Stopping Rule

The full campaign must not start until the pilot has been inspected.

The pilot will be accepted only when:

1. all 15 experiments reach `COMPLETED`;
2. metric collection succeeds;
3. prediction coverage is complete;
4. labels use `live-label-v2`;
5. incomplete or failed runs are explicitly reported;
6. both positive and negative labels are present, or the absence of one class
   is explicitly investigated;
7. no configuration error is silently ignored;
8. experiment identifiers and code versions are recorded;
9. label provenance is complete;
10. the class distribution is reviewed before further data collection.

If every pilot row belongs to only one class, the full campaign configuration
must be adjusted instead of blindly collecting more identical evidence.

## Full-Campaign Decision

The full experiment matrix will be chosen only after pilot analysis.

Possible expansion dimensions include:

- users: 30, 50 and 70;
- simulated work: 100, 200 and 300 ms;
- additional random seeds;
- all five traffic patterns;
- all three demand profiles.

A complete Cartesian product would contain:

```text
5 traffic patterns
× 3 demand profiles
× 3 user levels
× 3 work-duration levels
× multiple seeds
```

Running every combination may be unnecessarily expensive on the available
computer. The final matrix will therefore be selected to improve coverage while
remaining computationally feasible.

The final number of experiments must be justified by:

- missing workload coverage;
- observed class balance;
- runtime;
- storage use;
- repeatability;
- usefulness for model evaluation.

The number of runs will not be increased only to produce a larger headline
number.

## Experimental Execution Requirements

Every experiment must:

1. be created with a validated configuration;
2. begin in `DRAFT` status;
3. transition through the controlled experiment lifecycle;
4. execute the Locust workload and metric collector together;
5. reach a recorded terminal status;
6. preserve failed-run evidence;
7. store the configuration and random seed;
8. store the source-code version;
9. receive prediction-coverage verification;
10. receive eligible future labels using `live-label-v2`.

No failed experiment may be silently converted into a successful run.

## Data-Quality Requirements

Every accepted run must have:

- `COMPLETED` experiment status;
- complete metric samples;
- complete prediction coverage;
- no missing predictions;
- no duplicate predictions;
- no stale predictions;
- valid `live-label-v2` labels;
- complete label provenance;
- a recorded random seed;
- a recorded code version;
- a recorded experimental configuration.

A rejected-run report must preserve:

- experiment identifier;
- configuration;
- failure reason;
- failed quality rule;
- whether rerunning is permitted.

## Dataset Construction

The expanded dataset must:

- be saved as version 2;
- preserve version 1 unchanged;
- contain only accepted experiments;
- list excluded runs and exclusion reasons;
- split data by experiment identifier;
- prevent one experiment from appearing in multiple splits;
- report positive and negative class distribution;
- report missing values;
- report duplicate rows;
- report feature ranges;
- record source experiment identifiers;
- record dataset-generation code version.

Rows from the same experiment must remain within one dataset split. Randomly
splitting individual rows could place neighbouring time samples from the same
experiment in both training and testing data, causing data leakage.

## Model-Evaluation Boundary

Phase 8A collects and validates the expanded evidence.

Model comparison and final model selection occur only after dataset version 2
has passed its quality checks.

The future model-evaluation stage may compare:

- Logistic Regression;
- Random Forest;
- Gradient Boosting;
- one justified boosting implementation such as XGBoost or LightGBM.

No model will be declared superior before validation and untouched test
evaluation have been completed.

## Reproducibility

The following information must be preserved for every accepted experiment:

- experiment UUID;
- traffic pattern;
- demand profile;
- user count;
- spawn rate;
- hotspot ratio;
- hotspot-seat identifiers;
- simulated transaction work;
- duration;
- sample interval;
- random seed;
- booking mode;
- source-code version;
- collector version;
- experiment start and end times;
- prediction model version;
- label-contract version.

The same configuration and random seed should reproduce the same decision
sequence as far as permitted by operating-system and database scheduling.

Exact timing measurements may still vary because concurrent execution is
affected by runtime scheduling. Therefore, reproducibility does not mean that
every latency measurement must be numerically identical.

## Scope Boundary

Phase 8A covers:

- experiment planning;
- pilot execution;
- expanded controlled-data collection;
- prediction and label verification;
- dataset version 2 construction;
- data-quality documentation.

Phase 8A does not by itself claim:

- production readiness;
- validation on IRCTC or BookMyShow infrastructure;
- universal generalization;
- automatic traffic reduction;
- automatic database scaling;
- superiority over every published approach.

Dashboard development, intervention logic and control-versus-intervention
evaluation belong to later Phase 8 work.

## Risks and Responses

### Excessive runtime

If the full matrix is too expensive, a balanced subset will be selected and the
selection rule will be documented.

### Severe class imbalance

If nearly all outcomes have the same label, pressure settings will be adjusted
to collect informative boundary cases.

### Local-machine instability

Experiments affected by unrelated machine load will be identified through
quality checks and preserved in the rejected-run report.

### Coverage failure

Prediction or label-coverage failures will be diagnosed before additional runs
are launched.

### Configuration confounding

User count and transaction duration will be varied independently in the full
campaign.

### Duplicate evidence

Repeated configurations will use distinct seeds and will remain traceable
through experiment identifiers.

## Acceptance Criteria

Phase 8A is complete when:

1. The pilot configuration is documented.
2. The pilot matrix is machine-readable and validated.
3. The pilot campaign executes successfully.
4. Pilot prediction coverage and label coverage are inspected.
5. Pilot class balance and feature variation are reviewed.
6. The final expansion matrix is justified.
7. The approved full campaign is completed.
8. Dataset version 2 is generated.
9. Experiment-level splits contain no overlap.
10. Data-quality and provenance reports are generated.
11. Rejected experiments and reasons are documented.
12. Version-1 artifacts remain unchanged.
13. The repository tests pass.
14. All accepted configurations are reproducible.