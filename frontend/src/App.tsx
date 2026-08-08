/** Application shell: routing, sidebar, theme and the global data layer. */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { api } from './lib/api'
import { CAMPAIGN_STATUS_LABELS, label as toLabel } from './lib/format'
import { Alert, Badge, Button, Icons } from './components/ui'
import { CampaignNav } from './components/CampaignNav'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CampaignsPage } from './features/Campaigns'
import { CampaignShell } from './features/CampaignShell'
import { Dashboard } from './features/Dashboard'
import { Preparation } from './features/Preparation'
import { Counting } from './features/Counting'
import { Generic } from './features/Generic'
import { Analysis } from './features/Analysis'
import { Audit } from './features/Audit'
import { Assistant } from './features/Assistant'

type Theme = 'light' | 'dark' | 'system'

/** The campaign in the address bar, or `''` outside a campaign. */
function campaignIdOf(pathname: string): string {
  return pathname.match(/^\/campagnes\/([^/]+)/)?.[1] ?? ''
}

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
  const campaignId = campaignIdOf(location.pathname)

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
          <NavLink
            to="/campagnes"
            end
            className={({ isActive }) =>
              `navlink${isActive || location.pathname === '/' ? ' navlink--active' : ''}`
            }
          >
            <span className="navlink__icon">
              <Icons.scale size={17} />
            </span>
            Toutes les campagnes
          </NavLink>

          {campaignId && <CampaignSidebar campaignId={campaignId} />}
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
            <span className="spacer" />
            <Button
              variant="ghost"
              size="sm"
              icon={theme === 'dark' ? <Icons.sun size={15} /> : <Icons.moon size={15} />}
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label="Basculer le thème"
              title={`Thème : ${theme}`}
            />
          </div>
        </div>
      </aside>

      <main className="main">
        <div className="content stack" style={{ gap: 'var(--space-4)' }}>
          {health.data && !health.data.ready && (
            <Alert tone="warning" title="Mode dégradé">
              {health.data.startupError ??
                'Base de données injoignable : les écritures échoueront.'}
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
                <Route path="assistant" element={<Assistant />} />
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

/**
 * The open campaign's identity and navigation.
 *
 * It re-reads `/overview` under the key the campaign shell already uses, so the
 * two share one cache entry and one request — the sidebar costs nothing extra.
 */
function CampaignSidebar({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['overview', campaignId],
    queryFn: () => api.overview(campaignId),
    refetchInterval: 60_000,
  })
  if (!query.data) return null
  const { campaign } = query.data

  return (
    <>
      <div className="sidebar__campaign">
        <div className="sidebar__campaign-code truncate" title={campaign.label}>
          {campaign.code}
        </div>
        <Badge tone={campaign.status} dot>
          {toLabel(CAMPAIGN_STATUS_LABELS, campaign.status)}
        </Badge>
      </div>
      <CampaignNav overview={query.data} />
    </>
  )
}
