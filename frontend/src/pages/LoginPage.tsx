import {
    type FormEvent,
    useState,
} from 'react'
import {
    Link,
    useLocation,
    useNavigate,
} from 'react-router-dom'

import {
    createDemoUser,
    getDemoUser,
} from '../lib/demoSession'

interface LoginLocationState {
    from?: string
}

export function LoginPage() {
    const existingUser = getDemoUser()
    const navigate = useNavigate()
    const location = useLocation()

    const [name, setName] = useState(
        existingUser?.name ?? '',
    )
    const [email, setEmail] = useState(
        existingUser?.email ?? '',
    )
    const [errorMessage, setErrorMessage] = (
        useState<string | null>(null)
    )

    function handleSubmit(
        event: FormEvent<HTMLFormElement>,
    ): void {
        event.preventDefault()

        try {
            createDemoUser(name, email)

            const state = (
                location.state as LoginLocationState | null
            )

            navigate(state?.from ?? '/')
        } catch (error) {
            setErrorMessage(
                error instanceof Error
                    ? error.message
                    : 'Your details could not be saved.',
            )
        }
    }

    return (
        <main className="auth-page">
            <section className="auth-panel">
                <p className="eyebrow">
                    Demo access
                </p>

                <h1>
                    Save your seats under a name
                </h1>

                <p className="auth-panel__introduction">
                    Enter basic details to continue with the
                    booking demonstration. No password or payment
                    information is collected.
                </p>

                <form
                    className="auth-form"
                    onSubmit={handleSubmit}
                    noValidate
                >
                    <label>
                        <span>Name</span>
                        <input
                            type="text"
                            value={name}
                            onChange={(event) => {
                                setName(event.target.value)
                                setErrorMessage(null)
                            }}
                            autoComplete="name"
                            placeholder="Your name"
                            required
                        />
                    </label>

                    <label>
                        <span>Email address</span>
                        <input
                            type="email"
                            value={email}
                            onChange={(event) => {
                                setEmail(event.target.value)
                                setErrorMessage(null)
                            }}
                            autoComplete="email"
                            placeholder="you@example.com"
                            required
                        />
                    </label>

                    {errorMessage ? (
                        <p
                            className="form-message form-message--error"
                            role="alert"
                        >
                            {errorMessage}
                        </p>
                    ) : null}

                    <button
                        className="primary-button"
                        type="submit"
                    >
                        Continue to films
                    </button>
                </form>

                <p className="auth-panel__notice">
                    This identity is stored only in this browser
                    and is used to select a seeded demonstration
                    user.
                </p>

                <Link
                    className="inline-link"
                    to="/"
                >
                    Return to films
                </Link>
            </section>
        </main>
    )
}