/** Application shell: routing, sidebar, theme and the global data layer. */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './lib/api'
import { Alert, Badge, Button, Icons } from './components/ui'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CampaignsPage } from './features/Campaigns'
import { CampaignShell } from './features/CampaignShell'
import { Dashboard } from './features/Dashboard'
import { Preparation } from './features/Preparation'
import { Counting } from './features/Counting'
import { Generic } from './features/Generic'
import { Analysis } from './features/Analysis'
import { Audit } from './features/Audit'

type Theme = 'light' | 'dark' | 'system'

export function App() {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem('inv-theme') as Theme) ?? 'system',
  )
  const location = useLocation()

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    localStorage.setItem('inv-theme', theme)
  }, [theme])

  const health = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    refetchInterval: 120_000,
  })
  const me = useQuery({ queryKey: ['me'], queryFn: api.me, staleTime: Infinity })

  const inCampaign = location.pathname.startsWith('/campagnes/')

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar__brand">
          <span className="sidebar__mark">INV</span>
          <div>
            <div className="sidebar__title">Campagnes Inventaire</div>
            <div className="subtle">Site industriel</div>
          </div>
        </div>

        <nav className="sidebar__nav">
          <div className="sidebar__section">Pilotage</div>
          <NavLink
            to="/campagnes"
            className={({ isActive }) =>
              `navlink${isActive || location.pathname === '/' ? ' navlink--active' : ''}`
            }
          >
            <span className="navlink__icon">
              <Icons.scale size={17} />
            </span>
            Campagnes
          </NavLink>

          {inCampaign && (
            <>
              <div className="sidebar__section">Campagne courante</div>
              <span className="navlink navlink--active" style={{ cursor: 'default' }}>
                <span className="navlink__icon">
                  <Icons.chevronRight size={15} />
                </span>
                Ouverte
              </span>
            </>
          )}

          <div className="sidebar__section">Aide</div>
          <a
            className="navlink"
            href="/api/docs"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span className="navlink__icon">
              <Icons.info size={17} />
            </span>
            Documentation de l’API
          </a>
        </nav>

        <div className="sidebar__footer stack" style={{ gap: 'var(--space-2)' }}>
          {me.data && (
            <div className="truncate" title={me.data.actor}>
              {me.data.actor}
            </div>
          )}
          <div className="row" style={{ gap: 'var(--space-2)' }}>
            <Badge tone={health.data?.ready ? 'success' : 'warning'} dot>
              {health.data?.ready ? 'Connecté' : 'Dégradé'}
            </Badge>
            {health.data && <span className="subtle">v{health.data.version}</span>}
          </div>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="spacer" />
          <div className="row" style={{ gap: 'var(--space-1)' }}>
            <Button
              variant="ghost"
              icon={theme === 'dark' ? <Icons.sun size={16} /> : <Icons.moon size={16} />}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label="Basculer le thème"
              title={`Thème : ${theme}`}
            />
          </div>
        </header>

        <div className="content stack" style={{ gap: 'var(--space-4)' }}>
          {health.data && !health.data.ready && (
            <Alert tone="warning" title="Application en mode dégradé">
              {health.data.startupError ??
                'La base de données opérationnelle n’est pas joignable. Les écritures échoueront.'}
            </Alert>
          )}

          {/* Scoped to the routed screen so a crash never takes the shell —
              and therefore the navigation — down with it. */}
          <ErrorBoundary resetKey={location.pathname}>
            <Routes>
              <Route path="/" element={<Navigate to="/campagnes" replace />} />
              <Route path="/campagnes" element={<CampaignsPage />} />
              <Route path="/campagnes/:campaignId" element={<CampaignShell />}>
                <Route index element={<Dashboard />} />
                <Route path="preparation" element={<Preparation />} />
                <Route path="comptage" element={<Counting />} />
                <Route path="generique" element={<Generic />} />
                <Route path="analyse" element={<Analysis />} />
                <Route path="audit" element={<Audit />} />
              </Route>
              <Route
                path="*"
                element={
                  <Alert tone="warning" title="Page introuvable">
                    Cette adresse ne correspond à aucun écran.
                  </Alert>
                }
              />
            </Routes>
          </ErrorBoundary>
        </div>
      </main>
    </div>
  )
}
