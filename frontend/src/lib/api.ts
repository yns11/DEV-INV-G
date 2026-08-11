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
  AggregateRow,
  Analytics,
  Arbitration,
  AssignableCause,
  AuditEvent,
  Campaign,
  CauseSplit,
  ConsolidationLine,
  ControlsPayload,
  ErpSource,
  Finding,
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
  MultiScanReport,
  Overview,
  PrintMode,
  SheetStatus,
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
export async function download(path: string): Promise<void> {
  const response = await fetch(`${BASE}${path}`, { headers: { accept: '*/*' } })
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
  listCampaigns: (includeClosed = true) =>
    request<Campaign[]>(`/campaigns${qs({ includeClosed })}`),
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
  transitionReadiness: (id: string, target: string) =>
    request<TransitionReadiness>(`/campaigns/${id}/transition-readiness${qs({ target })}`),
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
  ) =>
    request<{ total: number; offset: number; limit: number; rows: Array<Record<string, unknown>> }>(
      `/campaigns/${id}/items${qs(params)}`,
    ),
  // No ceiling here: the endpoint returns the whole structure, which is what
  // a nomenclature read half-way would make unusable anyway.
  boms: (id: string, params: { parent?: string; counted?: boolean } = {}) =>
    request<Array<Record<string, unknown>>>(`/campaigns/${id}/boms${qs(params)}`),
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
      findings: Finding[]
    }>(`/campaigns/${id}/bom-health`),
  bookStock: (id: string, params: { limit?: number; offset?: number } = {}) =>
    request<{
      total: number
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
  } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (options.sheet) form.append('sheet', options.sheet)
    if (options.replace) form.append('replace', 'true')
    if (options.dryRun) form.append('dryRun', 'true')
    return request<ImportResult & ImportPreview>(`/campaigns/${id}/import/${target}`, {
      method: 'POST',
      body: form,
    })
  },
  /** Whether an ERP read is possible, and from which tables. */
  erpSource: () => request<ErpSource>('/erp/source'),
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
  } = {}) =>
    request<ImportResult & ImportPreview>(
      `/campaigns/${id}/import/${target}/erp${qs({
        dryRun: options.dryRun || undefined,
        replace: options.replace || undefined,
        approvedOnly: options.approvedOnly || undefined,
      })}`,
      { method: 'POST' },
    ),
  importPaste: (id: string, target: string, text: string, options: {
    dryRun?: boolean
    replace?: boolean
  } = {}) =>
    request<ImportResult & ImportPreview>(`/campaigns/${id}/import/${target}/paste`, {
      method: 'POST',
      body: JSON.stringify({ text, ...options }),
    }),
  importRows: (id: string, target: string, rows: Array<Record<string, unknown>>, options: {
    dryRun?: boolean
    replace?: boolean
  } = {}) =>
    request<ImportResult & ImportPreview>(`/campaigns/${id}/import/${target}/rows`, {
      method: 'POST',
      body: JSON.stringify({ rows, ...options }),
    }),

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
  deleteZone: (id: string, zoneId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/generic/zones/${zoneId}`, {
      method: 'DELETE',
    }),
  sheet: (id: string, sheetId: string) =>
    request<{ sheet: Record<string, unknown>; lines: Array<Record<string, unknown>> }>(
      `/campaigns/${id}/generic/sheets/${sheetId}`,
    ),
  transitionSheet: (id: string, sheetId: string, target: SheetStatus, counterName?: string) =>
    request<Record<string, unknown>>(
      `/campaigns/${id}/generic/sheets/${sheetId}/transition`,
      { method: 'POST', body: JSON.stringify({ target, counterName }) },
    ),
  saveSheetLines: (id: string, sheetId: string, lines: unknown[], replace = false) =>
    request<{ written: number }>(`/campaigns/${id}/generic/sheets/${sheetId}/lines`, {
      method: 'PUT',
      body: JSON.stringify({ lines, replace }),
    }),
  deleteSheetLine: (id: string, lineId: string) =>
    request<{ deleted: boolean }>(`/campaigns/${id}/generic/lines/${lineId}`, {
      method: 'DELETE',
    }),
  setZoneNegative: (id: string, zoneIds: string[], allowed: boolean) =>
    request<{ updated: number }>(`/campaigns/${id}/generic/zones/negative`, {
      method: 'POST',
      body: JSON.stringify({ zoneIds, allowed }),
    }),
  /** A scan holding several sheets, routed by the footer the app printed. */
  scanMultipleSheets: (id: string, file: File, overwriteReviewed = false) => {
    const form = new FormData()
    form.append('file', file)
    form.append('overwriteReviewed', String(overwriteReviewed))
    return request<MultiScanReport>(`/campaigns/${id}/generic/scan`, {
      method: 'POST',
      body: form,
    })
  },
  scanSheet: (id: string, sheetId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ report: Record<string, unknown>; sheet: Record<string, unknown> }>(
      `/campaigns/${id}/generic/sheets/${sheetId}/scan`,
      { method: 'POST', body: form },
    )
  },
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
  allCountingSheets: (id: string, passNo: number, options: PrintOptions = {}) =>
    `/campaigns/${id}/reports/counting-sheets.pdf${qs({ passNo, ...printQuery(options) })}`,
}
