import type {
    BookingConfirmation,
    BookingSelection,
    DemoUser,
} from '../types'

const USER_KEY = 'cinema-demo-user'
const SELECTION_KEY = 'cinema-booking-selection'
const CONFIRMATION_KEY = 'cinema-booking-confirmation'

function readStoredValue<T>(key: string): T | null {
    const storedValue = window.localStorage.getItem(key)

    if (!storedValue) {
        return null
    }

    try {
        return JSON.parse(storedValue) as T
    } catch {
        window.localStorage.removeItem(key)
        return null
    }
}

function writeStoredValue<T>(
    key: string,
    value: T,
): void {
    window.localStorage.setItem(
        key,
        JSON.stringify(value),
    )
}

function syntheticUserIdForEmail(
    email: string,
): number {
    let hash = 0

    for (const character of email.toLowerCase()) {
        hash = (
            (hash * 31 + character.charCodeAt(0))
            >>> 0
        )
    }

    return (hash % 200) + 1
}

export function createDemoUser(
    name: string,
    email: string,
): DemoUser {
    const normalizedName = name.trim()
    const normalizedEmail = email
        .trim()
        .toLowerCase()

    if (normalizedName.length < 2) {
        throw new Error(
            'Enter a name with at least two characters.',
        )
    }

    if (
        !normalizedEmail.includes('@')
        || normalizedEmail.startsWith('@')
        || normalizedEmail.endsWith('@')
    ) {
        throw new Error(
            'Enter a valid email address.',
        )
    }

    const user: DemoUser = {
        name: normalizedName,
        email: normalizedEmail,
        syntheticUserId: syntheticUserIdForEmail(
            normalizedEmail,
        ),
    }

    writeStoredValue(USER_KEY, user)

    return user
}

export function getDemoUser(): DemoUser | null {
    return readStoredValue<DemoUser>(USER_KEY)
}

export function clearDemoUser(): void {
    window.localStorage.removeItem(USER_KEY)
    window.localStorage.removeItem(SELECTION_KEY)
    window.localStorage.removeItem(CONFIRMATION_KEY)
}

export function saveBookingSelection(
    selection: BookingSelection,
): void {
    writeStoredValue(SELECTION_KEY, selection)
}

export function getBookingSelection():
    BookingSelection | null {
    return readStoredValue<BookingSelection>(
        SELECTION_KEY,
    )
}

export function clearBookingSelection(): void {
    window.localStorage.removeItem(SELECTION_KEY)
}

export function saveBookingConfirmation(
    confirmation: BookingConfirmation,
): void {
    writeStoredValue(
        CONFIRMATION_KEY,
        confirmation,
    )
    clearBookingSelection()
}

export function getBookingConfirmation():
    BookingConfirmation | null {
    return readStoredValue<BookingConfirmation>(
        CONFIRMATION_KEY,
    )
}

export function clearBookingConfirmation(): void {
    window.localStorage.removeItem(CONFIRMATION_KEY)
}