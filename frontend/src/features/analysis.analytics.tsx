/** Ce que les écarts disent une fois regroupés : segments, familles, récurrences. */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { DASH, moneyShort, percent, signClass, signedMoney } from '../lib/format'
import { CompositionBar, DistributionChart, VarianceBars } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { AsyncBoundary, Card, EmptyState, Icons, Skeleton } from '../components/ui'
import { BUCKET_LABELS } from './analysis.causes'

// --------------------------------------------------------------------------- //
// Analytics
// --------------------------------------------------------------------------- //

/**
 * Les colonnes des deux listes issues du modèle.
 *
 * Déclarées ici plutôt qu'en ligne pour que les tableaux restent lisibles, et
 * surtout parce qu'elles ne dépendent de rien : ce sont les mêmes à chaque
 * rendu, et les recréer ferait retrier la grille à chaque frappe dans le champ
 * de recherche.
 */
const RECOUNT_COLUMNS: Column[] = [
  { key: 'item_number', label: 'Article', width: 180 },
  { key: 'warehouse_id', label: 'Entrepôt', width: 110 },
  { key: 'location_id', label: 'Emplacement', width: 140 },
  {
    key: 'variance_value',
    label: 'Écart',
    numeric: true,
    width: 130,
    render: (row) => (
      <span className={`num ${signClass(Number(row.variance_value ?? 0))}`}>
        {signedMoney(Number(row.variance_value ?? 0))}
      </span>
    ),
    value: (row) => Number(row.variance_value ?? 0),
  },
  {
    key: 'variance_ratio',
    label: 'Écart relatif',
    numeric: true,
    width: 130,
    render: (row) =>
      row.variance_ratio === null || row.variance_ratio === undefined
        ? DASH
        : percent(Number(row.variance_ratio)),
    value: (row) => Number(row.variance_ratio ?? 0),
  },
  {
    key: 'p_counting_error',
    label: 'P(erreur comptage)',
    numeric: true,
    width: 170,
    render: (row) => percent(Number(row.p_counting_error ?? 0)),
    value: (row) => Number(row.p_counting_error ?? 0),
  },
  {
    key: 'recount_expected_value',
    label: 'Valeur attendue',
    numeric: true,
    width: 160,
    render: (row) => <strong>{moneyShort(Number(row.recount_expected_value ?? 0))}</strong>,
    value: (row) => Number(row.recount_expected_value ?? 0),
  },
]

const ANOMALY_COLUMNS: Column[] = [
  { key: 'item_number', label: 'Article', width: 180 },
  { key: 'warehouse_id', label: 'Entrepôt', width: 110 },
  { key: 'location_id', label: 'Emplacement', width: 140 },
  {
    key: 'variance_value',
    label: 'Écart',
    numeric: true,
    width: 130,
    render: (row) => (
      <span className={`num ${signClass(Number(row.variance_value ?? 0))}`}>
        {signedMoney(Number(row.variance_value ?? 0))}
      </span>
    ),
    value: (row) => Number(row.variance_value ?? 0),
  },
  {
    key: 'anomaly_percentile',
    label: 'Atypicité',
    numeric: true,
    width: 130,
    render: (row) => percent(Number(row.anomaly_percentile ?? 0)),
    value: (row) => Number(row.anomaly_percentile ?? 0),
  },
]

export function AnalyticsTab({ campaignId }: { campaignId: string }) {
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

            {/* Un graphique se regarde ; une liste se travaille. Chaque bloc
                d'analyse ouvre donc la sienne — sans quoi on lit « le segment AZ
                pèse 2,4 M€ » sans jamais pouvoir dire quels articles y sont. */}
            {data.abcXyz && data.abcXyz.items.length > 0 && (
              <SegmentedItems
                campaignId={campaignId}
                summary={data.abcXyz.summary}
                items={data.abcXyz.items}
              />
            )}

            {data.clusters && data.clusters.items.length > 0 && (
              <ClusterItems
                campaignId={campaignId}
                profiles={data.clusters.profiles}
                items={data.clusters.items}
              />
            )}

            {data.recountPriority && data.recountPriority.length > 0 && (
              <Card
                title="Priorité de recomptage"
                message="Classement par écart × probabilité d’une erreur de comptage : le montant seul enverrait recompter des écarts structurels."
                flush
              >
                <DataGrid
                  columns={RECOUNT_COLUMNS}
                  rows={data.recountPriority as unknown as Array<Record<string, unknown>>}
                  exportTitle="Priorité de recomptage"
                  campaignId={campaignId}
                  getRowId={(_row, index) => String(index)}
                  searchPlaceholder="Filtrer par article, emplacement…"
                  maxHeight={460}
                  initialSort={{ key: 'recount_expected_value', direction: 'desc' }}
                />
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
                    <DataGrid
                      columns={ANOMALY_COLUMNS}
                      rows={data.anomalies.flagged}
                      exportTitle="Écarts atypiques"
                      campaignId={campaignId}
                      getRowId={(_row, index) => String(index)}
                      searchPlaceholder="Filtrer par article, emplacement…"
                      maxHeight={360}
                      initialSort={{ key: 'anomaly_percentile', direction: 'desc' }}
                    />
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

/**
 * Les articles d'un segment ABC/XYZ.
 *
 * Le graphique dit que le segment AZ pèse tant ; il ne dit jamais *lesquels*.
 * C'est pourtant la seule chose qu'on emporte : la liste des références à
 * mettre sous inventaire tournant. Les pilules portent le compte du segment,
 * et la grille donne recherche, tri et export comme partout ailleurs.
 */
function SegmentedItems({
  campaignId,
  summary,
  items,
}: {
  campaignId: string
  summary: Array<{ segment: string; items: number; abs_variance_value: number }>
  items: Array<Record<string, unknown>>
}) {
  // AZ d'abord : forte valeur, faible fiabilité — c'est le segment pour lequel
  // cette analyse existe. Il ne se trouve pas tout seul dans une liste triée
  // alphabétiquement.
  const preferred = summary.some((s) => s.segment === 'AZ') ? 'AZ' : (summary[0]?.segment ?? '')
  const [segment, setSegment] = useState(preferred)
  const rows = useMemo(
    () => items.filter((row) => String(row.segment) === segment),
    [items, segment],
  )

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 180 },
    { key: 'segment', label: 'Segment', width: 100 },
    { key: 'item_type', label: 'Type', width: 140 },
    { key: 'category', label: 'Catégorie', width: 140 },
    { key: 'program', label: 'Programme', width: 130 },
    {
      key: 'book_value',
      label: 'Stock ERP',
      numeric: true,
      width: 140,
      render: (row) => moneyShort(Number(row.book_value ?? 0)),
      value: (row) => Number(row.book_value ?? 0),
    },
    {
      key: 'abs_variance_value',
      label: 'Écart absolu',
      numeric: true,
      width: 150,
      render: (row) => moneyShort(Number(row.abs_variance_value ?? 0)),
      value: (row) => Number(row.abs_variance_value ?? 0),
    },
    {
      key: 'variance_ratio',
      label: 'Écart relatif',
      numeric: true,
      width: 140,
      render: (row) =>
        row.variance_ratio === null || row.variance_ratio === undefined
          ? DASH
          : percent(Number(row.variance_ratio)),
      value: (row) => Number(row.variance_ratio ?? 0),
    },
  ]

  return (
    <Card
      title="Articles par segment"
      message="AZ — forte valeur, faible fiabilité — est la liste à mettre sous inventaire tournant."
      flush
    >
      <div className="chips" style={{ padding: '0 var(--space-4)' }}>
        {summary.map((row) => (
          <button
            key={row.segment}
            className={`chip${segment === row.segment ? ' chip--active' : ''}`}
            title={`${moneyShort(row.abs_variance_value)} d’écart absolu`}
            onClick={() => setSegment(row.segment)}
          >
            {row.segment} <span className="num">{row.items}</span>
          </button>
        ))}
      </div>
      <DataGrid
        columns={columns}
        rows={rows}
        exportTitle={`Segment ${segment}`}
        campaignId={campaignId}
        getRowId={(row, index) => String(row.item_number ?? index)}
        searchPlaceholder="Filtrer par article, catégorie, programme…"
        maxHeight={460}
        emptyTitle="Aucun article dans ce segment"
        initialSort={{ key: 'abs_variance_value', direction: 'desc' }}
      />
    </Card>
  )
}

/** Les articles d'un profil de comportement, même raison, même forme. */
function ClusterItems({
  campaignId,
  profiles,
  items,
}: {
  campaignId: string
  profiles: Array<{ cluster: number; items: number; label: string }>
  items: Array<Record<string, unknown>>
}) {
  const [cluster, setCluster] = useState(profiles[0]?.cluster ?? 0)
  const rows = useMemo(
    () => items.filter((row) => Number(row.cluster) === cluster),
    [items, cluster],
  )
  const active = profiles.find((p) => p.cluster === cluster)

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 180 },
    { key: 'warehouse_id', label: 'Entrepôt', width: 120 },
    { key: 'location_id', label: 'Emplacement', width: 150 },
    { key: 'item_type', label: 'Type', width: 140 },
    {
      key: 'book_value',
      label: 'Stock ERP',
      numeric: true,
      width: 140,
      render: (row) => moneyShort(Number(row.book_value ?? 0)),
      value: (row) => Number(row.book_value ?? 0),
    },
    {
      key: 'variance_value',
      label: 'Écart',
      numeric: true,
      width: 140,
      render: (row) => (
        <span className={`num ${signClass(Number(row.variance_value ?? 0))}`}>
          {signedMoney(Number(row.variance_value ?? 0))}
        </span>
      ),
      value: (row) => Number(row.variance_value ?? 0),
    },
  ]

  return (
    <Card title="Articles par profil" message={active?.label} flush>
      <div className="chips" style={{ padding: '0 var(--space-4)' }}>
        {profiles.map((profile) => (
          <button
            key={profile.cluster}
            className={`chip${cluster === profile.cluster ? ' chip--active' : ''}`}
            title={profile.label}
            onClick={() => setCluster(profile.cluster)}
          >
            Profil #{profile.cluster} <span className="num">{profile.items}</span>
          </button>
        ))}
      </div>
      <DataGrid
        columns={columns}
        rows={rows}
        exportTitle={`Profil ${cluster}`}
        campaignId={campaignId}
        getRowId={(_row, index) => String(index)}
        searchPlaceholder="Filtrer par article, emplacement…"
        maxHeight={420}
        emptyTitle="Aucun article dans ce profil"
        initialSort={{ key: 'variance_value', direction: 'desc' }}
      />
    </Card>
  )
}
