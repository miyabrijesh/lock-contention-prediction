import { Link, NavLink, useNavigate } from 'react-router-dom'

import {
    clearDemoUser,
    getDemoUser,
} from '../lib/demoSession'

function navClassName({
    isActive,
}: {
    isActive: boolean
}): string {
    return isActive
        ? 'site-nav__link site-nav__link--active'
        : 'site-nav__link'
}

export function SiteHeader() {
    const navigate = useNavigate()
    const user = getDemoUser()

    function handleSignOut(): void {
        clearDemoUser()
        navigate('/')
    }

    return (
        <header className="site-header">
            <div className="site-header__inner">
                <Link
                    className="wordmark"
                    to="/"
                    aria-label="Frame 24 home"
                >
                    <span className="wordmark__primary">
                        FRAME
                    </span>
                    <span className="wordmark__number">
                        24
                    </span>
                </Link>

                <nav
                    className="site-nav"
                    aria-label="Primary navigation"
                >
                    <NavLink
                        className={navClassName}
                        to="/"
                        end
                    >
                        Films
                    </NavLink>

                    <NavLink
                        className={navClassName}
                        to="/operations"
                    >
                        Live status
                    </NavLink>
                </nav>

                <div className="site-header__account">
                    {user ? (
                        <>
                            <span className="account-name">
                                {user.name}
                            </span>
                            <button
                                className="text-button"
                                type="button"
                                onClick={handleSignOut}
                            >
                                Sign out
                            </button>
                        </>
                    ) : (
                        <Link
                            className="header-action"
                            to="/login"
                        >
                            Sign in
                        </Link>
                    )}
                </div>
            </div>
        </header>
    )
}