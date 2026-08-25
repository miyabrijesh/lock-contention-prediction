Experiment Workload Design

Status: Checkpoint 30 implementation baseline

Purpose

This document defines how controlled reservation workloads vary traffic over
time and how externally calibrated demand profiles constrain hotspot
configuration. The objective is to create reproducible PostgreSQL lock
contention experiments without presenting synthetic reservations as real
customer booking records.

Traffic patterns

Traffic patterns control how the number of active Locust users changes during
an experiment.

Pattern

Behaviour

Intended use

STEADY

Gradually reaches and maintains the configured load

Stable baseline measurement

RAMP

Increases load through multiple stages

Observe progressive contention growth

BURST

Moves temporarily from low to high load and returns

Measure recovery after a short surge

FLASH_SALE

Produces a rapid increase to concentrated high load

Simulate sudden high-interest demand

COOLDOWN

Begins at high load and decreases over time

Measure contention recovery

The stored traffic_pattern is converted into a deterministic Locust load
shape. Experiment duration and maximum users remain the controlling safety
limits.

Demand profiles

Demand profiles constrain the experiment's hotspot ratio and number of
hotspot seats.

Profile

Hotspot ratio

Hotspot seats

Meaning

CUSTOM

User-controlled

User-controlled

Explicit experimental configuration without a MovieLens calibration claim

LOW

0.10–0.30

15–25

Demand distributed across a relatively large seat group

MEDIUM

0.40–0.80

5–12

Moderately concentrated demand

HIGH

0.85–1.00

3–5

Strong demand concentration on a small seat group

The API and workload runner both validate calibrated profiles. A configuration
outside its stored range is rejected instead of silently being relabelled.

MovieLens calibration boundary

The LOW, MEDIUM, and HIGH profiles are calibrated from rating-count
concentration in the MovieLens 1M dataset. MovieLens supplies real-world movie
interaction popularity, but it does not supply cinema ticket purchases,
seat-selection records, PostgreSQL locks, or contention labels.

Therefore:

MovieLens influences only demand-concentration ranges.

Locust generates the reservation requests.

PostgreSQL produces the measured lock and latency telemetry.

Future contention labels are calculated only from measured PostgreSQL and
booking telemetry.

Lock-hold duration remains a controlled experimental variable and is not
derived from MovieLens.

Runtime flow

An experiment is created with a traffic pattern and demand profile.

The API validates the demand profile before storing the experiment.

The experiment lifecycle changes the record from DRAFT to RUNNING.

The workload runner reloads and validates the stored configuration.

The runner passes the traffic pattern and demand profile to Locust through
the child-process environment.

The Locust load shape changes active users according to the stored traffic
pattern.

Reservation requests produce measured booking and PostgreSQL telemetry.

Reproducibility fields

Each experiment preserves:

experiment run ID

code version

random seed

traffic pattern

demand profile

configured users and spawn rate

experiment duration

hotspot ratio and hotspot seat IDs

simulated lock-work duration

sample interval

booking mode

These fields allow an experiment configuration to be audited and repeated.

Safety and interpretation

Only experiment shows may be used.

Only one experiment may be RUNNING at a time.

The workload requires EXPERIMENT_RECYCLE booking mode.

Experiment execution remains protected by the experiment-mode flag and
internal API token.

A calibrated demand profile describes input concentration; it does not
guarantee a particular contention outcome.

Contention results depend on the database, hardware, connection pool,
transaction implementation, user load, and lock-hold duration.

Checkpoint 30 validation

Checkpoint 30 is complete when:

both Alembic migrations are at the current head in development and test
databases;

traffic-pattern unit tests pass;

Locust load-shape tests pass;

demand-profile loader and validation tests pass;

API demand-profile tests pass;

the complete project test suite passes;

git diff --check reports no whitespace errors.