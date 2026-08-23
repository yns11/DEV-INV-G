/**
 * The campaign's navigation, as one declaration.
 *
 * Three levels, three different questions, three different shapes on screen:
 *
 *  - **phase** — where the campaign is in its life. A grouping label, never
 *    clickable: you do not navigate to a phase, you are in one.
 *  - **section** — a screen. This is what the URL addresses.
 *  - **sub-section** — a view inside a screen, carried in `?vue=`.
 *
 * One entry per screen, and the entries under a phase are in the order the work
 * is actually done: articles before the sheets that list them, ERP stock before
 * the counts it will be compared to. The tree is therefore also the procedure,
 * which is why the same order is enforced server-side
 * (`inventory.domain.sequence`) rather than merely suggested here.
 *
 * Keeping the whole tree in one file is what lets the sidebar, the page title
 * and the screens agree without any of them re-deriving the others' labels.
 */

import type { Icons } from '../components/ui'
import type { CampaignStatus, Overview } from './types'

/** Type-only, so this stays a declaration and never pulls the UI into `lib`. */
export type IconName = keyof typeof Icons

/** The lifecycle stage a section belongs to. Ordered as the campaign runs. */
export type PhaseGroup = 'PREPARATION' | 'COUNTING' | 'ANALYSIS'

export const PHASE_GROUPS: Array<{
  id: PhaseGroup
  label: string
  /** The campaign status this group is the working phase of, if any. */
  status?: CampaignStatus
}> = [
  { id: 'PREPARATION', label: 'Préparation', status: 'PREPARATION' },
  { id: 'COUNTING', label: 'Comptage', status: 'COUNTING' },
  { id: 'ANALYSIS', label: 'Analyse', status: 'ANALYSIS' },
]

/**
 * Compteurs d'alertes, chargés à part.
 *
 * Pas dans l'aperçu : les calculer fait tourner toute la batterie de contrôles,
 * et l'aperçu est demandé à chaque écran. La barre latérale les récupère de son
 * côté, et affiche zéro tant qu'ils n'arrivent pas — un badge absent se lit
 * « rien à signaler », ce qui est le bon défaut.
 */
export interface Alerts {
  controls: number
  consolidation: number
}

export interface SubSection {
  id: string
  label: string
  /** Optional heading that groups consecutive sub-sections under it. */
  group?: string
  count?: (overview: Overview, alerts: Alerts) => number | null
}

export interface Section {
  /** Route segment, relative to the campaign. */
  to: string
  label: string
  /**
   * Label that depends on the campaign — the generic warehouse is named in two
   * entries, and hard-coding `B06VRAC` in the navigation would make a
   * configuration change silently wrong on screen.
   */
  labelFor?: (overview: Overview) => string
  icon: IconName
  phase: PhaseGroup
  /** One line, shown as the screen's lede. Kept short on purpose. */
  lede?: string
  enabled: (overview: Overview) => boolean
  /** Why it is not available yet — shown instead of a silent dead link. */
  locked?: (overview: Overview) => string
  badge?: (overview: Overview, focus: boolean, alerts: Alerts) => number | null
  subs?: SubSection[]
}

/** The warehouse the loose-paper counting happens in, e.g. `B06VRAC`. */
const generic = (o: Overview) => o.campaign.config.generic_warehouse

/**
 * Why a step is not open yet, straight from the server.
 *
 * The interface never decides this on its own: it displays the same sentence
 * the API would answer with, so a section is never offered and then refused.
 */
const blocked = (o: Overview, aspect: string) => o.sequence?.blockedBy?.[aspect]
const ready = (o: Overview, aspect: string) => !blocked(o, aspect)

/**
 * Les écrans qui ne sont pas une étape du travail.
 *
 * L'assistant et le journal d'audit ne se rangent sous aucune phase : ils
 * s'ouvrent depuis n'importe où, à propos de ce qu'on est en train de faire. Ils
 * vivent donc avec les actions de l'en-tête plutôt que dans l'arborescence, où
 * ils formaient un groupe « Pilotage » qui n'était pas une étape et qu'il
 * fallait traverser à chaque fois pour atteindre la première.
 */
export const UTILITIES: Array<{
  to: string
  label: string
  /** Plus court dans l'en-tête, où la place se compte. */
  short: string
  icon: IconName
  lede: string
}> = [
  {
    to: 'assistant',
    label: 'Assistant',
    short: 'Assistant',
    icon: 'sparkles',
    lede: 'Posez vos questions sur la campagne en français.',
  },
  {
    to: 'audit',
    label: 'Journal d’audit',
    short: 'Audit',
    icon: 'history',
    lede: 'Qui a changé quoi, quand.',
  },
]

export const SECTIONS: Section[] = [
  // --- Préparation ----------------------------------------------------------
  {
    to: 'articles',
    label: 'Articles',
    icon: 'box',
    phase: 'PREPARATION',
    lede: 'Le référentiel sur lequel tout le reste s’appuie.',
    enabled: () => true,
  },
  {
    to: 'nomenclatures',
    label: 'Nomenclatures',
    icon: 'tree',
    phase: 'PREPARATION',
    lede: 'Ce qui permet d’éclater un en-cours en composants.',
    enabled: (o) => ready(o, 'boms'),
    locked: (o) => blocked(o, 'boms') ?? '',
  },
  {
    to: 'feuilles',
    label: 'Feuilles',
    labelFor: (o) => `Feuilles ${generic(o)}`,
    icon: 'printer',
    phase: 'PREPARATION',
    lede: 'Les zones, leurs feuilles, et l’impression — avant le jour J.',
    enabled: (o) => ready(o, 'zones'),
    locked: (o) => blocked(o, 'zones') ?? '',
  },
  {
    to: 'gestion',
    label: 'Gestion',
    icon: 'sliders',
    phase: 'PREPARATION',
    lede: 'Qui compte quoi, et à partir de quel montant un écart compte.',
    enabled: (o) => ready(o, 'thresholds'),
    locked: (o) => blocked(o, 'thresholds') ?? '',
    subs: [
      { id: 'managers', label: 'Gestionnaires' },
      { id: 'zone_scope', label: 'Affectation zones' },
      { id: 'journal_scope', label: 'Affectation journaux' },
      { id: 'thresholds', label: 'Seuils' },
    ],
  },

  // --- Comptage -------------------------------------------------------------
  {
    to: 'stock-erp',
    label: 'Stock ERP',
    icon: 'database',
    phase: 'COUNTING',
    lede: 'La photo du stock à laquelle les comptages seront comparés.',
    enabled: (o) => o.campaign.status !== 'PREPARATION',
    locked: () => 'Se charge au passage en comptage.',
  },
  {
    // Juste après le stock ERP, et pour la même raison qu'il vient là : les deux
    // disent ce que le système *croit* avoir. L'un le dit à un instant, l'autre
    // sur la période — et c'est la confrontation des deux au comptage qui sépare
    // ce que la production explique de ce qui reste à expliquer.
    to: 'backflush',
    label: 'Backflush',
    icon: 'layers',
    phase: 'COUNTING',
    lede: 'Ce que la production a consommé sans que l’ERP l’enregistre.',
    enabled: (o) => ready(o, 'backflush'),
    locked: (o) => blocked(o, 'backflush') ?? '',
  },
  {
    to: 'compil',
    label: 'Compil',
    labelFor: (o) => `Compil ${generic(o)}`,
    icon: 'grid',
    phase: 'COUNTING',
    lede: 'Un emplacement ERP, des dizaines de zones comptées sur papier.',
    enabled: (o) => ready(o, 'count_entries'),
    locked: (o) => blocked(o, 'count_entries') ?? '',
    // Ce qui reste à faire. Un badge qui compte les zones existantes affiche
    // le même nombre du premier au dernier jour ; celui-ci descend à zéro, ce
    // qui est la seule chose qu'on lui demande.
    badge: (o) => o.genericProgress.zones - o.genericProgress.done || null,
    subs: [
      { id: 'zones', label: 'Zones & feuilles', count: (o) => o.genericProgress.zones },
      {
        id: 'arbitration',
        label: 'Arbitrages',
        count: (o) => o.genericProgress.pendingArbitrations || null,
      },
      // Le nombre de constats *distincts* : « 400 » ne dirait rien d'autre
      // que « c'est grand ».
      {
        id: 'consolidation',
        label: 'Consolidation',
        count: (_o, alerts) => alerts.consolidation || null,
      },
    ],
  },
  {
    to: 'comptage',
    label: 'Journaux de comptage',
    icon: 'clipboard',
    phase: 'COUNTING',
    lede: 'Un journal par emplacement, saisi puis posté à l’ERP.',
    enabled: (o) => ready(o, 'count_journals'),
    locked: (o) => blocked(o, 'count_journals') ?? '',
    // Under focus the badge counts the perimeter, not the campaign: a "6" over
    // a list of four is the kind of small lie that makes people stop trusting
    // the numbers next to it.
    badge: (o, focus) =>
      focus
        ? o.perimeter.journalCount || null
        : o.journalProgress.total - o.journalProgress.complete || null,
  },

  // --- Analyse --------------------------------------------------------------
  {
    to: 'controles',
    label: 'Contrôles',
    icon: 'alert',
    phase: 'ANALYSIS',
    lede: 'Ce qui empêcherait de clôturer, et pourquoi.',
    enabled: (o) => o.campaign.book_stock_frozen_at !== null,
    locked: () => 'Disponible une fois le stock ERP gelé.',
    badge: (_o, _focus, alerts) => alerts.controls || null,
  },
  {
    to: 'ecarts',
    label: 'Écarts',
    icon: 'chart',
    phase: 'ANALYSIS',
    lede: 'Où ils sont, combien ils pèsent.',
    enabled: (o) => o.campaign.book_stock_frozen_at !== null,
    locked: () => 'Disponible une fois le stock ERP gelé.',
  },
  {
    to: 'causes',
    label: 'Analyses et causes',
    icon: 'search',
    phase: 'ANALYSIS',
    lede: 'Ce qui les explique.',
    enabled: (o) => o.campaign.book_stock_frozen_at !== null,
    locked: () => 'Disponible une fois le stock ERP gelé.',
    subs: [
      { id: 'causes', label: 'Causes' },
      { id: 'analytics', label: 'Analyses & ML' },
      { id: 'summary', label: 'Synthèse IA' },
    ],
  },
  {
    to: 'ajustements',
    label: 'Ajustements',
    icon: 'scale',
    phase: 'ANALYSIS',
    lede: 'Les mouvements postés après le comptage.',
    enabled: (o) => o.campaign.book_stock_frozen_at !== null,
    locked: () => 'Disponible une fois le stock ERP gelé.',
  },
  {
    // En dernier, après les ajustements : la comparaison part du stock physique
    // — comptage ajustements compris — et la lire avant de les avoir postés
    // reviendrait à comparer une étagère qu'on est encore en train de corriger.
    to: 'reconciliation',
    label: 'Comparaison',
    icon: 'history',
    phase: 'ANALYSIS',
    lede: 'Deux inventaires, et tout ce qui s’est passé entre les deux.',
    enabled: (o) => ready(o, 'stock_flow'),
    locked: (o) => blocked(o, 'stock_flow') ?? '',
    // Le rapport, puis une grille par flux. Les quantités qui le nourrissent
    // n'étaient visibles que par leur total : un stock attendu faux ne se
    // déboguait pas, faute de pouvoir regarder la ligne fautive.
    subs: [
      { id: 'rapport', label: 'Rapport' },
      { id: 'receptions', label: 'Réceptions' },
      { id: 'production', label: 'Production & conso.' },
      { id: 'expeditions', label: 'Expéditions' },
      { id: 'rebuts', label: 'Rebuts' },
    ],
  },
]

/** The label to draw for a section, campaign-aware when it needs to be. */
export function labelOf(section: Section, overview: Overview): string {
  return section.labelFor ? section.labelFor(overview) : section.label
}

/**
 * The section a pathname is currently on.
 *
 * The two utility screens answer here too: they are no longer in the tree, but
 * the page still has to be able to say what it is showing.
 */
export function sectionFor(pathname: string, campaignId: string): Section | undefined {
  const base = `/campagnes/${campaignId}`
  const rest = pathname.startsWith(base) ? pathname.slice(base.length) : ''
  const segment = rest.replace(/^\/+/, '').split('/')[0] ?? ''
  const section = SECTIONS.find((s) => s.to === segment)
  if (section) return section
  const utility = UTILITIES.find((u) => u.to === segment)
  return utility
    ? {
        to: utility.to,
        label: utility.label,
        icon: utility.icon,
        phase: 'PREPARATION',
        lede: utility.lede,
        enabled: () => true,
      }
    : undefined
}

/**
 * Les volets d'un écran, pour la barre horizontale qui les porte.
 *
 * Déclarés ici et lus là-bas : la liste et les compteurs n'existent qu'une
 * fois, donc un onglet ajouté à la déclaration apparaît sans que l'écran ait
 * quoi que ce soit à savoir.
 */
export function subsOf(to: string): SubSection[] {
  return SECTIONS.find((s) => s.to === to)?.subs ?? []
}
