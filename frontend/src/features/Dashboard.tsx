/**
 * Campaign dashboard — the "what needs my attention right now" screen.
 *
 * Genre: analytic overview. The primary task is triage, so the layout answers
 * three questions in order: is anything blocking? where is the money? what is
 * the state of the count? Everything else is one click away.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type { Finding, Kpis, Overview } from '../lib/types'
import {
  SEVERITY_LABELS,
  moneyShort,
  numShort,
  percent,
  signClass,
  signedMoney,
} from '../lib/format'
import { VarianceBars } from '../components/charts'
import {
  Alert,
  AsyncBoundary,
  Card,
  EmptyState,
  Icons,
  Skeleton,
} from '../components/ui'

export function Dashboard() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const hasBookStock = overview.campaign.book_stock_frozen_at !== null

  const kpis = useQuery({
    queryKey: ['kpis', campaignId],
    queryFn: () => api.kpis(campaignId),
    enabled: hasBookStock,
  })
  const controls = useQuery({
    queryKey: ['controls', campaignId],
    queryFn: () => api.controls(campaignId),
  })
  const byWarehouse = useQuery({
    queryKey: ['aggregate', campaignId, 'warehouse'],
    queryFn: () => api.aggregate(campaignId, 'warehouse', 12),
    enabled: hasBookStock,
  })

  return (
    <div className="stack" style={{ gap: 'var(--space-5)' }}>
      {!hasBookStock && (
        <Alert tone="info" title="Le stock livre n’est pas encore gelé">
          Les indicateurs d’écart apparaîtront dès que le snapshot ERP sera chargé et
          gelé, au début de la phase de comptage.{' '}
          <Link to="preparation">Aller aux référentiels</Link>
        </Alert>
      )}

      <ControlsBanner query={controls} />

      {hasBookStock && (
        <>
          <CoverageAlerts query={kpis} />

          <div className="grid grid--2">
            <AsyncBoundary
              query={byWarehouse}
              skeleton={<Card title="Écart par entrepôt"><Skeleton height={220} /></Card>}
              isEmpty={(rows) => rows.length === 0}
              empty={
                <Card title="Écart par entrepôt">
                  <EmptyState title="Aucun écart à afficher" />
                </Card>
              }
            >
              {(rows) => {
                const leader = rows[0]
                return (
                  <Card
                    title="Écart en valeur par entrepôt"
                    message={
                      leader
                        ? `${leader.key} concentre ${moneyShort(leader.absVarianceValue)} d’écart absolu, soit ${percent(
                            leader.absVarianceValue /
                              (rows.reduce((s, r) => s + r.absVarianceValue, 0) || 1),
                          )} du total.`
                        : undefined
                    }
                  >
                    <VarianceBars
                      data={rows.map((row) => ({ label: row.key, value: row.varianceValue }))}
                      format={moneyShort}
                      positiveIsGood
                    />
                  </Card>
                )
              }}
            </AsyncBoundary>

            <TopVariances campaignId={campaignId} />
          </div>
        </>
      )}
    </div>
  )
}

/**
 * The two facts that only show up as counts, and only matter when non-zero.
 *
 * The headline figures moved into the campaign header's carousel, where they
 * are visible from every screen. What is left here is what needs a sentence
 * rather than a number: stock the ERP never saw, and stock nobody counted.
 */
function CoverageAlerts({ query }: { query: { data: Kpis | undefined } }) {
  const data = query.data
  if (!data || (data.countedOnlyCount === 0 && data.bookOnlyCount === 0)) return null
  return (
    <div className="grid grid--2">
      {data.bookOnlyCount > 0 && (
        <Alert tone="danger" title={`${data.bookOnlyCount} couple(s) jamais comptés`}>
          Du stock livre existe sur ces couples article/emplacement sans aucun
          comptage. Ils seront soldés à zéro si l’inventaire est clôturé en l’état.{' '}
          <Link to="analyse">Voir la liste</Link>
        </Alert>
      )}
      {data.countedOnlyCount > 0 && (
        <Alert tone="warning" title={`${data.countedOnlyCount} couple(s) comptés hors ERP`}>
          Du stock a été compté là où l’ERP n’en voyait aucun. À vérifier avant
          ajustement.
        </Alert>
      )}
    </div>
  )
}

function ControlsBanner({
  query,
}: {
  query: {
    isPending: boolean
    isError: boolean
    error: unknown
    data: { summary: { bySeverity: Record<string, number>; hasBlocker: boolean }; findings: Finding[] } | undefined
    refetch?: () => void
  }
}) {
  if (query.isPending || query.isError || !query.data) return null
  const { summary, findings } = query.data
  const blockers = findings.filter((f) => f.severity === 'BLOCKER')
  const warnings = summary.bySeverity.WARNING ?? 0

  if (blockers.length === 0 && warnings === 0) {
    return (
      <Alert tone="success" title="Aucun point bloquant détecté">
        Les contrôles de cohérence sur les référentiels, le stock livre et les comptages
        ne remontent aucune anomalie.
      </Alert>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      {blockers.length > 0 && (
        <Alert
          tone="danger"
          title={`${blockers.length} point(s) bloquant(s) — action requise`}
          actions={
            <Link className="btn btn--secondary btn--sm" to="analyse">
              Voir les contrôles
            </Link>
          }
        >
          <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
            {blockers.slice(0, 4).map((finding, index) => (
              <li key={index}>{finding.message}</li>
            ))}
            {blockers.length > 4 && <li>… et {blockers.length - 4} autre(s)</li>}
          </ul>
        </Alert>
      )}
      {warnings > 0 && blockers.length === 0 && (
        <Alert tone="warning" title={`${warnings} avertissement(s)`}>
          Aucun blocage, mais des points méritent une vérification.{' '}
          <Link to="analyse">Consulter le détail</Link>
        </Alert>
      )}
    </div>
  )
}

function TopVariances({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['variances', campaignId, 'top'],
    queryFn: () => api.variances(campaignId, { limit: 12 }),
  })

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Card title="Principaux écarts"><Skeleton height={220} /></Card>}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <Card title="Principaux écarts">
          <EmptyState title="Aucun écart" icon={<Icons.check size={20} />}>
            Le stock compté correspond au stock livre sur l’ensemble du périmètre.
          </EmptyState>
        </Card>
      }
    >
      {(rows) => (
        <Card
          title="Principaux écarts en valeur"
          message="Triés par impact absolu — c’est la liste d’attaque du jour."
          flush
          footer={<Link to="analyse">Voir tous les écarts →</Link>}
        >
          <div className="table-wrap" style={{ maxHeight: 340 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Article</th>
                  <th className="num">Livre</th>
                  <th className="num">Compté</th>
                  <th className="num">Écart</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.itemNumber}-${row.warehouseId}-${row.locationId}`}>
                    <td>
                      <div className="mono">{row.itemNumber}</div>
                      <div className="subtle truncate" style={{ maxWidth: 220 }}>
                        {row.name}
                      </div>
                    </td>
                    <td className="num">{numShort(row.bookQty)}</td>
                    <td className="num">{numShort(row.countedQty)}</td>
                    <td className={`num ${signClass(row.varianceValue)}`}>
                      <strong>{signedMoney(row.varianceValue)}</strong>
                      <div className="subtle">{numShort(row.varianceQty)} {row.unit}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </AsyncBoundary>
  )
}

export { SEVERITY_LABELS }
