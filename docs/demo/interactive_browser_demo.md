# Interactive Browser Demonstration

## Purpose

This runbook covers the final browser-driven demonstration. The browser user
selects one to five consecutive seats, the terminal runner creates either a
baseline or contention workload, and the active model evaluates the resulting
telemetry.

## What the demonstration proves

A successful run shows that:

1. The browser and terminal coordinate through a persisted demo session.
2. The selected seats become the experiment hotspots.
3. Locust sends real reservation requests through FastAPI.
4. PostgreSQL processes transactional seat access.
5. The collector persists database and application telemetry.
6. The active registered model generates live predictions.
7. The browser displays the latest live risk.
8. All selected seats are booked atomically.
9. The experiment finishes through a controlled lifecycle.

It does not prove production scalability or perfect predictive accuracy.

## Preconditions

Before presenting, verify:

- PostgreSQL is running;
- development migrations are current;
- development seed data exists;
- exactly one model version is active;
- `.env` points to `lock_contention_db`;
- experiment mode is enabled;
- ports 8001 and 5173 are available;
- the Git working tree is clean;
- previously booked demonstration seats have been reset.

## Terminal layout

Use three terminal tabs:

| Terminal | Process |
|---|---|
| 1 | FastAPI backend |
| 2 | React frontend |
| 3 | Interactive demo runner |

## Start the backend

In Terminal 1:

```bash
cd ~/Documents/lock-contention-prediction
source .venv/bin/activate
export EXPERIMENT_MODE_ENABLED=true

python -m uvicorn   backend.app.main:app   --reload   --host 127.0.0.1   --port 8001
```

Keep this process running.

Confirm readiness in another terminal:

```bash
curl -i   http://127.0.0.1:8001/health/ready
```

A successful response uses HTTP status 200.

## Start the frontend

In Terminal 2:

```bash
cd ~/Documents/lock-contention-prediction/frontend
npm run dev
```

Open:

```text
http://localhost:5173
```

Sign in through the demonstration UI if no demo user is currently stored.

## Start automatic mode

In Terminal 3:

```bash
cd ~/Documents/lock-contention-prediction
source .venv/bin/activate

python -m load_testing.run_interactive_demo   --duration 60   --selection-timeout 300
```

The output identifies:

- the demo-session UUID;
- the randomly selected scenario;
- the expected risk direction;
- the experiment show;
- the browser-selection timeout.

Automatic mode randomly selects `BASELINE` or `CONTENTION`.

The expected risk is a presentation expectation, not a forced model result.

## Select seats in the browser

Open the experiment screening.

While the session is waiting:

1. Select between one and five seats.
2. Keep every seat in the same row.
3. Select consecutive seat numbers.
4. Submit the selection.

Valid examples:

```text
B4
B4, B5
B4, B5, B6, B7, B8
```

Invalid examples:

```text
B4, B6
B9, C1
six or more seats
booked seats
```

After submission, the terminal prints the received seat numbers and prepared
experiment configuration.

## Observe the experiment

The runner transitions the experiment to `RUNNING`, starts the collector, and
starts Locust.

Open **Live status** in the browser. Observe:

- prediction probability;
- LOW or HIGH risk;
- configured threshold;
- request rate;
- latency;
- active transactions;
- blocked sessions;
- waiting locks;
- prediction freshness.

Not every signal must increase in every two-second sample. Scheduling,
transaction timing, and workload ramp-up affect individual readings.

### Baseline expectation

The baseline preset uses:

| Setting | Value |
|---|---:|
| Users | 5 |
| Simulated lock work | 20 ms |
| Hotspot ratio | 0.10 |
| Expected model direction | LOW |

### Contention expectation

The contention preset uses:

| Setting | Value |
|---|---:|
| Users | 2 |
| Simulated lock work | 600 ms |
| Hotspot ratio | 1.00 |
| Expected model direction | HIGH |

A contention run may initially report LOW while the workload ramps up. The
acceptance requirement is that the model reaches HIGH under sustained
contention, not that every sample is forced to HIGH.

## Confirm the booking

Return to the seat-selection page when the experiment is running.

Confirm the selected seats together. The batch booking endpoint processes the
selection atomically:

- either every selected seat is confirmed;
- or the complete booking batch is rolled back.

The confirmation page should list every selected seat.

The runner detects completion and waits five seconds so final telemetry can be
collected. It then stops the workload and collector and marks the experiment
`COMPLETED`.

Expected terminal ending:

```text
Child processes stopped after the controlled completion condition was satisfied.
Experiment completed successfully.
Final experiment status: COMPLETED
Interactive demo session completed.
```

Negative child-process exit codes after controlled termination are expected
because the runner deliberately terminates those processes after the booking
condition is satisfied.

## After completion

The selected seats remain booked so the confirmation is visible and
auditable.

The Live status page may show:

```text
No recent prediction available
```

after completion. This is expected because the page represents the currently
running experiment rather than historical predictions.

## Reset selected seats

First perform a dry run:

```bash
python -m load_testing.reset_interactive_demo   --seat-number B4   --seat-number B5   --seat-number B6
```

Review:

- show ID;
- seat IDs;
- current states;
- confirmed experiment bookings to cancel.

Then apply:

```bash
python -m load_testing.reset_interactive_demo   --seat-number B4   --seat-number B5   --seat-number B6   --apply
```

Repeat `--seat-number` for every selected seat.

The utility:

- rejects non-experiment shows;
- rejects missing seats;
- rejects duplicate seat numbers;
- refuses to run while an experiment is active;
- locks requested records;
- cancels confirmed experiment bookings;
- restores seats to `AVAILABLE`;
- commits every requested reset atomically.

## Deterministic rehearsal

Use baseline rehearsal when demonstrating the normal case:

```bash
python -m load_testing.run_interactive_demo   --scenario baseline   --duration 60   --selection-timeout 300
```

Use contention rehearsal when demonstrating elevated risk:

```bash
python -m load_testing.run_interactive_demo   --scenario contention   --duration 60   --selection-timeout 300
```

Use automatic mode for the final random demonstration.

## Recovery

### Selection timeout

If the browser does not submit seats within the configured timeout, the
session is cancelled. Start a new runner command.

### Browser still shows a completed session

Refresh the experiment screening after starting a new runner.

### Seats appear booked before a run

Use the reset utility for those exact seats. Do not alter seat rows manually
with SQL unless diagnosing a defect.

### Experiment already running

Allow the existing experiment to finish or cancel it through the controlled
experiment lifecycle. Do not reset seats during execution.

### Backend unavailable

Verify Terminal 1 is still running and check:

```bash
curl -i   http://127.0.0.1:8001/health/ready
```

### Frontend cannot contact backend

Confirm the frontend is using:

```text
http://127.0.0.1:8001
```

If a different API port is used, start Vite with an appropriate
`VITE_API_BASE_URL`.

## Final acceptance evidence

The implemented workflow has been validated with:

- a baseline three-seat run that remained LOW;
- a contention three-seat run that reached HIGH;
- a random automatic contention run using five seats that reached HIGH;
- automatic completion after all selected seats were confirmed;
- successful multi-seat reset;
- 303 passing Python tests;
- a successful frontend production build;
- zero frontend lint errors;
- no pending Alembic schema operations.
