# External Demand Calibration Source

Status: Checkpoint 29 selection baseline

## Selected dataset

The selected external dataset is:

**MovieLens 1M Dataset**

The dataset was published by the GroupLens Research Project at the University
of Minnesota.

Official dataset page:

https://grouplens.org/datasets/movielens/

Official MovieLens 1M README:

https://files.grouplens.org/datasets/movielens/ml-1m-README.txt

Official download:

https://files.grouplens.org/datasets/movielens/ml-1m.zip

## Dataset summary

MovieLens 1M contains:

- 1,000,209 ratings
- 6,040 anonymized users
- approximately 3,900 movies
- rating timestamps
- movie titles
- movie genres

The users included in this dataset joined MovieLens in 2000.

Every included user submitted at least 20 ratings.

## Files provided by MovieLens 1M

The downloaded archive contains:

| File | Contents | Required by this project |
| --- | --- | --- |
| `ratings.dat` | User, movie, rating and timestamp | Yes |
| `movies.dat` | Movie title and genre information | Yes |
| `users.dat` | User demographic information | No |
| `README` | Dataset documentation and licence | Yes |

This project will not use demographic information from `users.dat`.

Demographic fields are unnecessary for generating lock-contention workloads.

## Ratings file structure

The original `ratings.dat` format is:

`UserID::MovieID::Rating::Timestamp`

The fields mean:

| Field | Meaning |
| --- | --- |
| UserID | An anonymized MovieLens user |
| MovieID | The movie receiving the rating |
| Rating | A whole-number rating between 1 and 5 |
| Timestamp | The time when the rating was submitted |

## Movies file structure

The original `movies.dat` format is:

`MovieID::Title::Genres`

The fields mean:

| Field | Meaning |
| --- | --- |
| MovieID | MovieLens movie identifier |
| Title | Movie title including its release year |
| Genres | One or more pipe-separated genres |

## Purpose in this project

MovieLens will be used to calibrate movie-popularity patterns.

The main popularity measurement will be:

`number of ratings received by each movie`

A movie receiving many ratings will be treated as more popular than a movie
receiving few ratings.

The calculated popularity distribution will influence:

- which simulated movies receive more demand
- how strongly customers concentrate on popular choices
- hotspot-selection weights
- low, medium and high popularity groups
- controlled booking-pressure configurations

## What MovieLens will not provide

MovieLens will not provide:

- PostgreSQL lock measurements
- blocked-session counts
- waiting-lock counts
- database deadlocks
- booking latency
- booking failures
- seat availability
- ticket inventory
- real ticket purchases
- the machine-learning target label

These values must come from the controlled PostgreSQL reservation experiments.

## Correct interpretation of rating data

A rating is evidence that a user interacted with a movie-recommendation
service.

A rating is not the same as:

- purchasing a cinema ticket
- attempting to reserve a seat
- attending a movie
- creating simultaneous booking traffic

The number of ratings will therefore be used only as a popularity proxy.

The rating value itself must not be interpreted as ticket demand.

For example:

- a movie with 5,000 ratings has more observed interaction than a movie with
  20 ratings
- a movie with a rating score of 5 is not automatically more popular than a
  movie with a score of 4

Popularity will primarily depend on interaction count, not average rating.

## Why MovieLens 1M was selected

MovieLens 1M was selected because:

1. It contains real human-movie interactions.
2. It is published by an established academic research group.
3. It contains enough observations to estimate movie popularity.
4. It is a fixed, named dataset release.
5. It is small enough to process locally on the development MacBook.
6. It contains anonymized user identifiers.
7. It includes documentation and research-use conditions.
8. It does not require collecting personal information from project users.

## Why the larger MovieLens datasets are unnecessary

Larger MovieLens datasets contain millions more ratings.

The project does not need tens of millions of rows because MovieLens is not
being used to train the lock-contention prediction model directly.

It is being used only to calculate a realistic popularity distribution.

Using a much larger dataset would increase:

- download size
- storage requirements
- processing time
- project complexity

without providing a proportionate benefit for this calibration task.

## Why MovieLens Latest Small was not selected

MovieLens Latest Small is convenient, but its official documentation describes
it as a development dataset that may change over time.

A fixed named release is preferable for:

- reproducibility
- report writing
- checksum verification
- future project demonstrations
- consistent results across machines

The project therefore selects MovieLens 1M.

## Planned calibration outputs

The calibration pipeline will eventually produce a processed table containing:

| Field | Meaning |
| --- | --- |
| movie_id | Original MovieLens movie identifier |
| title | Movie title |
| genres | Movie genres |
| rating_count | Number of ratings received |
| average_rating | Mean rating value |
| popularity_score | Normalized popularity value |
| popularity_percentile | Popularity percentile |
| popularity_tier | LOW, MEDIUM or HIGH |
| selection_weight | Probability weight for workload selection |

The processed calibration table will not contain PostgreSQL contention labels.

## Planned popularity tiers

Movies will be divided into three popularity tiers:

| Tier | Intended interpretation |
| --- | --- |
| LOW | Movies receiving relatively little interaction |
| MEDIUM | Movies receiving moderate interaction |
| HIGH | Movies receiving heavy interaction |

The exact percentile boundaries will be calculated after inspecting the real
distribution.

The boundaries must not be invented before the data is analysed.

## Connection to the reservation workload

The MovieLens-derived popularity distribution will influence the probability
that simulated customers choose popular targets.

A highly popular movie may create:

- a higher hotspot ratio
- greater concentration on a small seat group
- stronger burst behaviour
- more simultaneous booking attempts

A less popular movie may create:

- a lower hotspot ratio
- more distributed seat selection
- steadier traffic
- fewer simultaneous attempts

The database contention resulting from these workloads will still be directly
measured from PostgreSQL.

## Relationship to the Phase 6 dataset

The final Phase 6 dataset will combine two different kinds of evidence:

### External calibration evidence

MovieLens provides a defensible real-world popularity distribution.

### Experimental system evidence

The reservation system provides:

- booking traffic
- PostgreSQL locks
- blocked sessions
- waiting locks
- latency
- timeouts
- future-contention labels

MovieLens will not be joined directly to metric samples as though ratings were
bookings.

Instead, its calculated popularity patterns will configure the controlled
experiments.

## Licence and usage conditions

The MovieLens 1M documentation permits research use under stated conditions.

The project must:

- acknowledge the MovieLens dataset in resulting publications
- avoid implying endorsement by the University of Minnesota or GroupLens
- avoid commercial or revenue-bearing use without permission
- avoid redistributing the dataset without separate permission
- retain the official dataset documentation

The raw MovieLens archive and extracted files must not be committed to the
public Git repository.

Only project-created code, documentation and permitted derived summaries
should be committed.

## Required academic citation

The final report should cite:

F. Maxwell Harper and Joseph A. Konstan. 2015. The MovieLens Datasets:
History and Context. ACM Transactions on Interactive Intelligent Systems,
Volume 5, Issue 4, Article 19.

DOI:

https://doi.org/10.1145/2827872

## Planned local storage

The original archive will be stored locally at:

`data/external/movielens/ml-1m.zip`

The extracted files will be stored locally under:

`data/external/movielens/ml-1m/`

The raw external dataset must be ignored by Git.

A locally calculated SHA-256 checksum will be stored in project documentation
so that the exact downloaded archive can be verified.

## Ethical and privacy considerations

MovieLens user identifiers are anonymized.

This project will not attempt to:

- identify users
- connect MovieLens users to real people
- use demographic information
- infer sensitive personal characteristics
- contact MovieLens users

Only aggregated movie-popularity statistics will be used.

## Limitations

MovieLens has important limitations for this project:

1. Ratings are not ticket purchases.
2. Rating timestamps are not cinema booking timestamps.
3. The data represents MovieLens users rather than all moviegoers.
4. The data was collected from users who joined MovieLens in 2000.
5. Movie popularity changes over time.
6. The dataset contains no seat-selection information.
7. The dataset contains no database-concurrency information.
8. The dataset cannot prove that the workload matches a production ticketing
   company.

These limitations must be stated in the final report.

## Methodological claim permitted by this dataset

The project may state:

> Controlled reservation workloads were calibrated using popularity patterns
> derived from real anonymized MovieLens interactions.

The project must not state:

> The model was trained using real cinema ticket-booking transactions.

That second statement would be false.

## Checkpoint 29A completion condition

Checkpoint 29A is complete when:

- MovieLens 1M is formally selected
- its official source is documented
- its intended use is documented
- its files and relevant fields are documented
- its licence conditions are documented
- its limitations are documented
- the difference between ratings and bookings is explicitly stated
- the dataset is not incorrectly treated as a contention-label source