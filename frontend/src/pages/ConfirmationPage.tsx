import { Link } from 'react-router-dom'

import {
    clearBookingConfirmation,
    getBookingConfirmation,
} from '../lib/demoSession'

function formatBookingTime(
    dateValue: string,
): string {
    return new Intl.DateTimeFormat(
        'en-IN',
        {
            dateStyle: 'long',
            timeStyle: 'short',
        },
    ).format(new Date(dateValue))
}

export function ConfirmationPage() {
    const confirmation = getBookingConfirmation()

    if (!confirmation) {
        return (
            <main className="content-page">
                <section className="page-message">
                    <p className="eyebrow">
                        Booking confirmation
                    </p>
                    <h1>
                        No recent booking was found
                    </h1>
                    <p>
                        Select a screening and reserve an available
                        seat to create a demonstration booking.
                    </p>
                    <Link
                        className="primary-button"
                        to="/"
                    >
                        View films
                    </Link>
                </section>
            </main>
        )
    }

    const seatNumbers = confirmation.seats
        .map((seat) => seat.seat_number)
        .join(', ')

    return (
        <main className="confirmation-page">
            <section className="confirmation-ticket">
                <div className="confirmation-ticket__status">
                    Booking confirmed
                </div>

                <div className="confirmation-ticket__heading">
                    <div>
                        <p className="eyebrow">
                            FRAME 24
                        </p>
                        <h1>
                            The Last Signal
                        </h1>
                        <p>
                            Your demonstration seat has been reserved
                            in the booking database.
                        </p>
                    </div>

                    <div className="booking-reference">
                        <span>Booking reference</span>
                        <strong>
                            {confirmation.reference}
                        </strong>
                    </div>
                </div>

                <dl className="ticket-details">
                    <div>
                        <dt>Guest</dt>
                        <dd>
                            {confirmation.customer.name}
                        </dd>
                    </div>
                    <div>
                        <dt>Seat</dt>
                        <dd>{seatNumbers}</dd>
                    </div>
                    <div>
                        <dt>Venue</dt>
                        <dd>
                            {confirmation.show.venue}
                        </dd>
                    </div>
                    <div>
                        <dt>Screening</dt>
                        <dd>
                            {formatBookingTime(
                                confirmation.show.show_time,
                            )}
                        </dd>
                    </div>
                    <div>
                        <dt>Reserved at</dt>
                        <dd>
                            {formatBookingTime(
                                confirmation.bookedAt,
                            )}
                        </dd>
                    </div>
                    <div>
                        <dt>Amount collected</dt>
                        <dd>₹0 · Demonstration</dd>
                    </div>
                </dl>

                <div className="confirmation-ticket__technical">
                    <p className="eyebrow">
                        Transaction result
                    </p>
                    <p>
                        The seat state was changed through the
                        transactional FastAPI booking service.
                        Competing requests for this seat will now
                        receive a conflict response.
                    </p>
                </div>

                <div className="confirmation-actions">
                    <Link
                        className="primary-button"
                        to="/"
                        onClick={() => {
                            clearBookingConfirmation()
                        }}
                    >
                        Return to films
                    </Link>

                    <Link
                        className="secondary-button"
                        to="/operations"
                    >
                        View system status
                    </Link>
                </div>
            </section>
        </main>
    )
}