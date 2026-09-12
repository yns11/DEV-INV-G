/**
 * Les lignes brutes d'un journal ERP, telles que l'ERP les a produites.
 *
 * Elles vivaient en base sans qu'aucun écran ne les montre. L'application
 * agrège vers l'emplacement — c'est son grain de comptage, de progression et de
 * gel — et l'agrégat ne dit pas d'où il vient : quand un périmètre surprend,
 * qu'une étiquette est signalée, ou simplement pour vérifier qu'un import a
 * rapporté ce qu'on croit, c'est la ligne qu'on veut lire.
 *
 * Une colonne y compte plus que les autres : **dans le périmètre**. Un journal
 * porte des lignes sur des emplacements qu'il ne couvre pas — 1 932 lignes sur
 * 58 345 dans l'export réel — et celles-là sont conservées comme trace d'un
 * déplacement sans jamais compter. Sans cette colonne, la grille laisserait
 * additionner des quantités que le journal ne retient pas.
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { ErpJournal, ErpJournalLine } from '../lib/types'
import { qty } from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { AsyncBoundary, Badge, Modal, Skeleton } from '../components/ui'

const DASH = <span className="subtle">—</span>

export function ErpJournalLinesModal({
  campaignId,
  journal,
  onClose,
}: {
  campaignId: string
  journal: ErpJournal
  onClose: () => void
}) {
  // « Hors périmètre » est la lecture qu'on vient chercher : elle explique
  // pourquoi un journal de six cents lignes n'en compte que quatre cents.
  const [scope, setScope] = useState<'all' | 'in' | 'out'>('all')

  const query = useQuery({
    queryKey: ['erp-journal-lines', campaignId, journal.id],
    queryFn: () => api.erpJournalLines(campaignId, journal.id),
  })

  const columns: Column<ErpJournalLine>[] = [
    // Pas de colonne « N° ligne ». L'ERP n'en donne pas sur les journaux
    // comptés par étiquette, et la chaîne d'extraction en inventait — « 1, -1,
    // -2, … -79 » — pour départager des lignes qu'il numérote pareil. Afficher
    // ces numéros invitait à leur faire confiance ; ce qui identifie une ligne
    // est désormais ce qu'elle désigne : site, entrepôt, emplacement,
    // étiquette, article.
    { key: 'site_id', label: 'Site', width: 90, filter: 'choice' },
    { key: 'warehouse_id', label: 'Entrepôt', width: 110, filter: 'choice' },
    { key: 'location_id', label: 'Emplacement', width: 150, filter: 'choice' },
    {
      key: 'inScope',
      label: 'Périmètre',
      width: 130,
      filter: 'choice',
      value: (row) => (row.inScope ? 'Compté' : 'Hors périmètre'),
      render: (row) =>
        row.inScope ? (
          <Badge tone="success">Compté</Badge>
        ) : (
          <Badge tone="neutral" title="Trace d’un déplacement : cette ligne ne compte pas">
            Hors périmètre
          </Badge>
        ),
    },
    {
      key: 'item_number',
      label: 'Référence',
      width: 170,
      filter: 'text',
      render: (row) => <span className="mono">{row.item_number}</span>,
    },
    {
      key: 'label_id',
      label: 'Étiquette',
      width: 160,
      filter: 'text',
      // Un identifiant se transporte : ni majuscules ni zéros de tête retirés.
      render: (row) => (row.label_id ? <span className="mono">{row.label_id}</span> : DASH),
    },
    {
      key: 'serial_number',
      label: 'N° de série',
      width: 160,
      filter: 'text',
      render: (row) =>
        row.serial_number ? <span className="mono">{row.serial_number}</span> : DASH,
    },
    {
      key: 'qtyOnHand',
      label: 'Stock ERP',
      width: 120,
      numeric: true,
      filter: 'range',
      render: (row) => qty(row.qtyOnHand),
    },
    {
      key: 'qtyCounted',
      label: 'Qté comptée',
      width: 130,
      numeric: true,
      filter: 'range',
      render: (row) => qty(row.qtyCounted),
    },
    {
      key: 'varianceQty',
      label: 'Écart',
      width: 110,
      numeric: true,
      filter: 'range',
      render: (row) => (
        <span
          className={
            row.varianceQty === 0 ? 'neutral' : row.varianceQty > 0 ? 'pos' : 'neg'
          }
        >
          {qty(row.varianceQty)}
        </span>
      ),
    },
    { key: 'unit', label: 'Unité', width: 90, filter: 'choice' },
    {
      key: 'inventory_status_id',
      label: 'Statut qualité',
      width: 140,
      filter: 'choice',
      render: (row) => row.inventory_status_id || DASH,
    },
  ]

  return (
    <Modal
      title={
        <span className="row" style={{ gap: 'var(--space-3)' }}>
          Journal {journal.journalNumber}
          <Badge tone="neutral">{journal.kind}</Badge>
          {journal.isSealed && <Badge tone="success">Scellé</Badge>}
        </span>
      }
      onClose={onClose}
      width={1392}
    >
      <AsyncBoundary query={query} skeleton={<Skeleton height={320} />}>
        {(lines) => <Lines lines={lines} scope={scope} onScope={setScope} />}
      </AsyncBoundary>
    </Modal>
  )

  function Lines({
    lines,
    scope,
    onScope,
  }: {
    lines: ErpJournalLine[]
    scope: 'all' | 'in' | 'out'
    onScope: (next: 'all' | 'in' | 'out') => void
  }) {
    const counts = useMemo(() => {
      const inside = lines.filter((l) => l.inScope).length
      return { all: lines.length, in: inside, out: lines.length - inside }
    }, [lines])

    const rows = useMemo(
      () =>
        scope === 'all'
          ? lines
          : lines.filter((l) => (scope === 'in' ? l.inScope : !l.inScope)),
      [lines, scope],
    )

    const chips: Array<{ id: 'all' | 'in' | 'out'; label: string; hint: string }> = [
      { id: 'all', label: 'Toutes', hint: 'Le journal tel qu’il est arrivé' },
      {
        id: 'in',
        label: 'Comptées',
        hint: 'Dans le périmètre déclaré : ce sont elles qui font la référence et le comptage',
      },
      {
        id: 'out',
        label: 'Hors périmètre',
        hint: 'Conservées comme trace d’un déplacement — elles ne comptent pas',
      },
    ]

    return (
      <div className="stack">
        <div className="chips">
          {chips.map((chip) => (
            <button
              key={chip.id}
              className={`chip${scope === chip.id ? ' chip--active' : ''}`}
              title={chip.hint}
              onClick={() => onScope(chip.id)}
            >
              {chip.label} <span className="num">{counts[chip.id]}</span>
            </button>
          ))}
        </div>
        <DataGrid<ErpJournalLine>
          rows={rows}
          columns={columns}
          getRowId={(row) => row.id}
          maxHeight={454}
          exportTitle={`Journal ${journal.journalNumber}`}
          campaignId={campaignId}
          searchPlaceholder="Filtrer par référence, étiquette, emplacement…"
          emptyTitle="Aucune ligne"
          emptyBody={
            scope === 'out'
              ? 'Toutes les lignes de ce journal portent sur son périmètre.'
              : 'Ce journal ne porte aucune ligne.'
          }
        />
      </div>
    )
  }
}
