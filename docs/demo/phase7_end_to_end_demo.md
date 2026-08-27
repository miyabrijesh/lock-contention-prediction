# Phase 7 End-to-End Prediction Demonstration

## Purpose

This guide demonstrates the complete lock-contention prediction workflow:

1. Verify database and model readiness.
2. Create a controlled DRAFT experiment.
3. Start the reservation API in experiment mode.
4. Run the synchronized Locust workload and metrics collector.
5. Generate one live prediction for every complete metric sample.
6. Complete the experiment lifecycle.
7. Verify prediction coverage and quality invariants.

The workflow uses the real PostgreSQL development database and produces
persistent demonstration evidence.

## Demonstrated architecture

```text
Locust workload
    |
    v
Reservation API
    |
    v
PostgreSQL contention
    |
    v
Metrics collector
    |
    v
Complete MetricSample
    |
    v
Active model inference
    |
    v
Persisted Prediction
    |
    v
Prediction coverage verification
```

## Safety boundaries

Before running the demonstration:

- use the project development database, not the test database;
- confirm the Git working tree is clean;
- confirm exactly one model version is active;
- create a new DRAFT experiment for every demonstration;
- do not reuse a COMPLETED, FAILED, or CANCELLED experiment;
- do not print API-token values;
- do not interrupt PostgreSQL while the workload is active;
- allow the synchronized runner to apply the final lifecycle transition.

Automated tests remain isolated in:

```text
lock_contention_test_db
```

The demonstration uses:

```text
lock_contention_db
```

## Demonstration configuration

The recommended presentation configuration is:

| Setting | Value |
|---|---:|
| Users | 50 |
| Spawn rate | 20 users/second |
| Duration | 60 seconds |
| Traffic pattern | FLASH_SALE |
| Hotspot ratio | 0.8000 |
| Hotspot seats | First 5 experiment-show seats |
| Sample interval | 2 seconds |
| Simulated work | 200 milliseconds |
| Booking mode | EXPERIMENT_RECYCLE |
| Random seed | 3902 |

A 60-second run normally produces approximately 30 metric samples.

The exact count can vary slightly because process startup, scheduling, and
shutdown occur around the collection window.

## Terminal layout

Use two Terminal tabs:

| Terminal | Responsibility |
|---|---|
| Terminal 1 | Run the FastAPI application |
| Terminal 2 | Create, execute, and verify the experiment |

The examples use:

```text
http://127.0.0.1:8001
```

Port 8001 avoids a known local port-8000 conflict. Another free port may be
used if the same base URL is passed to the synchronized runner.

## Step 1 — enter the project

In both terminals:

```bash
cd ~/Documents/lock-contention-prediction
source .venv/bin/activate
```

## Step 2 — verify the Git working tree

In Terminal 2:

```bash
git status --short
```

Expected:

```text
```

No output means the working tree is clean.

## Step 3 — verify database readiness and the active model

In Terminal 2:

```bash
python - <<'PY'
from sqlalchemy import func, select

from backend.app.db.session import SessionLocal
from backend.app.models import ModelVersion


with SessionLocal() as session:
    active_model_count = session.scalar(
        select(func.count(ModelVersion.id)).where(
            ModelVersion.is_active.is_(True)
        )
    ) or 0

    active_model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.is_active.is_(True)
        )
    )

    print("Active models:", active_model_count)

    assert active_model_count == 1
    assert active_model is not None

    print("Model ID:", active_model.id)
    print("Model name:", active_model.name)
    print("Algorithm:", active_model.algorithm)

print("Model precondition passed")
PY
```

Required result:

```text
Active models: 1
Model precondition passed
```

The demonstration must stop if the active-model count is not exactly one.

## Step 4 — create a new DRAFT experiment

In Terminal 2:

```bash
python - <<'PY'
from sqlalchemy import select

from backend.app.db.session import SessionLocal
from backend.app.models import Seat, Show
from backend.app.schemas.experiment import ExperimentCreate
from backend.app.services.experiment_service import (
    create_experiment,
)


with SessionLocal() as session:
    experiment_show = session.scalar(
        select(Show).where(
            Show.is_experiment_show.is_(True)
        )
    )

    assert experiment_show is not None

    hotspot_seat_ids = list(
        session.scalars(
            select(Seat.id)
            .where(
                Seat.show_id == experiment_show.id
            )
            .order_by(Seat.id)
            .limit(5)
        )
    )

    assert len(hotspot_seat_ids) == 5

    command = ExperimentCreate(
        name="Phase 7 End-to-End Demonstration",
        show_id=experiment_show.id,
        user_count=50,
        spawn_rate_per_sec="20.00",
        duration_sec=60,
        traffic_pattern="FLASH_SALE",
        demand_profile="CUSTOM",
        hotspot_ratio="0.8000",
        hotspot_seat_ids=hotspot_seat_ids,
        sample_interval_sec=2,
        simulated_work_ms=200,
        booking_mode="EXPERIMENT_RECYCLE",
        random_seed=3902,
        code_version="phase7-demo-v1",
        created_by="phase7-demo",
    )

    experiment = create_experiment(
        session,
        command,
    )

    print("ID:", experiment.id)
    print("Status:", experiment.status.value)
    print("Show:", experiment.show_id)
    print("Hotspot seats:", experiment.hotspot_seat_ids)
PY
```

Required result:

```text
Status: DRAFT
```

Copy the printed experiment UUID.

In Terminal 2, store it without spaces:

```bash
EXPERIMENT_RUN_ID="PASTE-EXPERIMENT-UUID"
```

Verify:

```bash
printf '%s\n' "$EXPERIMENT_RUN_ID"
```

## Step 5 — start the API

In Terminal 1:

```bash
export EXPERIMENT_MODE_ENABLED=true

python -m uvicorn backend.app.main:app \
  --host 127.0.0.1 \
  --port 8001
```

Keep Terminal 1 running.

Expected startup evidence:

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8001
```

## Step 6 — verify API readiness

In Terminal 2:

```bash
export EXPERIMENT_MODE_ENABLED=true
```

Then:

```bash
curl -i http://127.0.0.1:8001/health/ready
```

Required response:

```text
HTTP/1.1 200 OK
```

The JSON response must report:

```json
{
  "status": "ok",
  "database": "available",
  "migration": "current"
}
```

## Step 7 — run the synchronized experiment

In Terminal 2:

```bash
python -m load_testing.run_synchronized_experiment \
  "$EXPERIMENT_RUN_ID" \
  --base-url http://127.0.0.1:8001 \
  --actor phase7-demo \
  --output-directory data/raw/locust
```

The runner performs these lifecycle operations:

1. Confirms experiment mode is enabled.
2. Confirms the API and database are ready.
3. Confirms the experiment is DRAFT.
4. Transitions the experiment to RUNNING.
5. Starts the metrics collector.
6. Starts the Locust workload.
7. Waits for both processes.
8. Transitions the experiment to COMPLETED if both exit successfully.
9. Transitions it to FAILED if an execution error occurs.
10. Transitions it to CANCELLED if the operator interrupts it.

## Step 8 — observe the live evidence

During the run, the collector prints one summary per interval:

```text
Collected sample 1:
requests_per_sec=...,
blocked_sessions=...,
waiting_locks=...,
p95_latency_ms=...,
prediction_probability=...,
risk_level=...,
threshold=...,
stale=False
```

The important presenter-visible signals are:

- rising blocked-session count;
- rising waiting-lock count;
- increased p95 latency;
- predicted contention probability;
- LOW, MEDIUM, or HIGH risk classification;
- the frozen model threshold;
- non-stale live inference.

The presenter must not claim that a high prediction probability proves future
contention with certainty. It is a model estimate based on the current sample.

## Step 9 — confirm runner completion

Required ending:

```text
Workload exit code: 0
Collector exit code: 0
Experiment completed successfully.
Final experiment status: COMPLETED
```

If either process has a nonzero exit code, the demonstration has failed and
must not be presented as a successful run.

## Step 10 — verify prediction coverage

In Terminal 2:

```bash
python -m monitoring.prediction_coverage \
  "$EXPERIMENT_RUN_ID"
```

Required invariants:

```text
Status: COMPLETED
Missing predictions: 0
Duplicate predictions: 0
Stale predictions: 0
Labeled predictions: 0
Model versions used: 1
Coverage: 1
Passed: True
Prediction coverage verification passed.
```

For the successful live experiment:

```text
complete metric samples = predictions
```

and:

```text
prediction coverage = 1.0
```

## Step 11 — inspect workload evidence

The synchronized runner writes Locust CSV output under:

```text
data/raw/locust/<experiment-run-id>/
```

The terminal summary contains:

- total request count;
- request-failure count;
- mean response time;
- median response time;
- p95 response time;
- p99 response time;
- request throughput.

These values provide workload evidence but do not replace prediction-coverage
verification.

## Demonstration explanation

A concise explanation for the audience is:

> The workload creates concurrent booking requests against a small set of
> hotspot seats. PostgreSQL experiences lock waiting and transaction
> contention. Every two seconds, the collector stores database and application
> telemetry. The active logistic-regression model immediately predicts the
> risk of contention during the next 30-second window. Each prediction is
> persisted with its model version, probability, threshold, risk level, target
> window, and quality flags.

## Evidence chain

A successful demonstration proves:

1. The API accepted real concurrent workload requests.
2. PostgreSQL produced measurable lock-contention telemetry.
3. Complete metric samples were persisted.
4. The active registered artifact performed inference.
5. Predictions were persisted automatically.
6. Each complete sample received exactly one prediction.
7. Predictions retained model provenance.
8. Live predictions were not stale.
9. Future labels were not used during inference.
10. The experiment lifecycle ended in COMPLETED.

It does not prove:

- perfect prediction accuracy;
- production scalability;
- universal performance across all hardware;
- absence of all possible runtime failures;
- causality between one individual feature and the prediction.

## Failure recovery

### API port already in use

Use another free port, for example 8002:

```bash
python -m uvicorn backend.app.main:app \
  --host 127.0.0.1 \
  --port 8002
```

Pass the same port to:

```text
--base-url http://127.0.0.1:8002
```

### Experiment mode disabled

Set this in both terminals:

```bash
export EXPERIMENT_MODE_ENABLED=true
```

Restart the API after changing it.

### Experiment is not DRAFT

Do not reuse the experiment. Create a new DRAFT experiment.

### Active model not found

Stop the demonstration. Activate the verified registered model through the
authenticated activation API. Do not silently select or activate a fallback.

### Collector fails after storing a sample

The stored metric sample remains available. The synchronized runner marks the
experiment FAILED because prediction coverage is incomplete.

Create a new DRAFT experiment only after diagnosing the failure.

### Prediction coverage fails

Do not rerun the coverage command until the underlying data is understood.

Inspect:

- experiment status;
- complete-sample count;
- prediction count;
- missing predictions;
- stale predictions;
- model-version count.

Do not manually insert predictions merely to make coverage pass.

## Known limitation

This demonstration uses a controlled local workload on one machine. The
contention is real inside the local PostgreSQL instance, but the workload is a
simulation rather than traffic from a commercial ticketing platform.

The project demonstrates the technical mechanism and predictive layer. It does
not claim to reproduce the complete scale or infrastructure of BookMyShow or
another production ticketing service.

## Demonstration completion checklist

- [ ] Git working tree was clean before the run.
- [ ] Exactly one verified model was active.
- [ ] A new DRAFT experiment was created.
- [ ] Experiment mode was enabled in both terminals.
- [ ] API readiness returned HTTP 200.
- [ ] Experiment transitioned to RUNNING.
- [ ] Locust produced concurrent booking traffic.
- [ ] Collector printed automatic predictions.
- [ ] Workload process exited with code 0.
- [ ] Collector process exited with code 0.
- [ ] Experiment transitioned to COMPLETED.
- [ ] Complete samples were greater than zero.
- [ ] Prediction coverage equalled 1.0.
- [ ] Missing prediction count was zero.
- [ ] Duplicate prediction count was zero.
- [ ] Stale prediction count was zero.
- [ ] Labeled live-prediction count was zero.
- [ ] Exactly one model version was used.