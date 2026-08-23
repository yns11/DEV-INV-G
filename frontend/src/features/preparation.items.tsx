/** Le référentiel articles d'une campagne : ce qu'on compte, et ce qu'on écarte. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GRID_ROW_CEILING, api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { ITEM_TYPE_LABELS, moneyShort } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { AsyncBoundary, Badge, Button, Card, Field, Icons, Modal, useErrorToast, useToast } from '../components/ui'
import { StockedFilter } from './preparation.shared'

const EXCLUSION_LABELS: Record<string, string> = {
  ALL: 'Hors périmètre',
  GENERIC: 'Hors GENERIQUE',
  BOM: 'Ignoré en BOM',
}

/**
 * L'exclusion d'une sélection entière, en un geste.
 *
 * Les deux facettes se cumulent : un article peut être hors GENERIQUE **et**
 * ignoré en nomenclature tout en restant dans le périmètre — ce sont deux
 * décisions séparées, pas deux valeurs d'un même réglage. Elles sont donc
 * offertes ensemble, et « hors périmètre » est le seul choix qui les remplace,
 * puisqu'il les recouvre déjà toutes les deux.
 */
const BULK_EXCLUSIONS: Array<{ value: string; label: string; scopes: string[] }> = [
  { value: 'NONE', label: 'Aucune — remettre dans le périmètre', scopes: [] },
  { value: 'GENERIC', label: EXCLUSION_LABELS.GENERIC!, scopes: ['GENERIC'] },
  { value: 'BOM', label: EXCLUSION_LABELS.BOM!, scopes: ['BOM'] },
  {
    value: 'GENERIC+BOM',
    label: 'Hors GENERIQUE et ignoré en BOM',
    scopes: ['GENERIC', 'BOM'],
  },
  { value: 'ALL', label: EXCLUSION_LABELS.ALL!, scopes: ['ALL'] },
]

// --------------------------------------------------------------------------- //
// Articles
// --------------------------------------------------------------------------- //

export function ItemsTab({
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
  const [search, setSearch] = useState('')
  const [counted, setCounted] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const editable = overview.permissions.items
  const query = useQuery({
    queryKey: ['items', campaignId, search, counted],
    queryFn: () =>
      api.items(campaignId, {
        limit: GRID_ROW_CEILING,
        search: search || undefined,
        counted: counted || undefined,
      }),
  })

  // Changer de filtre change la liste sous les cases cochées. Garder la
  // sélection reviendrait à agir sur des lignes qui ne sont plus à l'écran.
  const filterBy = (next: boolean) => {
    setCounted(next)
    setSelected(new Set())
  }

  const exclude = useMutation({
    mutationFn: (exclusions: string[]) =>
      api.setItemExclusions(campaignId, [...selected], exclusions),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['items'] })
      setSelected(new Set())
      toast.success(
        `${result.updated} article(s) mis à jour`,
        result.unchanged > 0
          ? `${result.unchanged} l’étaient déjà.`
          : undefined,
      )
    },
    onError: (error) => showError(error, 'Exclusion impossible'),
  })

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 280 },
    {
      key: 'item_type',
      label: 'Type',
      width: 130,
      // Une liste plutôt qu'un texte : quatre valeurs possibles, dont personne
      // ne retient l'orthographe exacte. La déduction automatique n'y suffit
      // pas sur un petit référentiel, où quatre types sur sept lignes ne se
      // distinguent pas d'un identifiant.
      filter: 'choice',
      // Le filtre propose ce que la colonne affiche : cocher « COMPONENT »
      // quand le tableau montre « Composant » ne se rattache à rien de visible.
      choiceLabel: (value) => ITEM_TYPE_LABELS[value] ?? value,
      render: (row) => (
        <Badge tone="neutral">
          {ITEM_TYPE_LABELS[String(row.item_type)] ?? String(row.item_type)}
        </Badge>
      ),
      value: (row) => String(row.item_type),
    },
    { key: 'category', label: 'Catégorie', width: 130, filter: 'choice' },
    { key: 'program', label: 'Programme', width: 120, filter: 'choice' },
    { key: 'unit', label: 'Unité', width: 80, filter: 'choice' },
    {
      key: 'stdPrice',
      label: 'Prix standard',
      numeric: true,
      width: 140,
      render: (row) => moneyShort(Number(row.stdPrice ?? 0)),
      value: (row) => Number(row.stdPrice ?? 0),
      totalFormat: (total) => moneyShort(total),
    },
    {
      key: 'exclusions',
      label: 'Exclusion',
      width: 160,
      filter: 'choice',
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
      // Une ligne peut porter deux exclusions : la valeur filtrée est la
      // combinaison, et son libellé les nomme toutes les deux.
      choiceLabel: (value) =>
        value
          .split(',')
          .map((one) => EXCLUSION_LABELS[one] ?? one)
          .join(' + '),
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
        message="Trois niveaux d’exclusion : hors périmètre complet, hors GENERIQUE, ou ignoré dans les nomenclatures. Sélectionnez des lignes pour en exclure un lot d’un coup."
        flush
      >
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              exportTitle="Articles"
              campaignId={campaignId}
              getRowId={(row) => String(row.item_number)}
              selectable={editable}
              selected={selected}
              onSelectedChange={setSelected}
              searchPlaceholder="Filtrer par référence, désignation…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} article(s)
                  {counted ? ' stockés ou comptés' : ' au référentiel'}
                  {data.total > data.rows.length && ` — ${data.rows.length} affichés`}
                </span>
              }
              toolbar={
                <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
                  <StockedFilter value={counted} onChange={filterBy} />
                  {editable && selected.size > 0 && (
                    <ExclusionBulkAction
                      disabled={exclude.isPending}
                      onPick={(exclusions) => exclude.mutate(exclusions)}
                    />
                  )}
                  {search && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Icons.search size={13} />}
                      onClick={() => setSearch('')}
                    >
                      Réinitialiser la recherche serveur
                    </Button>
                  )}
                </div>
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

function ExclusionBulkAction({
  disabled,
  onPick,
}: {
  disabled: boolean
  onPick: (exclusions: string[]) => void
}) {
  return (
    <select
      className="input"
      style={{ width: 260 }}
      value=""
      disabled={disabled}
      aria-label="Exclusion de la sélection"
      onChange={(event) => {
        const choice = BULK_EXCLUSIONS.find((o) => o.value === event.target.value)
        if (choice) onPick(choice.scopes)
      }}
    >
      <option value="">Exclusion de la sélection…</option>
      {BULK_EXCLUSIONS.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
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
