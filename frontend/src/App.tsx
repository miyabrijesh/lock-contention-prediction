import { useEffect } from 'react'
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom'

import './App.css'
import { SiteFooter } from './components/SiteFooter'
import { SiteHeader } from './components/SiteHeader'
import { ConfirmationPage } from './pages/ConfirmationPage'
import { HomePage } from './pages/HomePage'
import { LoginPage } from './pages/LoginPage'
import { OperationsPage } from './pages/OperationsPage'
import { PrivacyPage } from './pages/PrivacyPage'
import { ShowPage } from './pages/ShowPage'
import { TermsPage } from './pages/TermsPage'

function ScrollToTop() {
  const location = useLocation()

  useEffect(() => {
    window.scrollTo({
      top: 0,
      behavior: 'instant',
    })
  }, [location.pathname])

  return null
}

function SiteLayout() {
  return (
    <div className="site-shell">
      <ScrollToTop />
      <SiteHeader />
      <div className="site-content">
        <Outlet />
      </div>
      <SiteFooter />
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<SiteLayout />}>
          <Route
            path="/"
            element={<HomePage />}
          />
          <Route
            path="/login"
            element={<LoginPage />}
          />
          <Route
            path="/shows/:showId"
            element={<ShowPage />}
          />
          <Route
            path="/confirmation"
            element={<ConfirmationPage />}
          />
          <Route
            path="/operations"
            element={<OperationsPage />}
          />
          <Route
            path="/privacy"
            element={<PrivacyPage />}
          />
          <Route
            path="/terms"
            element={<TermsPage />}
          />
          <Route
            path="*"
            element={
              <Navigate
                to="/"
                replace
              />
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App