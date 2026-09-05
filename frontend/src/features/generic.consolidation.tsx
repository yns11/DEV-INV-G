/** Ce que le comptage donne au total, et ce qui l'empêche encore. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { ConsolidationLine, Finding, Overview } from '../lib/types'
import { moneyShort, qty } from '../lib/format'
import { CompositionBar } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { Alert, AsyncBoundary, Button, Card, EmptyState, Icons, Modal, Skeleton, useErrorToast, useToast } from '../components/ui'

/**
 * Ce que la consolidation a écarté, ou ajouté de sa propre initiative.
 *
 * Trois listes que le journal seul ne montre pas, et qui sont précisément
 * celles qu'on doit pouvoir relire :
 *
 *  - un produit fini compté en bord de ligne est une erreur de section, et sa
 *    quantité n'entre pas dans le stock — il faut aller chercher la feuille ;
 *  - compté en WIP assemblé il est noté à titre indicatif, ses composants étant
 *    déjà comptés par l'éclatement ;
 *  - un article que l'ERP porte et que personne n'a compté est soldé à zéro,
 *    et cette décision-là mérite d'être vue avant d'être postée.
 *
 * Chacune est une pilule plutôt qu'un tableau de plus, parce que les trois
 * partagent les mêmes colonnes et qu'empilées elles se noieraient.
 */
const EXCEPTION_PILLS: Array<{ code: string; label: string; hint: string }> = [
  {
    code: 'FINISHED_ON_LINE_SIDE',
    label: 'Produits finis en bord de ligne',
    hint: 'Erreur de section : la quantité n’est pas retenue. À corriger sur la feuille.',
  },
  {
    code: 'FINISHED_IN_WIP_OK',
    label: 'Produits finis en WIP assemblé',
    hint: 'Noté à titre indicatif : les composants sont déjà comptés par l’éclatement.',
  },
  {
    code: 'UNCOUNTED_WITH_BOOK_STOCK',
    label: 'Soldés à zéro',
    hint: 'Stock ERP en GENERIQUE que personne n’a compté : le journal le solde explicitement.',
  },
]

// --------------------------------------------------------------------------- //
// Consolidation
// --------------------------------------------------------------------------- //

export function ConsolidationTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [wipItem, setWipItem] = useState<string | null>(null)

  const preview = useQuery({
    queryKey: ['consolidation-preview', campaignId],
    queryFn: () => api.consolidationPreview(campaignId),
  })
  const current = useQuery({
    queryKey: ['consolidation', campaignId],
    queryFn: () => api.consolidation(campaignId),
  })
  const orphans = useQuery({
    queryKey: ['wip-without-bom', campaignId],
    queryFn: () => api.wipWithoutBom(campaignId),
  })

  const run = useMutation({
    mutationFn: () => api.runConsolidation(campaignId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `Consolidation effectuée : ${result.lines} article(s)`,
        `${result.zonesIncluded.length} zone(s) incluses. Le journal GENERIQUE est prêt à exporter.`,
      )
    },
    onError: (error) => showError(error, 'Consolidation impossible'),
  })

  const reclassify = useMutation({
    mutationFn: (lineIds: string[]) => api.reclassifyWip(campaignId, lineIds, 'WIP_OK'),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.updated} ligne(s) reclassée(s) en « WIP assemblé »`,
        'Ces assemblages seront comptés tels quels au lieu d’être éclatés.',
      )
    },
    onError: (error) => showError(error, 'Reclassement impossible'),
  })

  const blocking = preview.data?.findings.filter((f) => f.severity === 'BLOCKER') ?? []
  const orphanRows = orphans.data ?? []

  return (
    <div className="stack">
      {orphanRows.length > 0 && (
        <Alert
          tone="danger"
          title={`${orphanRows.length} ligne(s) WIP sans nomenclature`}
          actions={
            overview.permissions.countSheets && (
              <Button
                size="sm"
                variant="primary"
                disabled={reclassify.isPending}
                onClick={() => reclassify.mutate(orphanRows.map((row) => row.lineId))}
              >
                Compter ces assemblages tels quels
              </Button>
            )
          }
        >
          Ces assemblages sont comptés en WIP mais n’ont aucune nomenclature : les
          éclater ferait disparaître la quantité comptée. Comme les nomenclatures sont
          gelées pendant le comptage, la résolution est de les reclasser en{' '}
          <strong>WIP assemblé</strong>.
          <div className="table-wrap" style={{ maxHeight: 180, marginTop: 'var(--space-2)' }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Article</th>
                  <th className="num">Quantité</th>
                </tr>
              </thead>
              <tbody>
                {orphanRows.slice(0, 15).map((row) => (
                  <tr key={row.lineId}>
                    <td>{row.zoneCode}</td>
                    <td className="mono">{row.itemNumber}</td>
                    <td className="num">
                      {qty(row.qty)} {row.unit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Alert>
      )}

      <Card
        title="Consolidation GENERIQUE"
        message={
          preview.data
            ? `${preview.data.zonesIncluded.length} zone(s) prête(s), ${preview.data.zonesSkipped.length} en attente. ${blocking.length} point(s) bloquant(s).`
            : undefined
        }
        actions={
          <Button
            variant="primary"
            icon={<Icons.refresh size={14} />}
            disabled={!overview.permissions.countSheets || run.isPending || blocking.length > 0}
            onClick={() => run.mutate()}
          >
            {run.isPending ? 'Consolidation…' : 'Consolider et alimenter le journal'}
          </Button>
        }
      >
        <AsyncBoundary query={preview} skeleton={<Skeleton height={160} />}>
          {(data) => (
            <div className="stack">
              {data.zonesSkipped.length > 0 && (
                <Alert tone="warning" title={`${data.zonesSkipped.length} zone(s) non terminée(s)`}>
                  {data.zonesSkipped.join(', ')} — sans contribution tant que leurs
                  comptages ne sont pas validés.
                </Alert>
              )}
              {blocking.length > 0 && (
                <Alert tone="danger" title={`${blocking.length} point(s) bloquant(s)`}>
                  <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                    {blocking.slice(0, 8).map((finding, index) => (
                      <li key={index}>{finding.message}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              <dl className="kv">
                <dt>Articles au journal (aperçu)</dt>
                <dd className="num">{data.lines.length.toLocaleString('fr-FR')}</dd>
                <dt>Quantité totale</dt>
                <dd className="num">{qty(data.totalQty)}</dd>
                <dt>Zones incluses</dt>
                <dd>{data.zonesIncluded.join(', ') || '—'}</dd>
              </dl>
            </div>
          )}
        </AsyncBoundary>
      </Card>

      <ConsolidationExceptions findings={preview.data?.findings ?? []} />

      <AsyncBoundary
        query={current}
        skeleton={<Skeleton height={280} />}
        isEmpty={(data) => data.lines.length === 0}
        empty={
          <Card title="Journal consolidé">
            <EmptyState title="Aucune consolidation enregistrée">
              Lancez la consolidation lorsque toutes les zones sont terminées. Le
              résultat alimente le journal INVV de {overview.campaign.config.generic_warehouse}{' '}
              / {overview.campaign.config.generic_location}.
            </EmptyState>
          </Card>
        }
      >
        {(data) => (
          <ConsolidationResult
            campaignId={campaignId}
            lines={data.lines}
            onExploreWip={setWipItem}
          />
        )}
      </AsyncBoundary>

      {wipItem && (
        <WipModal campaignId={campaignId} itemNumber={wipItem} onClose={() => setWipItem(null)} />
      )}
    </div>
  )
}

function ConsolidationExceptions({ findings }: { findings: Finding[] }) {
  const [code, setCode] = useState(EXCEPTION_PILLS[0]!.code)
  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const finding of findings) {
      tally[finding.code] = (tally[finding.code] ?? 0) + 1
    }
    return tally
  }, [findings])

  const total = EXCEPTION_PILLS.reduce((sum, p) => sum + (counts[p.code] ?? 0), 0)
  if (total === 0) return null

  const rows = findings.filter((f) => f.code === code)
  const active = EXCEPTION_PILLS.find((p) => p.code === code)

  return (
    <Card
      title="Signalements de la consolidation"
      message={active?.hint}
      flush
    >
      <div className="chips" style={{ padding: '0 var(--space-4)' }}>
        {EXCEPTION_PILLS.map((pill) => (
          <button
            key={pill.code}
            className={`chip${code === pill.code ? ' chip--active' : ''}`}
            title={pill.hint}
            onClick={() => setCode(pill.code)}
          >
            {pill.label} <span className="num">{counts[pill.code] ?? 0}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState title="Rien à signaler dans cette catégorie" />
      ) : (
        <div className="table-wrap" style={{ maxHeight: 320 }}>
          <table className="data">
            <thead>
              <tr>
                <th style={{ width: 170 }}>Article</th>
                <th style={{ width: 170 }}>Zone</th>
                <th style={{ width: 190 }}>Feuille</th>
                <th className="num" style={{ width: 130 }}>Quantité</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 300).map((finding, index) => (
                <tr key={`${finding.item_number}-${index}`}>
                  <td className="mono">{finding.item_number || '—'}</td>
                  <td>{String(finding.context?.zone ?? '—')}</td>
                  <td>{String(finding.context?.sheets || '—')}</td>
                  <td className="num">
                    {qty(Number(finding.context?.qty ?? finding.context?.bookQty ?? 0))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function ConsolidationResult({
  campaignId,
  lines,
  onExploreWip,
}: {
  campaignId: string
  lines: ConsolidationLine[]
  onExploreWip: (itemNumber: string) => void
}) {
  // Les trois colonnes d'origine s'ouvrent comme le WIP le faisait déjà : une
  // quantité qu'on ne peut pas expliquer est une quantité qu'on ne peut pas
  // défendre, et c'est en réunion que la question se pose.
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect } | null
  >(null)
  const totals = useMemo(() => {
    return lines.reduce(
      (acc, line) => ({
        lineSide: acc.lineSide + line.qty_line_side,
        wipOk: acc.wipOk + line.qty_wip_ok,
        wipExploded: acc.wipExploded + line.qty_wip_exploded,
      }),
      { lineSide: 0, wipOk: 0, wipExploded: 0 },
    )
  }, [lines])

  const columns: Column<ConsolidationLine>[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 260 },
    {
      key: 'qty',
      label: 'Quantité totale',
      numeric: true,
      width: 150,
      render: (row) => (
        <DrillCell
          disabled={row.qty === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'generic' })}
        >
          <strong className="num">{qty(row.qty)}</strong>
        </DrillCell>
      ),
      value: (row) => row.qty,
    },
    {
      key: 'qty_line_side',
      label: 'Bord de ligne',
      numeric: true,
      width: 140,
      render: (row) => (
        <DrillCell
          disabled={row.qty_line_side === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'line_side' })}
        >
          <span className="num">{qty(row.qty_line_side)}</span>
        </DrillCell>
      ),
      value: (row) => row.qty_line_side,
    },
    {
      key: 'qty_wip_ok',
      label: 'WIP assemblé',
      numeric: true,
      width: 140,
      render: (row) => (
        <DrillCell
          disabled={row.qty_wip_ok === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'wip_ok' })}
        >
          <span className="num">{qty(row.qty_wip_ok)}</span>
        </DrillCell>
      ),
      value: (row) => row.qty_wip_ok,
    },
    {
      key: 'qty_wip_exploded',
      label: 'WIP éclaté',
      numeric: true,
      width: 150,
      render: (row) =>
        row.hasWip ? (
          <button
            className="btn btn--ghost btn--sm num"
            onClick={() => onExploreWip(row.item_number)}
            title="Voir de quoi se compose ce WIP"
          >
            {qty(row.qty_wip_exploded)}
            <Icons.chevronRight size={12} />
          </button>
        ) : (
          <span className="subtle">—</span>
        ),
      value: (row) => row.qty_wip_exploded,
    },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{moneyShort(row.value)}</span>,
      value: (row) => row.value,
    },
    {
      key: 'zone_codes',
      label: 'Zones',
      width: 200,
      render: (row) => <span className="subtle truncate">{row.zone_codes.join(', ')}</span>,
      value: (row) => row.zone_codes.join(','),
    },
  ]

  return (
    <div className="stack">
      <Card
        title="Origine des quantités consolidées"
        message="Le WIP éclaté n’est plus une valeur agrégée opaque : chaque quantité est traçable jusqu’à l’assemblage qui l’a produite."
      >
        <CompositionBar
          format={qty}
          segments={[
            { label: 'Bord de ligne', value: totals.lineSide, color: 'var(--cat-1)' },
            { label: 'WIP assemblé', value: totals.wipOk, color: 'var(--cat-2)' },
            { label: 'WIP éclaté en composants', value: totals.wipExploded, color: 'var(--cat-4)' },
          ]}
        />
      </Card>

      <Card title="Journal consolidé" flush>
        <DataGrid
          columns={columns}
          rows={lines}
          exportTitle="Consolidation"
          campaignId={campaignId}
          getRowId={(row) => row.item_number}
          searchPlaceholder="Filtrer par article…"
          maxHeight={560}
          initialSort={{ key: 'value', direction: 'desc' }}
        />
      </Card>

      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}

function WipModal({
  campaignId,
  itemNumber,
  onClose,
}: {
  campaignId: string
  itemNumber: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['wip', campaignId, itemNumber],
    queryFn: () => api.wipBreakdown(campaignId, itemNumber),
  })

  return (
    <Modal title={`Composition du WIP — ${itemNumber}`} onClose={onClose} width={780}>
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={180} />}
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState title="Aucune décomposition disponible" />}
      >
        {(rows) => (
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Assemblage compté</th>
                  <th className="num">Qté assemblage</th>
                  <th className="num">Qté / assemblage</th>
                  <th className="num">Quantité apportée</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index}>
                    <td>{row.zone_code || '—'}</td>
                    <td className="mono">{row.parent_item}</td>
                    <td className="num">{qty(row.parent_qty)}</td>
                    <td className="num">{qty(row.qty_per_parent)}</td>
                    <td className="num">
                      <strong>{qty(row.child_qty)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}
