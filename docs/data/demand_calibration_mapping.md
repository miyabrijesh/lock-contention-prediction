# MovieLens Demand Calibration Mapping

Status: Checkpoint 29 implementation record

## Purpose

MovieLens 1M is used to make the reservation workload's popularity
distribution more realistic.

MovieLens does not provide database contention labels.

## Observed popularity concentration

The calibration produced:

| Tier | Movies | Movie share | Ratings | Interaction share |
| --- | ---: | ---: | ---: | ---: |
| LOW | 1,940 | 49.96% | 65,852 | 6.58% |
| MEDIUM | 1,554 | 40.02% | 476,797 | 47.67% |
| HIGH | 389 | 10.02% | 457,560 | 45.75% |

Approximately ten percent of movies received nearly 46 percent of observed
interactions.

This demonstrates that customer attention is concentrated on a relatively
small group of popular items.

## Reservation-system mapping

| MovieLens tier | Workload levels | Hotspot ratio | Hotspot seats |
| --- | --- | --- | --- |
| LOW | VERY_LOW and LOW | 0.10–0.30 | 15–25 |
| MEDIUM | BORDERLINE and MEDIUM | 0.40–0.80 | 5–12 |
| HIGH | HIGH and EXTREME | 0.85–1.00 | 3–5 |

The MovieLens results determine the relative popularity ordering and provide
evidence that hotspot demand is reasonable.

The exact hotspot ratios and seat counts remain controlled experimental
parameters because MovieLens does not contain cinema seat selections.

## Independent simulated-work parameter

`simulated_work_ms` is not derived from MovieLens.

It represents how long the experimental booking transaction holds a database
lock.

It remains an independent variable so the project can measure how transaction
duration affects contention.

## Target-label protection

The following values are not taken from MovieLens:

- blocked sessions
- waiting locks
- latency
- timeout rate
- deadlock delta
- future contention label

These values are measured from PostgreSQL.

## Permitted project claim

The project may state:

> Reservation workloads were calibrated using popularity concentration
> measured from the MovieLens 1M dataset.

The project must not claim that MovieLens contains real cinema bookings or
database lock-contention measurements.

## Output files

The row-level popularity output is stored locally at:

`data/external/movielens/derived/movie_popularity.csv`

It remains excluded from Git.

The machine-readable workload profiles are stored at:

`load_testing/config/demand_profiles.json`

The non-row-level calibration summary is stored at:

`docs/data/movielens_calibration_summary.json`

## Checkpoint 29 completion condition

Checkpoint 29 is complete when:

- the official MovieLens archive is verified
- the raw dataset remains ignored by Git
- movie popularity is calculated from rating counts
- LOW, MEDIUM and HIGH popularity tiers exist
- real interaction concentration is documented
- popularity tiers map to workload ranges
- lock-hold duration remains independent
- PostgreSQL telemetry remains the only label source
- all automated tests pass