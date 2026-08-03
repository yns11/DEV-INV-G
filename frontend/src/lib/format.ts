/**
 * Number, currency and date formatting — French locale, one place.
 *
 * Two rules drive everything here and they come straight from IBCS:
 *
 *  1. **A number without a unit is not information.** Every formatter emits the
 *     unit, and large amounts are abbreviated with an explicit suffix (k€, M€)
 *     rather than silently truncated.
 *  2. **Missing is not zero.** `null` and `undefined` render as "—", never as 0.
 *     The distinction between "we have no figure" and "the figure is zero" is
 *     exactly what the legacy workbook destroyed.
 */

const NBSP = ' ' // narrow no-break space, the French thousands separator

const int = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 })
const dec = new Intl.NumberFormat('fr-FR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})
const decFlexible = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 3 })
const pct1 = new Intl.NumberFormat('fr-FR', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})
const pct2 = new Intl.NumberFormat('fr-FR', {
  style: 'percent',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

export const DASH = '—'

const isNil = (v: unknown): v is null | undefined =>
  v === null || v === undefined || (typeof v === 'number' && !Number.isFinite(v))

/** An integer quantity, e.g. `12 480`. */
export function num(value: number | null | undefined): string {
  return isNil(value) ? DASH : int.format(value)
}

/** A quantity keeping up to 3 decimals — for kilograms, metres, litres. */
export function qty(value: number | null | undefined, unit?: string): string {
  if (isNil(value)) return DASH
  const text = Number.isInteger(value) ? int.format(value) : decFlexible.format(value)
  return unit ? `${text}${NBSP}${unit}` : text
}

/** A full monetary amount, e.g. `1 234 567,89 €`. */
export function money(value: number | null | undefined): string {
  return isNil(value) ? DASH : `${dec.format(value)}${NBSP}€`
}

/**
 * A compact monetary amount for dense views: `1,2 M€`, `847 k€`, `312 €`.
 *
 * The suffix is always shown, so a compact figure can never be misread as a
 * full one — the mistake that turned a 22 M€ discrepancy into "22" on a slide.
 */
export function moneyShort(value: number | null | undefined): string {
  if (isNil(value)) return DASH
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  if (abs >= 1e9) return `${sign}${decFlexible.format(abs / 1e9)}${NBSP}Md€`
  if (abs >= 1e6) return `${sign}${decFlexible.format(abs / 1e6)}${NBSP}M€`
  if (abs >= 1e3) return `${sign}${int.format(Math.round(abs / 1e3))}${NBSP}k€`
  return `${sign}${int.format(Math.round(abs))}${NBSP}€`
}

/** A compact quantity: `12,4 M`, `847 k`, `312`. */
export function numShort(value: number | null | undefined): string {
  if (isNil(value)) return DASH
  const sign = value < 0 ? '-' : ''
  const abs = Math.abs(value)
  if (abs >= 1e6) return `${sign}${decFlexible.format(abs / 1e6)}${NBSP}M`
  if (abs >= 1e3) return `${sign}${int.format(Math.round(abs / 1e3))}${NBSP}k`
  return `${sign}${decFlexible.format(abs)}`
}

/** A ratio in [0, 1] as a percentage. */
export function percent(
  value: number | null | undefined,
  precision: 1 | 2 = 1,
): string {
  if (isNil(value)) return DASH
  return (precision === 2 ? pct2 : pct1).format(value)
}

/** A signed amount with an explicit `+` — variance always shows its direction. */
export function signedMoney(value: number | null | undefined): string {
  if (isNil(value)) return DASH
  return value > 0 ? `+${moneyShort(value)}` : moneyShort(value)
}

export function signedNum(value: number | null | undefined): string {
  if (isNil(value)) return DASH
  return value > 0 ? `+${numShort(value)}` : numShort(value)
}

/** CSS class for a signed value: direction only, never a judgement. */
export function signClass(value: number | null | undefined): string {
  if (isNil(value) || value === 0) return 'neutral'
  return value > 0 ? 'pos' : 'neg'
}

/** `13/06/2026` */
export function date(value: string | null | undefined): string {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return DASH
  return parsed.toLocaleDateString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  })
}

/** `13/06/2026 08:42` */
export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return DASH
  return parsed.toLocaleString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** `il y a 3 min`, `il y a 2 h`, `il y a 4 j` — for the audit trail. */
export function relativeTime(value: string | null | undefined): string {
  if (!value) return DASH
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return DASH
  const seconds = Math.round((Date.now() - parsed.getTime()) / 1000)
  if (seconds < 60) return "à l'instant"
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `il y a ${minutes} min`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `il y a ${hours} h`
  const days = Math.round(hours / 24)
  if (days < 30) return `il y a ${days} j`
  return date(value)
}

// --------------------------------------------------------------------------- //
// Business vocabulary
// --------------------------------------------------------------------------- //

export const CAMPAIGN_STATUS_LABELS: Record<string, string> = {
  PREPARATION: 'Préparation',
  COUNTING: 'Comptage',
  ANALYSIS: 'Analyse & ajustements',
  CLOSED: 'Clôturée',
}

export const JOURNAL_STATUS_LABELS: Record<string, string> = {
  PENDING: 'En attente',
  IN_PROGRESS: 'En cours',
  POSTED: 'Posté',
  BOOK_ENFORCED: 'Forcé au stock livre',
}

export const SHEET_STATUS_LABELS: Record<string, string> = {
  PENDING: 'En attente',
  COUNTING: 'Comptage en cours',
  ENCODING: 'Encodage en cours',
  DONE: 'Terminée',
}

export const ZONE_STATUS_LABELS: Record<string, string> = {
  PENDING: 'En attente',
  PASS_1_RUNNING: 'Comptage n°1',
  PASS_2_RUNNING: 'Comptage n°2',
  ARBITRATION: 'Arbitrage requis',
  DONE: 'Terminée',
}

/** The specification requires the legacy MOM wording to be surfaced as WIP. */
export const SECTION_LABELS: Record<string, string> = {
  LINE_SIDE: 'Bord de ligne',
  WIP: 'WIP (à éclater)',
  WIP_OK: 'WIP assemblé',
}

export const SECTION_HINTS: Record<string, string> = {
  LINE_SIDE: 'Composant compté tel quel',
  WIP: "En-cours non déclaré : éclaté en nomenclature à la consolidation",
  WIP_OK: 'Ensemble déclaré dans l’ERP : compté tel quel',
}

export const ITEM_TYPE_LABELS: Record<string, string> = {
  COMPONENT: 'Composant',
  SEMI_FINISHED: 'Semi-fini',
  FINISHED: 'Produit fini',
  PACKAGING: 'Emballage',
  UNKNOWN: 'Non typé',
}

export const SOURCE_LABELS: Record<string, string> = {
  ERP_IMPORT: 'Import ERP',
  FILE_IMPORT: 'Import fichier',
  MANUAL: 'Saisie manuelle',
  SCAN_AI: 'Extraction IA',
  CONSOLIDATION: 'Consolidation',
  ARBITRATION: 'Arbitrage',
  SYSTEM: 'Système',
}

export const SEVERITY_LABELS: Record<string, string> = {
  BLOCKER: 'Bloquant',
  WARNING: 'Avertissement',
  INFO: 'Information',
}

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  CREATE: 'Création',
  UPDATE: 'Modification',
  DELETE: 'Suppression',
  STATUS_CHANGE: 'Changement de statut',
  IMPORT: 'Import',
  EXPORT: 'Export',
  FREEZE: 'Gel',
  CONSOLIDATE: 'Consolidation',
  ARBITRATE: 'Arbitrage',
}

export const label = (map: Record<string, string>, key: string | null | undefined) =>
  (key && map[key]) || key || DASH

/** Deterministic categorical colour, stable across renders and reloads. */
export function categoricalColor(index: number): string {
  return `var(--cat-${(index % 8) + 1})`
}
