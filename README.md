# Lock Contention Prediction for PostgreSQL Reservation Workloads

A final-year academic project that simulates concurrent ticket-booking
workloads, collects PostgreSQL and application telemetry, and predicts whether
high lock contention will occur within a future 30-second window.

## Current status

Phase 7 is complete.

The project now provides an end-to-end controlled demonstration:

1. Create a reproducible ticket-booking experiment.
2. Generate concurrent booking traffic with Locust.
3. Produce real PostgreSQL lock contention.
4. Collect database and application metrics every two seconds.
5. Persist complete metric samples.
6. Run the active registered machine-learning model.
7. Persist one future-contention prediction per complete sample.
8. Verify prediction coverage and model provenance.
9. Complete or fail the experiment through a controlled lifecycle.

## Verified Phase 7 result

The integrated Phase 7 experiment produced:

| Evidence | Result |
|---|---:|
| Experiment status | COMPLETED |
| Complete metric samples | 15 |
| Persisted predictions | 15 |
| Prediction coverage | 100% |
| Missing predictions | 0 |
| Duplicate predictions | 0 |
| Stale predictions | 0 |
| Prematurely labeled predictions | 0 |
| Model versions used | 1 |
| Workload requests | 606 |
| Workload request failures | 0 |

The completion evidence is stored in:

```text
docs/model_training/phase7_completion_v1.json