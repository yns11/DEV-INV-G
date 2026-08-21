/**
 * The editable grid every import and every table screen is built on.
 *
 * It implements, in one place, the interaction the specification asks for:
 *
 *  - the grid is visible **before** any data exists, with the exact column
 *    headers the parser expects, so a user always knows what a file must contain;
 *  - a file can be dropped on it, or a block pasted from Excel into a cell
 *    (Ctrl-C / Ctrl-V) — including a multi-row, multi-column paste;
 *  - rows can be added, edited and deleted inline, with an explicit Save;
 *  - every column is sortable, and a single search box filters across all of them;
 *  - imported values and manually entered values are distinguished visually, and
 *    the source of each value is always shown.
 *
 * It is deliberately virtualisation-free: pages cap the row count server-side,
 * which keeps the component simple and the DOM small.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type ReactNode,
} from 'react'
import type { FieldSpec, GridContract } from '../lib/types'
import { DASH, SOURCE_LABELS } from '../lib/format'
import { download, downloads } from '../lib/api'
import { useHiddenColumns } from '../lib/columns'
import { Badge, Button, EmptyState, Icons, SearchInput, useErrorToast } from './ui'

/**
 * A grid row.
 *
 * `object` rather than `Record<string, unknown>` so that typed domain
 * interfaces (which have no index signature) can be passed directly; cell
 * access goes through `cellOf`, which narrows once instead of at every call.
 */
export type Row = object

export interface Column<T extends Row = Record<string, unknown>> {
  key: string
  label: string
  /** Right-aligned, tabular figures. */
  numeric?: boolean
  width?: number
  sortable?: boolean
  editable?: boolean
  /** Values offered when the cell is edited. */
  choices?: string[]
  render?: (row: T, index: number) => ReactNode
  /** Value used for sorting and searching when `render` returns a node. */
  value?: (row: T) => string | number | null
  help?: string
  /**
   * Filtre propre à cette colonne, en plus de la recherche libre.
   *
   * La recherche cherche partout à la fois : pratique pour retrouver une
   * référence, inutilisable pour « les composants en kilos dont le prix
   * dépasse cent euros ». Trois formes, déduites de la colonne quand elles ne
   * sont pas déclarées : une liste de valeurs, un intervalle sur une colonne
   * numérique, une recherche de texte.
   */
  filter?: 'choice' | 'range' | 'text' | false
  /**
   * Ce que la colonne totalise en pied de tableau.
   *
   * Somme par défaut sur les colonnes numériques. `false` pour celles dont la
   * somme ne veut rien dire — un pourcentage, un ratio, un identifiant qui se
   * trouve être un nombre.
   */
  total?: 'sum' | false
  /** Rendu du total, quand un nombre nu ne suffit pas (euros, unités). */
  totalFormat?: (total: number) => ReactNode
}

/** Build grid columns straight from a backend column contract. */
export function columnsFromContract(contract: GridContract): Column[] {
  return contract.fields.map((field: FieldSpec) => ({
    key: field.name,
    label: field.label,
    numeric: field.type === 'number' || field.type === 'integer',
    width: field.width,
    sortable: true,
    editable: true,
    choices: field.choices.length ? field.choices : undefined,
    help: [field.required ? 'Obligatoire' : null, field.help]
      .filter(Boolean)
      .join(' · '),
  }))
}

type SortState = { key: string; direction: 'asc' | 'desc' } | null

/** Ce qu'un filtre de colonne retient. Vide = colonne non filtrée. */
type ColumnFilter =
  | { kind: 'choice'; values: string[] }
  | { kind: 'range'; min: number | null; max: number | null }
  | { kind: 'text'; needle: string }

type Filters = Record<string, ColumnFilter>

/** Au-delà, une liste de valeurs cesse d'être un choix et devient un annuaire. */
const CHOICE_CEILING = 40

/**
 * Quelle forme de filtre convient à une colonne.
 *
 * Déduite quand elle n'est pas déclarée : une colonne numérique se borne, une
 * colonne **qui se répète** se choisit dans une liste, le reste se cherche au
 * texte.
 *
 * La répétition est ce qui distingue une catégorie d'un identifiant, et le seul
 * plafond de cardinalité ne suffit pas à les séparer : sur six articles, six
 * références distinctes passaient sous les quarante valeurs et « Article »
 * s'affichait en liste déroulante — c'est-à-dire l'annuaire qu'on voulait
 * éviter. Exiger que chaque valeur revienne au moins deux fois en moyenne
 * range « Type » et « Unité » dans les listes, et les références dans le texte.
 */
function filterKind<T extends Row>(
  column: Column<T>, distinct: number, rowCount: number,
): 'choice' | 'range' | 'text' {
  if (column.filter) return column.filter
  if (column.numeric) return 'range'
  if (column.choices?.length) return 'choice'
  if (distinct > 0 && distinct <= CHOICE_CEILING && distinct * 2 <= rowCount) {
    return 'choice'
  }
  return 'text'
}


/** Untyped read of one cell, used by sorting, filtering and default rendering. */
function cellOf(row: Row, key: string): unknown {
  return (row as Record<string, unknown>)[key]
}

function defaultValue<T extends Row>(row: T, column: Column<T>): string | number | null {
  const raw = column.value ? column.value(row) : cellOf(row, column.key)
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number' || typeof raw === 'string') return raw
  if (typeof raw === 'boolean') return raw ? 1 : 0
  return String(raw)
}

export function DataGrid<T extends Row>({
  columns,
  rows,
  getRowId,
  emptyTitle = 'Aucune ligne',
  emptyBody,
  emptyAction,
  toolbar,
  footer,
  maxHeight,
  selectable = false,
  selected,
  onSelectedChange,
  editable = false,
  canAdd = true,
  onRowsChange,
  onPaste,
  onDropFile,
  searchable = true,
  searchPlaceholder = 'Filtrer…',
  initialSort,
  rowClassName,
  dense = false,
  exportTitle,
  campaignId,
}: {
  columns: Column<T>[]
  rows: T[]
  getRowId: (row: T, index: number) => string
  emptyTitle?: string
  emptyBody?: ReactNode
  emptyAction?: ReactNode
  toolbar?: ReactNode
  footer?: ReactNode
  maxHeight?: number
  selectable?: boolean
  selected?: Set<string>
  onSelectedChange?: (selected: Set<string>) => void
  editable?: boolean
  /**
   * Whether a blank row may be added.
   *
   * Off when a new row would have nowhere to be saved — a flat list spanning
   * several sheets, before one is chosen. Offering the button anyway produces
   * a row somebody fills in and that is then silently dropped on save, which is
   * worse than not offering it.
   */
  canAdd?: boolean
  onRowsChange?: (rows: T[]) => void
  /** Receives a clipboard block pasted anywhere in the grid. */
  onPaste?: (text: string) => void
  onDropFile?: (file: File) => void
  searchable?: boolean
  searchPlaceholder?: string
  initialSort?: SortState
  rowClassName?: (row: T) => string | undefined
  dense?: boolean
  /** Name of the exported workbook's sheet. Both this and `campaignId` are
   *  required for the export button to appear — without a name the file would
   *  be called « export » on every screen. */
  exportTitle?: string
  campaignId?: string
}) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState>(initialSort ?? null)
  const [dragOver, setDragOver] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // ---- colonnes visibles ---------------------------------------------------
  //
  // Le nom de l'export sert d'identité à la grille : c'est le seul libellé
  // stable qu'elle porte déjà, et il est unique par écran. Sans lui le choix
  // vaut pour la session, ce qui reste mieux que rien.
  const { hidden, toggle: toggleColumn, reset: showAllColumns } =
    useHiddenColumns(exportTitle ?? null)
  const visible = useMemo(
    () => columns.filter((column) => !hidden.has(column.key)),
    [columns, hidden],
  )

  // ---- filtres par colonne -------------------------------------------------
  const [filters, setFilters] = useState<Filters>({})

  /** Les valeurs distinctes d'une colonne, pour alimenter une liste de choix. */
  const distinct = useMemo(() => {
    const out: Record<string, string[]> = {}
    for (const column of columns) {
      if (column.filter === false) continue
      const seen = new Set<string>()
      for (const row of rows) {
        const value = defaultValue(row, column)
        if (value === null || value === '') continue
        seen.add(String(value))
        if (seen.size > CHOICE_CEILING) break
      }
      out[column.key] = [...seen].sort((a, b) =>
        a.localeCompare(b, 'fr', { numeric: true }),
      )
    }
    return out
  }, [rows, columns])

  const filterable = useMemo(
    () =>
      visible
        .filter((column) => column.filter !== false && column.label)
        .map((column) => ({
          column,
          kind: filterKind(column, distinct[column.key]?.length ?? 0, rows.length),
        })),
    [visible, distinct, rows.length],
  )

  const setFilter = (key: string, filter: ColumnFilter | null) => {
    setFilters((current) => {
      const next = { ...current }
      if (filter === null) delete next[key]
      else next[key] = filter
      return next
    })
  }

  // ---- filtering -----------------------------------------------------------
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const active = Object.entries(filters)
    if (!needle && active.length === 0) return rows
    const byKey = new Map(columns.map((column) => [column.key, column]))
    return rows.filter((row) => {
      if (
        needle &&
        !columns.some((column) => {
          const value = defaultValue(row, column)
          return value !== null && String(value).toLowerCase().includes(needle)
        })
      ) {
        return false
      }
      return active.every(([key, filter]) => {
        const column = byKey.get(key)
        if (!column) return true
        const value = defaultValue(row, column)
        if (filter.kind === 'choice') {
          return filter.values.includes(value === null ? '' : String(value))
        }
        if (filter.kind === 'range') {
          const numeric = typeof value === 'number' ? value : Number(value)
          if (!Number.isFinite(numeric)) return false
          if (filter.min !== null && numeric < filter.min) return false
          if (filter.max !== null && numeric > filter.max) return false
          return true
        }
        return String(value ?? '').toLowerCase().includes(filter.needle.toLowerCase())
      })
    })
  }, [rows, search, columns, filters])

  // ---- sorting -------------------------------------------------------------
  const sorted = useMemo(() => {
    if (!sort) return filtered
    const column = columns.find((c) => c.key === sort.key)
    if (!column) return filtered
    const factor = sort.direction === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => {
      const left = defaultValue(a, column)
      const right = defaultValue(b, column)
      // Missing values always sort last, whichever direction is active: a gap
      // is not "the smallest value".
      if (left === null && right === null) return 0
      if (left === null) return 1
      if (right === null) return -1
      if (typeof left === 'number' && typeof right === 'number') {
        return (left - right) * factor
      }
      return String(left).localeCompare(String(right), 'fr', { numeric: true }) * factor
    })
  }, [filtered, sort, columns])

  // ---- totaux --------------------------------------------------------------
  //
  // Sur les lignes **affichées**, pas sur toutes : un total qui ne bougerait
  // pas quand on filtre ne répondrait à aucune question qu'on se pose en
  // filtrant. Les colonnes numériques totalisent par défaut ; celles dont la
  // somme n'a pas de sens — un taux, un ratio — se retirent par `total: false`.
  const totals = useMemo(() => {
    const out: Array<{ key: string; sum: number }> = []
    for (const column of visible) {
      const wanted = column.total ?? (column.numeric ? 'sum' : false)
      if (wanted !== 'sum') continue
      let sum = 0
      let seen = false
      for (const row of sorted) {
        const value = defaultValue(row, column)
        const numeric = typeof value === 'number' ? value : Number(value)
        if (!Number.isFinite(numeric)) continue
        sum += numeric
        seen = true
      }
      if (seen) out.push({ key: column.key, sum })
    }
    return out
  }, [sorted, visible])

  const toggleSort = (key: string) => {
    setSort((current) => {
      if (current?.key !== key) return { key, direction: 'asc' }
      if (current.direction === 'asc') return { key, direction: 'desc' }
      return null
    })
  }

  // ---- selection -----------------------------------------------------------
  const allSelected =
    selectable && sorted.length > 0 &&
    sorted.every((row, index) => selected?.has(getRowId(row, index)))

  const toggleAll = () => {
    if (!onSelectedChange) return
    if (allSelected) {
      onSelectedChange(new Set())
    } else {
      onSelectedChange(new Set(sorted.map((row, index) => getRowId(row, index))))
    }
  }

  const toggleOne = (id: string) => {
    if (!onSelectedChange || !selected) return
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    onSelectedChange(next)
  }

  // ---- editing -------------------------------------------------------------
  const updateCell = useCallback(
    (rowIndex: number, key: string, value: string) => {
      if (!onRowsChange) return
      const target = sorted[rowIndex]
      if (!target) return
      const originalIndex = rows.indexOf(target)
      if (originalIndex < 0) return
      const next = [...rows]
      next[originalIndex] = { ...target, [key]: value === '' ? null : value } as unknown as T
      onRowsChange(next)
    },
    [rows, sorted, onRowsChange],
  )

  const addRow = () => {
    if (!onRowsChange) return
    const blank = Object.fromEntries(columns.map((c) => [c.key, null])) as T
    onRowsChange([...rows, blank])
  }

  const removeRow = (rowIndex: number) => {
    if (!onRowsChange) return
    const target = sorted[rowIndex]
    if (!target) return
    onRowsChange(rows.filter((row) => row !== target))
  }

  // ---- paste ---------------------------------------------------------------
  const handlePaste = useCallback(
    (event: ClipboardEvent<HTMLDivElement>) => {
      if (!onPaste) return
      const text = event.clipboardData.getData('text/plain')
      // A single cell with no tab or newline is an ordinary edit, not a block
      // paste — let the input handle it.
      if (!text || (!text.includes('\t') && !text.includes('\n'))) return
      event.preventDefault()
      onPaste(text)
    },
    [onPaste],
  )

  // ---- drag & drop ---------------------------------------------------------
  useEffect(() => {
    const node = containerRef.current
    if (!node || !onDropFile) return
    const prevent = (event: DragEvent) => {
      event.preventDefault()
      event.stopPropagation()
    }
    const onEnter = (event: DragEvent) => {
      prevent(event)
      setDragOver(true)
    }
    const onLeave = (event: DragEvent) => {
      prevent(event)
      if (event.target === node) setDragOver(false)
    }
    const onDrop = (event: DragEvent) => {
      prevent(event)
      setDragOver(false)
      const file = event.dataTransfer?.files?.[0]
      if (file) onDropFile(file)
    }
    node.addEventListener('dragenter', onEnter)
    node.addEventListener('dragover', prevent)
    node.addEventListener('dragleave', onLeave)
    node.addEventListener('drop', onDrop)
    return () => {
      node.removeEventListener('dragenter', onEnter)
      node.removeEventListener('dragover', prevent)
      node.removeEventListener('dragleave', onLeave)
      node.removeEventListener('drop', onDrop)
    }
  }, [onDropFile])

  const [showFilters, setShowFilters] = useState(false)
  const activeFilters = Object.keys(filters).length
  const showToolbar =
    searchable || toolbar || editable || columns.length > 1 || filterable.length > 0
  const selectedCount = selected?.size ?? 0

  // ---- export --------------------------------------------------------------
  //
  // What leaves is what is on screen: the search box, the sort and the
  // selection have already been applied to `sorted`, and re-deriving the rows
  // server-side would give a file that does not match the table it came from.
  // With nothing selected the whole filtered table goes, which is what somebody
  // who filtered and then clicked "Excel" is asking for.
  const exportable = exportTitle !== undefined && campaignId !== undefined
  const [exporting, setExporting] = useState(false)
  const showError = useErrorToast()
  const exportRows = async () => {
    if (!exportable) return
    const chosen =
      selectedCount > 0
        ? sorted.filter((row, index) => selected?.has(getRowId(row, index)))
        : sorted
    setExporting(true)
    try {
      await download(downloads.table(campaignId), {
        title: exportTitle,
        columns: visible
          .filter((c) => c.label)
          .map((c) => ({ key: c.key, label: c.label })),
        rows: chosen.map((row) =>
          Object.fromEntries(
            visible
              .filter((c) => c.label)
              // La valeur, pas ce qui est peint : une cellule rendue en badge ou
              // en deux lignes a derrière elle un nombre, et c'est lui qu'un
              // tableur peut trier et sommer.
              .map((c) => [c.key, defaultValue(row, c)]),
          ),
        ),
      })
    } catch (error) {
      showError(error, 'Export impossible')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div
      ref={containerRef}
      onPaste={handlePaste}
      style={
        dragOver
          ? { outline: '2px dashed var(--accent)', outlineOffset: -4, borderRadius: 'var(--radius-lg)' }
          : undefined
      }
    >
      {showToolbar && (
        <div className="table-toolbar">
          {searchable && (
            <SearchInput value={search} onChange={setSearch} placeholder={searchPlaceholder} />
          )}
          {search && (
            <Button
              variant="ghost"
              size="sm"
              icon={<Icons.x size={13} />}
              onClick={() => setSearch('')}
            >
              Effacer
            </Button>
          )}
          {sort && (
            <button className="chip chip--active" onClick={() => setSort(null)}>
              Tri : {columns.find((c) => c.key === sort.key)?.label}{' '}
              {sort.direction === 'asc' ? '↑' : '↓'}
              <span className="chip__remove">
                <Icons.x size={11} />
              </span>
            </button>
          )}
          {selectedCount > 0 && (
            <Badge tone="accent">{selectedCount} sélectionnée(s)</Badge>
          )}
          <span className="spacer" />
          {exportable && sorted.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Icons.download size={13} />}
              disabled={exporting}
              onClick={() => void exportRows()}
              title={
                selectedCount > 0
                  ? `Exporter les ${selectedCount} ligne(s) sélectionnée(s)`
                  : `Exporter les ${sorted.length} ligne(s) affichée(s)`
              }
            >
              {exporting
                ? 'Export…'
                : selectedCount > 0
                  ? `Excel (${selectedCount})`
                  : 'Excel'}
            </Button>
          )}
          {editable && canAdd && (
            <Button size="sm" icon={<Icons.plus size={13} />} onClick={addRow}>
              Ajouter une ligne
            </Button>
          )}
          {filterable.length > 0 && (
            <Button
              size="sm"
              variant={activeFilters > 0 ? 'primary' : 'ghost'}
              icon={<Icons.filter size={13} />}
              onClick={() => setShowFilters((open) => !open)}
              title="Filtrer colonne par colonne"
            >
              {activeFilters > 0 ? `Filtres (${activeFilters})` : 'Filtres'}
            </Button>
          )}
          {columns.length > 1 && (
            <ColumnPicker
              columns={columns.filter((column) => column.label)}
              hidden={hidden}
              onToggle={toggleColumn}
              onReset={showAllColumns}
            />
          )}
          {toolbar}
        </div>
      )}

      {showFilters && filterable.length > 0 && (
        <div className="filter-bar">
          {filterable.map(({ column, kind }) => (
            <ColumnFilterField
              key={column.key}
              column={column}
              kind={kind}
              choices={distinct[column.key] ?? []}
              value={filters[column.key] ?? null}
              onChange={(filter) => setFilter(column.key, filter)}
            />
          ))}
          {activeFilters > 0 && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Icons.x size={13} />}
              onClick={() => setFilters({})}
            >
              Tout effacer
            </Button>
          )}
        </div>
      )}

      {sorted.length === 0 ? (
        <EmptyState title={search ? 'Aucun résultat' : emptyTitle} action={emptyAction}>
          {search
            ? `Aucune ligne ne correspond à « ${search} ».`
            : emptyBody}
        </EmptyState>
      ) : (
        <div
          className="table-wrap"
          style={maxHeight ? ({ '--table-max-height': `${maxHeight}px` } as React.CSSProperties) : undefined}
        >
          <table className="data">
            <thead>
              <tr>
                {selectable && (
                  <th style={{ width: 36 }}>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="Tout sélectionner"
                    />
                  </th>
                )}
                {visible.map((column) => (
                  <th
                    key={column.key}
                    className={[
                      column.numeric ? 'num' : '',
                      column.sortable !== false ? 'sortable' : '',
                    ].join(' ')}
                    style={column.width ? { minWidth: column.width } : undefined}
                    onClick={() => column.sortable !== false && toggleSort(column.key)}
                    title={column.help}
                    aria-sort={
                      sort?.key === column.key
                        ? sort.direction === 'asc'
                          ? 'ascending'
                          : 'descending'
                        : undefined
                    }
                  >
                    {column.label}
                    {sort?.key === column.key && (
                      <span aria-hidden="true"> {sort.direction === 'asc' ? '↑' : '↓'}</span>
                    )}
                  </th>
                ))}
                {editable && <th style={{ width: 44 }} />}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, index) => {
                const id = getRowId(row, index)
                return (
                  <tr
                    key={id}
                    data-selected={selected?.has(id) || undefined}
                    className={rowClassName?.(row)}
                    style={dense ? { fontSize: 'var(--text-xs)' } : undefined}
                  >
                    {selectable && (
                      <td>
                        <input
                          type="checkbox"
                          checked={selected?.has(id) ?? false}
                          onChange={() => toggleOne(id)}
                          aria-label={`Sélectionner ${id}`}
                        />
                      </td>
                    )}
                    {visible.map((column) => {
                      if (column.render) {
                        return (
                          <td key={column.key} className={column.numeric ? 'num' : undefined}>
                            {column.render(row, index)}
                          </td>
                        )
                      }
                                      const raw = cellOf(row, column.key)
                      const text = raw === null || raw === undefined || raw === '' ? '' : String(raw)
                      if (editable && column.editable !== false) {
                        return (
                          <td key={column.key} className="editable">
                            {column.choices ? (
                              <select
                                value={text}
                                onChange={(e) => updateCell(index, column.key, e.target.value)}
                                aria-label={column.label}
                              >
                                <option value="" />
                                {column.choices.map((choice) => (
                                  <option key={choice} value={choice}>
                                    {choice}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input
                                className={column.numeric ? 'num' : undefined}
                                value={text}
                                inputMode={column.numeric ? 'decimal' : undefined}
                                onChange={(e) => updateCell(index, column.key, e.target.value)}
                                aria-label={column.label}
                              />
                            )}
                          </td>
                        )
                      }
                      return (
                        <td key={column.key} className={column.numeric ? 'num' : undefined}>
                          {text || <span className="subtle">{DASH}</span>}
                        </td>
                      )
                    })}
                    {editable && (
                      <td>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon={<Icons.trash size={13} />}
                          onClick={() => removeRow(index)}
                          aria-label="Supprimer la ligne"
                        />
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
            {totals.length > 0 && (
              <tfoot>
                <tr>
                  {selectable && <td />}
                  {visible.map((column, index) => {
                    const total = totals.find((t) => t.key === column.key)
                    // Le premier libellé libre porte le mot : une ligne de
                    // chiffres en gras sous le tableau ne dit pas d'elle-même
                    // ce qu'elle additionne.
                    if (!total && index === 0) {
                      return (
                        <td key={column.key} className="subtle">
                          Total
                        </td>
                      )
                    }
                    return (
                      <td key={column.key} className={column.numeric ? 'num' : ''}>
                        {total
                          ? column.totalFormat
                            ? column.totalFormat(total.sum)
                            : total.sum.toLocaleString('fr-FR', {
                                maximumFractionDigits: 2,
                              })
                          : null}
                      </td>
                    )
                  })}
                  {editable && <td />}
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}

      <div className="table-foot">
        <span className="num">
          {sorted.length.toLocaleString('fr-FR')}
          {sorted.length !== rows.length && ` / ${rows.length.toLocaleString('fr-FR')}`} ligne(s)
        </span>
        {onPaste && (
          <span className="subtle">
            Astuce : copiez un bloc depuis Excel et collez-le (Ctrl+V) dans la grille.
          </span>
        )}
        <span className="spacer" />
        {footer}
      </div>
    </div>
  )
}

/**
 * Le petit bouton qui décide des colonnes affichées.
 *
 * Une grille de douze colonnes sert douze usages ; aucune session n'en a besoin
 * de douze à la fois. Masquer n'est pas supprimer : la colonne reste dans
 * l'export tant qu'elle est affichée, et se rappelle d'une visite à l'autre.
 *
 * La dernière colonne visible ne se masque pas — une grille sans colonne est un
 * écran vide dont on ne devine plus comment sortir.
 */
function ColumnPicker<T extends Row>({
  columns,
  hidden,
  onToggle,
  onReset,
}: {
  columns: Column<T>[]
  hidden: ReadonlySet<string>
  onToggle: (key: string, value: boolean) => void
  onReset: () => void
}) {
  const [open, setOpen] = useState(false)
  const shownCount = columns.filter((c) => !hidden.has(c.key)).length
  return (
    <span className="colpicker">
      <Button
        size="sm"
        variant={hidden.size > 0 ? 'primary' : 'ghost'}
        icon={<Icons.sliders size={13} />}
        onClick={() => setOpen((value) => !value)}
        title="Choisir les colonnes affichées"
      >
        {hidden.size > 0 ? `${shownCount}/${columns.length}` : ''}
      </Button>
      {open && (
        <>
          <button
            className="colpicker__scrim"
            aria-label="Fermer"
            onClick={() => setOpen(false)}
          />
          <div className="colpicker__menu" role="group" aria-label="Colonnes affichées">
            {columns.map((column) => {
              const shown = !hidden.has(column.key)
              return (
                <label key={column.key} className="colpicker__row">
                  <input
                    type="checkbox"
                    checked={shown}
                    disabled={shown && shownCount === 1}
                    onChange={(event) => onToggle(column.key, !event.target.checked)}
                  />
                  {column.label || column.key}
                </label>
              )
            })}
            {hidden.size > 0 && (
              <button className="colpicker__reset" onClick={onReset}>
                Tout afficher
              </button>
            )}
          </div>
        </>
      )}
    </span>
  )
}

/** Un filtre de colonne, dans la forme que sa nature appelle. */
function ColumnFilterField<T extends Row>({
  column,
  kind,
  choices,
  value,
  onChange,
}: {
  column: Column<T>
  kind: 'choice' | 'range' | 'text'
  choices: string[]
  value: ColumnFilter | null
  onChange: (filter: ColumnFilter | null) => void
}) {
  if (kind === 'range') {
    const range = value?.kind === 'range' ? value : { min: null, max: null }
    const update = (part: 'min' | 'max', raw: string) => {
      const next = {
        kind: 'range' as const,
        min: range.min,
        max: range.max,
        [part]: raw === '' ? null : Number(raw),
      }
      onChange(next.min === null && next.max === null ? null : next)
    }
    return (
      <span className="filter-field">
        <span className="filter-field__label">{column.label}</span>
        <input
          className="input input--mini num"
          type="number"
          placeholder="min"
          value={range.min ?? ''}
          onChange={(event) => update('min', event.target.value)}
        />
        <input
          className="input input--mini num"
          type="number"
          placeholder="max"
          value={range.max ?? ''}
          onChange={(event) => update('max', event.target.value)}
        />
      </span>
    )
  }

  if (kind === 'choice') {
    const picked = value?.kind === 'choice' ? value.values : []
    return (
      <span className="filter-field">
        <span className="filter-field__label">{column.label}</span>
        <select
          className="input input--mini"
          multiple
          size={Math.min(choices.length, 3)}
          value={picked}
          onChange={(event) => {
            const values = [...event.target.selectedOptions].map((o) => o.value)
            onChange(values.length ? { kind: 'choice', values } : null)
          }}
        >
          {choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      </span>
    )
  }

  const needle = value?.kind === 'text' ? value.needle : ''
  return (
    <span className="filter-field">
      <span className="filter-field__label">{column.label}</span>
      <input
        className="input input--mini"
        value={needle}
        placeholder="contient…"
        onChange={(event) =>
          onChange(event.target.value ? { kind: 'text', needle: event.target.value } : null)
        }
      />
    </span>
  )
}

/**
 * The provenance chip required by the specification: an imported value, a
 * manual correction and an AI reading must never look the same.
 */
export function SourceBadge({
  source,
  overridden = false,
  confidence,
}: {
  source: string
  overridden?: boolean
  confidence?: number | null
}) {
  const tone =
    source === 'MANUAL' || source === 'ARBITRATION'
      ? 'accent'
      : source === 'SCAN_AI'
        ? 'warning'
        : source === 'SYSTEM' || source === 'CONSOLIDATION'
          ? 'neutral'
          : 'info'
  return (
    <span className="row" style={{ gap: 'var(--space-1)' }}>
      <Badge tone={tone}>{SOURCE_LABELS[source] ?? source}</Badge>
      {overridden && <Badge tone="accent">corrigé</Badge>}
      {confidence !== null && confidence !== undefined && source === 'SCAN_AI' && (
        <Badge tone={confidence < 0.75 ? 'danger' : 'neutral'}>
          {Math.round(confidence * 100)} %
        </Badge>
      )}
    </span>
  )
}
