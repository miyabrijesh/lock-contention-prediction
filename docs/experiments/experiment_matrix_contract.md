Checkpoint 31 Experiment Matrix Contract

Status: implementation baseline

Matrix version: 1.0.0

Objective

Generate a reproducible training dataset containing multiple demand
concentrations, time-varying traffic patterns, system-pressure levels, and
random seeds. This replaces reliance on the 89-row pilot dataset, which is
useful for pipeline verification but too small for credible model evaluation.

Matrix dimensions

Dimension

Values

Count

Traffic pattern

STEADY, RAMP, BURST, FLASH_SALE, COOLDOWN

5

Demand profile

LOW, MEDIUM, HIGH

3

Pressure level

STANDARD, STRESSED

2

Random seed

4101, 4102

2

Total experiment runs:

5 × 3 × 2 × 2 = 60

Fixed settings

Setting

Value

Experiment show

2

Duration

120 seconds

Sampling interval

2 seconds

Prediction horizon

30 seconds

Booking mode

EXPERIMENT_RECYCLE

A 120-second run sampled every 2 seconds produces approximately 60 metric
samples. Removing samples without a complete 30-second future window should
leave approximately 45 labelled rows per run. The expected combined dataset
size is therefore approximately 2,700 rows.

Demand-profile settings

Profile

Hotspot ratio

Hotspot seats

LOW

0.20

20

MEDIUM

0.60

8

HIGH

0.95

4

All values are inside the tracked MovieLens-calibrated ranges. The profiles
represent demand concentration only. They do not provide contention labels.

Pressure levels

Level

Users

Spawn rate

Simulated lock work

STANDARD

8

4 users/second

75 ms

STRESSED

18

8 users/second

250 ms

Pressure is varied independently from demand concentration. This is necessary
because using only one pressure level per demand profile would confound demand
profile with contention outcome.

Hotspot-seat selection

The matrix runner must load available seats for experiment show 2, sort them
by database ID, and select the first required number for each demand profile.
It must not assume that seat IDs always begin at 101.

Run naming

Each experiment name must include:

matrix version

demand profile

traffic pattern

pressure level

random seed

Example:

M31-v1-HIGH-BURST-STRESSED-seed4101

Lifecycle requirements

For each run, the automation must:

Verify no experiment is currently RUNNING.

Create a DRAFT experiment through the API.

Start the synchronized workload and metrics collector.

Require successful completion of both child processes.

Verify final experiment status is COMPLETED.

Export the labelled experiment CSV.

Record the experiment ID, configuration, export path, row count, checksum,
status, and any failure reason in a matrix-run manifest.

Stop the matrix on an unexpected infrastructure or validation error unless
an explicit resume operation is requested.

Resume and idempotency requirements

The runner must maintain a manifest so interrupted collection can resume
without silently duplicating completed scenarios. A scenario identity is the
combination of matrix version, traffic pattern, demand profile, pressure level,
and random seed.

The automation must never delete previous experiments or exports. Rebuilding a
dataset should create a new versioned combined file and manifest.

Dataset-quality requirements

Before the matrix is accepted:

every planned scenario must have a terminal status;

successful runs must have metric samples and a registered data export;

source export checksums must match their files;

both target labels must exist in the combined dataset;

incomplete telemetry rows must be excluded;

no duplicate scenario identity may appear in the matrix manifest;

the combined dataset must preserve experiment IDs for grouped evaluation.

Evaluation leakage rule

Rows from one experiment are temporally related and must not be divided across
training and test sets. Later model evaluation must split by
experiment_run_id, not by randomly splitting individual rows.

Scientific limitations

The reservations remain controlled synthetic transactions.

MovieLens supplies real-world popularity concentration, not ticket purchases
or database-lock measurements.

Results initially describe the local PostgreSQL instance and Mac hardware.

More seeds, pressure levels, hardware environments, and real operational
traces may be added later as new versioned datasets.