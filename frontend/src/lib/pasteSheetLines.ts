/**
 * Parse a block pasted from Excel into counting-sheet lines.
 *
 * Adding a sheet's content one row at a time is unusable for a real zone, and
 * a strict column order would be worse: the blocks people actually copy come
 * from an ERP extract, a previous campaign or a hand-made list, each with its
 * own column order and its own vocabulary for the sections.
 *
 * So nothing is positional. Every cell of a row is classified by what it *is*
 * — a section name, a unit, a quantity, an article reference — and what is
 * left over is the article. A row that yields no article is reported rather
 * than silently dropped: a line lost between the shop floor and the sheet is
 * exactly the failure this application exists to remove.
 */

export type ParsedSheetLine = {
  item_number: string
  section: string
  unit: string
  qty: string | null
}

export type PasteOutcome = {
  lines: ParsedSheetLine[]
  /** 1-based indexes of pasted rows no article could be read from. */
  rejected: number[]
  /** True when the first row was recognised and consumed as a header. */
  headerSkipped: boolean
}

/** Section vocabulary, including the words the legacy workbook used. */
const SECTIONS: Record<string, string> = {
  LINE_SIDE: 'LINE_SIDE',
  'BORD DE LIGNE': 'LINE_SIDE',
  BDL: 'LINE_SIDE',
  BL: 'LINE_SIDE',
  WIP: 'WIP',
  'MOM WAITING': 'WIP',
  'MOM EN ATTENTE': 'WIP',
  'EN COURS': 'WIP',
  ENCOURS: 'WIP',
  WIP_OK: 'WIP_OK',
  'WIP OK': 'WIP_OK',
  'MOM OK': 'WIP_OK',
  'STATUT MOM: OK': 'WIP_OK',
  ECLATEE: 'WIP_OK',
  ASSEMBLE: 'WIP_OK',
}

/** Units seen on this site's sheets and in the D365 extract. */
const UNITS = new Set([
  'PCE', 'PC', 'P', 'U', 'UN', 'UNITE', 'EA',
  'M', 'ML', 'M2', 'M3', 'CM', 'MM',
  'KG', 'G', 'T', 'L', 'CL', 'ML3',
  'BOB', 'LOT', 'SET', 'BOI', 'ROU',
])

const HEADER_WORDS = /^(article|référence|reference|ref|item|désignation|designation|section|unité|unite|unit|quantité|quantite|qty|qté)$/i

/** Accent- and case-insensitive form used for every comparison. */
function fold(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .trim()
    .toUpperCase()
}

/** Split a pasted row into cells: Excel uses tabs, CSV exports use ; or |. */
function cellsOf(row: string): string[] {
  const separator = row.includes('\t') ? '\t' : row.includes(';') ? ';' : row.includes('|') ? '|' : '  '
  return row
    .split(separator === '  ' ? /\s{2,}/ : separator)
    .map((cell) => cell.trim())
    .filter((cell) => cell.length > 0)
}

/** A quantity, in French (1 234,5) or English (1,234.5) notation. */
function asQuantity(cell: string): string | null {
  const cleaned = cell.replace(/\s| /g, '')
  if (!/^[+-]?[\d.,]+$/.test(cleaned)) return null
  // Last separator wins as the decimal mark; the other is a thousands group.
  const lastComma = cleaned.lastIndexOf(',')
  const lastDot = cleaned.lastIndexOf('.')
  let normalised = cleaned
  if (lastComma > lastDot) normalised = cleaned.replace(/\./g, '').replace(',', '.')
  else if (lastDot > lastComma) normalised = cleaned.replace(/,/g, '')
  return Number.isFinite(Number(normalised)) ? normalised : null
}

/**
 * An article reference looks like a code, not like prose: it carries a digit
 * and no space. Requiring a digit is what keeps a description column ("VIS
 * TETE HEXAGONALE") from being mistaken for a reference.
 */
function looksLikeReference(cell: string): boolean {
  return /\d/.test(cell) && !/\s/.test(cell) && cell.length >= 3
}

export function parseSheetLines(text: string): PasteOutcome {
  const rows = text
    .split(/\r?\n/)
    .map((row) => row.trim())
    .filter((row) => row.length > 0)

  let headerSkipped = false
  if (rows.length > 1) {
    const first = cellsOf(rows[0] ?? '')
    // A header has no reference in it and names at least one known column.
    if (first.some((cell) => HEADER_WORDS.test(cell)) && !first.some(looksLikeReference)) {
      rows.shift()
      headerSkipped = true
    }
  }

  const lines: ParsedSheetLine[] = []
  const rejected: number[] = []

  rows.forEach((row, index) => {
    const cells = cellsOf(row)
    let section = ''
    let unit = ''
    let qty: string | null = null
    const rest: string[] = []

    for (const cell of cells) {
      const folded = fold(cell)
      if (!section && SECTIONS[folded]) {
        section = SECTIONS[folded]
        continue
      }
      if (!unit && UNITS.has(folded)) {
        unit = folded === 'P' || folded === 'PC' ? 'PCE' : folded
        continue
      }
      if (qty === null) {
        const parsed = asQuantity(cell)
        // A pure number is a quantity; a reference keeps its digits *and* its
        // letters or dashes, so the two never compete.
        if (parsed !== null) {
          qty = parsed
          continue
        }
      }
      rest.push(cell)
    }

    // Second chance for a reference with no digit — rare but legitimate —
    // while still refusing prose: a description column must never become an
    // article, it would create a line the referential cannot match.
    const reference =
      rest.find(looksLikeReference) ??
      rest.find((cell) => !/\s/.test(cell) && cell.length >= 3)
    if (!reference) {
      rejected.push(index + 1 + (headerSkipped ? 1 : 0))
      return
    }

    lines.push({
      item_number: fold(reference),
      // Defaults chosen to be the safe reading: a line counted at the line
      // side is the ordinary case, and PCE is this site's default unit.
      section: section || 'LINE_SIDE',
      unit: unit || 'PCE',
      qty,
    })
  })

  return { lines, rejected, headerSkipped }
}
