/**
 * Variances, analytics, root causes and adjustments — the replacement for
 * `BILAN INVENTAIRE.xlsx`.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type {
  AssignableCause,
  GridContract,
  Overview,
  VarianceRow,
} from '../lib/types'
import {
  ITEM_TYPE_LABELS,
  moneyShort,
  numShort,
  percent,
  signClass,
  signedMoney,
} from '../lib/format'
import { CompositionBar, DistributionChart, Pareto, VarianceBars } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { ImportPanel } from '../components/ImportPanel'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Icons,
  Modal,
  Skeleton,
  Tabs,
  useErrorToast,
  useToast,
} from '../components/ui'

type Tab = 'variances' | 'analytics' | 'causes' | 'adjustments' | 'controls' | 'summary'

export function Analysis() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useState<Tab>('variances')

  if (!overview.campaign.book_stock_frozen_at) {
    return (
      <Card>
        <EmptyState title="Analyse indisponible" icon={<Icons.lock size={20} />}>
          Les écarts se calculent à partir du stock livre gelé. Chargez puis gelez le
          snapshot ERP dans l’onglet Référentiels.
        </EmptyState>
      </Card>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'variances', label: 'Écarts' },
          { id: 'analytics', label: 'Analyses & ML' },
          { id: 'causes', label: 'Causes' },
          { id: 'adjustments', label: 'Ajustements' },
          { id: 'controls', label: 'Contrôles' },
          { id: 'summary', label: 'Synthèse IA' },
        ]}
      />

      {tab === 'variances' && <VariancesTab campaignId={campaignId} overview={overview} />}
      {tab === 'analytics' && <AnalyticsTab campaignId={campaignId} />}
      {tab === 'causes' && <CausesTab campaignId={campaignId} overview={overview} />}
      {tab === 'adjustments' && <AdjustmentsTab campaignId={campaignId} overview={overview} />}
      {tab === 'controls' && <ControlsTab campaignId={campaignId} />}
      {tab === 'summary' && <SummaryTab campaignId={campaignId} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Variances
// --------------------------------------------------------------------------- //

const DIMENSIONS = [
  { id: 'item_type', label: 'Type d’article' },
  { id: 'category', label: 'Catégorie' },
  { id: 'program', label: 'Programme' },
  { id: 'warehouse', label: 'Entrepôt' },
  { id: 'location', label: 'Emplacement' },
]

function VariancesTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const [materialOnly, setMaterialOnly] = useState(false)
  const [granularity, setGranularity] = useState<'item' | 'item_location'>('item')
  const [dimension, setDimension] = useState('item_type')
  const [explain, setExplain] = useState<string | null>(null)

  const variances = useQuery({
    queryKey: ['variances', campaignId, materialOnly, granularity],
    queryFn: () => api.variances(campaignId, { limit: 1000, materialOnly, granularity }),
  })
  const aggregate = useQuery({
    queryKey: ['aggregate', campaignId, dimension],
    queryFn: () => api.aggregate(campaignId, dimension, 40),
  })
  const pareto = useQuery({
    queryKey: ['pareto', campaignId],
    queryFn: () => api.pareto(campaignId, 0.8),
  })
  // The chart needs the whole ranked population: computing a cumulative curve
  // over an already-filtered top-N would place the 80 % marker at a rank that
  // does not exist in reality.
  const ranked = useQuery({
    queryKey: ['aggregate', campaignId, 'item'],
    queryFn: () => api.aggregate(campaignId, 'item', 5000),
  })

  const maxAbs = useMemo(
    () => Math.max(...(variances.data ?? []).map((r) => Math.abs(r.varianceValue)), 1),
    [variances.data],
  )

  const columns: Column<VarianceRow>[] = [
    {
      key: 'itemNumber',
      label: 'Article',
      width: 200,
      render: (row) => (
        <div>
          <div className="mono">{row.itemNumber}</div>
          <div className="subtle truncate" style={{ maxWidth: 190 }}>
            {row.name}
          </div>
        </div>
      ),
      value: (row) => row.itemNumber,
    },
    ...(granularity === 'item_location'
      ? [
          { key: 'warehouseId', label: 'Entrepôt', width: 110 } as Column<VarianceRow>,
          { key: 'locationId', label: 'Emplacement', width: 140 } as Column<VarianceRow>,
        ]
      : []),
    {
      key: 'itemType',
      label: 'Type',
      width: 120,
      render: (row) => <Badge tone="neutral">{ITEM_TYPE_LABELS[row.itemType]}</Badge>,
      value: (row) => row.itemType,
    },
    {
      key: 'bookQty',
      label: 'Stock livre',
      numeric: true,
      width: 130,
      render: (row) => (
        <span className="num">
          {numShort(row.bookQty)}
          <div className="subtle">{moneyShort(row.bookValue)}</div>
        </span>
      ),
      value: (row) => row.bookQty,
    },
    {
      key: 'countedQty',
      label: 'Compté',
      numeric: true,
      width: 120,
      render: (row) => <span className="num">{numShort(row.countedQty)}</span>,
      value: (row) => row.countedQty,
    },
    {
      key: 'varianceValue',
      label: 'Écart',
      numeric: true,
      width: 170,
      render: (row) => (
        <div>
          <strong className={`num ${signClass(row.varianceValue)}`}>
            {signedMoney(row.varianceValue)}
          </strong>
          <div className="subtle num">
            {numShort(row.varianceQty)} {row.unit}
          </div>
          <CellBarInline value={row.varianceValue} max={maxAbs} />
        </div>
      ),
      value: (row) => row.varianceValue,
    },
    {
      key: 'residualValue',
      label: 'Résiduel',
      numeric: true,
      width: 140,
      render: (row) => (
        <span className={`num ${signClass(row.residualValue)}`}>
          {signedMoney(row.residualValue)}
        </span>
      ),
      value: (row) => row.residualValue,
    },
    {
      key: 'flags',
      label: 'Signalements',
      width: 200,
      sortable: false,
      render: (row) => (
        <span className="row-wrap" style={{ gap: 'var(--space-1)' }}>
          {row.isMaterial && <Badge tone="danger">au-delà des seuils</Badge>}
          {row.bookOnly && <Badge tone="warning">non compté</Badge>}
          {row.countedOnly && <Badge tone="info">hors ERP</Badge>}
          {row.causeCode && <Badge tone="success">cause {row.causeCode}</Badge>}
          {!row.causeCode && row.aiSuggestedCause && (
            <Badge tone="accent" title={row.aiRationale}>
              IA : {row.aiSuggestedCause}
            </Badge>
          )}
        </span>
      ),
    },
    {
      key: 'explain',
      label: '',
      width: 60,
      sortable: false,
      render: (row) => (
        <Button
          variant="ghost"
          size="sm"
          icon={<Icons.sparkles size={13} />}
          onClick={() => setExplain(row.itemNumber)}
          title="Expliquer cet écart"
          aria-label="Expliquer"
        />
      ),
    },
  ]

  return (
    <div className="stack">
      <div className="grid grid--2">
        <AsyncBoundary query={aggregate} skeleton={<Card title="Répartition"><Skeleton height={260} /></Card>}>
          {(rows) => (
            <Card
              title="Écart en valeur par dimension"
              message={
                rows[0]
                  ? `${rows[0].key} porte le plus gros écart absolu (${moneyShort(rows[0].absVarianceValue)}).`
                  : undefined
              }
              actions={
                <div className="chips">
                  {DIMENSIONS.map((option) => (
                    <button
                      key={option.id}
                      className={`chip${dimension === option.id ? ' chip--active' : ''}`}
                      onClick={() => setDimension(option.id)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              }
            >
              <VarianceBars
                data={rows.slice(0, 12).map((row) => ({ label: row.key, value: row.varianceValue }))}
                format={moneyShort}
              />
            </Card>
          )}
        </AsyncBoundary>

        <AsyncBoundary query={ranked} skeleton={<Card title="Pareto"><Skeleton height={260} /></Card>}>
          {(rows) => (
            <Card
              title="Concentration des écarts"
              message={
                pareto.data
                  ? `${pareto.data.length} article(s) sur ${rows.length} portent 80 % de l’écart absolu — c’est la liste sur laquelle concentrer l’analyse.`
                  : undefined
              }
            >
              <Pareto
                data={rows.map((row) => ({ label: row.key, value: row.absVarianceValue }))}
                format={moneyShort}
                coverage={0.8}
              />
            </Card>
          )}
        </AsyncBoundary>
      </div>

      <TransferCard campaignId={campaignId} onDrillDown={() => setGranularity('item_location')} />

      <Card
        title={
          granularity === 'item'
            ? 'Écarts par référence'
            : 'Écarts par référence et emplacement'
        }
        message={
          granularity === 'item'
            ? 'La lecture de référence : un transfert entre deux emplacements n’est pas une perte, donc les emplacements sont agrégés. C’est ce chiffre qui dit ce que le site a réellement perdu ou gagné.'
            : 'Vue opérationnelle : le détail par emplacement dit où aller recompter. Un article déplacé d’un bac à l’autre y apparaît deux fois — en moins ici, en plus là.'
        }
        actions={
          <div className="row-wrap">
            <div className="chips">
              <button
                className={`chip${granularity === 'item' ? ' chip--active' : ''}`}
                onClick={() => setGranularity('item')}
                title="La perte ou le gain réel du site"
              >
                Par référence
              </button>
              <button
                className={`chip${granularity === 'item_location' ? ' chip--active' : ''}`}
                onClick={() => setGranularity('item_location')}
                title="Où aller recompter"
              >
                Détail par emplacement
              </button>
            </div>
            <button
              className={`chip${materialOnly ? ' chip--active' : ''}`}
              onClick={() => setMaterialOnly((value) => !value)}
            >
              <Icons.filter size={12} />
              Au-delà des seuils uniquement
            </button>
          </div>
        }
        flush
      >
        <AsyncBoundary
          query={variances}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState title="Aucun écart" icon={<Icons.check size={20} />}>
              {materialOnly
                ? 'Aucun écart ne dépasse les seuils de matérialité configurés.'
                : 'Le stock compté correspond au stock livre.'}
            </EmptyState>
          }
        >
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row, index) => `${row.itemNumber}-${row.warehouseId}-${row.locationId}-${index}`}
              searchPlaceholder="Filtrer par article, désignation, programme…"
              maxHeight={640}
              initialSort={{ key: 'varianceValue', direction: 'desc' }}
              footer={
                <span className="subtle">
                  Périmètre : {overview.campaign.code} · stock livre gelé le{' '}
                  {new Date(overview.campaign.book_stock_frozen_at!).toLocaleDateString('fr-FR')}
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>

      {explain && (
        <ExplainModal campaignId={campaignId} itemNumber={explain} onClose={() => setExplain(null)} />
      )}
    </div>
  )
}

function CellBarInline({ value, max }: { value: number; max: number }) {
  const ratio = Math.min(Math.abs(value) / max, 1)
  return (
    <span className="cell-bar" aria-hidden="true">
      <span
        className="cell-bar__fill"
        style={{
          width: `${ratio * 100}%`,
          background: value >= 0 ? 'var(--variance-positive)' : 'var(--variance-negative)',
        }}
      />
    </span>
  )
}

function ExplainModal({
  campaignId,
  itemNumber,
  onClose,
}: {
  campaignId: string
  itemNumber: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['explain', campaignId, itemNumber],
    queryFn: () => api.explain(campaignId, itemNumber),
  })

  return (
    <Modal title={`Analyse de l’écart — ${itemNumber}`} onClose={onClose} width={820}>
      <AsyncBoundary query={query} skeleton={<Skeleton count={5} height={18} />}>
        {(data) => (
          <div className="stack">
            <Alert tone="info" title="Généré par IA — à vérifier">
              Cette explication est une proposition fondée sur les chiffres de la
              campagne. Elle n’affecte aucune donnée et ne remplace pas votre analyse.
            </Alert>
            <div style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--text-base)' }}>
              {data.explanation}
            </div>
            {data.wipBreakdown.length > 0 && (
              <Card title="Composition du WIP">
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Zone</th>
                        <th>Assemblage</th>
                        <th className="num">Quantité apportée</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.wipBreakdown.map((row, index) => (
                        <tr key={index}>
                          <td>{row.zone_code}</td>
                          <td className="mono">{row.parent_item}</td>
                          <td className="num">{numShort(row.child_qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
            {data.movements.length > 0 && (
              <Card title="Mouvements enregistrés">
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Nature</th>
                        <th className="num">Quantité</th>
                        <th className="num">Valeur</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.movements.map((row, index) => (
                        <tr key={index}>
                          <td>{String(row.date ?? '—')}</td>
                          <td>{String(row.kind)}</td>
                          <td className="num">{numShort(Number(row.qty))}</td>
                          <td className="num">{moneyShort(Number(row.value))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Analytics
// --------------------------------------------------------------------------- //

function AnalyticsTab({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['analytics', campaignId],
    queryFn: () => api.analytics(campaignId),
    staleTime: 5 * 60_000,
  })

  return (
    <AsyncBoundary
      query={query}
      skeleton={
        <div className="grid grid--2">
          <Card title="Analyses en cours de calcul"><Skeleton height={220} /></Card>
          <Card title="…"><Skeleton height={220} /></Card>
        </div>
      }
    >
      {(data) => {
        if (!data.available) {
          return (
            <Card>
              <EmptyState title="Analyses indisponibles">{data.reason}</EmptyState>
            </Card>
          )
        }
        return (
          <div className="stack" style={{ gap: 'var(--space-4)' }}>
            <div className="grid grid--2">
              {data.abcXyz && (
                <Card
                  title="Segmentation ABC / XYZ"
                  message="ABC sur la valeur du stock, XYZ sur la fiabilité du comptage. Le segment AZ — forte valeur, faible fiabilité — est celui à mettre sous inventaire tournant."
                >
                  <VarianceBars
                    data={data.abcXyz.summary.map((row) => ({
                      label: `${row.segment} (${row.items} art.)`,
                      value: row.abs_variance_value,
                    }))}
                    format={moneyShort}
                    positiveIsGood={false}
                    maxBars={9}
                  />
                </Card>
              )}

              {data.clusters && data.clusters.n > 0 && (
                <Card
                  title="Familles de comportements d’écart"
                  message={`${data.clusters.n} profils distincts identifiés (silhouette ${(data.clusters.silhouette ?? 0).toFixed(2)}). Les articles d’un même profil échouent de la même façon : une action corrective en couvre plusieurs.`}
                  flush
                >
                  <div className="table-wrap">
                    <table className="data">
                      <thead>
                        <tr>
                          <th>Profil</th>
                          <th className="num">Articles</th>
                          <th className="num">Écart absolu</th>
                          <th>Caractérisation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.clusters.profiles.map((profile) => (
                          <tr key={profile.cluster}>
                            <td className="num">#{profile.cluster}</td>
                            <td className="num">{profile.items}</td>
                            <td className="num">{moneyShort(profile.total_abs_variance)}</td>
                            <td>{profile.label}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </div>

            {data.recountPriority && data.recountPriority.length > 0 && (
              <Card
                title="Priorité de recomptage"
                message="Classement par valeur attendue = |écart €| × probabilité que ce soit une erreur de comptage. Trier par montant seul envoie les équipes recompter des écarts structurels qui ne bougeront pas."
                flush
              >
                <div className="table-wrap" style={{ maxHeight: 420 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Article</th>
                        <th className="num">Écart</th>
                        <th className="num">Écart relatif</th>
                        <th className="num">P(erreur comptage)</th>
                        <th className="num">Valeur attendue du recomptage</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.recountPriority.slice(0, 25).map((row, index) => (
                        <tr key={index}>
                          <td className="mono">
                            {row.item_number}
                            {row.location_id && (
                              <div className="subtle">
                                {row.warehouse_id} / {row.location_id}
                              </div>
                            )}
                          </td>
                          <td className={`num ${signClass(row.variance_value)}`}>
                            {signedMoney(row.variance_value)}
                          </td>
                          <td className="num">
                            {row.variance_ratio === null ? '—' : percent(row.variance_ratio)}
                          </td>
                          <td className="num">{percent(row.p_counting_error)}</td>
                          <td className="num">
                            <strong>{moneyShort(row.recount_expected_value)}</strong>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <div className="grid grid--2">
              {data.anomalies && (
                <Card
                  title="Écarts atypiques"
                  message={`${data.anomalies.flagged.length} écart(s) dont la *forme* est inhabituelle — pas seulement la taille. Détection par forêt d’isolement, graine fixée pour être reproductible.`}
                  flush
                >
                  {data.anomalies.flagged.length === 0 ? (
                    <EmptyState title="Aucun écart atypique" icon={<Icons.check size={20} />} />
                  ) : (
                    <div className="table-wrap" style={{ maxHeight: 320 }}>
                      <table className="data">
                        <thead>
                          <tr>
                            <th>Article</th>
                            <th className="num">Écart</th>
                            <th className="num">Atypicité</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.anomalies.flagged.slice(0, 20).map((row, index) => (
                            <tr key={index}>
                              <td className="mono">{String(row.item_number)}</td>
                              <td className={`num ${signClass(Number(row.variance_value))}`}>
                                {signedMoney(Number(row.variance_value))}
                              </td>
                              <td className="num">
                                {percent(Number(row.anomaly_percentile ?? 0))}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </Card>
              )}

              {data.dataQuality && (
                <Card
                  title="Qualité des comptages"
                  message={data.dataQuality.benford.conclusion}
                >
                  <div className="stack">
                    <DistributionChart
                      labels={data.dataQuality.benford.digits}
                      observed={data.dataQuality.benford.observed}
                      expected={data.dataQuality.benford.expected}
                      observedLabel="Premiers chiffres observés"
                      expectedLabel="Loi de Benford"
                    />
                    <hr className="divider" />
                    <div>
                      <strong style={{ fontSize: 'var(--text-sm)' }}>Biais d’arrondi</strong>
                      <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                        {data.dataQuality.digitPreference.conclusion}
                      </p>
                      <CompositionBar
                        format={(v) => percent(v)}
                        segments={Object.entries(data.dataQuality.digitPreference.buckets).map(
                          ([key, value]) => ({
                            label: BUCKET_LABELS[key] ?? key,
                            value,
                          }),
                        )}
                      />
                    </div>
                  </div>
                </Card>
              )}
            </div>
          </div>
        )
      }}
    </AsyncBoundary>
  )
}

const BUCKET_LABELS: Record<string, string> = {
  multiplesOf10: 'Multiples de 10',
  multiplesOf50: 'Multiples de 50',
  multiplesOf100: 'Multiples de 100',
  endingIn5: 'Terminant par 5',
}

// --------------------------------------------------------------------------- //
// Causes
// --------------------------------------------------------------------------- //

function CausesTab({ campaignId, overview }: { campaignId: string; overview: Overview }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const split = useQuery({
    queryKey: ['cause-split', campaignId],
    queryFn: () => api.causeSplit(campaignId),
  })
  const causes = useQuery({
    queryKey: ['causes', campaignId],
    queryFn: () => api.causes(campaignId),
  })
  const variances = useQuery({
    queryKey: ['variances', campaignId, 'causes'],
    queryFn: () => api.variances(campaignId, { limit: 200 }),
  })

  const suggest = useMutation({
    mutationFn: () => api.suggestCauses(campaignId, 40),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.suggestions} proposition(s) générée(s)`,
        'Les propositions IA sont stockées à côté de la décision humaine, jamais à sa place.',
      )
    },
    onError: (error) => showError(error, 'Génération impossible'),
  })

  const save = useMutation({
    mutationFn: ({ itemNumber, causeCode }: { itemNumber: string; causeCode: string | null }) =>
      api.saveVarianceAnalysis(campaignId, itemNumber, {
        causeCode,
        comment: '',
        accepted: causeCode !== null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Cause enregistrée')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const editable = overview.permissions.analysis

  return (
    <div className="stack">
      <AsyncBoundary query={split} skeleton={<Skeleton height={200} />}>
        {(data) => (
          <Card
            title="Répartition des écarts par cause"
            message={
              data.unassignedShare > 0
                ? `${percent(data.unassignedShare)} de l’écart absolu reste sans cause affectée — c’est ce qui alimente le plan d’action de la prochaine campagne.`
                : 'Tous les écarts significatifs ont une cause affectée.'
            }
            actions={
              editable && (
                <Button
                  icon={<Icons.sparkles size={14} />}
                  disabled={suggest.isPending}
                  onClick={() => suggest.mutate()}
                >
                  {suggest.isPending ? 'Analyse IA…' : 'Proposer des causes par IA'}
                </Button>
              )
            }
          >
            <CompositionBar
              segments={data.rows.map((row) => ({
                label: row.label,
                value: row.absValue,
                color: row.code === null ? 'var(--fg-subtle)' : undefined,
              }))}
              format={moneyShort}
            />
          </Card>
        )}
      </AsyncBoundary>

      <Card
        title="Affectation des causes"
        message="Une proposition IA n’est jamais écrite dans la colonne de décision : elle est affichée à côté, avec sa justification, et vous l’acceptez ou non."
        flush
      >
        <AsyncBoundary query={variances} isEmpty={(rows) => rows.length === 0}>
          {(rows) => (
            <div className="table-wrap" style={{ maxHeight: 620 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Article</th>
                    <th className="num">Écart</th>
                    <th style={{ width: 260 }}>Cause retenue</th>
                    <th>Proposition IA</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.itemNumber}>
                      <td>
                        <div className="mono">{row.itemNumber}</div>
                        <div className="subtle truncate" style={{ maxWidth: 200 }}>
                          {row.name}
                        </div>
                      </td>
                      <td className={`num ${signClass(row.varianceValue)}`}>
                        {signedMoney(row.varianceValue)}
                      </td>
                      <td>
                        <select
                          className="select"
                          value={row.causeCode ?? ''}
                          disabled={!editable || save.isPending}
                          onChange={(event) =>
                            save.mutate({
                              itemNumber: row.itemNumber,
                              causeCode: event.target.value || null,
                            })
                          }
                        >
                          <option value="">— non affectée —</option>
                          {(causes.data ?? []).map((cause: AssignableCause) => (
                            <option key={cause.code} value={cause.code}>
                              {cause.code} — {cause.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {row.aiSuggestedCause ? (
                          <div className="stack" style={{ gap: 'var(--space-1)' }}>
                            <span className="row" style={{ gap: 'var(--space-2)' }}>
                              <Badge tone="accent">
                                {row.aiSuggestedCause}
                                {row.aiConfidence !== null &&
                                  ` · ${Math.round(row.aiConfidence * 100)} %`}
                              </Badge>
                              {editable && row.causeCode !== row.aiSuggestedCause && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() =>
                                    save.mutate({
                                      itemNumber: row.itemNumber,
                                      causeCode: row.aiSuggestedCause,
                                    })
                                  }
                                >
                                  Accepter
                                </Button>
                              )}
                            </span>
                            {row.aiRationale && (
                              <span className="subtle">{row.aiRationale}</span>
                            )}
                          </div>
                        ) : (
                          <span className="subtle">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Adjustments
// --------------------------------------------------------------------------- //

function AdjustmentsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract: GridContract | undefined = contracts.data?.find((c) => c.key === 'adjustments')
  const query = useQuery({
    queryKey: ['adjustments', campaignId],
    queryFn: () => api.adjustments(campaignId),
  })

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'physical_date', label: 'Date', width: 120 },
    { key: 'kind', label: 'Nature', width: 130 },
    { key: 'journal_number', label: 'Journal', width: 140 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 120,
      render: (row) => (
        <span className={`num ${signClass(Number(row.qty))}`}>{numShort(Number(row.qty))}</span>
      ),
      value: (row) => Number(row.qty),
    },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => (
        <span className={`num ${signClass(Number(row.value))}`}>
          {signedMoney(Number(row.value))}
        </span>
      ),
      value: (row) => Number(row.value),
    },
    { key: 'warehouse_id', label: 'Entrepôt', width: 110 },
    { key: 'location_id', label: 'Emplacement', width: 140 },
    { key: 'comment', label: 'Commentaire', width: 220 },
  ]

  return (
    <div className="stack">
      {contract && (
        <ImportPanel
          campaignId={campaignId}
          contract={contract}
          target="adjustments"
          disabled={!overview.permissions.adjustments}
          disabledReason="Les ajustements sont modifiables pendant la phase d’analyse uniquement."
          onImported={() => void queryClient.invalidateQueries()}
        />
      )}

      <Card
        title="Mouvements et ajustements"
        message="Quantité et valeur signées : négatif = diminution de stock. Chaque mouvement réduit l’écart résiduel."
        flush
      >
        <AsyncBoundary
          query={query}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState title="Aucun ajustement">
              Chargez l’export des transactions de stock, ou saisissez les ajustements
              postés dans l’ERP après analyse.
            </EmptyState>
          }
        >
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row, index) => String(row.id ?? index)}
              searchPlaceholder="Filtrer les mouvements…"
              maxHeight={560}
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Controls & summary
// --------------------------------------------------------------------------- //

function ControlsTab({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['controls', campaignId],
    queryFn: () => api.controls(campaignId),
  })

  return (
    <AsyncBoundary query={query} skeleton={<Skeleton height={280} />}>
      {(data) => (
        <div className="stack">
          <div className="grid grid--kpi">
            {(['BLOCKER', 'WARNING', 'INFO'] as const).map((severity) => (
              <div key={severity} className="kpi">
                <div className="kpi__label">
                  {severity === 'BLOCKER'
                    ? 'Bloquants'
                    : severity === 'WARNING'
                      ? 'Avertissements'
                      : 'Informations'}
                </div>
                <div
                  className={`kpi__value num ${severity === 'BLOCKER' && data.summary.bySeverity[severity] ? 'neg' : ''}`}
                >
                  {data.summary.bySeverity[severity] ?? 0}
                </div>
              </div>
            ))}
          </div>

          <Card title="Constats de contrôle" flush>
            <DataGrid
              columns={[
                {
                  key: 'severity',
                  label: 'Sévérité',
                  width: 130,
                  render: (row) => (
                    <Badge
                      tone={
                        row.severity === 'BLOCKER'
                          ? 'danger'
                          : row.severity === 'WARNING'
                            ? 'warning'
                            : 'info'
                      }
                    >
                      {String(row.severity)}
                    </Badge>
                  ),
                  value: (row) => String(row.severity),
                },
                { key: 'code', label: 'Code', width: 220 },
                { key: 'item_number', label: 'Article', width: 160 },
                { key: 'message', label: 'Constat', width: 520 },
              ]}
              rows={data.findings as unknown as Array<Record<string, unknown>>}
              getRowId={(_, index) => String(index)}
              searchPlaceholder="Filtrer les constats…"
              maxHeight={600}
            />
          </Card>
        </div>
      )}
    </AsyncBoundary>
  )
}

function SummaryTab({ campaignId }: { campaignId: string }) {
  const [enabled, setEnabled] = useState(false)
  const query = useQuery({
    queryKey: ['ai-summary', campaignId],
    queryFn: () => api.aiSummary(campaignId),
    enabled,
    staleTime: 15 * 60_000,
  })

  return (
    <Card
      title="Synthèse de campagne"
      message="Rédigée à partir des chiffres calculés — jamais inventés. À relire et à valider avant diffusion."
      actions={
        <Button
          variant="primary"
          icon={<Icons.sparkles size={14} />}
          disabled={query.isFetching}
          onClick={() => {
            setEnabled(true)
            void query.refetch()
          }}
        >
          {query.isFetching ? 'Génération…' : 'Générer la synthèse'}
        </Button>
      }
    >
      {!enabled ? (
        <EmptyState title="Aucune synthèse générée" icon={<Icons.sparkles size={20} />}>
          La synthèse reprend les indicateurs, les principaux contributeurs, les
          contrôles et propose des actions priorisées avec leur enjeu en euros.
        </EmptyState>
      ) : (
        <AsyncBoundary query={query} skeleton={<Skeleton count={10} height={16} />}>
          {(data) => (
            <div className="stack">
              <Alert tone="warning" title="Contenu généré par IA">
                Vérifiez chaque chiffre avant diffusion : le texte est produit à partir
                des données de la campagne, mais reste une rédaction automatique.
              </Alert>
              <div style={{ whiteSpace: 'pre-wrap', lineHeight: 'var(--leading-normal)' }}>
                {data.markdown}
              </div>
            </div>
          )}
        </AsyncBoundary>
      )}
    </Card>
  )
}

/**
 * How much of the variance is a move between bins rather than a loss.
 *
 * The reason the screen opens on the per-reference reading. A pallet moved from
 * one location to another shows up twice in the per-location view — short here,
 * over there — and drags the IRA down without a single part having been lost.
 * That is a location-accuracy problem, worth fixing, but it is not the same
 * alarm as a shortfall, and conflating the two sends people chasing the wrong
 * thing.
 */
function TransferCard({
  campaignId,
  onDrillDown,
}: {
  campaignId: string
  onDrillDown: () => void
}) {
  const query = useQuery({
    queryKey: ['transfers', campaignId],
    queryFn: () => api.transfers(campaignId, 20),
  })

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={160} />}
      isEmpty={(data) => data.grossValue === 0}
      empty={null}
    >
      {(data) => (
        <Card
          title="Perte sèche ou simple transfert ?"
          message="L’écart vu par emplacement compte deux fois une palette déplacée. La différence avec l’écart par référence mesure exactement cette part-là."
          actions={
            data.itemCount > 0 ? (
              <Button size="sm" variant="ghost" onClick={onDrillDown}>
                Voir le détail par emplacement
              </Button>
            ) : null
          }
        >
          <div className="stack">
            <CompositionBar
              format={moneyShort}
              segments={[
                {
                  label: 'Écart net par référence',
                  value: data.netValue,
                  color: 'var(--cat-4)',
                },
                {
                  label: 'Transfert entre emplacements',
                  value: data.transferValue,
                  color: 'var(--cat-2)',
                },
              ]}
            />
            <p className="subtle">
              {percent(data.transferShare)} de l’écart brut par emplacement (
              {moneyShort(data.grossValue)}) se compense entre deux emplacements de la
              même référence, sur {data.itemCount.toLocaleString('fr-FR')} référence(s).
              Ce n’est pas une perte : c’est le stock qui n’est pas là où l’ERP le
              croit.
            </p>
            {data.rows.length > 0 && (
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Référence</th>
                      <th className="num">Écart par référence</th>
                      <th className="num">Écart par emplacement</th>
                      <th className="num">Dont transfert</th>
                      <th className="num">Emplacements</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row) => (
                      <tr key={row.itemNumber}>
                        <td>
                          <div className="mono">{row.itemNumber}</div>
                          <div className="subtle truncate" style={{ maxWidth: 240 }}>
                            {row.name}
                          </div>
                        </td>
                        <td className="num">{moneyShort(row.netValue)}</td>
                        <td className="num">{moneyShort(row.grossValue)}</td>
                        <td className="num">
                          <strong>{moneyShort(row.transferValue)}</strong>{' '}
                          <span className="subtle">
                            ({percent(row.transferShare)})
                          </span>
                        </td>
                        <td className="num">{row.locations}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      )}
    </AsyncBoundary>
  )
}
