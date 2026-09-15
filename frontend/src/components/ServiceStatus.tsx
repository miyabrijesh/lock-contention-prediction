import {
    useEffect,
    useState,
} from 'react'

import {
    ApiClientError,
    getContentionStatus,
} from '../lib/api'
import type {
    ContentionStatus,
    RequestState,
} from '../types'

interface ServiceStatusProps {
    detailed?: boolean
}

function formatProbability(
    probability: number | null,
): string {
    if (probability === null) {
        return 'Unavailable'
    }

    return `${(probability * 100).toFixed(1)}%`
}

function formatMetric(
    value: number | null,
    suffix = '',
): string {
    if (value === null) {
        return 'Unavailable'
    }

    return `${value.toLocaleString()}${suffix}`
}

export function ServiceStatus({
    detailed = false,
}: ServiceStatusProps) {
    const [status, setStatus] = (
        useState<ContentionStatus | null>(null)
    )
    const [requestState, setRequestState] = (
        useState<RequestState>('loading')
    )
    const [errorMessage, setErrorMessage] = (
        useState<string | null>(null)
    )

    useEffect(() => {
        let active = true

        async function loadStatus(): Promise<void> {
            try {
                const nextStatus = (
                    await getContentionStatus()
                )

                if (!active) {
                    return
                }

                setStatus(nextStatus)
                setRequestState('success')
                setErrorMessage(null)
            } catch (error) {
                if (!active) {
                    return
                }

                setRequestState('error')
                setErrorMessage(
                    error instanceof ApiClientError
                        ? error.message
                        : 'Live status is currently unavailable.',
                )
            }
        }

        void loadStatus()

        const intervalId = window.setInterval(
            () => {
                void loadStatus()
            },
            5000,
        )

        return () => {
            active = false
            window.clearInterval(intervalId)
        }
    }, [])

    if (requestState === 'loading') {
        return (
            <section
                className="service-status service-status--loading"
                aria-live="polite"
            >
                <span className="service-status__label">
                    Booking demand
                </span>
                <span>Reading current conditions</span>
            </section>
        )
    }

    if (
        requestState === 'error'
        || !status
        || !status.available
    ) {
        return (
            <section
                className="service-status service-status--unavailable"
                aria-live="polite"
            >
                <span className="service-status__label">
                    Booking demand
                </span>
                <span>
                    {errorMessage ?? 'No recent prediction available'}
                </span>
            </section>
        )
    }

    const riskClassName = (
        `service-status--${status.riskLevel?.toLowerCase()}`
    )

    if (!detailed) {
        return (
            <section
                className={`service-status ${riskClassName}`}
                aria-live="polite"
            >
                <span className="service-status__label">
                    Booking demand
                </span>
                <strong>
                    {status.riskLevel ?? 'Unavailable'}
                </strong>
                <span>
                    {formatProbability(status.probability)}
                    {' '}predicted contention
                </span>
            </section>
        )
    }

    return (
        <section
            className={`status-panel ${riskClassName}`}
            aria-live="polite"
        >
            <div className="status-panel__heading">
                <div>
                    <p className="eyebrow">
                        Latest prediction
                    </p>
                    <h2>
                        {status.riskLevel} contention risk
                    </h2>
                </div>

                <div className="risk-reading">
                    {formatProbability(status.probability)}
                </div>
            </div>

            <dl className="metrics-table">
                <div>
                    <dt>Active transactions</dt>
                    <dd>
                        {formatMetric(
                            status.activeTransactions,
                        )}
                    </dd>
                </div>
                <div>
                    <dt>Blocked sessions</dt>
                    <dd>
                        {formatMetric(
                            status.blockedSessions,
                        )}
                    </dd>
                </div>
                <div>
                    <dt>Waiting locks</dt>
                    <dd>
                        {formatMetric(
                            status.waitingLocks,
                        )}
                    </dd>
                </div>
                <div>
                    <dt>Requests per second</dt>
                    <dd>
                        {formatMetric(
                            status.requestsPerSecond,
                        )}
                    </dd>
                </div>
                <div>
                    <dt>p95 latency</dt>
                    <dd>
                        {formatMetric(
                            status.p95LatencyMs,
                            ' ms',
                        )}
                    </dd>
                </div>
                <div>
                    <dt>Model threshold</dt>
                    <dd>
                        {formatProbability(status.threshold)}
                    </dd>
                </div>
            </dl>

            <p className="status-panel__timestamp">
                Prediction recorded{' '}
                {status.predictedAt
                    ? new Date(
                        status.predictedAt,
                    ).toLocaleString()
                    : 'at an unavailable time'}
            </p>
        </section>
    )
}