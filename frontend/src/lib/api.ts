/**
 * Typed HTTP client.
 *
 * One place that knows the URL shapes and the error contract, so every screen
 * gets the same behaviour: a failed call raises an `ApiError` carrying the
 * backend's stable `code`, its human-readable French `message` and its details.
 * Components render `error.message` directly — the backend already writes for
 * the end user, so the UI never has to invent a message.
 */

import type {
  Drift,
  DriftResolution,
  ErpJournal,
  LabelAlert,
  RecountedInPlace,
  LabelResolution,
  RescanLocation,
  ScopeCandidate,
  ClosureChecklist,
  CampaignPage,
  AggregateRow,
  Analytics,
  Arbitration,
  AssignableCause,
  AuditEvent,
  BackflushView,
  Campaign,
  CauseSplit,
  ConsolidationLine,
  ControlsPayload,
  ErpSource,
  Finding,
  FindingGroup,
  GridContract,
  Health,
  ImportPreview,
  ImportResult,
  Journal,
  JournalDetail,
  JournalStatus,
  Kpis,
  LocationStatus,
  ManagerOverview,
  Me,
  ScanJob,
  Overview,
  PrintMode,
  StockBasis,
  StockFlowCandidate,
  StockFlowErpRead,
  StockFlowErpRow,
  StockFlowInputRow,
  StockFlowReport,
  StockFlowSaveResult,
  StockFlowRun,
  Threshold,
  TransferAnalysis,
  TransitionReadiness,
  VarianceRow,
  WipBreakdownRow,
  WipWithoutBom,
  Zone,
} from './types'
import type {
  AssistantAnswer,
  AssistantProfiles,
  AssistantTurn,
} from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** Blocking findings returned by a refused transition or consolidation. */
  get findings(): Finding[] {
    const raw = this.details.findings ?? this.details.blockers
    return Array.isArray(raw) ? (raw as Finding[]) : []
  }
}

const BASE = '/api'

/**
 * Rows a grid asks for in one go — the server's own ceiling.
 *
 * Views are not paginated: a stock referential read half-way is a referential
 * you cannot trust, and a counter looking for one article should not have to
 * wonder whether it is on page 2. The ceiling stays as a safety valve for the
 * 6 GB container, and the grids say plainly when it truncates.
 */
export const GRID_ROW_CEILING = 20_000

/**
 * Une tranche de liste, et le nombre de lignes qu'elle ne montre pas.
 *
 * `total` est la partie qui compte : sans lui, une liste tronquée est
 * indistinguable d'une liste complète, et l'écran ne peut pas dire qu'il en
 * manque. Les trois référentiels — articles, nomenclatures, lignes de feuilles
 * — rendent désormais cette même forme.
 */
export type Page<T = Record<string, unknown>> = {
  total: number
  offset: number
  limit: number
  rows: T[]
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  })

  if (!response.ok) {
    let code = 'http_error'
    let message = `Erreur ${response.status}`
    let details: Record<string, unknown> = {}
    try {
      const payload = await response.json()
      code = payload.code ?? code
      message = payload.message ?? message
      details = payload.details ?? {}
    } catch {
      /* a non-JSON body (proxy timeout, HTML error page) keeps the defaults */
    }
    throw new ApiError(response.status, code, message, details)
  }

  if (response.status === 204) return undefined as T
  const type = response.headers.get('content-type') ?? ''
  if (!type.includes('application/json')) return (await response.text()) as T
  return response.json() as Promise<T>
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** Trigger a browser download without leaving the SPA. */
/**
 * Fetch a file and hand it to the browser, surfacing server refusals.
 *
 * Navigating an anchor straight at the URL is simpler, but it makes every
 * refusal invisible: printing an empty counting sheet answers 422 with a
 * perfectly clear message ("cette feuille ne contient aucune ligne"), and the
 * user saw nothing happen at all. Fetching first means an error can be raised
 * as an ApiError and shown like any other.
 */
export async function download(path: string, body?: unknown): Promise<void> {
  const response = await fetch(`${BASE}${path}`, {
    // A body means the file is built *from what the client is showing* — a
    // table export carries its own rows — so it cannot be a GET.
    ...(body === undefined
      ? { headers: { accept: '*/*' } }
      : {
          method: 'POST',
          headers: { accept: '*/*', 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
  })
  if (!response.ok) {
    let body: { code?: string; message?: string; details?: Record<string, unknown> } = {}
    try {
      body = await response.json()
    } catch {
      body = {}
    }
    throw new ApiError(
      response.status,
      body.code ?? 'download_failed',
      body.message ?? `Téléchargement impossible (HTTP ${response.status}).`,
      body.details ?? {},
    )
  }

  // Content-Disposition carries the server-chosen filename; fall back to the
  // last path segment so the file is never called "download".
  const disposition = response.headers.get('content-disposition') ?? ''
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  const filename = decodeURIComponent(match?.[1] ?? path.split('/').pop() ?? 'export')

  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export const api = {
  // ---------------------------------------------------------------- system
  health: () => request<Health>('/health'),
  me: () => request<Me>('/me'),
  contracts: () => request<GridContract[]>('/contracts'),

  // -------------------------------------------------------------- campaigns
  /**
   * Une page de campagnes, et combien il y en a en tout.
   *
   * La réponse était un tableau nu, borné à cent côté serveur sans le dire :
   * après quelques années d'inventaires, les plus anciennes disparaissaient de
   * l'écran sans qu'aucun message ne l'annonce. `total` est ce qui permet de
   * proposer les suivantes plutôt que de faire comme si elles n'existaient pas.
   */
  listCampaigns: (includeClosed = true, limit?: number) =>
    request<CampaignPage>(`/campaigns${qs({ includeClosed, limit })}`),
  getCampaign: (id: string) => request<Campaign>(`/campaigns/${id}`),
  overview: (id: string) => request<Overview>(`/campaigns/${id}/overview`),
  createCampaign: (body: {
    code: string
    label: string
    countDate: string
  }) => request<Campaign>('/campaigns', { method: 'POST', body: JSON.stringify(body) }),
  cloneCampaign: (body: {
    sourceCampaignId: string
    code: string
    label: string
    countDate: string
    includeZones: boolean
    includeSheetLines: boolean
  }) =>
    request<Campaign>('/campaigns/clone', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteCampaign: (id: string) =>
    request<void>(`/campaigns/${id}`, { method: 'DELETE' }),
  transitionReadiness: (id: string, target: string) =>
    request<TransitionReadiness>(`/campaigns/${id}/transition-readiness${qs({ target })}`),
  // Lisible pendant toute la phase d'analyse, et pas seulement dans la fenêtre
  // qui clôture : découvrir trois points bloquants au moment de cliquer, un
  // vendredi soir, est exactement ce qu'on évite.
  closureChecklist: (id: string) =>
    request<ClosureChecklist>(`/campaigns/${id}/closure-checklist`),
  transition: (id: string, target: string) =>
    request<Campaign>(`/campaigns/${id}/transition`, {
      method: 'POST',
      body: JSON.stringify({ target }),
    }),
  thresholds: (id: string) => request<Threshold[]>(`/campaigns/${id}/thresholds`),
  saveThresholds: (id: string, thresholds: unknown[]) =>
    request<Threshold[]>(`/campaigns/${id}/thresholds`, {
      method: 'PUT',
      body: JSON.stringify({ thresholds }),
    }),
  // Rend la campagne entière : le réglage vit dans sa configuration, et un
  // écran qui recollerait un fragment à ce qu'il avait déjà finirait par
  // afficher l'un pendant que la base porte l'autre.
  saveSettings: (id: string, settings: { allowFormulas: boolean }) =>
    request<Campaign>(`/campaigns/${id}/settings`, {
      method: 'PUT',
      body: JSON.stringify(settings),
    }),
  audit: (id: string, params: { entityType?: string; limit?: number } = {}) =>
    request<AuditEvent[]>(`/campaigns/${id}/audit${qs(params)}`),
  importHistory: (id: string) =>
    request<Array<Record<string, unknown>>>(`/campaigns/${id}/imports`),

  // ------------------------------------------------------------ referentials
  // `counted` keeps only what a GENERIQUE sheet or a counting journal names.
  // Filtered server-side so `total` keeps meaning what it says.
  items: (
    id: string,
    params: {
      limit?: number
      offset?: number
      search?: string
      counted?: boolean
    } = {},
  ) => request<Page>(`/campaigns/${id}/items${qs(params)}`),
  // No ceiling here: the endpoint returns the whole structure, which is what
  // a nomenclature read half-way would make unusable anyway.
  // Paginé comme les articles. Une nomenclature complète se compte en dizaines
  // de milliers de liens ; la lire entière pour en afficher trente était le
  // seul appel capable de tenir une seconde à lui tout seul.
  boms: (
    id: string,
    params: {
      parent?: string
      counted?: boolean
      limit?: number
      offset?: number
    } = {},
  ) => request<Page>(`/campaigns/${id}/boms${qs(params)}`),
  // Editing one line rather than re-importing the file. A referential arrives
  // with a designation missing here and a type wrong there; before this the
  // only remedy was to redo the whole load, so people stopped correcting.
  updateItem: (id: string, itemNumber: string, patch: Record<string, unknown>) =>
    request<Record<string, unknown>>(
      `/campaigns/${id}/items/${encodeURIComponent(itemNumber)}`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    ),
  // Une exclusion se décide par famille — un programme parti du site, une gamme
  // après-vente comptée ailleurs — pas ligne à ligne.
  setItemExclusions: (id: string, itemNumbers: string[], exclusions: string[]) =>
    request<{ updated: number; unchanged: number; exclusions: string[] }>(
      `/campaigns/${id}/items/exclusions`,
      { method: 'POST', body: JSON.stringify({ itemNumbers, exclusions }) },
    ),
  setBomActivation: (
    id: string,
    links: Array<{ parentItem: string; childItem: string }>,
    active: boolean,
  ) =>
    request<{ updated: number; unchanged: number }>(
      `/campaigns/${id}/boms/activation`,
      { method: 'POST', body: JSON.stringify({ links, active }) },
    ),
  // Paginé : une campagne de deux cents zones à trois cents lignes en fait
  // soixante mille, et c'est la liste qu'on ouvre pour en corriger une.
  sheetLines: (
    id: string,
    zoneId?: string,
    params: { limit?: number; offset?: number } = {},
  ) => request<Page>(`/campaigns/${id}/generic/lines${qs({ zoneId, ...params })}`),
  deleteSheetLines: (id: string, lineIds: string[]) =>
    request<{ deleted: number }>(`/campaigns/${id}/generic/lines/delete`, {
      method: 'POST',
      body: JSON.stringify({ lineIds }),
    }),
  // « D'où vient ce chiffre ? » — une seule forme de réponse pour toutes les
  // colonnes, donc une seule fenêtre côté écran.
  breakdown: (
    id: string,
    itemNumber: string,
    aspect: string,
    params: { warehouseId?: string; locationId?: string } = {},
  ) =>
    request<{
      itemNumber: string
      name: string
      aspect: string
      unit: string
      unitCost: number
      total: number
      totalValue: number
      rows: Array<Record<string, unknown>>
    }>(
      `/campaigns/${id}/analysis/breakdown/${encodeURIComponent(itemNumber)}${qs({
        aspect,
        ...params,
      })}`,
    ),
  alerts: (id: string) =>
    request<{ controls: number; consolidation: number }>(
      `/campaigns/${id}/analysis/alerts`,
    ),
  deleteItem: (id: string, itemNumber: string) =>
    request<{ deleted: boolean }>(
      `/campaigns/${id}/items/${encodeURIComponent(itemNumber)}`,
      { method: 'DELETE' },
    ),
  updateBomLink: (id: string, patch: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/campaigns/${id}/boms`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  deleteBomLink: (id: string, parent: string, child: string) =>
    request<{ deleted: boolean }>(
      `/campaigns/${id}/boms${qs({ parent, child })}`,
      { method: 'DELETE' },
    ),
  bomHealth: (id: string) =>
    request<{
      linkCount: number
      parentCount: number
      cycles: string[]
      summary: ControlsPayload['summary']
      groups: FindingGroup[]
      findings: Finding[]
    }>(`/campaigns/${id}/bom-health`),
  bookStock: (
    id: string,
    params: { limit?: number; offset?: number; top?: number } = {},
  ) =>
    request<{
      total: number
      totalValue: number
      /** Part de la valeur portée par les lignes retenues, `null` sans filtre. */
      topShare: number | null
      frozenAt: string | null
      rows: Array<Record<string, unknown>>
    }>(`/campaigns/${id}/book-stock${qs(params)}`),
  freezeBookStock: (id: string) =>
    request<Campaign>(`/campaigns/${id}/book-stock/freeze`, { method: 'POST' }),
  locations: (id: string) =>
    request<{
      warehouses: Array<Record<string, unknown>>
      locations: Array<Record<string, unknown>>
    }>(`/campaigns/${id}/locations`),

  // ----------------------------------------------------------------- imports
  importFile: (id: string, target: string, file: File, options: {
    sheet?: string
    replace?: boolean
    dryRun?: boolean
    /** Paramètres propres à la grille — les bornes de période, par exemple. */
    params?: Record<string, string | number | boolean | undefined>
  } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (options.sheet) form.append('sheet', options.sheet)
    if (options.replace) form.append('replace', 'true')
    if (options.dryRun) form.append('dryRun', 'true')
    return request<ImportResult & ImportPreview>(
      `/campaigns/${id}/import/${target}${qs(options.params ?? {})}`,
      { method: 'POST', body: form },
    )
  },
  /** Whether an ERP read is possible, and from which tables. */
  erpSource: () => request<ErpSource>('/erp/source'),
  /**
   * Les photos de stock que la source propose, la plus récente d'abord.
   *
   * Séparé de `erpSource`, lu par tous les écrans d'import : cette liste
   * n'intéresse que le Stock ERP, et elle coûte une requête à la source.
   */
  erpStockDates: () => request<{ dates: string[] }>('/erp/stock-dates'),
  /**
   * Load a grid straight from the ERP silver tables.
   *
   * Same dry-run-then-confirm loop as a file: `dryRun` returns what *would* be
   * loaded, without writing anything.
   */
  importErp: (id: string, target: string, options: {
    dryRun?: boolean
    replace?: boolean
    approvedOnly?: boolean
    params?: Record<string, string | number | boolean | undefined>
  } = {}) =>
    request<ImportResult & ImportPreview>(
      `/campaigns/${id}/import/${target}/erp${qs({
        dryRun: options.dryRun || undefined,
        replace: options.replace || undefined,
        approvedOnly: options.approvedOnly || undefined,
        ...(options.params ?? {}),
      })}`,
      { method: 'POST' },
    ),
  importPaste: (id: string, target: string, text: string, options: {
    dryRun?: boolean
    replace?: boolean
    params?: Record<string, string | number | boolean | undefined>
  } = {}) => {
    const { params, ...body } = options
    return request<ImportResult & ImportPreview>(
      `/campaigns/${id}/import/${target}/paste${qs(params ?? {})}`,
      { method: 'POST', body: JSON.stringify({ text, ...body }) },
    )
  },
  importRows: (id: string, target: string, rows: Array<Record<string, unknown>>, options: {
    dryRun?: boolean
    replace?: boolean
    params?: Record<string, string | number | boolean | undefined>
  } = {}) => {
    const { params, ...body } = options
    return request<ImportResult & ImportPreview>(
      `/campaigns/${id}/import/${target}/rows${qs(params ?? {})}`,
      { method: 'POST', body: JSON.stringify({ rows, ...body }) },
    )
  },

  // -------------------------------------------------------- comptages avancés
  erpJournals: (id: string) =>
    request<ErpJournal[]>(`/campaigns/${id}/early-counts/journals`),
  scopeProposal: (id: string, journalId: string) =>
    request<ScopeCandidate[]>(
      `/campaigns/${id}/early-counts/journals/${journalId}/scope-proposal`,
    ),
  declareScope: (
    id: string,
    journalId: string,
    locations: { warehouseId: string; locationId: string }[],
  ) =>
    request<{ locations: number }>(
      `/campaigns/${id}/early-counts/journals/${journalId}/scope`,
      { method: 'PUT', body: JSON.stringify({ locations }) },
    ),
  unsealJournal: (id: string, journalId: string, reason: string) =>
    request<{ locations: number }>(
      `/campaigns/${id}/early-counts/journals/${journalId}/unseal`,
      { method: 'POST', body: JSON.stringify({ reason }) },
    ),
  decideLabel: (
    id: string,
    body: {
      labelId: string
      itemNumber: string
      decision: LabelResolution
      sealedWarehouseId: string
      sealedLocationId: string
      otherWarehouseId: string
      otherLocationId: string
      comment?: string
    },
  ) =>
    request<LabelAlert>(`/campaigns/${id}/early-counts/label-alerts/decide`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  toRescan: (id: string) =>
    request<RescanLocation[]>(`/campaigns/${id}/early-counts/to-rescan`),
  drifts: (id: string) =>
    request<Drift[]>(`/campaigns/${id}/early-counts/drifts`),
  resolveDrifts: (
    id: string,
    body: {
      driftIds: string[]
      resolution: DriftResolution
      causeCode?: string
      comment?: string
    },
  ) =>
    request<{ resolved: number }>(`/campaigns/${id}/early-counts/drifts/resolve`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  labelAlerts: (id: string) =>
    request<LabelAlert[]>(`/campaigns/${id}/early-counts/label-alerts`),
  recountedInPlace: (id: string) =>
    request<RecountedInPlace[]>(
      `/campaigns/${id}/early-counts/recounted-in-place`,
    ),

  // ---------------------------------------------------------------- counting
  // `focus` is a server-side filter: the browser asks for it, the server
  // resolves who is asking and answers with that perimeter only. Nothing
  // outside it is ever sent, which is the whole point.
  journals: (
    id: string,
    params: { status?: string; warehouseId?: string; focus?: boolean } = {},
  ) => request<Journal[]>(`/campaigns/${id}/counting/journals${qs(params)}`),
  journal: (id: string, journalId: string) =>
    request<JournalDetail>(`/campaigns/${id}/counting/journals/${journalId}`),
  countingControls: (id: string) =>
    request<Finding[]>(`/campaigns/${id}/counting/controls`),
  setJournalStatus: (id: string, journalIds: string[], status: JournalStatus) =>
    request<{ updated: number }>(`/campaigns/${id}/counting/journals/status`, {
      method: 'POST',
      body: JSON.stringify({ journalIds, status }),
    }),
  saveJournalLine: (id: string, journalId: string, body: {
    lineId?: string | null
    itemNumber: string
    qty: number | null
    unit?: string
    comment?: string
  }) =>
    request<Record<string, unknown>>(
      `/campaigns/${id}/counting/journals/${journalId}/lines`,
      { method: 'POST', body: JSON.stringify(body) },
    ),
  deleteJournalLine: (id: string, lineId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/counting/lines/${lineId}`, {
      method: 'DELETE',
    }),
  setLocationStatus: (
    id: string,
    locations: Array<{ warehouseId: string; locationId: string }>,
    status: LocationStatus,
  ) =>
    request<{ updated: number; journalsRemoved: number; journalsCreated: number }>(
      `/campaigns/${id}/counting/locations/status`,
      { method: 'POST', body: JSON.stringify({ locations, status }) },
    ),

  // ---------------------------------------------------------------- GENERIQUE
  zones: (id: string, params: { focus?: boolean } = {}) =>
    request<Zone[]>(`/campaigns/${id}/generic/zones${qs(params)}`),
  createZone: (id: string, body: {
    code: string
    label?: string
    sector?: string
    displayOrder?: number
    passes?: 1 | 2
    freeEntry?: boolean
    managerCode?: string
  }) =>
    request<Zone>(`/campaigns/${id}/generic/zones`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  setZonePasses: (id: string, zoneIds: string[], passes: 1 | 2) =>
    request<{ updated: number; sheetsRemoved: number; sheetsCreated: number }>(
      `/campaigns/${id}/generic/zones/passes`,
      { method: 'POST', body: JSON.stringify({ zoneIds, passes }) },
    ),
  /**
   * Retire des zones et leurs feuilles, pendant la préparation seulement.
   *
   * Une liste même pour une seule zone : le bouton de ligne et l'action
   * groupée passent par le même chemin, donc par la même règle.
   */
  deleteZones: (id: string, zoneIds: string[]) =>
    request<{ zones: number; sheets: number }>(
      `/campaigns/${id}/generic/zones/delete`,
      { method: 'POST', body: JSON.stringify({ zoneIds }) },
    ),
  sheet: (id: string, sheetId: string) =>
    request<{ sheet: Record<string, unknown>; lines: Array<Record<string, unknown>> }>(
      `/campaigns/${id}/generic/sheets/${sheetId}`,
    ),
  /**
   * Déclare une zone terminée, ou la rouvre.
   *
   * La seule décision d'état du parcours de comptage. Elle a remplacé quatre
   * transitions par feuille — en attente, comptage, encodage, terminée — qu'il
   * fallait faire avancer à la main sans qu'aucune écriture n'en dépende.
   */
  setZoneClosed: (id: string, zoneId: string, closed: boolean) =>
    request<{ id: string; closed: boolean }>(
      `/campaigns/${id}/generic/zones/${zoneId}/closure`,
      { method: 'POST', body: JSON.stringify({ closed }) },
    ),
  // `expectedVersion` est la version de la feuille telle que l'écran l'a lue.
  // Un enregistrement qui remplace écrase l'ensemble : sans elle, deux
  // personnes sur la même feuille s'effacent l'une l'autre sans un mot.
  saveSheetLines: (
    id: string,
    sheetId: string,
    lines: unknown[],
    replace = false,
    expectedVersion?: number,
  ) =>
    request<{ written: number }>(`/campaigns/${id}/generic/sheets/${sheetId}/lines`, {
      method: 'PUT',
      body: JSON.stringify({ lines, replace, expectedVersion }),
    }),
  deleteSheetLine: (id: string, lineId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/generic/lines/${lineId}`, {
      method: 'DELETE',
    }),
  /**
   * Le texte imprimé en tête de chaque section d'une zone.
   *
   * Un texte vide n'est pas enregistré : il remet le défaut. La réponse rend ce
   * qui est **retenu**, pas ce qui a été envoyé, pour que l'écran voie cette
   * différence tout de suite plutôt qu'au prochain rechargement.
   */
  setSectionLabels: (id: string, zoneId: string, labels: Record<string, string>) =>
    request<{ labels: Record<string, string> }>(
      `/campaigns/${id}/generic/zones/${zoneId}/section-labels`,
      { method: 'POST', body: JSON.stringify({ labels }) },
    ),
  setZoneNegative: (id: string, zoneIds: string[], allowed: boolean) =>
    request<{ updated: number }>(`/campaigns/${id}/generic/zones/negative`, {
      method: 'POST',
      body: JSON.stringify({ zoneIds, allowed }),
    }),
  /**
   * Dépose une pile scannée. Rend un **travail**, pas un rapport.
   *
   * Cent feuilles font deux cents pages et se lisent en plusieurs minutes :
   * attendre le rapport dans cette requête faisait couper la passerelle avant
   * la fin. Le rapport arrive par `scanJob`, quand le travail est terminé.
   */
  scanMultipleSheets: (id: string, file: File, overwriteReviewed = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('overwriteReviewed', String(overwriteReviewed))
    return request<ScanJob>(`/campaigns/${id}/generic/scan`, {
      method: 'POST',
      body: form,
    })
  },
  /** Où en est la lecture d'une pile déposée. */
  scanJob: (id: string, jobId: string) =>
    request<ScanJob>(`/campaigns/${id}/generic/scan/jobs/${jobId}`),
  scanJobs: (id: string) =>
    request<ScanJob[]>(`/campaigns/${id}/generic/scan/jobs`),
  /**
   * Dépose le scan d'une feuille. Rend un **travail**, pas un rapport.
   *
   * Comme pour une pile : la lecture dure de dix secondes à plus d'une minute,
   * et l'attendre dans cette requête ne laissait rien à regarder. Le rapport
   * arrive par `scanJob`, quand le travail est terminé.
   */
  scanSheet: (id: string, sheetId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ScanJob>(`/campaigns/${id}/generic/sheets/${sheetId}/scan`, {
      method: 'POST',
      body: form,
    })
  },
  /** Le dernier scan de cette feuille, pour reprendre un suivi interrompu. */
  sheetScanJob: (id: string, sheetId: string) =>
    request<ScanJob | null>(`/campaigns/${id}/generic/sheets/${sheetId}/scan/job`),
  arbitrations: (id: string, zoneId?: string) =>
    request<Arbitration[]>(`/campaigns/${id}/generic/arbitrations${qs({ zoneId })}`),
  refreshArbitrations: (id: string, zoneId: string) =>
    request<Arbitration[]>(
      `/campaigns/${id}/generic/zones/${zoneId}/arbitrations/refresh`,
      { method: 'POST' },
    ),
  decideArbitration: (id: string, arbitrationId: string, qty: number, comment = '') =>
    request<{ decided: boolean }>(
      `/campaigns/${id}/generic/arbitrations/${arbitrationId}`,
      { method: 'POST', body: JSON.stringify({ qty, comment }) },
    ),
  // Fills the fields; it does not decide. Each line still has to be validated
  // before the consolidation will use it.
  prefillWithPass2: (id: string, zoneId: string) =>
    request<{ proposed: number }>(
      `/campaigns/${id}/generic/zones/${zoneId}/arbitrations/prefill-pass-2`,
      { method: 'POST' },
    ),
  wipWithoutBom: (id: string) =>
    request<WipWithoutBom[]>(`/campaigns/${id}/generic/wip-without-bom`),
  reclassifyWip: (id: string, lineIds: string[], section: 'WIP_OK' | 'LINE_SIDE' | 'WIP') =>
    request<{ updated: number }>(`/campaigns/${id}/generic/reclassify-wip`, {
      method: 'POST',
      body: JSON.stringify({ lineIds, section }),
    }),
  consolidationPreview: (id: string) =>
    request<{
      lines: ConsolidationLine[]
      totalQty: number
      zonesIncluded: string[]
      zonesSkipped: string[]
      findings: Finding[]
      groups: FindingGroup[]
      blocking: number
    }>(`/campaigns/${id}/generic/consolidation/preview`),
  consolidation: (id: string) =>
    request<{
      run: Record<string, unknown> | null
      lines: ConsolidationLine[]
      breakdown: WipBreakdownRow[]
    }>(`/campaigns/${id}/generic/consolidation`),
  runConsolidation: (id: string) =>
    request<{
      runId: string
      journalId: string
      lines: number
      totalQty: number
      zonesIncluded: string[]
      zonesSkipped: string[]
      findings: Finding[]
    }>(`/campaigns/${id}/generic/consolidation`, { method: 'POST' }),
  wipBreakdown: (id: string, itemNumber: string) =>
    request<WipBreakdownRow[]>(`/campaigns/${id}/generic/wip/${itemNumber}`),

  // ------------------------------------------------------------ gestionnaires
  managers: (id: string) => request<ManagerOverview>(`/campaigns/${id}/managers`),
  saveManagers: (id: string, managers: unknown[]) =>
    request<Array<Record<string, unknown>>>(`/campaigns/${id}/managers`, {
      method: 'PUT',
      body: JSON.stringify({ managers }),
    }),
  assignWarehouses: (
    id: string,
    assignments: Array<{ warehouseId: string; managerCode: string }>,
  ) =>
    request<{ updated: number }>(`/campaigns/${id}/managers/warehouses`, {
      method: 'POST',
      body: JSON.stringify({ assignments }),
    }),
  assignZones: (id: string, zoneIds: string[], managerCode: string) =>
    request<{ updated: number }>(`/campaigns/${id}/managers/zones`, {
      method: 'POST',
      body: JSON.stringify({ zoneIds, managerCode }),
    }),

  // ---------------------------------------------------------------- analysis
  kpis: (id: string) => request<Kpis>(`/campaigns/${id}/analysis/kpis`),
  variances: (id: string, params: {
    limit?: number
    materialOnly?: boolean
    granularity?: 'item' | 'item_location'
  } = {}) => request<VarianceRow[]>(`/campaigns/${id}/analysis/variances${qs(params)}`),
  aggregate: (id: string, dimension: string, limit = 200) =>
    request<AggregateRow[]>(`/campaigns/${id}/analysis/aggregate${qs({ dimension, limit })}`),
  transfers: (id: string, limit = 100) =>
    request<TransferAnalysis>(`/campaigns/${id}/analysis/transfers${qs({ limit })}`),
  pareto: (id: string, coverage = 0.8) =>
    request<AggregateRow[]>(`/campaigns/${id}/analysis/pareto${qs({ coverage })}`),
  controls: (id: string) => request<ControlsPayload>(`/campaigns/${id}/analysis/controls`),
  analytics: (id: string) => request<Analytics>(`/campaigns/${id}/analysis/analytics`),
  compare: (id: string, otherCampaignId: string) =>
    request<Record<string, unknown>>(
      `/campaigns/${id}/analysis/compare${qs({ otherCampaignId })}`,
    ),
  causes: (id: string) => request<AssignableCause[]>(`/campaigns/${id}/analysis/causes`),
  causeSplit: (id: string) => request<CauseSplit>(`/campaigns/${id}/analysis/cause-split`),
  saveVarianceAnalysis: (id: string, itemNumber: string, body: {
    causeCode: string | null
    comment: string
    accepted: boolean
  }) =>
    request<Record<string, unknown>>(`/campaigns/${id}/analysis/variances/${itemNumber}`, {
      method: 'PUT',
      body: JSON.stringify({ itemNumber, ...body }),
    }),
  suggestCauses: (id: string, maxItems = 40) =>
    request<{ suggestions: number }>(
      `/campaigns/${id}/analysis/ai/suggest-causes${qs({ maxItems })}`,
      { method: 'POST' },
    ),
  aiSummary: (id: string) =>
    request<{ markdown: string }>(`/campaigns/${id}/analysis/ai/summary`),
  explain: (id: string, itemNumber: string) =>
    request<{
      itemNumber: string
      explanation: string
      wipBreakdown: WipBreakdownRow[]
      movements: Array<Record<string, unknown>>
    }>(`/campaigns/${id}/analysis/ai/explain/${itemNumber}`),
  adjustments: (id: string, limit = 1000) =>
    request<Array<Record<string, unknown>>>(
      `/campaigns/${id}/analysis/adjustments${qs({ limit })}`,
    ),
  saveAdjustments: (id: string, rows: unknown[]) =>
    request<{ written: number }>(`/campaigns/${id}/analysis/adjustments`, {
      method: 'PUT',
      body: JSON.stringify(rows),
    }),
  deleteAdjustment: (id: string, lineId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/analysis/adjustments/${lineId}`, {
      method: 'DELETE',
    }),

  // --------------------------------------------------------------- backflush
  backflush: (id: string) =>
    request<BackflushView>(`/campaigns/${id}/analysis/backflush`),
  /** La période que l'écran pré-remplit, et que l'utilisateur peut changer. */
  backflushPeriod: (id: string) =>
    request<{ periodStart: string; periodEnd: string }>(
      `/campaigns/${id}/analysis/backflush/period`,
    ),

  // ----------------------------------------------- réconciliation de campagnes
  stockFlowCandidates: (id: string) =>
    request<StockFlowCandidate[]>(`/campaigns/${id}/stock-flow/candidates`),
  stockFlowRuns: (id: string) =>
    request<StockFlowRun[]>(`/campaigns/${id}/stock-flow`),
  openStockFlow: (id: string, baselineCampaignId: string) =>
    request<StockFlowRun>(`/campaigns/${id}/stock-flow`, {
      method: 'POST',
      body: JSON.stringify({ baselineCampaignId }),
    }),
  deleteStockFlow: (id: string, runId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/stock-flow/${runId}`, {
      method: 'DELETE',
    }),
  stockFlowReport: (
    id: string,
    runId: string,
    basis: { opening: StockBasis; closing: StockBasis } = {
      opening: 'PHYSICAL',
      closing: 'PHYSICAL',
    },
  ) =>
    request<StockFlowReport>(
      `/campaigns/${id}/stock-flow/${runId}${qs({
        openingBasis: basis.opening,
        closingBasis: basis.closing,
      })}`,
    ),
  refreshStockFlowErp: (id: string, runId: string) =>
    request<{
      /** Retenus : lus *et* présents au référentiel de la campagne. */
      items: number
      /** Lus depuis la table de faits, avant filtrage. */
      rowsRead: number
      outOfScope: number
      producedQty: number
      consumedQty: number
      /** La période réellement interrogée, et la table lue. */
      periodStart: string
      periodEnd: string
      source: string
      /** Lecture par le miroir local plutôt que par Unity Catalog. */
      mirror: boolean
      sourceLoadedAt: string | null
    }>(`/campaigns/${id}/stock-flow/${runId}/erp`, { method: 'POST' }),
  skipStockFlowScrap: (id: string, runId: string) =>
    request<{ scrapLoaded: boolean }>(
      `/campaigns/${id}/stock-flow/${runId}/scrap/skip`, { method: 'POST' },
    ),
  /** Les trois étapes chargées, lues dans l'ERP au lieu d'être retapées. */
  refreshStockFlowStep: (id: string, runId: string, kind: string) =>
    request<StockFlowErpRead>(
      `/campaigns/${id}/stock-flow/${runId}/erp/${kind}`, { method: 'POST' },
    ),
  /**
   * Les cinq mesures, en une lecture.
   *
   * Elles sont sur la même ligne de la table : ou bien la lecture aboutit et
   * les cinq sont écrites ensemble, ou bien elle échoue et `error` dit pourquoi
   * — les quantités précédentes restant alors intactes.
   */
  refreshStockFlowAll: (id: string, runId: string) =>
    request<{
      steps: Array<{ ok: true; kind: string; label: string; items: number }>
      loaded: number
      failed: number
      error?: string
      rowsRead?: number
      outOfScope?: number
      source?: string
    }>(`/campaigns/${id}/stock-flow/${runId}/erp-all`, { method: 'POST' }),
  stockFlowInputs: (id: string, runId: string, kind: string) =>
    request<StockFlowInputRow[]>(
      `/campaigns/${id}/stock-flow/${runId}/inputs/${kind}`,
    ),
  saveStockFlowInputs: (
    id: string, runId: string, kind: string, rows: readonly object[],
  ) =>
    request<StockFlowSaveResult>(
      `/campaigns/${id}/stock-flow/${runId}/inputs/${kind}`,
      { method: 'PUT', body: JSON.stringify({ rows }) },
    ),
  stockFlowErpRows: (id: string, runId: string) =>
    request<StockFlowErpRow[]>(`/campaigns/${id}/stock-flow/${runId}/erp-rows`),
  saveStockFlowErpRows: (
    id: string, runId: string, rows: readonly object[],
  ) =>
    request<StockFlowSaveResult>(`/campaigns/${id}/stock-flow/${runId}/erp-rows`, {
      method: 'PUT',
      body: JSON.stringify({ rows }),
    }),
  // Les trois chargements passent par la même boucle « voir avant d'écrire »
  // que toutes les autres grilles : c'est elle qui empêche un fichier de
  // devenir la vérité de la campagne sans que personne l'ait regardé.
  loadStockFlowFile: (
    id: string, runId: string, kind: string, file: File,
    options: { dryRun?: boolean; sheet?: string } = {},
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (options.sheet) form.append('sheet', options.sheet)
    if (options.dryRun) form.append('dryRun', 'true')
    return request<ImportResult & ImportPreview>(
      `/campaigns/${id}/stock-flow/${runId}/inputs/${kind}`,
      { method: 'POST', body: form },
    )
  },
  loadStockFlowPaste: (
    id: string, runId: string, kind: string, text: string,
    options: { dryRun?: boolean } = {},
  ) =>
    request<ImportResult & ImportPreview>(
      `/campaigns/${id}/stock-flow/${runId}/inputs/${kind}/paste`,
      { method: 'POST', body: JSON.stringify({ text, ...options }) },
    ),
  loadStockFlowRows: (
    id: string, runId: string, kind: string,
    rows: Array<Record<string, unknown>>,
    options: { dryRun?: boolean } = {},
  ) =>
    request<ImportResult & ImportPreview>(
      `/campaigns/${id}/stock-flow/${runId}/inputs/${kind}/rows`,
      { method: 'POST', body: JSON.stringify({ rows, ...options }) },
    ),
}

/** Download URLs, kept next to the API so paths live in one file. */
/**
 * Print options, shared by the single-sheet and whole-pass endpoints.
 *
 * `mode` is the whole contract: which of the three documents to produce. Which
 * ones a zone can produce right now comes from the server as `zone.printModes`
 * — the matrix is not re-implemented here.
 */
export type PrintOptions = {
  mode?: PrintMode
  /** Add the provenance and comment columns. Only meaningful with `filled`. */
  withSources?: boolean
  /** Rows on a free-entry sheet, 10–180. Only meaningful with `blank`. */
  blankLines?: number
}

const printQuery = (options: PrintOptions) => ({
  mode: options.mode ?? 'list',
  withSources: options.withSources ? true : undefined,
  blankLines: options.blankLines || undefined,
})

export const assistantApi = {
  /** The framings the server offers, and which one answers by default. */
  profiles: (id: string) =>
    request<AssistantProfiles>(`/campaigns/${id}/assistant/profiles`),
  /** A question, answered under the given framing. */
  ask: (id: string, question: string, history: AssistantTurn[], profile?: string) =>
    request<AssistantAnswer>(`/campaigns/${id}/assistant/ask`, {
      method: 'POST',
      body: JSON.stringify({ question, history, profile }),
    }),
  /** Same, with documents attached: images and PDFs are shown to the model. */
  askWithFiles: (
    id: string,
    question: string,
    history: AssistantTurn[],
    files: File[],
    profile?: string,
  ) => {
    const form = new FormData()
    form.append('question', question)
    form.append('history', JSON.stringify(history))
    if (profile) form.append('profile', profile)
    files.forEach((file) => form.append('files', file))
    return request<AssistantAnswer>(`/campaigns/${id}/assistant/ask-with-files`, {
      method: 'POST',
      body: form,
    })
  },
  /** Exactly what the model is shown — so the answers can be calibrated. */
  context: (id: string, profile?: string) =>
    request<Record<string, unknown>>(
      `/campaigns/${id}/assistant/context${qs({ profile })}`,
    ),
}

export const downloads = {
  campaignWorkbook: (id: string) => `/campaigns/${id}/reports/campaign.xlsx`,
  gridTemplate: (id: string, key: string) => `/campaigns/${id}/reports/grids/${key}.xlsx`,
  journal: (id: string, journalId: string) =>
    `/campaigns/${id}/reports/journals/${journalId}.xlsx`,
  countingSheet: (id: string, sheetId: string, options: PrintOptions = {}) =>
    `/campaigns/${id}/reports/counting-sheets/${sheetId}.pdf${qs(printQuery(options))}`,
  allCountingSheets: (
    id: string,
    passNo: number,
    options: PrintOptions & { zoneIds?: string } = {},
  ) =>
    `/campaigns/${id}/reports/counting-sheets.pdf${qs({
      passNo,
      zoneIds: options.zoneIds,
      ...printQuery(options),
    })}`,
  // Les filtres de l'écran voyagent avec l'export : un fichier qui ne
  // contiendrait pas ce qu'on avait sous les yeux au moment de cliquer serait
  // le genre d'écart qu'on ne découvre qu'en réunion.
  variances: (
    id: string,
    format: 'xlsx' | 'pdf',
    params: { granularity?: string; materialOnly?: boolean } = {},
  ) => `/campaigns/${id}/reports/variances.${format}${qs(params)}`,
  //ostensiblement générique : chaque grille poste ses propres colonnes et ses
  // propres lignes, donc un tableau ajouté demain a le bouton sans rien coder.
  table: (id: string) => `/campaigns/${id}/reports/table.xlsx`,
  /**
   * Les pièces justificatives : le fichier tel qu'il a été reçu.
   *
   * L'adresse ne porte que l'identifiant du lot ou de la feuille. Le serveur
   * sait où la pièce est rangée ; un chemin de volume dans une URL serait à la
   * fois du jargon exposé et une adresse que rien n'oblige à rester juste.
   */
  importEvidence: (id: string, batchId: string) =>
    `/campaigns/${id}/imports/${batchId}/evidence`,
  sheetEvidence: (id: string, sheetId: string) =>
    `/campaigns/${id}/sheets/${sheetId}/evidence`,
}
