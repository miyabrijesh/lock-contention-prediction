import type {
    ApiErrorBody,
    Booking,
    ContentionStatus,
    Seat,
    Show,
} from '../types'

const configuredBaseUrl = (
    import.meta.env.VITE_API_BASE_URL
    ?? 'http://127.0.0.1:8001'
)

export const API_BASE_URL = (
    configuredBaseUrl.replace(/\/+$/, '')
)

export class ApiClientError extends Error {
    readonly status: number
    readonly code: string | null

    constructor(
        message: string,
        status: number,
        code: string | null = null,
    ) {
        super(message)
        this.name = 'ApiClientError'
        this.status = status
        this.code = code
    }
}

async function readError(
    response: Response,
): Promise<ApiClientError> {
    let body: ApiErrorBody | null = null

    try {
        body = await response.json() as ApiErrorBody
    } catch {
        return new ApiClientError(
            'The booking service returned an unreadable response.',
            response.status,
        )
    }

    if (typeof body.detail === 'string') {
        return new ApiClientError(
            body.detail,
            response.status,
        )
    }

    const message = (
        body.detail?.message
        ?? 'The request could not be completed.'
    )

    return new ApiClientError(
        message,
        response.status,
        body.detail?.code ?? null,
    )
}

async function request<T>(
    path: string,
    options?: RequestInit,
): Promise<T> {
    let response: Response

    try {
        response = await fetch(
            `${API_BASE_URL}${path}`,
            {
                ...options,
                headers: {
                    Accept: 'application/json',
                    ...options?.headers,
                },
            },
        )
    } catch {
        throw new ApiClientError(
            'The booking service is unavailable. Check that the API is running.',
            0,
            'NETWORK_ERROR',
        )
    }

    if (!response.ok) {
        throw await readError(response)
    }

    return await response.json() as T
}

export function listShows(): Promise<Show[]> {
    return request<Show[]>('/api/v1/shows')
}

export function getShow(
    showId: number,
): Promise<Show> {
    return request<Show>(
        `/api/v1/shows/${showId}`,
    )
}

export function listSeats(
    showId: number,
): Promise<Seat[]> {
    return request<Seat[]>(
        `/api/v1/shows/${showId}/seats`,
    )
}

interface CreateDemoBookingRequest {
    user_id: number
    show_id: number
    seat_id: number
}

export function createDemoBooking(
    payload: CreateDemoBookingRequest,
): Promise<Booking> {
    return request<Booking>(
        '/api/v1/demo/bookings',
        {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Idempotency-Key': crypto.randomUUID(),
            },
            body: JSON.stringify(payload),
        },
    )
}

export async function createDemoBookings(
    payloads: CreateDemoBookingRequest[],
): Promise<Booking[]> {
    const bookings: Booking[] = []

    for (const payload of payloads) {
        bookings.push(
            await createDemoBooking(payload),
        )
    }

    return bookings
}

export function getContentionStatus():
    Promise<ContentionStatus> {
    return request<ContentionStatus>(
        '/api/v1/demo/contention-status',
    )
}

export async function checkApiHealth():
    Promise<boolean> {
    try {
        const response = await fetch(
            `${API_BASE_URL}/health/ready`,
            {
                headers: {
                    Accept: 'application/json',
                },
            },
        )

        return response.ok
    } catch {
        return false
    }
}