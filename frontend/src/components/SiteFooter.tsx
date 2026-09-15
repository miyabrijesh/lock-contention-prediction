import { Link } from 'react-router-dom'

export function SiteFooter() {
    return (
        <footer className="site-footer">
            <div className="site-footer__inner">
                <div>
                    <div className="site-footer__brand">
                        FRAME 24
                    </div>
                    <p className="site-footer__description">
                        A controlled cinema-booking demonstration
                        for the lock-contention prediction system.
                    </p>
                </div>

                <nav
                    className="site-footer__links"
                    aria-label="Footer navigation"
                >
                    <Link to="/privacy">
                        Privacy
                    </Link>
                    <Link to="/terms">
                        Terms
                    </Link>
                    <Link to="/operations">
                        System status
                    </Link>
                </nav>

                <p className="site-footer__note">
                    Demonstration environment. No payment is
                    collected and no real tickets are issued.
                </p>
            </div>
        </footer>
    )
}