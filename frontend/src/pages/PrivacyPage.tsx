export function PrivacyPage() {
    return (
        <main className="legal-page">
            <article>
                <p className="eyebrow">
                    Demonstration policy
                </p>
                <h1>Privacy</h1>

                <p className="legal-page__updated">
                    Last updated: 15 September 2026
                </p>

                <section>
                    <h2>Purpose</h2>
                    <p>
                        FRAME 24 is a college-project
                        demonstration of a booking system and a
                        PostgreSQL lock-contention prediction
                        workflow.
                    </p>
                </section>

                <section>
                    <h2>Information entered</h2>
                    <p>
                        The demonstration asks for a name and email
                        address. These details are stored in the
                        browser’s local storage to maintain a
                        temporary demo identity.
                    </p>
                </section>

                <section>
                    <h2>Booking records</h2>
                    <p>
                        A booking request is associated with a
                        seeded synthetic user identifier. The
                        database records the selected show, seat,
                        transaction outcome and technical timing
                        information.
                    </p>
                </section>

                <section>
                    <h2>Payments</h2>
                    <p>
                        No payment details are requested, processed
                        or stored. Every ticket and price shown by
                        the interface is part of the demonstration.
                    </p>
                </section>

                <section>
                    <h2>Removal</h2>
                    <p>
                        Signing out removes the locally stored demo
                        identity and recent booking information
                        from the browser. Database demonstration
                        records may remain for project evaluation.
                    </p>
                </section>
            </article>
        </main>
    )
}