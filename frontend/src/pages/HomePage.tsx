import {
    useEffect,
    useState,
} from 'react'
import { Link } from 'react-router-dom'

import { ServiceStatus } from '../components/ServiceStatus'
import {
    ApiClientError,
    listShows,
} from '../lib/api'
import type {
    MoviePresentation,
    RequestState,
    Show,
} from '../types'

const DEFAULT_PRESENTATION: MoviePresentation = {
    eyebrow: 'Now showing',
    title: 'The Last Signal',
    synopsis: (
        'A communications engineer receives a transmission '
        + 'from a station that was abandoned twenty years ago.'
    ),
    language: 'English',
    certification: 'U/A 13+',
    duration: '2h 08m',
    genres: [
        'Drama',
        'Mystery',
    ],
    posterClassName: 'film-poster--signal',
}

const PRESENTATIONS_BY_SHOW_ID: Record<
    number,
    MoviePresentation
> = {
    1: DEFAULT_PRESENTATION,
    2: DEFAULT_PRESENTATION,
}

function presentationForShow(
    show: Show,
): MoviePresentation {
    return (
        PRESENTATIONS_BY_SHOW_ID[show.id]
        ?? {
            ...DEFAULT_PRESENTATION,
            title: show.name,
        }
    )
}

function formatShowDate(
    dateValue: string,
): string {
    return new Intl.DateTimeFormat(
        'en-IN',
        {
            weekday: 'short',
            day: 'numeric',
            month: 'short',
            hour: 'numeric',
            minute: '2-digit',
        },
    ).format(new Date(dateValue))
}

export function HomePage() {
    const [shows, setShows] = useState<Show[]>([])
    const [requestState, setRequestState] = (
        useState<RequestState>('loading')
    )
    const [errorMessage, setErrorMessage] = (
        useState<string | null>(null)
    )

    useEffect(() => {
        let active = true

        async function load(): Promise<void> {
            try {
                const result = await listShows()

                if (!active) {
                    return
                }

                setShows(
                    result.filter(
                        (show) => show.is_experiment_show,
                    ),
                )
                setRequestState('success')
            } catch (error) {
                if (!active) {
                    return
                }

                setRequestState('error')
                setErrorMessage(
                    error instanceof ApiClientError
                        ? error.message
                        : 'The programme could not be loaded.',
                )
            }
        }

        void load()

        return () => {
            active = false
        }
    }, [])

    return (
        <main>
            <section className="programme-intro">
                <div className="programme-intro__copy">
                    <p className="eyebrow">
                        Chennai · Today
                    </p>
                    <h1>
                        A focused programme for the big screen
                    </h1>
                    <p>
                        Choose a screening, select an available
                        seat and complete a live booking against
                        the reservation database.
                    </p>
                </div>

                <ServiceStatus />
            </section>

            <section
                className="programme"
                aria-labelledby="programme-title"
            >
                <div className="section-heading">
                    <div>
                        <p className="eyebrow">
                            Current programme
                        </p>
                        <h2 id="programme-title">
                            Films and screenings
                        </h2>
                    </div>

                    <p>
                        Seat availability is read directly from
                        the booking service.
                    </p>
                </div>

                {requestState === 'loading' ? (
                    <p
                        className="page-message"
                        aria-live="polite"
                    >
                        Loading today’s programme
                    </p>
                ) : null}

                {requestState === 'error' ? (
                    <div
                        className="page-message page-message--error"
                        role="alert"
                    >
                        <h3>
                            Programme unavailable
                        </h3>
                        <p>{errorMessage}</p>
                        <p>
                            Confirm that the FastAPI service is
                            running on port 8001.
                        </p>
                    </div>
                ) : null}

                {(
                    requestState === 'success'
                    && shows.length === 0
                ) ? (
                    <div className="page-message">
                        <h3>
                            No public screenings are available
                        </h3>
                        <p>
                            Seed the normal demonstration show and
                            refresh this page.
                        </p>
                    </div>
                ) : null}

                <div className="film-list">
                    {shows.map((show) => {
                        const presentation = (
                            presentationForShow(show)
                        )

                        return (
                            <article
                                className="film-entry"
                                key={show.id}
                            >
                                <div
                                    className={
                                        `film-poster ${presentation.posterClassName
                                        }`
                                    }
                                    aria-label={
                                        `${presentation.title} poster`
                                    }
                                >
                                    <span className="film-poster__edition">
                                        FRAME 24 PRESENTS
                                    </span>
                                    <strong>
                                        {presentation.title}
                                    </strong>
                                    <span className="film-poster__credit">
                                        A fictional screening for the
                                        reservation demonstration
                                    </span>
                                </div>

                                <div className="film-entry__content">
                                    <p className="eyebrow">
                                        {presentation.eyebrow}
                                    </p>

                                    <h3>
                                        {presentation.title}
                                    </h3>

                                    <p className="film-entry__synopsis">
                                        {presentation.synopsis}
                                    </p>

                                    <ul
                                        className="film-metadata"
                                        aria-label="Film details"
                                    >
                                        <li>{presentation.language}</li>
                                        <li>{presentation.certification}</li>
                                        <li>{presentation.duration}</li>
                                        {presentation.genres.map(
                                            (genre) => (
                                                <li key={genre}>
                                                    {genre}
                                                </li>
                                            ),
                                        )}
                                    </ul>

                                    <div className="screening-row">
                                        <div>
                                            <span className="screening-row__label">
                                                Screening
                                            </span>
                                            <strong>
                                                {formatShowDate(
                                                    show.show_time,
                                                )}
                                            </strong>
                                            <span>
                                                {show.venue}
                                            </span>
                                        </div>

                                        <Link
                                            className="primary-button"
                                            to={`/shows/${show.id}`}
                                        >
                                            Select seats
                                        </Link>
                                    </div>
                                </div>
                            </article>
                        )
                    })}
                </div>
            </section>

            <section className="research-note">
                <p className="eyebrow">
                    Demonstration environment
                </p>
                <h2>
                    Every seat request uses the transactional
                    booking system
                </h2>
                <p>
                    Concurrent attempts for the same seat are
                    resolved by PostgreSQL locking. The separate
                    live-status view presents telemetry and the
                    latest contention-risk prediction.
                </p>
                <Link
                    className="secondary-button"
                    to="/operations"
                >
                    View live system status
                </Link>
            </section>
        </main>
    )
}