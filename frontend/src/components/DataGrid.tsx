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
import { Badge, Button, EmptyState, Icons, SearchInput } from './ui'

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
  onRowsChange,
  onPaste,
  onDropFile,
  searchable = true,
  searchPlaceholder = 'Filtrer…',
  initialSort,
  rowClassName,
  dense = false,
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
  onRowsChange?: (rows: T[]) => void
  /** Receives a clipboard block pasted anywhere in the grid. */
  onPaste?: (text: string) => void
  onDropFile?: (file: File) => void
  searchable?: boolean
  searchPlaceholder?: string
  initialSort?: SortState
  rowClassName?: (row: T) => string | undefined
  dense?: boolean
}) {
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortState>(initialSort ?? null)
  const [dragOver, setDragOver] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // ---- filtering -----------------------------------------------------------
  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const needle = search.trim().toLowerCase()
    return rows.filter((row) =>
      columns.some((column) => {
        const value = defaultValue(row, column)
        return value !== null && String(value).toLowerCase().includes(needle)
      }),
    )
  }, [rows, search, columns])

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

  const showToolbar = searchable || toolbar || editable
  const selectedCount = selected?.size ?? 0

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
          {editable && (
            <Button size="sm" icon={<Icons.plus size={13} />} onClick={addRow}>
              Ajouter une ligne
            </Button>
          )}
          {toolbar}
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
                {columns.map((column) => (
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
                    {columns.map((column) => {
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
