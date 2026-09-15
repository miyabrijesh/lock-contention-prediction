import { ServiceStatus } from '../components/ServiceStatus'

export function OperationsPage() {
    return (
        <main className="operations-page">
            <section className="operations-heading">
                <div>
                    <p className="eyebrow">
                        Staff operations
                    </p>
                    <h1>
                        Reservation system status
                    </h1>
                    <p>
                        This view reads the latest persisted
                        PostgreSQL telemetry and contention-risk
                        prediction from the FastAPI service.
                    </p>
                </div>

                <div className="operations-heading__stamp">
                    <span>Environment</span>
                    <strong>Controlled demonstration</strong>
                </div>
            </section>

            <ServiceStatus detailed />

            <section className="operations-section">
                <div className="section-heading">
                    <div>
                        <p className="eyebrow">
                            Prediction workflow
                        </p>
                        <h2>
                            How the current reading is produced
                        </h2>
                    </div>
                </div>

                <ol className="process-list">
                    <li>
                        <span>01</span>
                        <div>
                            <h3>Booking traffic</h3>
                            <p>
                                Concurrent reservation requests compete
                                for PostgreSQL seat rows.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>02</span>
                        <div>
                            <h3>Telemetry collection</h3>
                            <p>
                                The collector records locks, blocked
                                sessions, throughput and latency.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>03</span>
                        <div>
                            <h3>Risk inference</h3>
                            <p>
                                The active model estimates contention
                                risk for the following 30-second window.
                            </p>
                        </div>
                    </li>
                    <li>
                        <span>04</span>
                        <div>
                            <h3>Persisted evidence</h3>
                            <p>
                                Predictions are stored with model,
                                threshold and quality provenance.
                            </p>
                        </div>
                    </li>
                </ol>
            </section>

            <section className="operations-section">
                <div className="section-heading">
                    <div>
                        <p className="eyebrow">
                            Active model
                        </p>
                        <h2>
                            Deployment remains conservative
                        </h2>
                    </div>
                </div>

                <div className="model-record">
                    <dl>
                        <div>
                            <dt>Registered model</dt>
                            <dd>contention-logistic-v1</dd>
                        </div>
                        <div>
                            <dt>High-risk threshold</dt>
                            <dd>0.34</dd>
                        </div>
                        <div>
                            <dt>Prediction horizon</dt>
                            <dd>30 seconds</dd>
                        </div>
                        <div>
                            <dt>Current deployment</dt>
                            <dd>Retained</dd>
                        </div>
                    </dl>

                    <div className="model-record__note">
                        <h3>
                            Phase 8A candidate decision
                        </h3>
                        <p>
                            A random-forest candidate improved
                            recognition of low-contention conditions,
                            but held-out recall fell to 78.18%.
                            It was preserved for research evidence
                            and rejected for activation.
                        </p>
                    </div>
                </div>
            </section>

            <section className="operations-section">
                <div className="section-heading">
                    <div>
                        <p className="eyebrow">
                            Demonstration boundaries
                        </p>
                        <h2>
                            What this status represents
                        </h2>
                    </div>
                </div>

                <div className="boundary-copy">
                    <p>
                        Readings are produced by controlled local
                        booking experiments. They demonstrate the
                        complete monitoring and prediction
                        mechanism.
                    </p>
                    <p>
                        Results are experimental evidence and are
                        not presented as production performance
                        claims.
                    </p>
                </div>
            </section>
        </main>
    )
}