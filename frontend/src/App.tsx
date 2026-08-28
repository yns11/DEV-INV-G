/** Application shell: routing, sidebar, theme and the global data layer. */

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
  useOutletContext,
} from 'react-router-dom'
import { api } from './lib/api'
import type { CampaignStatus, Overview } from './lib/types'
import { CAMPAIGN_STATUS_LABELS, date as fmtDate, label as toLabel } from './lib/format'
import { Alert, Badge, Button, Icons } from './components/ui'
import { Logo } from './components/Logo'
import { CampaignNav } from './components/CampaignNav'
import { ErrorBoundary } from './components/ErrorBoundary'
import { CampaignsPage } from './features/Campaigns'
import { CampaignShell } from './features/CampaignShell'
import { Preparation } from './features/Preparation'
import { Counting } from './features/Counting'
import EarlyCounts from './features/EarlyCounts'
import { Generic } from './features/Generic'
import { Analysis } from './features/Analysis'
import { Backflush } from './features/Backflush'
import { Reconciliation } from './features/Reconciliation'
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
          <div className="stack" style={{ gap: 'var(--space-1)' }}>
            <Logo height={38} />
            <div className="sidebar__title">Campagnes Inventaire</div>
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
                {/* One route per navigation entry, in the order the work is
                    done. The two screens that serve several entries take the
                    view as a prop rather than reading the path themselves —
                    the tree is declared once, in `lib/navigation`. */}
                <Route index element={<CampaignHome />} />
                <Route path="assistant" element={<Assistant />} />
                <Route path="audit" element={<Audit />} />

                <Route path="articles" element={<Preparation view="items" />} />
                <Route path="nomenclatures" element={<Preparation view="boms" />} />
                <Route path="feuilles" element={<Preparation view="count_sheets" />} />
                <Route path="gestion" element={<Preparation view="gestion" />} />

                <Route path="stock-erp" element={<Preparation view="book_stock" />} />
                <Route path="backflush" element={<Backflush />} />
                <Route path="compil" element={<Generic />} />
                <Route path="comptage" element={<Counting />} />
                <Route path="comptages-avances" element={<EarlyCounts />} />

                <Route path="controles" element={<Analysis view="controls" />} />
                <Route path="ecarts" element={<Analysis view="variances" />} />
                <Route path="causes" element={<Analysis view="causes" />} />
                <Route path="reconciliation" element={<Reconciliation />} />
                <Route path="ajustements" element={<Analysis view="adjustments" />} />

                {/* Anciennes adresses : un lien en favori doit continuer de
                    tomber sur l'écran correspondant. */}
                <Route path="preparation" element={<Navigate to="../articles" replace />} />
                <Route path="generique" element={<Navigate to="../compil" replace />} />
                <Route path="analyse" element={<Navigate to="../ecarts" replace />} />
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
      {/* L'identité de la campagne — son code, son libellé, sa date de
          comptage — vivait sur le tableau de bord, qui n'existe plus. Elle est
          ici : c'est le bloc qui répond à « dans quelle campagne suis-je ? »,
          et il est visible depuis tous les écrans. */}
      <div className="sidebar__campaign">
        <div className="sidebar__campaign-code truncate" title={campaign.label}>
          {campaign.code}
        </div>
        <Badge tone={campaign.status} dot>
          {toLabel(CAMPAIGN_STATUS_LABELS, campaign.status)}
        </Badge>
      </div>
      <div className="subtle" style={{ padding: '0 var(--space-3) var(--space-2)' }}>
        <div className="truncate" title={campaign.label}>{campaign.label}</div>
        <div>Comptage du {fmtDate(campaign.count_date)}</div>
        {campaign.book_stock_frozen_at && (
          <div>Stock ERP gelé le {fmtDate(campaign.book_stock_frozen_at)}</div>
        )}
      </div>
      <CampaignNav overview={query.data} />
    </>
  )
}

/**
 * L'écran d'accueil d'une campagne : celui de l'étape en cours.
 *
 * Il n'y a plus de tableau de bord — il redisait ce que le carrousel de
 * l'en-tête montre déjà sur *tous* les écrans, et il obligeait à un clic de
 * plus avant d'atteindre le travail. Ouvrir une campagne mène donc là où il y a
 * quelque chose à faire, ce qui dépend de sa phase.
 */
const HOME_OF: Record<CampaignStatus, string> = {
  PREPARATION: 'articles',
  // toujours atteignable : `compil` attend que le stock ERP soit chargé,
  // et envoyer quelqu'un sur un écran verrouillé n'est pas l'accueillir.
  COUNTING: 'stock-erp',
  ANALYSIS: 'ecarts',
  CLOSED: 'ecarts',
}

function CampaignHome() {
  const overview = useOutletContext<Overview>()
  return <Navigate to={HOME_OF[overview.campaign.status]} replace />
}
