export type SeatState = 'AVAILABLE' | 'HELD' | 'BOOKED'

export type RiskLevel = 'LOW' | 'MEDIUM' | 'HIGH'

export type BookingStatus =
    | 'CONFIRMED'
    | 'CANCELLED'
    | 'FAILED'

export interface Show {
    id: number
    name: string
    venue: string
    show_time: string
    is_experiment_show: boolean
    created_at: string
    updated_at: string
}

export interface Seat {
    id: number
    show_id: number
    seat_number: string
    state: SeatState
    hold_expires_at: string | null
    version: number
    created_at: string
    updated_at: string
}

export interface Booking {
    id: string
    idempotency_key: string
    user_id: number
    show_id: number
    seat_id: number
    experiment_run_id: string | null
    status: BookingStatus
    booked_at: string
    cancelled_at: string | null
    created_at: string
    updated_at: string
}

export interface Prediction {
    id: string
    experiment_run_id: string
    metric_sample_id: string
    model_version_id: string
    predicted_at: string
    target_window_start: string
    target_window_end: string
    probability: string
    risk_level: RiskLevel
    threshold_used: string
    is_stale: boolean
    quality_flags: string[]
    actual_label: boolean | null
    actual_label_assigned_at: string | null
    actual_label_contract_version: string | null
    actual_label_evidence: Record<string, unknown> | null
}

export interface DemoUser {
    name: string
    email: string
    syntheticUserId: number
}

export interface BookingSelection {
    show: Show
    seats: Seat[]
}

export interface BookingConfirmation {
    bookingIds: string[]
    reference: string
    show: Show
    seats: Seat[]
    customer: DemoUser
    bookedAt: string
}

export interface ContentionStatus {
    available: boolean
    experimentRunId: string | null
    probability: number | null
    riskLevel: RiskLevel | null
    predictedAt: string | null
    threshold: number | null
    isStale: boolean | null
    activeTransactions: number | null
    blockedSessions: number | null
    waitingLocks: number | null
    requestsPerSecond: number | null
    p95LatencyMs: number | null
}
export interface ActiveExperiment {
    active: boolean
    experimentRunId: string | null
    showId: number | null
    status: 'RUNNING' | null
    hotspotSeatIds: number[]
    startedAt: string | null
    durationSec: number | null
}

export interface ApiErrorDetail {
    code?: string
    message?: string
}

export interface ApiErrorBody {
    detail?: ApiErrorDetail | string
}

export interface MoviePresentation {
    eyebrow: string
    title: string
    synopsis: string
    language: string
    certification: string
    duration: string
    genres: string[]
    posterClassName: string
}

export type RequestState =
    | 'idle'
    | 'loading'
    | 'success'
    | 'error'