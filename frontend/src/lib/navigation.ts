/**
 * The campaign's navigation, as one declaration.
 *
 * There are three levels and they are three different questions, so they get
 * three different shapes on screen rather than three identical bars:
 *
 *  - **phase** — where the campaign is in its life. A grouping label, never
 *    clickable: you do not navigate to a phase, you are in one.
 *  - **section** — a screen. This is what the URL addresses.
 *  - **sub-section** — a view inside a screen, carried in `?vue=`.
 *
 * Keeping the whole tree here is what lets the sidebar, the page title and the
 * screens themselves agree without any of them re-deriving the others' labels.
 */

import type { Icons } from '../components/ui'
import type { CampaignStatus, Overview } from './types'

/** Type-only, so this stays a declaration and never pulls the UI into `lib`. */
export type IconName = keyof typeof Icons

/** The lifecycle stage a section belongs to. Ordered as the campaign runs. */
export type PhaseGroup = 'PILOTAGE' | 'PREPARATION' | 'COUNTING' | 'ANALYSIS' | 'TRACE'

export const PHASE_GROUPS: Array<{
  id: PhaseGroup
  label: string
  /** The campaign status this group is the working phase of, if any. */
  status?: CampaignStatus
}> = [
  { id: 'PILOTAGE', label: 'Pilotage' },
  { id: 'PREPARATION', label: 'Préparation', status: 'PREPARATION' },
  { id: 'COUNTING', label: 'Comptage', status: 'COUNTING' },
  { id: 'ANALYSIS', label: 'Analyse', status: 'ANALYSIS' },
  { id: 'TRACE', label: 'Traçabilité' },
]

export interface SubSection {
  id: string
  label: string
  /** Optional heading that groups consecutive sub-sections under it. */
  group?: string
  count?: (overview: Overview) => number | null
}

export interface Section {
  /** Route segment, relative to the campaign. `''` is the dashboard. */
  to: string
  label: string
  icon: IconName
  phase: PhaseGroup
  /** One line, shown as the screen's lede. Kept short on purpose. */
  lede?: string
  enabled: (overview: Overview) => boolean
  /** Why it is not available yet — shown instead of a silent dead link. */
  locked?: (overview: Overview) => string
  badge?: (overview: Overview, focus: boolean) => number | null
  subs?: SubSection[]
}

export const SECTIONS: Section[] = [
  {
    to: '',
    label: 'Tableau de bord',
    icon: 'dashboard',
    phase: 'PILOTAGE',
    lede: 'L’état de la campagne en un écran.',
    enabled: () => true,
  },
  {
    to: 'assistant',
    label: 'Assistant',
    icon: 'sparkles',
    phase: 'PILOTAGE',
    lede: 'Posez vos questions sur la campagne en français.',
    enabled: () => true,
  },
  {
    to: 'preparation',
    label: 'Référentiels & seuils',
    icon: 'layers',
    phase: 'PREPARATION',
    lede: 'Ce sur quoi la campagne s’appuie : articles, stock livre, seuils, périmètres.',
    enabled: () => true,
    subs: [
      {
        id: 'items',
        label: 'Articles',
        group: 'Données de référence',
        count: (o) => o.counts.items,
      },
      { id: 'boms', label: 'Nomenclatures', group: 'Données de référence' },
      {
        id: 'book_stock',
        label: 'Stock livre',
        group: 'Données de référence',
        count: (o) => o.counts.bookStockLines,
      },
      { id: 'count_sheets', label: 'Feuilles de comptage', group: 'Données de référence' },
      { id: 'thresholds', label: 'Seuils', group: 'Pilotage' },
      { id: 'managers', label: 'Gestionnaires', group: 'Pilotage' },
      { id: 'journal_scope', label: 'Affectation journaux', group: 'Pilotage' },
      { id: 'zone_scope', label: 'Affectation zones', group: 'Pilotage' },
    ],
  },
  {
    to: 'comptage',
    label: 'Journaux de comptage',
    icon: 'clipboard',
    phase: 'COUNTING',
    lede: 'Un journal par emplacement, saisi puis posté à l’ERP.',
    enabled: (o) => o.campaign.status !== 'PREPARATION',
    locked: () => 'Disponible une fois la campagne passée en comptage.',
    // Under focus the badge counts the perimeter, not the campaign: a "6" over
    // a list of four is the kind of small lie that makes people stop trusting
    // the numbers next to it.
    badge: (o, focus) =>
      focus
        ? o.perimeter.journalCount || null
        : o.journalProgress.total - o.journalProgress.complete || null,
  },
  {
    to: 'generique',
    label: 'GENERIQUE',
    icon: 'grid',
    phase: 'COUNTING',
    lede: 'Un emplacement ERP, des dizaines de zones physiques comptées sur papier.',
    enabled: () => true,
    badge: (o, focus) =>
      focus
        ? o.perimeter.zoneCount || null
        : o.genericProgress.pendingArbitrations || null,
    subs: [
      { id: 'zones', label: 'Zones & feuilles', count: (o) => o.genericProgress.zones },
      {
        id: 'arbitration',
        label: 'Arbitrages',
        count: (o) => o.genericProgress.pendingArbitrations || null,
      },
      { id: 'consolidation', label: 'Consolidation' },
    ],
  },
  {
    to: 'analyse',
    label: 'Écarts & analyses',
    icon: 'chart',
    phase: 'ANALYSIS',
    lede: 'Où sont les écarts, ce qui les explique, ce qui reste à ajuster.',
    enabled: (o) => o.campaign.book_stock_frozen_at !== null,
    locked: () => 'Disponible une fois le stock livre gelé.',
  },
  {
    to: 'audit',
    label: 'Journal d’audit',
    icon: 'history',
    phase: 'TRACE',
    lede: 'Qui a changé quoi, quand.',
    enabled: () => true,
  },
]

/** The section a pathname is currently on. */
export function sectionFor(pathname: string, campaignId: string): Section | undefined {
  const base = `/campagnes/${campaignId}`
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : ''
  const segment = rest.replace(/^\/+/, '').split('/')[0] ?? ''
  return SECTIONS.find((s) => s.to === segment)
}
