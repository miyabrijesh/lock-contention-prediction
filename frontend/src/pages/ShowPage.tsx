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
    createDemoBatchBooking,
    getActiveExperiment,
    getDemoSession,
    getShow,
    listSeats,
    submitDemoSeatSelection,
} from '../lib/api'
import {
    getDemoUser,
    saveBookingConfirmation,
} from '../lib/demoSession'
import type {
    ActiveExperiment,
    DemoSession,
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

function seatPosition(
    seatNumber: string,
): {
    row: string
    number: number
} | null {
    const match = seatNumber.match(
        /^([A-Za-z]+)([1-9][0-9]*)$/,
    )

    if (!match) {
        return null
    }

    return {
        row: match[1].toUpperCase(),
        number: Number(match[2]),
    }
}

function seatRow(
    seatNumber: string,
): string {
    return (
        seatPosition(seatNumber)?.row
        ?? 'Other'
    )
}

function sameSeatIds(
    left: number[],
    right: number[],
): boolean {
    if (left.length !== right.length) {
        return false
    }

    const rightIds = new Set(right)

    return left.every(
        (seatId) => rightIds.has(seatId),
    )
}

function validConsecutiveSelection(
    selectedSeats: Seat[],
): boolean {
    if (
        selectedSeats.length < 1
        || selectedSeats.length > 5
    ) {
        return false
    }

    const positions = selectedSeats.map(
        (seat) => seatPosition(
            seat.seat_number,
        ),
    )

    if (positions.some(
        (position) => position === null,
    )) {
        return false
    }

    const validPositions = positions.filter(
        (
            position,
        ): position is {
            row: string
            number: number
        } => position !== null,
    )

    const rows = new Set(
        validPositions.map(
            (position) => position.row,
        ),
    )

    if (rows.size !== 1) {
        return false
    }

    const numbers = validPositions
        .map((position) => position.number)
        .sort((left, right) => left - right)

    return numbers.every(
        (number, index) => (
            index === 0
            || number === numbers[index - 1] + 1
        ),
    )
}

export function ShowPage() {
    const { showId } = useParams()
    const navigate = useNavigate()
    const location = useLocation()

    const numericShowId = Number(showId)

    const [show, setShow] = useState<Show | null>(null)
    const [seats, setSeats] = useState<Seat[]>([])
    const [
        selectedSeatIds,
        setSelectedSeatIds,
    ] = useState<number[]>([])
    const [requestState, setRequestState] = (
        useState<RequestState>('loading')
    )
    const [bookingState, setBookingState] = (
        useState<RequestState>('idle')
    )
    const [errorMessage, setErrorMessage] = (
        useState<string | null>(null)
    )
    const [
        activeExperiment,
        setActiveExperiment,
    ] = useState<ActiveExperiment | null>(null)
    const [
        demoSession,
        setDemoSession,
    ] = useState<DemoSession | null>(null)

    const loadScreening = useCallback(
        async (): Promise<void> => {
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
                setSelectedSeatIds(
                    (currentSeatIds) => (
                        currentSeatIds.filter(
                            (seatId) => (
                                nextSeats.some(
                                    (seat) => (
                                        seat.id === seatId
                                        && seat.state
                                        === 'AVAILABLE'
                                    ),
                                )
                            ),
                        )
                    ),
                )
                setRequestState('success')
            } catch (error) {
                setRequestState('error')
                setErrorMessage(
                    error instanceof ApiClientError
                        ? error.message
                        : (
                            'The screening could not '
                            + 'be loaded.'
                        ),
                )
            }
        },
        [numericShowId],
    )

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

        async function loadLiveState(): Promise<void> {
            const [
                experimentResult,
                sessionResult,
            ] = await Promise.allSettled([
                getActiveExperiment(numericShowId),
                getDemoSession(numericShowId),
            ])

            if (!mounted) {
                return
            }

            if (experimentResult.status === 'fulfilled') {
                setActiveExperiment(
                    experimentResult.value,
                )
            } else {
                setActiveExperiment(null)
            }

            if (sessionResult.status === 'fulfilled') {
                const nextSession = sessionResult.value

                setDemoSession(nextSession)

                if (
                    nextSession.available
                    && (
                        nextSession.status === 'READY'
                        || nextSession.status === 'RUNNING'
                    )
                ) {
                    setSelectedSeatIds(
                        nextSession.selectedSeatIds,
                    )
                }
            } else {
                setDemoSession(null)
            }
        }

        const initialTimer = window.setTimeout(() => {
            void loadLiveState()
        }, 0)

        const intervalId = window.setInterval(() => {
            void loadLiveState()
        }, 1000)

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

    const selectedSeats = useMemo(
        () => (
            selectedSeatIds
                .map(
                    (seatId) => seats.find(
                        (seat) => seat.id === seatId,
                    ),
                )
                .filter(
                    (seat): seat is Seat => (
                        seat !== undefined
                    ),
                )
        ),
        [
            seats,
            selectedSeatIds,
        ],
    )

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

    const selectedSeatsMatchExperiment = (
        activeExperiment?.active === true
        && sameSeatIds(
            selectedSeatIds,
            activeExperiment.hotspotSeatIds,
        )
    )

    const waitingForSelection = (
        show?.is_experiment_show === true
        && demoSession?.available === true
        && demoSession.status
        === 'WAITING_FOR_SELECTION'
    )

    const selectionSubmitted = (
        show?.is_experiment_show === true
        && demoSession?.available === true
        && demoSession.status === 'READY'
    )

    const liveDemoReady = (
        show?.is_experiment_show === true
        && demoSession?.available === true
        && demoSession.status === 'RUNNING'
        && selectedSeatsMatchExperiment
    )

    const selectionLocked = (
        show?.is_experiment_show === true
        && !waitingForSelection
    )

    function selectSeat(seat: Seat): void {
        if (
            seat.state !== 'AVAILABLE'
            || bookingState === 'loading'
            || selectionLocked
        ) {
            return
        }

        const alreadySelected = (
            selectedSeatIds.includes(seat.id)
        )

        const candidateIds = (
            alreadySelected
                ? selectedSeatIds.filter(
                    (seatId) => seatId !== seat.id,
                )
                : [
                    ...selectedSeatIds,
                    seat.id,
                ]
        )

        if (candidateIds.length === 0) {
            setSelectedSeatIds([])
            setErrorMessage(null)
            return
        }

        if (candidateIds.length > 5) {
            setErrorMessage(
                'You can select a maximum of five seats.',
            )
            return
        }

        const candidateSeats = candidateIds
            .map(
                (seatId) => seats.find(
                    (candidateSeat) => (
                        candidateSeat.id === seatId
                    ),
                ),
            )
            .filter(
                (
                    candidateSeat,
                ): candidateSeat is Seat => (
                    candidateSeat !== undefined
                ),
            )

        if (
            !validConsecutiveSelection(
                candidateSeats,
            )
        ) {
            setErrorMessage(
                'Select 1–5 consecutive seats '
                + 'from the same row.',
            )
            return
        }

        candidateSeats.sort((left, right) => {
            const leftPosition = seatPosition(
                left.seat_number,
            )
            const rightPosition = seatPosition(
                right.seat_number,
            )

            return (
                (leftPosition?.number ?? 0)
                - (rightPosition?.number ?? 0)
            )
        })

        setSelectedSeatIds(
            candidateSeats.map(
                (candidateSeat) => candidateSeat.id,
            ),
        )
        setErrorMessage(null)
    }

    async function submitSelection(): Promise<void> {
        if (
            !demoSession?.sessionId
            || !waitingForSelection
        ) {
            setErrorMessage(
                'Start the interactive runner before '
                + 'submitting seats.',
            )
            return
        }

        if (
            !validConsecutiveSelection(
                selectedSeats,
            )
        ) {
            setErrorMessage(
                'Select 1–5 consecutive available seats '
                + 'from the same row.',
            )
            return
        }

        setBookingState('loading')
        setErrorMessage(null)

        try {
            const nextSession = (
                await submitDemoSeatSelection(
                    demoSession.sessionId,
                    selectedSeatIds,
                )
            )

            setDemoSession(nextSession)
            setSelectedSeatIds(
                nextSession.selectedSeatIds,
            )
            setBookingState('idle')
        } catch (error) {
            setBookingState('error')
            setErrorMessage(
                error instanceof ApiClientError
                    ? error.message
                    : (
                        'The seat selection could '
                        + 'not be submitted.'
                    ),
            )
        }
    }

    async function completeBooking(): Promise<void> {
        if (!show || selectedSeats.length === 0) {
            setErrorMessage(
                'Select at least one available seat.',
            )
            return
        }

        if (show.is_experiment_show) {
            if (waitingForSelection) {
                await submitSelection()
                return
            }

            if (!liveDemoReady) {
                setErrorMessage(
                    selectionSubmitted
                        ? (
                            'The runner is preparing the '
                            + 'experiment. Please wait.'
                        )
                        : (
                            'No live demo is ready for '
                            + 'this selection.'
                        ),
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
            const result = await createDemoBatchBooking({
                userId: user.syntheticUserId,
                showId: show.id,
                seatIds: selectedSeatIds,
            })

            const firstBooking = result.bookings[0]

            if (!firstBooking) {
                throw new Error(
                    'The booking response was empty.',
                )
            }

            const reference = (
                firstBooking.id.replaceAll('-', '')
                    .slice(0, 10)
                    .toUpperCase()
            )

            saveBookingConfirmation({
                bookingIds: result.bookings.map(
                    (booking) => booking.id,
                ),
                reference,
                show,
                seats: selectedSeats,
                customer: user,
                bookedAt: firstBooking.booked_at,
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
                    'One or more selected seats were '
                    + 'booked by another request.',
                )
                setSelectedSeatIds([])
                await loadScreening()
                return
            }

            setErrorMessage(
                error instanceof ApiClientError
                    ? error.message
                    : (
                        'The booking could not '
                        + 'be completed.'
                    ),
            )
        }
    }

    function primaryButtonText(): string {
        if (bookingState === 'loading') {
            return (
                waitingForSelection
                    ? 'Submitting seats'
                    : 'Reserving seats'
            )
        }

        if (!show?.is_experiment_show) {
            return 'Confirm booking'
        }

        if (waitingForSelection) {
            return (
                selectedSeats.length > 0
                    ? `Submit ${selectedSeats.length} seat${
                        selectedSeats.length === 1
                            ? ''
                            : 's'
                    }`
                    : 'Select seats'
            )
        }

        if (selectionSubmitted) {
            return 'Preparing experiment'
        }

        if (liveDemoReady) {
            return 'Confirm booking'
        }

        return 'Waiting for demo runner'
    }

    const primaryButtonDisabled = (
        selectedSeats.length === 0
        || bookingState === 'loading'
        || (
            show?.is_experiment_show === true
            && !waitingForSelection
            && !liveDemoReady
        )
    )

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
                        Select 1–5 consecutive seats
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
                                Live contested seats
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
                                            selectedSeatIds.includes(
                                                seat.id,
                                            )
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
                                                : (
                                                    seat.state
                                                        .toLowerCase()
                                                )
                                        )

                                        return (
                                            <button
                                                className={className}
                                                type="button"
                                                key={seat.id}
                                                disabled={
                                                    seat.state
                                                    !== 'AVAILABLE'
                                                    || bookingState
                                                    === 'loading'
                                                }
                                                aria-disabled={
                                                    selectionLocked
                                                    && !selected
                                                }
                                                aria-pressed={selected}
                                                aria-label={
                                                    `${seat.seat_number}: ${
                                                        seatDescription
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
                        {selectedSeats.length > 0
                            ? selectedSeats
                                .map(
                                    (seat) => (
                                        seat.seat_number
                                    ),
                                )
                                .join(', ')
                            : 'No seats selected'}
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
                            <dt>Tickets</dt>
                            <dd>
                                {selectedSeats.length > 0
                                    ? (
                                        `${selectedSeats.length} × ₹240`
                                    )
                                    : '—'}
                            </dd>
                        </div>

                        <div>
                            <dt>Total</dt>
                            <dd>
                                {selectedSeats.length > 0
                                    ? (
                                        `₹${
                                            selectedSeats.length
                                            * 240
                                        }`
                                    )
                                    : '₹0'}
                            </dd>
                        </div>
                    </dl>

                    {show.is_experiment_show ? (
                        <p
                            className="booking-summary__notice"
                            aria-live="polite"
                        >
                            {waitingForSelection
                                ? (
                                    'The demo runner is waiting. '
                                    + 'Choose 1–5 consecutive '
                                    + 'seats and submit them.'
                                )
                                : selectionSubmitted
                                    ? (
                                        'Selection submitted. '
                                        + 'The runner is preparing '
                                        + 'the experiment.'
                                    )
                                    : liveDemoReady
                                        ? (
                                            'Live experiment running '
                                            + `for ${contestedSeatNumbers
                                                .join(', ')}.`
                                        )
                                        : (
                                            'Run the interactive demo '
                                            + 'command to begin.'
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
                        disabled={primaryButtonDisabled}
                        onClick={() => {
                            void completeBooking()
                        }}
                    >
                        {primaryButtonText()}
                    </button>
                </aside>
            </div>
        </main>
    )
}
