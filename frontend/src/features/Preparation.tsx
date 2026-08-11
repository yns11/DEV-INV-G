/** Referentials, printable sheets, perimeters and materiality thresholds. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { GRID_ROW_CEILING, api } from '../lib/api'
import type {
  GridContract, Manager, Overview, PrintMode, Threshold,
} from '../lib/types'
import { ITEM_TYPE_LABELS, moneyShort, qty, percent } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { PrintModal } from '../components/PrintModal'
import { useSubSection } from '../lib/subsection'
import { ZonesAdminGrid } from './zones'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  Field,
  Icons,
  Modal,
  Skeleton,
  Switch,
  useErrorToast,
  useToast,
} from '../components/ui'

/**
 * The screens this file serves, one per navigation entry.
 *
 * `gestion` is the one that still holds several views: managers, the two
 * perimeter assignments and the thresholds are four short forms that belong to
 * the same decision — who counts what, and from which amount an variance
 * matters — and splitting them into four sidebar entries would have made the
 * tree longer without making anything easier to find.
 */
export type PreparationView =
  | 'items'
  | 'boms'
  | 'book_stock'
  | 'count_sheets'
  | 'gestion'

type GestionTab = 'managers' | 'zone_scope' | 'journal_scope' | 'thresholds'

const GESTION_TABS: GestionTab[] = [
  'managers', 'zone_scope', 'journal_scope', 'thresholds',
]

export function Preparation({ view }: { view: PreparationView }) {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  // The sidebar draws this level, so the screen only reads it.
  const [gestion] = useSubSection<GestionTab>('managers', GESTION_TABS)
  const tab = view

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract = (key: string): GridContract | undefined =>
    contracts.data?.find((c) => c.key === key)

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      {contracts.isPending && <Skeleton height={240} />}

      {tab === 'items' && contract('items') && (
        <ItemsTab campaignId={campaignId} contract={contract('items')!} overview={overview} />
      )}
      {tab === 'boms' && contract('boms') && (
        <BomsTab campaignId={campaignId} contract={contract('boms')!} overview={overview} />
      )}
      {tab === 'book_stock' && contract('book_stock') && (
        <BookStockTab
          campaignId={campaignId}
          contract={contract('book_stock')!}
          overview={overview}
        />
      )}
      {tab === 'count_sheets' && contract('count_sheets') && (
        <CountSheetsTab
          campaignId={campaignId}
          contract={contract('count_sheets')!}
          overview={overview}
        />
      )}
      {tab === 'gestion' && gestion === 'thresholds' && (
        <ThresholdsTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'managers' && (
        <ManagersTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'journal_scope' && (
        <JournalScopeTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'gestion' && gestion === 'zone_scope' && (
        <ZoneScopeTab campaignId={campaignId} overview={overview} />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Articles
// --------------------------------------------------------------------------- //

function ItemsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const editable = overview.permissions.items
  const query = useQuery({
    queryKey: ['items', campaignId, search],
    queryFn: () => api.items(campaignId, { limit: GRID_ROW_CEILING, search: search || undefined }),
  })

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 280 },
    {
      key: 'item_type',
      label: 'Type',
      width: 130,
      render: (row) => (
        <Badge tone="neutral">
          {ITEM_TYPE_LABELS[String(row.item_type)] ?? String(row.item_type)}
        </Badge>
      ),
      value: (row) => String(row.item_type),
    },
    { key: 'category', label: 'Catégorie', width: 130 },
    { key: 'program', label: 'Programme', width: 120 },
    { key: 'unit', label: 'Unité', width: 80 },
    {
      key: 'stdPrice',
      label: 'Prix standard',
      numeric: true,
      width: 140,
      render: (row) => moneyShort(Number(row.stdPrice ?? 0)),
      value: (row) => Number(row.stdPrice ?? 0),
    },
    {
      key: 'exclusions',
      label: 'Exclusion',
      width: 160,
      render: (row) => {
        const values = (row.exclusions as string[] | undefined) ?? []
        if (values.length === 0) return <span className="subtle">—</span>
        return (
          <span className="row" style={{ gap: 'var(--space-1)' }}>
            {values.map((value) => (
              <Badge key={value} tone={value === 'ALL' ? 'danger' : 'warning'}>
                {EXCLUSION_LABELS[value] ?? value}
              </Badge>
            ))}
          </span>
        )
      },
      value: (row) => ((row.exclusions as string[] | undefined) ?? []).join(','),
    },
    {
      key: 'edit',
      label: '',
      width: 52,
      sortable: false,
      render: (row) => (
        <Button
          variant="ghost"
          size="sm"
          title={editable ? 'Modifier cet article' : 'Référentiel gelé'}
          disabled={!editable}
          onClick={() => setEditing(row)}
        >
          <Icons.sliders size={14} />
        </Button>
      ),
    },
  ]

  return (
    <div className="stack">
      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="items"
        disabled={!overview.permissions.items}
        disabledReason="Le référentiel articles est gelé depuis le passage en phase de comptage."
        onImported={() => void queryClient.invalidateQueries({ queryKey: ['items'] })}
      />

      <Card
        title="Référentiel articles de la campagne"
        message="Trois niveaux d’exclusion : hors périmètre complet, hors GENERIQUE, ou ignoré dans les nomenclatures."
        flush
      >
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              getRowId={(row) => String(row.item_number)}
              searchPlaceholder="Filtrer par référence, désignation…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} article(s) au référentiel
                  {data.total > data.rows.length && ` — ${data.rows.length} affichés`}
                </span>
              }
              toolbar={
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Icons.search size={13} />}
                  onClick={() => setSearch('')}
                  disabled={!search}
                >
                  Réinitialiser la recherche serveur
                </Button>
              }
            />
          )}
        </AsyncBoundary>
      </Card>

      {editing && (
        <ItemEditModal
          campaignId={campaignId}
          row={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

const EXCLUSION_LABELS: Record<string, string> = {
  ALL: 'Hors périmètre',
  GENERIC: 'Hors GENERIQUE',
  BOM: 'Ignoré en BOM',
}

/**
 * The four possible exclusions, as one control.
 *
 * « Aucune » is a state, not a fourth checkbox: an article excluded from
 * nothing is the normal case, and it has to be reachable in one click — the
 * point of the screen is to *undo* an exclusion as easily as to set one.
 * GENERIC and BOM are independent facets and combine; ALL subsumes both, so
 * picking it clears them rather than leaving three boxes ticked that say the
 * same thing three times.
 */
function ExclusionPicker({
  value,
  onChange,
}: {
  value: string[]
  onChange: (next: string[]) => void
}) {
  const has = (scope: string) => value.includes(scope)
  const toggle = (scope: string) => {
    if (scope === 'ALL') {
      onChange(has('ALL') ? [] : ['ALL'])
      return
    }
    const without = value.filter((v) => v !== 'ALL' && v !== scope)
    onChange(has(scope) ? without : [...without, scope])
  }

  return (
    <Field
      label="Exclusion"
      hint="Hors périmètre exclut l’article de tout comptage et de toute analyse."
    >
      <div className="chips">
        <button
          className={`chip${value.length === 0 ? ' chip--active' : ''}`}
          onClick={() => onChange([])}
        >
          Aucune
        </button>
        {(['GENERIC', 'BOM', 'ALL'] as const).map((scope) => (
          <button
            key={scope}
            className={`chip${has(scope) ? ' chip--active' : ''}`}
            onClick={() => toggle(scope)}
          >
            {EXCLUSION_LABELS[scope]}
          </button>
        ))}
      </div>
    </Field>
  )
}

/**
 * Correcting one article, in place.
 *
 * Only the fields a human legitimately fixes are here. The article number is
 * shown and not editable: it is the identity of the line, and changing it would
 * be creating a different article while quietly orphaning every count, journal
 * line and bill-of-materials edge that referenced the old one.
 */
function ItemEditModal({
  campaignId,
  row,
  onClose,
}: {
  campaignId: string
  row: Record<string, unknown>
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const itemNumber = String(row.item_number)

  const [draft, setDraft] = useState({
    name: String(row.name ?? ''),
    itemType: String(row.item_type ?? 'UNKNOWN'),
    category: String(row.category ?? ''),
    program: String(row.program ?? ''),
    unit: String(row.unit ?? 'PCE'),
    stdPrice: String(row.stdPrice ?? 0),
  })
  const [exclusions, setExclusions] = useState<string[]>(
    ((row.exclusions as string[] | undefined) ?? []).filter((e) => e !== 'NONE'),
  )

  const save = useMutation({
    mutationFn: () =>
      api.updateItem(campaignId, itemNumber, {
        ...draft,
        stdPrice: Number(draft.stdPrice.replace(',', '.')),
        exclusions,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['items'] })
      toast.success(`Article ${itemNumber} modifié`)
      onClose()
    },
    onError: (error) => showError(error, 'Modification impossible'),
  })

  const priceInvalid = !Number.isFinite(Number(draft.stdPrice.replace(',', '.')))
  const set = (key: keyof typeof draft) => (value: string) =>
    setDraft((current) => ({ ...current, [key]: value }))

  return (
    <Modal
      title={`Article ${itemNumber}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={save.isPending || priceInvalid}
            onClick={() => save.mutate()}
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Désignation">
          <input
            className="input"
            value={draft.name}
            onChange={(event) => set('name')(event.target.value)}
          />
        </Field>
        <Field label="Type">
          <select
            className="input"
            value={draft.itemType}
            onChange={(event) => set('itemType')(event.target.value)}
          >
            {Object.entries(ITEM_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <div className="row" style={{ gap: 'var(--space-3)' }}>
          <Field label="Catégorie">
            <input
              className="input"
              value={draft.category}
              onChange={(event) => set('category')(event.target.value)}
            />
          </Field>
          <Field label="Programme">
            <input
              className="input"
              value={draft.program}
              onChange={(event) => set('program')(event.target.value)}
            />
          </Field>
        </div>
        <div className="row" style={{ gap: 'var(--space-3)' }}>
          <Field label="Unité">
            <input
              className="input"
              value={draft.unit}
              onChange={(event) => set('unit')(event.target.value)}
            />
          </Field>
          <Field
            label="Prix standard (€)"
            hint="Pour une unité."
            error={priceInvalid ? 'Nombre attendu.' : undefined}
          >
            <input
              className="input num"
              inputMode="decimal"
              value={draft.stdPrice}
              onChange={(event) => set('stdPrice')(event.target.value)}
            />
          </Field>
        </div>
        <ExclusionPicker value={exclusions} onChange={setExclusions} />
      </div>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Bills of materials
// --------------------------------------------------------------------------- //

function BomsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const editable = overview.permissions.boms
  const health = useQuery({
    queryKey: ['bom-health', campaignId],
    queryFn: () => api.bomHealth(campaignId),
  })
  const links = useQuery({
    queryKey: ['boms', campaignId],
    queryFn: () => api.boms(campaignId),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['boms'] })
    void queryClient.invalidateQueries({ queryKey: ['bom-health'] })
  }

  const remove = useMutation({
    mutationFn: ({ parent, child }: { parent: string; child: string }) =>
      api.deleteBomLink(campaignId, parent, child),
    onSuccess: () => {
      refresh()
      toast.success('Lien supprimé')
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  const columns: Column[] = [
    { key: 'parent_item', label: 'Assemblage', width: 180 },
    { key: 'child_item', label: 'Composant', width: 180 },
    {
      key: 'qtyPer',
      label: 'Qté par assemblage',
      numeric: true,
      width: 170,
      render: (row) => <span className="num">{qty(Number(row.qtyPer ?? 0))}</span>,
      value: (row) => Number(row.qtyPer ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 90 },
    {
      key: 'active',
      label: 'Version',
      width: 110,
      render: (row) =>
        row.active === false ? (
          <Badge tone="warning">Inactive</Badge>
        ) : (
          <Badge tone="success">En vigueur</Badge>
        ),
      value: (row) => (row.active === false ? 'Inactive' : 'En vigueur'),
    },
    {
      key: 'edit',
      label: '',
      width: 92,
      sortable: false,
      render: (row) => (
        <span className="row" style={{ gap: 'var(--space-1)' }}>
          <Button
            variant="ghost"
            size="sm"
            title={editable ? 'Modifier ce lien' : 'Nomenclatures gelées'}
            disabled={!editable}
            onClick={() => setEditing(row)}
          >
            <Icons.sliders size={14} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title={editable ? 'Supprimer ce lien' : 'Nomenclatures gelées'}
            disabled={!editable || remove.isPending}
            onClick={() => {
              if (
                window.confirm(
                  `Supprimer le lien ${row.parent_item} → ${row.child_item} ?`,
                )
              ) {
                remove.mutate({
                  parent: String(row.parent_item),
                  child: String(row.child_item),
                })
              }
            }}
          >
            <Icons.trash size={14} />
          </Button>
        </span>
      ),
    },
  ]

  return (
    <div className="stack">
      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="boms"
        disabled={!overview.permissions.boms}
        disabledReason="Les nomenclatures sont gelées depuis le passage en phase de comptage."
        onImported={() => {
          void queryClient.invalidateQueries({ queryKey: ['boms'] })
          void queryClient.invalidateQueries({ queryKey: ['bom-health'] })
        }}
      />

      <AsyncBoundary query={health} skeleton={<Skeleton height={140} />}>
        {(data) => (
          <Card
            title="Santé des nomenclatures"
            message={
              data.cycles.length > 0
                ? `${data.cycles.length} cycle(s) détecté(s) : l’éclatement du WIP est impossible tant qu’ils subsistent.`
                : `${data.linkCount.toLocaleString('fr-FR')} liens couvrant ${data.parentCount} assemblage(s), sans cycle.`
            }
          >
            <div className="stack">
              {data.cycles.length > 0 && (
                <Alert tone="danger" title="Cycles de nomenclature">
                  <ul style={{ margin: 0, paddingLeft: '1.1rem' }} className="mono">
                    {data.cycles.slice(0, 6).map((cycle) => (
                      <li key={cycle}>{cycle}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              {data.findings.length > 0 ? (
                <div className="table-wrap" style={{ maxHeight: 260 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th style={{ width: 110 }}>Sévérité</th>
                        <th style={{ width: 170 }}>Article</th>
                        <th>Constat</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.findings.slice(0, 60).map((finding, index) => (
                        <tr key={index}>
                          <td>
                            <Badge tone={finding.severity === 'BLOCKER' ? 'danger' : 'warning'}>
                              {finding.severity === 'BLOCKER' ? 'Bloquant' : 'Avertissement'}
                            </Badge>
                          </td>
                          <td className="mono">{finding.item_number || '—'}</td>
                          <td>{finding.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Alert tone="success" title="Aucune anomalie structurelle détectée" />
              )}
            </div>
          </Card>
        )}
      </AsyncBoundary>

      <Card title="Liens de nomenclature" flush>
        <AsyncBoundary query={links} isEmpty={(rows) => rows.length === 0}>
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row, index) => `${row.parent_item}-${row.child_item}-${index}`}
              searchPlaceholder="Filtrer par assemblage ou composant…"
              maxHeight={520}
            />
          )}
        </AsyncBoundary>
      </Card>

      {editing && (
        <BomLinkEditModal
          campaignId={campaignId}
          row={editing}
          onSaved={refresh}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  )
}

/**
 * Correcting one bill-of-materials edge.
 *
 * Parent and child are shown and fixed: they *are* the edge. Changing one is
 * deleting a link and creating another, which the grid already offers — and
 * doing it silently here would leave the original in place.
 *
 * A wrong quantity per assembly is invisible until consolidation explodes a WIP
 * and produces a component count nobody can explain, so it is worth being able
 * to fix in one field.
 */
function BomLinkEditModal({
  campaignId,
  row,
  onSaved,
  onClose,
}: {
  campaignId: string
  row: Record<string, unknown>
  onSaved: () => void
  onClose: () => void
}) {
  const toast = useToast()
  const showError = useErrorToast()
  const parent = String(row.parent_item)
  const child = String(row.child_item)
  const [qtyPer, setQtyPer] = useState(String(row.qtyPer ?? ''))
  const [unit, setUnit] = useState(String(row.unit ?? 'PCE'))
  const [active, setActive] = useState(row.active !== false)

  const parsed = Number(qtyPer.replace(',', '.'))
  const invalid = !Number.isFinite(parsed) || parsed <= 0

  const save = useMutation({
    mutationFn: () =>
      api.updateBomLink(campaignId, {
        parentItem: parent,
        childItem: child,
        qtyPer: parsed,
        unit,
        active,
      }),
    onSuccess: () => {
      onSaved()
      toast.success(`Lien ${parent} → ${child} modifié`)
      onClose()
    },
    onError: (error) => showError(error, 'Modification impossible'),
  })

  return (
    <Modal
      title={`${parent} → ${child}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={save.isPending || invalid}
            onClick={() => save.mutate()}
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="Quantité par assemblage"
          hint="Combien de composants un assemblage consomme."
          error={invalid ? 'Nombre strictement positif attendu.' : undefined}
        >
          <input
            className="input num"
            inputMode="decimal"
            value={qtyPer}
            onChange={(event) => setQtyPer(event.target.value)}
          />
        </Field>
        <Field label="Unité">
          <input
            className="input"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
          />
        </Field>
        {/* Une version retirée reste chargée — c'est ce qui distingue « recette
            périmée » de « aucune recette » — mais elle n'est pas éclatée. La
            remettre en vigueur ici évite un aller-retour par l'ERP quand c'est
            le statut, et non la structure, qui était faux. */}
        <Switch
          checked={active}
          onChange={setActive}
          label="Version en vigueur (seules celles-ci sont éclatées)"
        />
      </div>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Book stock
// --------------------------------------------------------------------------- //

function BookStockTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const query = useQuery({
    queryKey: ['book-stock', campaignId],
    queryFn: () => api.bookStock(campaignId, { limit: GRID_ROW_CEILING }),
  })

  const freeze = useMutation({
    mutationFn: () => api.freezeBookStock(campaignId),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Stock ERP gelé', 'Les écarts sont désormais reproductibles.')
    },
    onError: (error) => showError(error, 'Gel impossible'),
  })

  const frozen = overview.campaign.book_stock_frozen_at !== null
  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'warehouse_id', label: 'Entrepôt', width: 120 },
    { key: 'location_id', label: 'Emplacement', width: 150 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 130,
      render: (row) => <span className="num">{qty(Number(row.qty ?? 0))}</span>,
      value: (row) => Number(row.qty ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 80 },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{moneyShort(Number(row.value ?? 0))}</span>,
      value: (row) => Number(row.value ?? 0),
    },
  ]

  return (
    <div className="stack">
      {frozen ? (
        <Alert tone="success" title="Stock ERP gelé">
          Tout écart calculé aujourd’hui restera recalculable à l’identique.
        </Alert>
      ) : (
        <Alert
          tone="warning"
          title="Stock ERP non gelé"
          actions={
            <Button
              variant="primary"
              size="sm"
              icon={<Icons.lock size={13} />}
              disabled={query.data?.total === 0 || freeze.isPending}
              onClick={() => freeze.mutate()}
            >
              Geler le stock ERP
            </Button>
          }
        >
          Chargez l’export ERP puis gelez-le. Le chargement crée aussi le référentiel
          entrepôts/emplacements et un journal de comptage par emplacement actif.
        </Alert>
      )}

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="book_stock"
        disabled={!overview.permissions.bookStock || frozen}
        disabledReason={
          frozen
            ? undefined
            : 'Le chargement du stock ERP se fait pendant la phase de comptage.'
        }
        onImported={() => void queryClient.invalidateQueries()}
      />

      <Card title="Stock ERP (snapshot ERP)" flush>
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              getRowId={(row, index) =>
                `${row.item_number}-${row.warehouse_id}-${row.location_id}-${index}`
              }
              searchPlaceholder="Filtrer par article, entrepôt, emplacement…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} ligne(s)
                  {data.total > data.rows.length && ` — ${data.rows.length} affichées`}
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Prepared counting sheets
// --------------------------------------------------------------------------- //

function CountSheetsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const [printing, setPrinting] = useState(false)
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })
  const zones = useQuery({
    queryKey: ['zones', campaignId, false],
    queryFn: () => api.zones(campaignId, {}),
  })

  // Which documents the whole set can produce, and how many sheets each would
  // yield. Computed from the zones themselves — the print matrix lives on the
  // server and arrives as `zone.printModes`, so this screen never guesses.
  const batch = useMemo(() => {
    const counts = { blank: 0, list: 0, filled: 0 } as Record<PrintMode, number>
    for (const zone of zones.data ?? []) {
      for (const mode of zone.printModes ?? []) counts[mode] = (counts[mode] ?? 0) + 1
    }
    const modes = (['list', 'blank', 'filled'] as PrintMode[]).filter(
      (m) => (counts[m] ?? 0) > 0,
    )
    return { modes, counts }
  }, [zones.data])

  return (
    <div className="stack">
      {/* Printing belongs here, not three screens away under the counting
          phase: the sheets are handed out *before* anyone counts, and having to
          go looking for the button in GENERIQUE was the reason people printed
          from the wrong screen. */}
      <Card
        title="Feuilles à imprimer"
        message="Préparez et sortez le papier avant la journée de comptage."
        actions={
          <Button
            variant="primary"
            icon={<Icons.printer size={14} />}
            disabled={batch.modes.length === 0}
            title={
              batch.modes.length === 0
                ? 'Créez d’abord au moins une zone.'
                : undefined
            }
            onClick={() => setPrinting(true)}
          >
            Imprimer toutes les feuilles
          </Button>
        }
      >
        <p className="muted">
          {batch.modes.length === 0
            ? 'Aucune zone pour l’instant : les feuilles apparaîtront ici dès qu’une zone existe.'
            : `${zones.data?.length ?? 0} zone(s) prêtes à être imprimées.`}
        </p>
      </Card>

      {overview.permissions.countSheets && (
      <Alert tone="info" title="Une ligne par couple feuille / article">
        Une feuille inconnue est créée, une feuille connue complétée. Les lignes sont
        posées sur <strong>les deux comptages</strong>, quantités vides.
      </Alert>
      )}

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="count_sheets"
        disabled={!overview.permissions.countSheets}
        disabledReason="Les feuilles de comptage sont gelées depuis le passage en phase d’analyse."
        onImported={() => void queryClient.invalidateQueries()}
      />

      {overview.permissions.countSheets && (
        <Alert tone="warning" title="Le référentiel articles fait foi">
          Un article absent du référentiel est rejeté, jamais créé à la volée.
        </Alert>
      )}

      <ZonesAdminGrid
        campaignId={campaignId}
        editable={overview.permissions.zones}
        managers={managers.data?.managers ?? []}
      />

      {printing && (
        <PrintModal
          campaignId={campaignId}
          modes={batch.modes}
          zonesByMode={batch.counts}
          onClose={() => setPrinting(false)}
        />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Thresholds
// --------------------------------------------------------------------------- //

function ThresholdsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Threshold[] | null>(null)

  const query = useQuery({
    queryKey: ['thresholds', campaignId],
    queryFn: () => api.thresholds(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Threshold[]) =>
      api.saveThresholds(
        campaignId,
        rows.map((row) => ({
          itemType: row.item_type,
          valueAbsEur: Number(row.value_abs_eur),
          qtyRelative: row.qty_relative === null ? null : Number(row.qty_relative),
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['thresholds', campaignId] })
      setDraft(null)
      toast.success('Seuils enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: keyof Threshold, value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value === '' ? null : value }
    setDraft(next)
  }

  return (
    <Card
      title="Seuils de matérialité"
      message="Un écart est « matériel » lorsqu’il franchit toutes les barrières configurées de son type. Exiger la conjonction — et non l’une ou l’autre — garde la liste d’exceptions à une taille exploitable le jour J."
      actions={
        editable && draft ? (
          <>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Annuler
            </Button>
            <Button
              variant="primary"
              disabled={save.isPending}
              onClick={() => save.mutate(rows)}
            >
              Enregistrer
            </Button>
          </>
        ) : null
      }
      flush
    >
      {!editable && (
        <div style={{ padding: 'var(--space-4)' }}>
          <Alert tone="info" title="Seuils gelés">
            Figés au passage en comptage, pour que les exceptions signalées restent
            les mêmes jusqu’à l’analyse.
          </Alert>
        </div>
      )}

      <AsyncBoundary query={query} isEmpty={() => false}>
        {() => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 160 }}>Type d’article</th>
                  <th className="num" title="Écart en valeur absolue au-delà duquel la ligne est une exception">
                    Valeur absolue (€)
                  </th>
                  <th className="num" title="|Δqté| / qté ERP au-delà duquel la ligne est une exception">
                    Écart relatif
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.item_type}>
                    <td>
                      <strong>{ITEM_TYPE_LABELS[row.item_type] ?? row.item_type}</strong>
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={String(row.value_abs_eur ?? '')}
                          onChange={(e) => update(index, 'value_abs_eur', e.target.value)}
                        />
                      ) : (
                        moneyShort(Number(row.value_abs_eur))
                      )}
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={row.qty_relative === null ? '' : String(row.qty_relative)}
                          placeholder="désactivé"
                          onChange={(e) => update(index, 'qty_relative', e.target.value)}
                        />
                      ) : row.qty_relative === null ? (
                        <span className="subtle">désactivé</span>
                      ) : (
                        percent(Number(row.qty_relative), 2)
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
  )
}

// --------------------------------------------------------------------------- //
// Managers and perimeters
// --------------------------------------------------------------------------- //

/**
 * The five manager slots.
 *
 * The identity column is the load-bearing one: it is what lets the server
 * answer "who is asking?" when a screen requests `focus=true`, so the browser
 * never has to name a manager — and never receives what the filter excluded.
 */
function ManagersTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Manager[] | null>(null)

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Manager[]) =>
      api.saveManagers(
        campaignId,
        rows.map((row, index) => ({
          code: row.code,
          label: row.label,
          actor: row.actor,
          active: row.active,
          displayOrder: index,
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      setDraft(null)
      toast.success('Gestionnaires enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data?.managers ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: 'label' | 'actor', value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value }
    setDraft(next)
  }

  return (
    <div className="stack">
      <Alert tone="info" title="Un périmètre, pas une habilitation">
        Une affectation ne restreint aucune action : c’est le filtre « Mon périmètre ».
        Chacun garde le droit d’agir partout.
      </Alert>

      <Card
        title="Gestionnaires de la campagne"
        message="L’identité est celle transmise par l’authentification (votre adresse e-mail). C’est elle qui résout « Mon périmètre » côté serveur."
        actions={
          editable && draft ? (
            <>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Annuler
              </Button>
              <Button
                variant="primary"
                disabled={save.isPending}
                onClick={() => save.mutate(rows)}
              >
                Enregistrer
              </Button>
            </>
          ) : null
        }
        flush
      >
        {!editable && (
          <div style={{ padding: 'var(--space-4)' }}>
            <Alert tone="info" title="Gestionnaires gelés">
              Figés au passage en comptage, comme le reste de la configuration.
            </Alert>
          </div>
        )}
        <AsyncBoundary query={query} skeleton={<Skeleton height={220} />}>
          {() => (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 160 }}>Poste</th>
                    <th>Libellé</th>
                    <th>Identité (e-mail)</th>
                    <th className="num" style={{ width: 110 }}>Entrepôts</th>
                    <th className="num" style={{ width: 100 }}>Journaux</th>
                    <th className="num" style={{ width: 90 }}>Zones</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.code}>
                      <td className="mono subtle">{row.code}</td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.label}
                            placeholder="Nom du poste"
                            onChange={(e) => update(index, 'label', e.target.value)}
                          />
                        ) : (
                          row.label || <span className="subtle">—</span>
                        )}
                      </td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.actor}
                            inputMode="email"
                            placeholder="prenom.nom@exemple.fr"
                            onChange={(e) => update(index, 'actor', e.target.value)}
                          />
                        ) : row.actor ? (
                          <span className="mono">{row.actor}</span>
                        ) : (
                          <span className="subtle">poste inoccupé</span>
                        )}
                      </td>
                      <td className="num">
                        {
                          (query.data?.warehouses ?? []).filter(
                            (w) => w.managerCode === row.code,
                          ).length
                        }
                      </td>
                      <td className="num">{row.journalCount}</td>
                      <td className="num">{row.zoneCount}</td>
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

/** Warehouses — and therefore their counting journals — assigned to a manager. */
function JournalScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const assign = useMutation({
    mutationFn: (input: { warehouseId: string; managerCode: string }) =>
      api.assignWarehouses(campaignId, [input]),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Affectation enregistrée')
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const editable = overview.permissions.thresholds
  const managers = query.data?.managers ?? []

  return (
    <div className="stack">
      <Alert tone="info" title="Un journal suit son entrepôt">
        Un journal suit son entrepôt. La ligne <strong>AUTRES</strong> rattache d’un
        coup tous les entrepôts sans affectation explicite — sans elle, un entrepôt
        découvert par un import tomberait hors de tout périmètre.
      </Alert>

      <Card title="Affectation des entrepôts" flush>
        <AsyncBoundary query={query} skeleton={<Skeleton height={240} />}>
          {(data) => (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 200 }}>Entrepôt</th>
                    <th className="num" style={{ width: 130 }}>Journaux</th>
                    <th style={{ width: 260 }}>Gestionnaire</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.warehouses.map((warehouse) => (
                    <tr key={warehouse.warehouseId}>
                      <td>
                        <span className="mono">{warehouse.warehouseId}</span>
                        {warehouse.isCatchAll && (
                          <>
                            {' '}
                            <Badge tone="info">fourre-tout</Badge>
                          </>
                        )}
                      </td>
                      <td className="num">
                        {warehouse.isCatchAll ? (
                          <span className="subtle">—</span>
                        ) : (
                          warehouse.journalCount
                        )}
                      </td>
                      <td className="editable">
                        <select
                          className="input"
                          disabled={!editable || assign.isPending}
                          value={warehouse.managerCode}
                          onChange={(event) =>
                            assign.mutate({
                              warehouseId: warehouse.warehouseId,
                              managerCode: event.target.value,
                            })
                          }
                        >
                          <option value="">— aucun —</option>
                          {managers.map((manager) => (
                            <option key={manager.code} value={manager.code}>
                              {manager.label || manager.code}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="subtle">
                        {!warehouse.known && !warehouse.isCatchAll
                          ? 'aucun journal pour l’instant'
                          : ''}
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

/** GENERIQUE zones assigned to a manager, in bulk over a selection. */
function ZoneScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  return (
    <div className="stack">
      <Alert tone="info" title="Rattacher les zones à leur gestionnaire">
        Sélectionnez des zones, puis choisissez un gestionnaire dans la barre d’outils.
      </Alert>

      <ZonesAdminGrid
        campaignId={campaignId}
        editable={overview.permissions.zones}
        managers={managers.data?.managers ?? []}
      />
    </div>
  )
}
