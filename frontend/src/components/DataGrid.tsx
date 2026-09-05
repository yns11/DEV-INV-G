/**
 * The editable grid every import and every table screen is built on.
 *
 * It implements, in one place, the interaction the specification asks for:
 *
 *  - une grille sans ligne montre ce qui manque et le geste qui l'obtient,
 *    plutôt qu'un tableau nu : les colonnes attendues d'un fichier se lisent
 *    dans le panneau d'import, qui les tient du contrat côté serveur ;
 *  - a file can be dropped on it, or a block pasted from Excel into a cell
 *    (Ctrl-C / Ctrl-V) — including a multi-row, multi-column paste;
 *  - rows can be added, edited and deleted inline, with an explicit Save;
 *  - every column is sortable, and a single search box filters across all of them;
 *  - imported values and manually entered values are distinguished visually, and
 *    the source of each value is always shown.
 *
 * Au-delà de quelques centaines de lignes, seules celles qu'on voit entrent
 * dans le DOM. Voir `VIRTUAL_FROM` : en dessous du seuil rien ne change, et
 * c'est voulu — la fenêtre coûte une hypothèse (des lignes de hauteur égale)
 * qu'il est inutile de payer sur une grille de quarante lignes.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
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
  /**
   * La colonne a-t-elle un sens **sur cette ligne** ?
   *
   * Une cellule sans objet ne montre rien : ni valeur, ni champ de saisie, ni
   * tiret. Sur une feuille de comptage, un intertitre et une ligne vide ne
   * portent ni référence, ni quantité, ni unité, ni provenance — les afficher
   * mettait « Bord de ligne · 0 · PCE · Saisie manuelle » en face d'un titre,
   * et offrir un champ de saisie invitait à en faire un article.
   *
   * Rendue vide plutôt qu'absente : la ligne garde ses colonnes, donc son
   * alignement avec les lignes d'articles au-dessus et au-dessous.
   */
  appliesTo?: (row: T) => boolean
  /** Values offered when the cell is edited. */
  choices?: string[]
  render?: (row: T, index: number) => ReactNode
  /** Value used for sorting and searching when `render` returns a node. */
  value?: (row: T) => string | number | null
  /**
   * Les étiquettes d'une colonne qui en porte **plusieurs par ligne**.
   *
   * « Signalements » en affiche jusqu'à quatre côte à côte : au-delà des
   * seuils, hors ERP, non compté, la cause retenue. Avec un `value` unique, il
   * n'y avait que deux issues, toutes deux fausses. Sans rien — le cas jusqu'ici
   * — le filtre ne lisait aucune valeur et ne proposait que « (vide) », sur une
   * colonne pourtant remplie de badges. Avec une chaîne jointe, chaque
   * *combinaison* serait devenue une entrée : « au-delà des seuils · hors ERP »
   * à côté de « au-delà des seuils », et cocher la seconde n'aurait pas montré
   * les lignes de la première.
   *
   * Déclarée ici, la facette liste les étiquettes une à une et une ligne est
   * retenue dès qu'elle en porte **une** de celles cochées. C'est ce que
   * « montre-moi les hors ERP » veut dire.
   *
   * Rend une liste vide pour une ligne sans étiquette : elle se retrouve alors
   * sous « (vide) », comme partout ailleurs.
   */
  tags?: (row: T) => string[]
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
   * Comment nommer une valeur dans la liste de filtres.
   *
   * Une colonne peut afficher « Composant » et valoir `COMPONENT` — c'est le
   * code qui trie, s'exporte et se compare, et le libellé qui se lit. Sans
   * cette correspondance, le filtre proposait de cocher des valeurs
   * introuvables dans le tableau, et la recherche du panneau ne trouvait pas
   * ce que l'utilisateur avait sous les yeux.
   */
  choiceLabel?: (value: string) => string
  /**
   * Épingle la colonne au bord droit du tableau.
   *
   * Réservé à la colonne d'actions d'une grille large : sans cela, le bouton
   * qui fait avancer la ligne se retrouve hors écran dès qu'il y a dix
   * colonnes, et il faut faire défiler horizontalement à chaque ligne pour
   * l'atteindre.
   */
  sticky?: 'right'
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
  return contract.fields.map((field: FieldSpec) => {
    // Le contrat porte le code **et** son libellé. Sans reprendre le second,
    // la grille d'import offrait de choisir « LINE_SIDE » dans une liste
    // déroulante, et l'export Excel écrivait le même code : deux endroits où
    // l'utilisateur lisait du vocabulaire interne.
    const labels = field.choiceLabels ?? {}
    return {
      key: field.name,
      label: field.label,
      numeric: field.type === 'number' || field.type === 'integer',
      width: field.width,
      sortable: true,
      editable: true,
      choices: field.choices.length ? field.choices : undefined,
      choiceLabel: Object.keys(labels).length
        ? (value: string) => labels[value] ?? value
        : undefined,
      help: [field.required ? 'Obligatoire' : null, field.help]
        .filter(Boolean)
        .join(' · '),
    }
  })
}

type SortState = { key: string; direction: 'asc' | 'desc' } | null

/** Ce qu'un filtre de colonne retient. Vide = colonne non filtrée. */
type ColumnFilter =
  | { kind: 'choice'; values: string[] }
  | { kind: 'range'; min: number | null; max: number | null }
  | { kind: 'text'; needle: string }
  /**
   * L'absence de valeur, ou sa présence.
   *
   * « Les articles dans le périmètre » se lit dans la colonne Exclusion comme
   * un trait, « les lignes sans commentaire » comme une case vide : deux
   * questions courantes qu'aucun des trois autres filtres ne pose. Une liste de
   * valeurs répond par sa case *(vide)* ; une colonne texte, dont les valeurs
   * ne se listent pas, a besoin de ce filtre-ci.
   */
  | { kind: 'blank'; empty: boolean }

type Filters = Record<string, ColumnFilter>

/** Une valeur possible d'une colonne, telle qu'elle se lit, et son poids. */
interface Choice {
  /** Ce qui filtre — le code, jamais le libellé. */
  value: string
  /** Ce qui s'affiche et se cherche. */
  label: string
  count: number
}

/** Comment se nomme l'absence de valeur, partout où elle se coche ou se résume. */
const BLANK_LABEL = '(vide)'

/** Au-delà, une liste de valeurs cesse d'être un choix et devient un annuaire. */
const CHOICE_CEILING = 40

/**
 * À partir de combien de lignes seules celles qu'on voit entrent dans le DOM.
 *
 * Une ligne de dix colonnes fait onze éléments. À vingt mille lignes cela fait
 * deux cent mille éléments à construire, à styler et à garder en mémoire, pour
 * en montrer trente : le navigateur passe plusieurs secondes figé, et le
 * défilement reste saccadé ensuite. C'est le référentiel articles d'une
 * campagne réelle.
 *
 * Le seuil n'est pas zéro, et c'est délibéré. La fenêtre coûte une hypothèse —
 * des lignes de hauteur égale — et une conséquence : la recherche du navigateur
 * (Ctrl+F) ne voit plus que les lignes rendues. Sur quarante lignes, ce prix
 * n'achète rien. Au-delà du seuil il est déjà payé autrement, puisqu'une page
 * figée ne se cherche pas non plus ; et la grille a sa propre recherche, qui
 * elle porte sur l'ensemble.
 */
const VIRTUAL_FROM = 300

/**
 * Lignes rendues au-delà de la fenêtre visible, de chaque côté.
 *
 * Sans marge, un défilement rapide montre du blanc le temps d'un rendu. Douze
 * lignes coûtent quelques dizaines d'éléments et suppriment le clignotement.
 */
const OVERSCAN = 12

/** Hauteur d'une ligne avant que la première n'ait été mesurée, en pixels. */
const ROW_HEIGHT_GUESS = 37

/**
 * Hauteur donnée au cadre défilant quand l'écran n'en impose pas.
 *
 * La fenêtre a besoin d'un conteneur qui défile lui-même : si c'est la page
 * entière qui défile, le cadre ne bouge jamais et la fenêtre reste collée en
 * haut. La plupart des écrans passent déjà `maxHeight` ; ce repli ne concerne
 * que les grilles qui n'en donnent pas — et seulement au-delà du seuil.
 */
const VIRTUAL_MAX_HEIGHT = 620

/**
 * Quelles lignes rendre, et quelle hauteur laisser au-dessus et au-dessous.
 *
 * Extraite du composant parce que c'est la seule partie qui peut être fausse
 * sans qu'on le voie : une erreur d'un cran ici affiche les bonnes lignes au
 * mauvais endroit, ou laisse un blanc en fin de liste — deux défauts qu'on
 * attribue au navigateur avant de les attribuer au calcul.
 *
 * Les deux cales portent la hauteur des lignes absentes. Sans elles, vingt
 * mille lignes défileraient sur la hauteur de trente : la barre de défilement
 * mentirait sur la longueur du tableau, et il n'y aurait plus rien à faire
 * défiler pour atteindre la fin.
 */
export function windowOf({
  total,
  scrollTop,
  rowHeight,
  viewport,
  overscan = OVERSCAN,
}: {
  total: number
  scrollTop: number
  rowHeight: number
  viewport: number
  overscan?: number
}): { start: number; end: number; before: number; after: number } {
  // Une hauteur de ligne nulle viendrait d'une mesure faite avant le premier
  // rendu ; diviser par elle donnerait l'infini, et la fenêtre serait vide.
  const height = rowHeight > 0 ? rowHeight : ROW_HEIGHT_GUESS
  const start = Math.max(0, Math.floor(scrollTop / height) - overscan)
  const end = Math.min(total, Math.ceil((scrollTop + viewport) / height) + overscan)
  return {
    start,
    end: Math.max(start, end),
    before: start * height,
    after: Math.max(0, total - Math.max(start, end)) * height,
  }
}

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
  if (column.tags) return 'choice'
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

/**
 * Les étiquettes d'une ligne pour cette colonne, ou `null` si elle n'en a pas.
 *
 * `null` et non `[]` : l'absence d'étiquette doit se ranger sous « (vide) »
 * comme une cellule vide, et un tableau vide se serait fondu dans le décompte
 * sans jamais apparaître dans la liste.
 */
function tagsOf<T extends Row>(row: T, column: Column<T>): string[] | null {
  if (!column.tags) return null
  const found = column.tags(row).map((tag) => tag.trim()).filter(Boolean)
  return found.length > 0 ? found : null
}

function defaultValue<T extends Row>(row: T, column: Column<T>): string | number | null {
  const raw = column.value ? column.value(row) : cellOf(row, column.key)
  if (raw === null || raw === undefined) return null
  if (typeof raw === 'number' || typeof raw === 'string') return raw
  if (typeof raw === 'boolean') return raw ? 1 : 0
  return String(raw)
}

/**
 * Ce qu'une cellule vaut dans un fichier exporté.
 *
 * La valeur brute, sauf sur une colonne qui sait nommer ses codes : là, le
 * libellé. Un tableur ne trie ni ne somme `LINE_SIDE`, et personne ne le lit.
 */
export function exportValue<T extends Row>(
  row: T,
  column: Column<T>,
): string | number | null {
  const value = defaultValue(row, column)
  // `typeof` restreint le type — un libellé ne se demande que sur du texte.
  // La cellule vide, elle, est un choix : une colonne peut nommer l'absence
  // pour que le filtre la propose, et ce nom-là n'a rien à faire dans un
  // fichier.
  if (!column.choiceLabel || typeof value !== 'string' || value === '') return value
  return column.choiceLabel(value)
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

  /**
   * Les valeurs distinctes d'une colonne, **avec leur nombre de lignes**.
   *
   * Le compte n'est pas décoratif : « TERMINÉE 412 / EN COURS 3 » dit d'un coup
   * d'œil où est la matière, et évite de cocher une valeur pour découvrir
   * qu'elle ne ramène rien. C'est aussi ce qui permet de trier les valeurs les
   * plus représentées en tête d'une liste de quarante.
   */
  const distinct = useMemo(() => {
    const out: Record<string, Choice[]> = {}
    for (const column of columns) {
      if (column.filter === false) continue
      const tally = new Map<string, number>()
      for (const row of rows) {
        // Une colonne à étiquettes en compte plusieurs par ligne : c'est
        // l'étiquette qui est la valeur, jamais la combinaison. Sans quoi
        // « hors ERP » et « au-delà des seuils · hors ERP » seraient deux
        // entrées, et cocher la première laisserait la seconde de côté.
        const tags = tagsOf(row, column)
        if (tags) {
          for (const tag of tags) tally.set(tag, (tally.get(tag) ?? 0) + 1)
          if (tally.size > CHOICE_CEILING) break
          continue
        }
        const value = column.tags ? null : defaultValue(row, column)
        // La case vide est une valeur comme une autre : c'est celle qui répond
        // à « les articles dans le périmètre » ou « les lignes sans
        // commentaire ». L'écarter rendait ces deux questions impossibles à
        // poser, alors qu'elles sont parmi les plus courantes.
        const key = value === null ? '' : String(value)
        tally.set(key, (tally.get(key) ?? 0) + 1)
        if (tally.size > CHOICE_CEILING) break
      }
      out[column.key] = [...tally]
        .map(([value, count]) => ({
          value,
          label: value === '' ? BLANK_LABEL : column.choiceLabel?.(value) ?? value,
          count,
        }))
        // La case vide en tête : c'est le complément de toutes les autres, et
        // la chercher au milieu d'un ordre alphabétique n'a pas de sens.
        .sort((a, b) =>
          a.value === '' ? -1
          : b.value === '' ? 1
          : a.label.localeCompare(b.label, 'fr', { numeric: true }),
        )
    }
    return out
  }, [rows, columns])

  /**
   * Les bornes réelles d'une colonne numérique, sur **toutes** les lignes.
   *
   * Elles ne peuvent pas se déduire de `distinct`, qui s'arrête à quarante
   * valeurs : sur un référentiel de cent vingt prix, le panneau annonçait
   * « valeurs présentes : 87 à 1787 » alors que la colonne descend à 0,40 —
   * une aide de saisie qui ment est pire que pas d'aide du tout.
   */
  const bounds = useMemo(() => {
    const out: Record<string, { min: number; max: number }> = {}
    for (const column of columns) {
      if (!column.numeric || column.filter === false) continue
      let min = Infinity
      let max = -Infinity
      for (const row of rows) {
        const value = Number(defaultValue(row, column))
        if (!Number.isFinite(value)) continue
        if (value < min) min = value
        if (value > max) max = value
      }
      if (min <= max) out[column.key] = { min, max }
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
          const tags = tagsOf(row, column)
          if (tags) {
            return tags.some((tag) => tag.toLowerCase().includes(needle))
          }
          const value = defaultValue(row, column)
          return value !== null && String(value).toLowerCase().includes(needle)
        })
      ) {
        return false
      }
      return active.every(([key, filter]) => {
        const column = byKey.get(key)
        if (!column) return true
        const tags = column.tags ? tagsOf(row, column) : null
        if (column.tags && filter.kind === 'choice') {
          // « Une des étiquettes cochées », et non « toutes » : on demande
          // « montre-moi les hors ERP », pas « ceux qui ne sont *que* hors ERP ».
          return tags === null
            ? filter.values.includes('')
            : tags.some((tag) => filter.values.includes(tag))
        }
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
        if (filter.kind === 'blank') {
          const empty = value === null || String(value).trim() === ''
          return empty === filter.empty
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

  // ---- fenêtre de lignes ---------------------------------------------------
  //
  // Ce qui suit ne change que ce qui entre dans le DOM. Tout le reste — le
  // tri, les filtres, les totaux, l'export, « tout sélectionner » — travaille
  // sur `sorted`, c'est-à-dire sur l'ensemble. Une fenêtre qui déciderait de ce
  // qu'on additionne ou de ce qu'on exporte serait un piège, pas une
  // optimisation.
  const scrollRef = useRef<HTMLDivElement>(null)
  const bodyRef = useRef<HTMLTableSectionElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState(VIRTUAL_MAX_HEIGHT)
  const [rowHeight, setRowHeight] = useState(ROW_HEIGHT_GUESS)
  const windowed = sorted.length > VIRTUAL_FROM

  // La hauteur est mesurée sur une ligne réelle plutôt que fixée : `dense`, la
  // densité du navigateur et le zoom la changent, et une valeur écrite en dur
  // décalerait progressivement la fenêtre du défilement.
  useLayoutEffect(() => {
    const row = bodyRef.current?.querySelector<HTMLElement>('tr[data-row]')
    const measured = row?.getBoundingClientRect().height
    if (measured && Math.abs(measured - rowHeight) > 0.5) setRowHeight(measured)
  }, [windowed, dense, visible.length, sorted.length, rowHeight])

  // La hauteur du cadre, et ses changements : replier un bloc ou redimensionner
  // la fenêtre modifie le nombre de lignes visibles.
  useLayoutEffect(() => {
    const frame = scrollRef.current
    if (!frame || !windowed) return
    const measure = () => setViewport(frame.clientHeight || VIRTUAL_MAX_HEIGHT)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(frame)
    return () => observer.disconnect()
  }, [windowed])

  // Filtrer ou trier remet en haut. Sans cela, un filtre qui ramène la liste à
  // dix lignes laisse le cadre défilé à la hauteur de vingt mille : la fenêtre
  // calculée est alors vide, et l'écran affiche un tableau vide sur un jeu de
  // résultats qui n'est pas vide.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0
    setScrollTop(0)
  }, [search, sort, filters])

  const { start, before, after, shown } = useMemo(() => {
    if (!windowed) {
      return { start: 0, before: 0, after: 0, shown: sorted }
    }
    const frame = windowOf({
      total: sorted.length,
      scrollTop,
      rowHeight,
      viewport,
    })
    return { ...frame, shown: sorted.slice(frame.start, frame.end) }
  }, [windowed, sorted, scrollTop, rowHeight, viewport])

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
              //
              // Un code fait exception. `LINE_SIDE` n'est pas une valeur qu'on
              // trie ou qu'on somme, c'est un mot de passe interne ; le fichier
              // qui arrivait chez le gestionnaire en était plein. Là où la
              // colonne sait nommer ses valeurs, c'est le nom qui part — et il
              // se recharge, l'import reconnaissant le libellé comme le code.
              .map((c) => [c.key, exportValue(row, c)]),
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

      {/* Ce qui filtre, visible sans rouvrir quoi que ce soit.
          Un compteur « Filtres (3) » dit qu'il y a trois critères ; il ne dit
          pas lesquels, et le tableau amputé de ses deux tiers reste alors
          inexplicable tant qu'on n'a pas rouvert le panneau.
          Barre ouverte, les déclencheurs portent déjà cet état : afficher les
          deux ferait deux rangées disant la même chose. */}
      {activeFilters > 0 && !showFilters && (
        <div className="filter-chips">
          {filterable
            .filter(({ column }) => filters[column.key])
            .map(({ column }) => (
              <button
                key={column.key}
                type="button"
                className="chip chip--active"
                title={`Retirer le filtre sur « ${column.label} »`}
                onClick={() => setFilter(column.key, null)}
              >
                <span className="subtle">{column.label}</span>{' '}
                {summarise(filters[column.key], distinct[column.key])}
                <span className="chip__remove">
                  <Icons.x size={11} />
                </span>
              </button>
            ))}
          <button
            type="button"
            className="chip"
            onClick={() => setFilters({})}
          >
            Tout effacer
          </button>
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
              observed={bounds[column.key]}
              value={filters[column.key] ?? null}
              onChange={(filter) => setFilter(column.key, filter)}
            />
          ))}
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
          ref={scrollRef}
          onScroll={
            windowed
              ? (event) => setScrollTop(event.currentTarget.scrollTop)
              : undefined
          }
          style={
            // Une grille fenêtrée a besoin d'un cadre qui défile lui-même :
            // sans hauteur, c'est la page qui défile et la fenêtre ne bouge
            // jamais. La plupart des écrans en donnent une ; ce repli ne
            // concerne que les autres.
            maxHeight || windowed
              ? ({
                  '--table-max-height': `${maxHeight ?? VIRTUAL_MAX_HEIGHT}px`,
                } as React.CSSProperties)
              : undefined
          }
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
                      column.sticky === 'right' ? 'sticky-right' : '',
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
            <tbody ref={bodyRef}>
              {/* Les deux cales portent la hauteur des lignes absentes, pour
                  que la barre de défilement dise la vérité sur la longueur du
                  tableau. Sans elles, vingt mille lignes défileraient sur la
                  hauteur de trente. */}
              {before > 0 && (
                <tr aria-hidden="true">
                  <td style={{ height: before, padding: 0, border: 'none' }} />
                </tr>
              )}
              {shown.map((row, offset) => {
                const index = start + offset
                const id = getRowId(row, index)
                return (
                  <tr
                    key={id}
                    data-row=""
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
                      const sticky = column.sticky === 'right' ? ' sticky-right' : ''
                      // Une colonne sans objet sur cette ligne ne montre rien.
                      // Avant `render`, parce que c'est vrai des deux modes :
                      // une valeur affichée en lecture et un champ de saisie en
                      // modification sont deux façons de la même erreur.
                      if (column.appliesTo?.(row) === false) {
                        return <td key={column.key} className={sticky || undefined} />
                      }
                      if (column.render) {
                        return (
                          <td
                            key={column.key}
                            className={`${column.numeric ? 'num' : ''}${sticky}`.trim() || undefined}
                          >
                            {column.render(row, index)}
                          </td>
                        )
                      }
                      // Une colonne sans `render` affiche la valeur portée par
                      // la clé — et, à défaut, ce que `value` calcule. Sans ce
                      // repli, une colonne dérivée d'un champ imbriqué (la zone
                      // d'une feuille, par exemple) déclarait bien sa valeur
                      // pour le tri, le filtre et l'export, et rendait une
                      // cellule vide à l'écran.
                      const raw = column.key in row
                        ? cellOf(row, column.key)
                        : defaultValue(row, column)
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
                                    {column.choiceLabel?.(choice) ?? choice}
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
              {after > 0 && (
                <tr aria-hidden="true">
                  <td style={{ height: after, padding: 0, border: 'none' }} />
                </tr>
              )}
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
                      <td
                        key={column.key}
                        className={[
                          column.numeric ? 'num' : '',
                          // La colonne épinglée l'est aussi sous les totaux,
                          // sinon elle laisse un blanc sur cette ligne-là.
                          column.sticky === 'right' ? 'sticky-right' : '',
                        ].join(' ').trim()}
                      >
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

/**
 * Un filtre de colonne, dans la forme que sa nature appelle.
 *
 * **Un déclencheur compact, un panneau au clic.** La première version posait le
 * filtre de chaque colonne à plat dans la barre — dont, pour une liste de
 * valeurs, un `<select multiple>` natif. Trois défauts, dans cet ordre :
 *
 * * la sélection multiple native se fait au Ctrl+clic, un geste que personne
 *   n'a de raison de connaître, et qui perd tout au clic simple suivant ;
 * * la liste n'a ni recherche ni compte, donc quarante valeurs sont un mur ;
 * * quinze colonnes filtrables faisaient quinze champs déployés en permanence,
 *   qui repoussaient le tableau hors de l'écran.
 *
 * Ici le repos est une puce d'une ligne, et le détail ne s'ouvre que sur
 * demande — au-dessus du tableau, sans le déplacer.
 */
function ColumnFilterField<T extends Row>({
  column,
  kind,
  choices,
  observed,
  value,
  onChange,
}: {
  column: Column<T>
  kind: 'choice' | 'range' | 'text'
  choices: Choice[]
  /** Les bornes réelles de la colonne, quand elle est numérique. */
  observed: { min: number; max: number } | undefined
  value: ColumnFilter | null
  onChange: (filter: ColumnFilter | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [needle, setNeedle] = useState('')
  const box = useRef<HTMLDivElement>(null)

  // Fermeture au clic extérieur et à Échap. Sans cela, deux panneaux ouverts se
  // recouvrent et masquent la barre elle-même.
  useEffect(() => {
    if (!open) return
    const onClick = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const active = value !== null
  return (
    <div className="filter" ref={box}>
      <button
        type="button"
        className={`filter__trigger${active ? ' filter__trigger--active' : ''}`}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={column.help}
      >
        <span className="filter__label">{column.label}</span>
        {/* Au repos, le nom de la colonne suffit : un « toutes » accolé à
            chaque puce est du bruit, et son accord dépend d'un libellé dont
            le genre varie. Le résumé n'apparaît qu'une fois le filtre posé. */}
        {active && <span className="filter__summary">{summarise(value, choices)}</span>}
        {active && (
          <span
            className="filter__clear"
            role="button"
            tabIndex={-1}
            aria-label={`Retirer le filtre ${column.label}`}
            onClick={(event) => {
              // Sans cela, le clic ouvre le panneau qu'il vient de vider.
              event.stopPropagation()
              onChange(null)
              setOpen(false)
            }}
          >
            <Icons.x size={11} />
          </span>
        )}
        <Icons.chevronDown size={12} className="filter__caret" />
      </button>

      {open && (
        <div className="filter__panel" role="dialog" aria-label={column.label}>
          {kind === 'choice' && (
            <ChoicePanel
              choices={choices}
              needle={needle}
              onNeedle={setNeedle}
              picked={value?.kind === 'choice' ? value.values : []}
              onChange={(values) =>
                onChange(values.length ? { kind: 'choice', values } : null)
              }
            />
          )}
          {kind === 'range' && (
            <RangePanel
              range={value?.kind === 'range' ? value : { min: null, max: null }}
              observed={observed}
              onChange={onChange}
            />
          )}
          {kind === 'text' && (
            <TextPanel
              value={value}
              onChange={onChange}
            />
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Ce que porte une puce de filtre : le critère, pas le nom de la colonne.
 *
 * Les libellés viennent de `choices` : une puce qui afficherait « COMPONENT »
 * quand la colonne montre « Composant » ne se rattache à rien de visible.
 */
function summarise(
  value: ColumnFilter | null | undefined,
  choices: Choice[] = [],
): string {
  if (!value) return ''
  if (value.kind === 'choice') {
    const [first] = value.values
    if (value.values.length === 1 && first !== undefined) {
      return choices.find((choice) => choice.value === first)?.label ?? first
    }
    return `${value.values.length} valeurs`
  }
  if (value.kind === 'range') {
    const { min, max } = value
    if (min !== null && max !== null) return `${min} – ${max}`
    if (min !== null) return `≥ ${min}`
    return `≤ ${max}`
  }
  if (value.kind === 'blank') return value.empty ? BLANK_LABEL : 'renseignées'
  return `« ${value.needle} »`
}

function ChoicePanel({
  choices,
  needle,
  onNeedle,
  picked,
  onChange,
}: {
  choices: Choice[]
  needle: string
  onNeedle: (value: string) => void
  picked: string[]
  onChange: (values: string[]) => void
}) {
  const term = needle.trim().toLowerCase()
  const shown = term
    ? choices.filter((choice) => choice.label.toLowerCase().includes(term))
    : choices
  const toggle = (value: string) =>
    onChange(
      picked.includes(value)
        ? picked.filter((v) => v !== value)
        : [...picked, value],
    )
  return (
    <>
      {/* La recherche n'apparaît qu'au-delà de ce qui se parcourt à l'œil. */}
      {choices.length > 7 && (
        <input
          className="input input--sm filter__search"
          placeholder="Rechercher une valeur…"
          value={needle}
          onChange={(event) => onNeedle(event.target.value)}
          autoFocus
        />
      )}
      <div className="filter__actions">
        <button
          type="button"
          className="filter__action"
          disabled={shown.length === 0}
          onClick={() => onChange([...new Set([...picked, ...shown.map((c) => c.value)])])}
        >
          Tout cocher{term && ` (${shown.length})`}
        </button>
        <button
          type="button"
          className="filter__action"
          disabled={picked.length === 0}
          onClick={() => onChange([])}
        >
          Effacer
        </button>
      </div>
      <div className="filter__list">
        {shown.length === 0 && <p className="subtle filter__empty">Aucune valeur</p>}
        {shown.map((choice) => (
          <label key={choice.value} className="filter__option">
            <input
              type="checkbox"
              checked={picked.includes(choice.value)}
              onChange={() => toggle(choice.value)}
            />
            <span className="truncate">{choice.label}</span>
            {/* Combien de lignes portent cette valeur : cocher devient une
                décision plutôt qu'un essai. */}
            <span className="filter__count num">{choice.count}</span>
          </label>
        ))}
      </div>
    </>
  )
}

function RangePanel({
  range,
  observed,
  onChange,
}: {
  range: { min: number | null; max: number | null }
  /** Les bornes réelles, affichées en aide de saisie : sans elles, « de … à … »
      sur une colonne inconnue se remplit au hasard. */
  observed: { min: number; max: number } | undefined
  onChange: (filter: ColumnFilter | null) => void
}) {
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
    <>
      <div className="filter__range">
        <input
          className="input input--sm num"
          type="number"
          placeholder={observed ? String(observed.min) : 'min'}
          aria-label="Minimum"
          value={range.min ?? ''}
          onChange={(event) => update('min', event.target.value)}
          autoFocus
        />
        <span className="subtle">à</span>
        <input
          className="input input--sm num"
          type="number"
          placeholder={observed ? String(observed.max) : 'max'}
          aria-label="Maximum"
          value={range.max ?? ''}
          onChange={(event) => update('max', event.target.value)}
        />
      </div>
      <p className="subtle filter__hint">
        {observed
          ? `Une seule borne suffit. Valeurs présentes : ${observed.min} à ${observed.max}.`
          : 'Une seule borne suffit.'}
      </p>
    </>
  )
}

function TextPanel({
  value,
  onChange,
}: {
  value: ColumnFilter | null
  onChange: (filter: ColumnFilter | null) => void
}) {
  const needle = value?.kind === 'text' ? value.needle : ''
  const blank = value?.kind === 'blank' ? value.empty : null
  return (
    <>
      <input
        className="input input--sm"
        placeholder="Contient…"
        value={needle}
        onChange={(event) =>
          onChange(event.target.value ? { kind: 'text', needle: event.target.value } : null)
        }
        autoFocus
      />
      {/* Une colonne dont les valeurs ne se listent pas — un commentaire, un nom
          de compteur — n'offrait aucun moyen de demander « celles qui sont
          restées vides », qui est pourtant la question la plus fréquente qu'on
          lui pose. Deux boutons, mutuellement exclusifs avec la recherche. */}
      <div className="filter__actions">
        <button
          type="button"
          className={`filter__action${blank === true ? ' filter__action--on' : ''}`}
          onClick={() =>
            onChange(blank === true ? null : { kind: 'blank', empty: true })
          }
        >
          Vides
        </button>
        <button
          type="button"
          className={`filter__action${blank === false ? ' filter__action--on' : ''}`}
          onClick={() =>
            onChange(blank === false ? null : { kind: 'blank', empty: false })
          }
        >
          Renseignées
        </button>
      </div>
      <p className="subtle filter__hint">
        Insensible à la casse et aux accents.
      </p>
    </>
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
