/** Les écarts, référence par référence, et ce qui les explique. */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, download, downloads } from '../lib/api'
import type { Overview, VarianceRow } from '../lib/types'
import { DASH, ITEM_TYPE_LABELS, moneyShort, qty, percent, signClass, signedMoney, signedNum } from '../lib/format'
import { CompositionBar, Pareto, VarianceBars } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Icons, Modal, Skeleton, useErrorToast } from '../components/ui'

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

export function VariancesTab({
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
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect; warehouseId?: string; locationId?: string } | null
  >(null)
  const [exporting, setExporting] = useState<'xlsx' | 'pdf' | null>(null)
  const showError = useErrorToast()

  // En vue par emplacement, la ligne cliquée en désigne un : décomposer l'article
  // entier répondrait à une autre question que celle posée.
  const openDrill = (row: VarianceRow, aspect: BreakdownAspect) =>
    setDrill({
      itemNumber: row.itemNumber,
      aspect,
      ...(granularity === 'item_location'
        ? { warehouseId: row.warehouseId, locationId: row.locationId }
        : {}),
    })

  const exportAs = async (format: 'xlsx' | 'pdf') => {
    setExporting(format)
    try {
      await download(
        downloads.variances(campaignId, format, {
          granularity,
          materialOnly: materialOnly || undefined,
        }),
      )
    } catch (error) {
      showError(error, 'Export impossible')
    } finally {
      setExporting(null)
    }
  }

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
    // Every cell of this row that carries both figures uses the same
    // arrangement: quantity on the first line, amount on the second. Mixing the
    // two orders — a quantity heading one column and an amount heading the next
    // — puts different units at the same height, and the eye reading across a
    // row cannot tell which is which without checking the header every time.
    {
      key: 'bookQty',
      label: 'Stock ERP',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.bookQty === 0}
          onOpen={() => openDrill(row, 'book')}
        >
          <QtyOverValue qty={qty(row.bookQty)} value={moneyShort(row.bookValue)} />
        </DrillCell>
      ),
      value: (row) => row.bookQty,
    },
    {
      key: 'countedQty',
      label: 'Compté',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.countedQty === 0}
          onOpen={() => openDrill(row, 'counted')}
        >
          <QtyOverValue
            qty={qty(row.countedQty)}
            value={moneyShort(row.countedQty * row.unitCost)}
          />
        </DrillCell>
      ),
      value: (row) => row.countedQty,
    },
    {
      key: 'physicalQty',
      label: 'Physique',
      numeric: true,
      width: 130,
      render: (row) => (
        <DrillCell
          disabled={row.physicalQty === 0}
          onOpen={() => openDrill(row, 'physical')}
        >
          <QtyOverValue
            qty={qty(row.physicalQty)}
            value={moneyShort(row.physicalValue)}
          />
        </DrillCell>
      ),
      value: (row) => row.physicalQty,
    },
    {
      key: 'varianceValue',
      label: 'Écart',
      numeric: true,
      width: 170,
      render: (row) => (
        <div>
          <DrillCell
            disabled={row.varianceQty === 0}
            onOpen={() => openDrill(row, 'variance')}
          >
            <QtyOverValue
              qty={signedNum(row.varianceQty)}
              value={signedMoney(row.varianceValue)}
              tone={signClass(row.varianceValue)}
            />
          </DrillCell>
          <CellBarInline value={row.varianceValue} max={maxAbs} />
        </div>
      ),
      value: (row) => row.varianceValue,
    },
    // Ce que le comptage seul montrait. Sans ajustement il répète l'écart à
    // l'identique : une colonne de doublons est une colonne qu'on apprend à
    // sauter, donc une colonne à ne pas afficher.
    ...(variances.data?.some((row) => row.adjustedQty !== 0)
      ? [
          {
            key: 'countedVarianceValue',
            label: 'Avant ajust.',
            numeric: true,
            width: 140,
            render: (row: VarianceRow) => (
              <DrillCell
                disabled={row.countedVarianceQty === 0}
                onOpen={() => openDrill(row, 'counted')}
              >
                <QtyOverValue
                  qty={signedNum(row.countedVarianceQty)}
                  value={signedMoney(row.countedVarianceValue)}
                  tone={signClass(row.countedVarianceValue)}
                />
              </DrillCell>
            ),
            value: (row: VarianceRow) => row.countedVarianceValue,
          } as Column<VarianceRow>,
        ]
      : []),
    // Le backflush n'apparaît que là où il a été mesuré. Une colonne pleine de
    // tirets sur une campagne qui ne l'a pas chargé serait une colonne à
    // ignorer, c'est-à-dire une colonne à retirer.
    ...(variances.data?.some((row) => row.backflushMeasured)
      ? [
          {
            key: 'unexplainedValue',
            label: 'Inexpliqué',
            numeric: true,
            width: 150,
            render: (row: VarianceRow) =>
              row.backflushMeasured ? (
                <DrillCell
                  disabled={row.backflushShareQty === 0}
                  onOpen={() => openDrill(row, 'variance')}
                >
                  <QtyOverValue
                    qty={signedNum(row.unexplainedQty)}
                    value={signedMoney(row.unexplainedValue)}
                    tone={signClass(row.unexplainedValue)}
                  />
                </DrillCell>
              ) : (
                <span className="subtle">{DASH}</span>
              ),
            value: (row: VarianceRow) => row.unexplainedValue,
          } as Column<VarianceRow>,
          {
            key: 'backflushShareQty',
            label: 'Part backflush',
            numeric: true,
            width: 140,
            render: (row: VarianceRow) =>
              row.backflushMeasured ? (
                <QtyOverValue
                  qty={signedNum(row.backflushShareQty)}
                  value={signedMoney(row.backflushShareValue)}
                />
              ) : (
                <span className="subtle">{DASH}</span>
              ),
            value: (row: VarianceRow) => row.backflushShareQty,
          } as Column<VarianceRow>,
        ]
      : []),
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
                <div className="segmented">
                  {DIMENSIONS.map((option) => (
                    <button
                      key={option.id}
                      className={`segmented__item${dimension === option.id ? ' segmented__item--active' : ''}`}
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
            ? 'Emplacements agrégés : ce que le site a réellement perdu ou gagné.'
            : 'Où aller recompter. Un article déplacé apparaît deux fois — en moins ici, en plus là.'
        }
        actions={
          <div className="row-wrap">
            <div className="segmented">
              <button
                className={`segmented__item${granularity === 'item' ? ' segmented__item--active' : ''}`}
                onClick={() => setGranularity('item')}
                title="La perte ou le gain réel du site"
              >
                Par référence
              </button>
              <button
                className={`segmented__item${granularity === 'item_location' ? ' segmented__item--active' : ''}`}
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
            {/* Les deux boutons emportent la vue telle qu'elle est réglée —
                granularité et filtre compris. Un export qui ignorerait les
                réglages produirait un fichier qui ne ressemble pas à l'écran
                depuis lequel on l'a demandé. */}
            <Button
              size="sm"
              icon={<Icons.download size={13} />}
              disabled={exporting !== null}
              onClick={() => void exportAs('xlsx')}
              title="Quantités, valeurs et écarts en colonnes séparées, stock ERP et stock compté"
            >
              {exporting === 'xlsx' ? 'Export…' : 'Excel'}
            </Button>
            <Button
              size="sm"
              icon={<Icons.printer size={13} />}
              disabled={exporting !== null}
              onClick={() => void exportAs('pdf')}
              title="Le tableau imprimable, plus gros écarts en tête"
            >
              {exporting === 'pdf' ? 'Export…' : 'PDF'}
            </Button>
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
                : 'Le stock compté correspond au stock ERP.'}
            </EmptyState>
          }
        >
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              exportTitle="Écarts"
              campaignId={campaignId}
              getRowId={(row, index) => `${row.itemNumber}-${row.warehouseId}-${row.locationId}-${index}`}
              searchPlaceholder="Filtrer par article, désignation, programme…"
              maxHeight={640}
              initialSort={{ key: 'varianceValue', direction: 'desc' }}
              footer={
                <span className="subtle">
                  Périmètre : {overview.campaign.code} · stock ERP gelé le{' '}
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
      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          warehouseId={drill.warehouseId}
          locationId={drill.locationId}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}

/**
 * A quantity over an amount, in that order, everywhere.
 *
 * One component rather than the same two lines written per column: the point is
 * that the arrangement cannot drift, and a shared component is the only version
 * of "consistent" that survives the next column being added.
 */
export function QtyOverValue({
  qty: quantity,
  value,
  tone,
}: {
  qty: string
  value: string
  tone?: string
}) {
  return (
    <div className="num">
      <strong className={tone}>{quantity}</strong>
      <div className="subtle">{value}</div>
    </div>
  )
}

export function CellBarInline({ value, max }: { value: number; max: number }) {
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
              Proposition fondée sur les chiffres de la campagne. Elle n’écrit rien.
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
                          <td className="num">{qty(row.child_qty)}</td>
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
                          <td className="num">{qty(Number(row.qty))}</td>
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
export function TransferCard({
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
