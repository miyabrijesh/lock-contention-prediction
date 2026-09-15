import {
    useCallback,
    useEffect,
    useMemo,
    useState,
} from 'react'
import {
    Link,
    useLocation,
    useNavigate,
    useParams,
} from 'react-router-dom'

import {
    ApiClientError,
    createDemoBooking,
    getActiveExperiment,
    getShow,
    listSeats,
} from '../lib/api'
import {
    getDemoUser,
    saveBookingConfirmation,
} from '../lib/demoSession'
import type {
    ActiveExperiment,
    RequestState,
    Seat,
    Show,
} from '../types'

function formatScreeningTime(
    dateValue: string,
): string {
    return new Intl.DateTimeFormat(
        'en-IN',
        {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
            hour: 'numeric',
            minute: '2-digit',
        },
    ).format(new Date(dateValue))
}

function seatRow(
    seatNumber: string,
): string {
    const match = seatNumber.match(/^[A-Za-z]+/)

    return match?.[0]?.toUpperCase() ?? 'Other'
}

export function ShowPage() {
    const { showId } = useParams()
    const navigate = useNavigate()
    const location = useLocation()

    const numericShowId = Number(showId)

    const [show, setShow] = useState<Show | null>(null)
    const [seats, setSeats] = useState<Seat[]>([])
    const [selectedSeatId, setSelectedSeatId] = (
        useState<number | null>(null)
    )
    const [requestState, setRequestState] = (
        useState<RequestState>('loading')
    )
    const [bookingState, setBookingState] = (
        useState<RequestState>('idle')
    )
    const [errorMessage, setErrorMessage] = (
        useState<string | null>(null)
    )
    const [activeExperiment, setActiveExperiment] = (
        useState<ActiveExperiment | null>(null)
    )

    const loadScreening = useCallback(async (): Promise<void> => {
        if (
            !Number.isInteger(numericShowId)
            || numericShowId < 1
        ) {
            setRequestState('error')
            setErrorMessage(
                'The screening identifier is invalid.',
            )
            return
        }

        setRequestState('loading')
        setErrorMessage(null)

        try {
            const [
                nextShow,
                nextSeats,
            ] = await Promise.all([
                getShow(numericShowId),
                listSeats(numericShowId),
            ])

            setShow(nextShow)
            setSeats(nextSeats)
            setSelectedSeatId((currentSeatId) => {
                if (currentSeatId === null) {
                    return null
                }

                const currentSeat = nextSeats.find(
                    (seat) => seat.id === currentSeatId,
                )

                return (
                    currentSeat?.state === 'AVAILABLE'
                        ? currentSeatId
                        : null
                )
            })
            setRequestState('success')
        } catch (error) {
            setRequestState('error')
            setErrorMessage(
                error instanceof ApiClientError
                    ? error.message
                    : 'The screening could not be loaded.',
            )
        }
    }, [numericShowId])

    useEffect(() => {
        const timerId = window.setTimeout(() => {
            void loadScreening()
        }, 0)

        return () => {
            window.clearTimeout(timerId)
        }
    }, [loadScreening])

    useEffect(() => {
        if (
            !Number.isInteger(numericShowId)
            || numericShowId < 1
        ) {
            return
        }

        let mounted = true

        async function loadExperiment(): Promise<void> {
            try {
                const result = await getActiveExperiment(
                    numericShowId,
                )

                if (mounted) {
                    setActiveExperiment(result)
                }
            } catch {
                if (mounted) {
                    setActiveExperiment(null)
                }
            }
        }

        const initialTimer = window.setTimeout(() => {
            void loadExperiment()
        }, 0)

        const intervalId = window.setInterval(() => {
            void loadExperiment()
        }, 2000)

        return () => {
            mounted = false
            window.clearTimeout(initialTimer)
            window.clearInterval(intervalId)
        }
    }, [numericShowId])

    const seatsByRow = useMemo(() => {
        const grouped = new Map<string, Seat[]>()

        for (const seat of seats) {
            const row = seatRow(seat.seat_number)
            const rowSeats = grouped.get(row) ?? []

            rowSeats.push(seat)
            grouped.set(row, rowSeats)
        }

        for (const rowSeats of grouped.values()) {
            rowSeats.sort((leftSeat, rightSeat) => (
                leftSeat.seat_number.localeCompare(
                    rightSeat.seat_number,
                    undefined,
                    {
                        numeric: true,
                    },
                )
            ))
        }

        return Array.from(grouped.entries())
    }, [seats])

    const selectedSeat = seats.find(
        (seat) => seat.id === selectedSeatId,
    ) ?? null

    const contestedSeatIds = useMemo(
        () => new Set(
            activeExperiment?.hotspotSeatIds ?? [],
        ),
        [activeExperiment],
    )

    const contestedSeatNumbers = useMemo(
        () => (
            seats
                .filter((seat) => (
                    contestedSeatIds.has(seat.id)
                ))
                .map((seat) => seat.seat_number)
        ),
        [
            contestedSeatIds,
            seats,
        ],
    )

    const selectedSeatIsContested = (
        selectedSeat !== null
        && contestedSeatIds.has(selectedSeat.id)
    )

    const experimentBookingReady = (
        !show?.is_experiment_show
        || (
            activeExperiment?.active === true
            && selectedSeatIsContested
        )
    )

    function selectSeat(seat: Seat): void {
        if (
            seat.state !== 'AVAILABLE'
            || bookingState === 'loading'
        ) {
            return
        }

        setSelectedSeatId(
            seat.id === selectedSeatId
                ? null
                : seat.id,
        )
        setErrorMessage(null)
    }

    async function completeBooking(): Promise<void> {
        if (!show || !selectedSeat) {
            setErrorMessage(
                'Select one available seat to continue.',
            )
            return
        }

        if (show.is_experiment_show) {
            if (!activeExperiment?.active) {
                setErrorMessage(
                    'No contention experiment is currently '
                    + 'running. Start the interactive runner '
                    + 'before booking.',
                )
                return
            }

            if (!contestedSeatIds.has(selectedSeat.id)) {
                setErrorMessage(
                    'Select the seat targeted by the live '
                    + `experiment: ${contestedSeatNumbers.join(', ')
                    || 'the highlighted seat'
                    }.`,
                )
                return
            }
        }

        const user = getDemoUser()

        if (!user) {
            navigate(
                '/login',
                {
                    state: {
                        from: location.pathname,
                    },
                },
            )
            return
        }

        setBookingState('loading')
        setErrorMessage(null)

        try {
            const booking = await createDemoBooking({
                user_id: user.syntheticUserId,
                show_id: show.id,
                seat_id: selectedSeat.id,
            })

            const reference = (
                booking.id.replaceAll('-', '')
                    .slice(0, 10)
                    .toUpperCase()
            )

            saveBookingConfirmation({
                bookingIds: [booking.id],
                reference,
                show,
                seats: [selectedSeat],
                customer: user,
                bookedAt: booking.booked_at,
            })

            setBookingState('success')
            navigate('/confirmation')
        } catch (error) {
            setBookingState('error')

            if (
                error instanceof ApiClientError
                && error.code === 'SEAT_UNAVAILABLE'
            ) {
                setErrorMessage(
                    'That seat was booked by another request. '
                    + 'Choose another available seat.',
                )
                setSelectedSeatId(null)
                await loadScreening()
                return
            }

            setErrorMessage(
                error instanceof ApiClientError
                    ? error.message
                    : 'The booking could not be completed.',
            )
        }
    }

    if (requestState === 'loading') {
        return (
            <main className="content-page">
                <p
                    className="page-message"
                    aria-live="polite"
                >
                    Loading seats
                </p>
            </main>
        )
    }

    if (
        requestState === 'error'
        || !show
    ) {
        return (
            <main className="content-page">
                <section className="page-message page-message--error">
                    <p className="eyebrow">
                        Screening unavailable
                    </p>
                    <h1>
                        We could not load this screening
                    </h1>
                    <p>{errorMessage}</p>
                    <Link
                        className="secondary-button"
                        to="/"
                    >
                        Return to films
                    </Link>
                </section>
            </main>
        )
    }

    return (
        <main className="seat-page">
            <section className="screening-summary">
                <div>
                    <Link
                        className="back-link"
                        to="/"
                    >
                        Films
                    </Link>

                    <p className="eyebrow">
                        Select one seat
                    </p>

                    <h1>
                        The Last Signal
                    </h1>

                    <p>
                        {formatScreeningTime(show.show_time)}
                        <br />
                        {show.venue}
                    </p>
                </div>

                <button
                    className="text-button"
                    type="button"
                    onClick={() => {
                        void loadScreening()
                    }}
                >
                    Refresh availability
                </button>
            </section>

            <div className="booking-layout">
                <section
                    className="auditorium"
                    aria-labelledby="seat-map-title"
                >
                    <div className="cinema-screen">
                        <span>SCREEN</span>
                    </div>

                    <h2
                        id="seat-map-title"
                        className="visually-hidden"
                    >
                        Auditorium seat map
                    </h2>

                    <div className="seat-legend">
                        <span>
                            <i className="seat-swatch seat-swatch--available" />
                            Available
                        </span>

                        <span>
                            <i className="seat-swatch seat-swatch--selected" />
                            Selected
                        </span>

                        <span>
                            <i className="seat-swatch seat-swatch--booked" />
                            Booked
                        </span>

                        {activeExperiment?.active ? (
                            <span>
                                <i
                                    className={
                                        'seat-swatch '
                                        + 'seat-swatch--selected'
                                    }
                                />
                                Live contested seat
                            </span>
                        ) : null}
                    </div>

                    <div className="seat-map">
                        {seatsByRow.map(([
                            row,
                            rowSeats,
                        ]) => (
                            <div
                                className="seat-row"
                                key={row}
                            >
                                <span className="seat-row__label">
                                    {row}
                                </span>

                                <div className="seat-row__seats">
                                    {rowSeats.map((seat) => {
                                        const selected = (
                                            seat.id === selectedSeatId
                                        )
                                        const contested = (
                                            contestedSeatIds.has(
                                                seat.id,
                                            )
                                        )

                                        const className = [
                                            'seat-button',
                                            selected
                                                ? 'seat-button--selected'
                                                : '',
                                            contested
                                                ? 'seat-button--contested'
                                                : '',
                                            seat.state !== 'AVAILABLE'
                                                ? 'seat-button--unavailable'
                                                : '',
                                        ]
                                            .filter(Boolean)
                                            .join(' ')

                                        const seatDescription = (
                                            selected
                                                ? 'selected'
                                                : seat.state.toLowerCase()
                                        )

                                        return (
                                            <button
                                                className={className}
                                                type="button"
                                                key={seat.id}
                                                disabled={
                                                    seat.state
                                                    !== 'AVAILABLE'
                                                }
                                                aria-pressed={selected}
                                                aria-label={
                                                    `${seat.seat_number}: ${seatDescription
                                                    }${contested
                                                        ? ', live contested seat'
                                                        : ''
                                                    }`
                                                }
                                                style={
                                                    contested
                                                        && !selected
                                                        ? {
                                                            outline: (
                                                                '3px solid '
                                                                + '#a22028'
                                                            ),
                                                            outlineOffset: (
                                                                '3px'
                                                            ),
                                                            boxShadow: (
                                                                '0 0 0 2px '
                                                                + '#f3eee4'
                                                            ),
                                                        }
                                                        : undefined
                                                }
                                                onClick={() => {
                                                    selectSeat(seat)
                                                }}
                                            >
                                                {seat.seat_number}
                                            </button>
                                        )
                                    })}
                                </div>

                                <span
                                    className="seat-row__label"
                                    aria-hidden="true"
                                >
                                    {row}
                                </span>
                            </div>
                        ))}
                    </div>
                </section>

                <aside className="booking-summary">
                    <p className="eyebrow">
                        Your booking
                    </p>

                    <h2>
                        {selectedSeat
                            ? `Seat ${selectedSeat.seat_number}`
                            : 'No seat selected'}
                    </h2>

                    <dl>
                        <div>
                            <dt>Film</dt>
                            <dd>The Last Signal</dd>
                        </div>

                        <div>
                            <dt>Venue</dt>
                            <dd>{show.venue}</dd>
                        </div>

                        <div>
                            <dt>Ticket</dt>
                            <dd>
                                {selectedSeat ? '₹240' : '—'}
                            </dd>
                        </div>

                        <div>
                            <dt>Booking fee</dt>
                            <dd>₹0</dd>
                        </div>
                    </dl>

                    {show.is_experiment_show ? (
                        <p
                            className="booking-summary__notice"
                            aria-live="polite"
                        >
                            {activeExperiment?.active
                                ? (
                                    'Live contention experiment '
                                    + 'running. Contested seat: '
                                    + (
                                        contestedSeatNumbers.join(
                                            ', ',
                                        )
                                        || 'loading'
                                    )
                                    + '.'
                                )
                                : (
                                    'Waiting for a live contention '
                                    + 'experiment. Select the seat '
                                    + 'specified in the runner, then '
                                    + 'confirm when the experiment '
                                    + 'becomes active.'
                                )}
                        </p>
                    ) : null}

                    <p className="booking-summary__notice">
                        Demonstration booking only. No payment
                        information is required.
                    </p>

                    {errorMessage ? (
                        <p
                            className="form-message form-message--error"
                            role="alert"
                        >
                            {errorMessage}
                        </p>
                    ) : null}

                    <button
                        className="primary-button primary-button--wide"
                        type="button"
                        disabled={
                            !selectedSeat
                            || bookingState === 'loading'
                            || !experimentBookingReady
                        }
                        onClick={() => {
                            void completeBooking()
                        }}
                    >
                        {bookingState === 'loading'
                            ? 'Reserving seat'
                            : (
                                show.is_experiment_show
                                    && !activeExperiment?.active
                                    ? 'Waiting for experiment'
                                    : 'Confirm booking'
                            )}
                    </button>
                </aside>
            </div>
        </main>
    )
}