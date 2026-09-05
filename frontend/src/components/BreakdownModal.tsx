/**
 * « D'où vient ce chiffre ? », pour n'importe quel chiffre.
 *
 * Seule la colonne WIP était explorable ; toutes les autres devaient être crues
 * sur parole. Or une quantité qu'on ne peut pas expliquer est une quantité
 * qu'on ne peut pas défendre — et c'est en réunion, six mois plus tard, que la
 * question se pose.
 *
 * Une seule fenêtre pour toutes les colonnes, parce que le serveur répond
 * partout la même forme : origine, endroit, détail, quantité, valeur. Six
 * fenêtres se seraient mises à diverger dès la deuxième.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { moneyShort, qty as fmtQty, signClass, signedMoney, signedNum } from '../lib/format'
import { DataGrid, type Column } from './DataGrid'
import { AsyncBoundary, Badge, Button, EmptyState, Modal, Skeleton } from './ui'

/** Les colonnes décomposables, et ce qu'on en dit à l'écran. */
export type BreakdownAspect =
  | 'book'
  | 'counted'
  | 'physical'
  | 'line_side'
  | 'wip_ok'
  | 'wip'
  | 'variance'
  | 'generic'

const ASPECT_LABELS: Record<BreakdownAspect, { title: string; hint: string }> = {
  book: {
    title: 'Stock ERP',
    hint: 'Ce que l’ERP portait au moment du gel, emplacement par emplacement.',
  },
  counted: {
    title: 'Quantité comptée',
    hint: 'Chaque journal, et la part GENERIQUE ventilée par origine.',
  },
  generic: {
    title: 'Quantité consolidée GENERIQUE',
    hint: 'Ce que chaque zone apporte au journal consolidé — quantité retenue, arbitrages appliqués.',
  },
  physical: {
    title: 'Stock physique',
    hint: 'Ce qui a été compté, plus chaque mouvement posté depuis.',
  },
  line_side: {
    title: 'Bord de ligne',
    hint: 'Les lignes de feuilles comptées en bord de ligne, zone par zone.',
  },
  wip_ok: {
    title: 'WIP assemblé',
    hint: 'Les ensembles déclarés, comptés tels quels.',
  },
  wip: {
    title: 'WIP éclaté',
    hint: 'Les assemblages dont l’éclatement a produit cette quantité.',
  },
  variance: {
    title: 'Écart',
    hint: 'L’écart emplacement par emplacement — là où la différence s’est faite.',
  },
}

/** Les aspects dont le signe porte l'information. */
const SIGNED: ReadonlySet<BreakdownAspect> = new Set(['variance'])

export function BreakdownModal({
  campaignId,
  itemNumber,
  aspect,
  warehouseId,
  locationId,
  onClose,
}: {
  campaignId: string
  itemNumber: string
  aspect: BreakdownAspect
  /** Restreint à un emplacement, quand la cellule cliquée en désignait un. */
  warehouseId?: string
  locationId?: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['breakdown', campaignId, itemNumber, aspect, warehouseId, locationId],
    queryFn: () =>
      api.breakdown(campaignId, itemNumber, aspect, { warehouseId, locationId }),
  })
  const label = ASPECT_LABELS[aspect]
  const signed = SIGNED.has(aspect)

  // Les largeurs tiennent dans la fenêtre : une colonne Valeur coupée par le
  // bord droit est la seule que personne ne pense à aller chercher.
  const columns: Column[] = [
    { key: 'origin', label: 'Origine', width: 190 },
    { key: 'where', label: 'Emplacement', width: 175 },
    { key: 'detail', label: 'Détail', width: 215 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 125,
      render: (row) => (
        <span className={`num ${signed ? signClass(Number(row.qty ?? 0)) : ''}`}>
          {signed ? signedNum(Number(row.qty ?? 0)) : fmtQty(Number(row.qty ?? 0))}
        </span>
      ),
      value: (row) => Number(row.qty ?? 0),
    },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 135,
      render: (row) => (
        <span className={`num ${signed ? signClass(Number(row.value ?? 0)) : ''}`}>
          {signed
            ? signedMoney(Number(row.value ?? 0))
            : moneyShort(Number(row.value ?? 0))}
        </span>
      ),
      value: (row) => Number(row.value ?? 0),
    },
  ]

  return (
    <Modal
      title={`${label.title} — ${itemNumber}`}
      onClose={onClose}
      width={980}
      footer={<Button onClick={onClose}>Fermer</Button>}
    >
      <div className="stack">
        <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
          {label.hint}
        </p>
        <AsyncBoundary query={query} skeleton={<Skeleton height={220} />}>
          {(data) => (
            <div className="stack">
              <div className="row-wrap">
                <Badge tone="neutral">{data.name || itemNumber}</Badge>
                {/* Le total est calculé sur les lignes rendues, côté serveur :
                    une décomposition dont le total contredit ses propres lignes
                    est pire que pas de décomposition. */}
                <span className="num">
                  Total{' '}
                  <strong>
                    {signed ? signedNum(data.total) : fmtQty(data.total)} {data.unit}
                  </strong>
                </span>
                <span className={`num ${signed ? signClass(data.totalValue) : ''}`}>
                  {signed ? signedMoney(data.totalValue) : moneyShort(data.totalValue)}
                </span>
              </div>
              {data.rows.length === 0 ? (
                <EmptyState title="Rien à décomposer">
                  Cette quantité ne vient d’aucune ligne enregistrée.
                </EmptyState>
              ) : (
                <DataGrid
                  columns={columns}
                  rows={data.rows}
                  exportTitle={`${label.title} ${itemNumber}`}
                  campaignId={campaignId}
                  getRowId={(_row, index) => String(index)}
                  searchPlaceholder="Filtrer par origine, emplacement…"
                  maxHeight={380}
                  initialSort={{ key: 'qty', direction: 'desc' }}
                />
              )}
            </div>
          )}
        </AsyncBoundary>
      </div>
    </Modal>
  )
}

/**
 * Une cellule chiffrée qui s'ouvre.
 *
 * Rendue comme un bouton discret et non comme un lien : c'est une valeur qu'on
 * lit d'abord et qu'on interroge ensuite, et souligner tous les nombres d'un
 * tableau le rendrait illisible. Une cellule vide ne s'ouvre pas — il n'y aurait
 * rien derrière.
 */
export function DrillCell({
  children,
  onOpen,
  disabled = false,
}: {
  children: React.ReactNode
  onOpen: () => void
  disabled?: boolean
}) {
  if (disabled) return <>{children}</>
  return (
    <button className="drill" onClick={onOpen} title="Voir la décomposition">
      {children}
    </button>
  )
}
