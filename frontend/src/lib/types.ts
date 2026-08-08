/**
 * Wire types shared with the FastAPI backend.
 *
 * Hand-written rather than generated, because the surface is small and stable
 * and a hand-written type can carry the documentation that matters (what a KPI
 * actually means, why two reliability figures exist).
 */

export type CampaignStatus = 'PREPARATION' | 'COUNTING' | 'ANALYSIS' | 'CLOSED'
export type JournalStatus = 'PENDING' | 'IN_PROGRESS' | 'POSTED' | 'BOOK_ENFORCED'
export type SheetStatus = 'PENDING' | 'COUNTING' | 'ENCODING' | 'DONE'
export type ZoneStatus =
  | 'PENDING' | 'PASS_1_RUNNING' | 'PASS_2_RUNNING' | 'ARBITRATION' | 'DONE'
export type CountSection = 'LINE_SIDE' | 'WIP' | 'WIP_OK'
export type ItemType =
  | 'COMPONENT' | 'SEMI_FINISHED' | 'FINISHED' | 'PACKAGING' | 'UNKNOWN'
export type LocationStatus = 'ACTIVE' | 'DISABLED'
export type Severity = 'BLOCKER' | 'WARNING' | 'INFO'
export type DataSource =
  | 'ERP_IMPORT' | 'FILE_IMPORT' | 'MANUAL' | 'SCAN_AI'
  | 'CONSOLIDATION' | 'ARBITRATION' | 'SYSTEM'

export interface Campaign {
  id: string
  code: string
  label: string
  count_date: string
  status: CampaignStatus
  config: CampaignConfig
  referentials_frozen_at: string | null
  book_stock_frozen_at: string | null
  counting_frozen_at: string | null
  closed_at: string | null
  cloned_from_code: string | null
  engine_version: string
  created_by: string
  created_at: string
}

export interface CampaignConfig {
  generic_warehouse: string
  generic_location: string
  generic_passes: number
  arbitration_tolerance: string | number
  max_bom_depth: number
  currency: string
}

/** What the current phase still allows to be modified. */
export interface Permissions {
  thresholds: boolean
  items: boolean
  boms: boolean
  locations: boolean
  bookStock: boolean
  zones: boolean
  countJournals: boolean
  countSheets: boolean
  adjustments: boolean
  analysis: boolean
}

/**
 * What the signed-in user owns, as resolved by the server.
 *
 * `resolved` is false when the identity is not registered as a manager. The
 * distinction matters: an unresolved perimeter is not "everything", it is
 * "nothing", and the interface has to say so rather than show an empty list
 * indistinguishable from a campaign without data.
 */
export interface PerimeterSummary {
  resolved: boolean
  managerCode: string | null
  managerLabel: string | null
  warehouses: string[]
  catchAll: boolean
  journalCount: number
  zoneCount: number
}

export interface Overview {
  campaign: Campaign
  permissions: Permissions
  perimeter: PerimeterSummary
  journalProgress: {
    total: number
    complete: number
    running: number
    pending: number
    ratio: number | null
  }
  genericProgress: {
    zones: number
    done: number
    ratio: number | null
    byStatus: Record<string, number>
    pendingArbitrations: number
  }
  counts: { items: number; bookStockLines: number }
}

export interface Threshold {
  item_type: ItemType
  value_abs_eur: string | number
  qty_relative: string | number | null
  qty_abs_floor: string | number
  ira_tolerance: string | number
}

/**
 * Headline campaign figures.
 *
 * Three reliability measures are returned on purpose — they answer three
 * different questions and are not interchangeable:
 *  - `netReliabilityValue`   offsets allowed: did we gain or lose overall?
 *  - `grossReliabilityValue` absolute errors: how much did we get wrong?
 *  - `ira`                   share of records within tolerance (WMS standard).
 */
export interface Kpis {
  bookQty: number | null
  bookValue: number | null
  countedQty: number | null
  countedValue: number | null
  netVarianceQty: number | null
  netVarianceValue: number | null
  grossVarianceQty: number | null
  grossVarianceValue: number | null
  residualValue: number | null
  netReliabilityValue: number | null
  grossReliabilityValue: number | null
  grossReliabilityQty: number | null
  ira: number | null
  lineCount: number
  accurateLineCount: number
  materialLineCount: number
  countedOnlyCount: number
  bookOnlyCount: number
}

export interface VarianceRow {
  itemNumber: string
  name: string
  warehouseId: string
  locationId: string
  itemType: ItemType
  category: string
  program: string
  unit: string
  unitCost: number
  bookQty: number
  bookValue: number
  countedQty: number
  varianceQty: number
  varianceValue: number
  adjustedQty: number
  residualQty: number
  residualValue: number
  finalQty: number
  countedOnly: boolean
  bookOnly: boolean
  isMaterial: boolean
  causeCode: string | null
  comment: string
  accepted: boolean
  aiSuggestedCause: string | null
  aiConfidence: number | null
  aiRationale: string
}

export interface AggregateRow {
  key: string
  bookQty: number
  bookValue: number
  varianceQty: number
  varianceValue: number
  absVarianceQty: number
  absVarianceValue: number
  residualValue: number
  lineCount: number
  materialCount: number
}

export interface Finding {
  code: string
  severity: Severity
  message: string
  entity_type: string
  entity_id: string
  item_number: string
  warehouse_id: string
  location_id: string
  context: Record<string, unknown>
}

export interface ControlsPayload {
  summary: {
    total: number
    bySeverity: Record<string, number>
    byCode: Record<string, number>
    hasBlocker: boolean
  }
  findings: Finding[]
}

export interface Journal {
  id: string
  campaign_id: string
  warehouse_id: string
  location_id: string
  kind: 'INVE' | 'INVV'
  status: JournalStatus
  journal_number: string
  description: string
  posted_at: string | null
  auto_created: boolean
  lineCount: number
  countedQty: number
  overriddenLines: number
  locationType: string
  locationStatus: LocationStatus
  zone: string
}

export interface JournalLine {
  id: string
  journal_id: string
  item_number: string
  qty_imported: number | null
  qty_manual: number | null
  qty: number
  unit: string
  source: DataSource
  effectiveSource: DataSource
  isOverridden: boolean
  comment: string
  bookQty: number
  varianceQty: number
  row_version?: number
}

export interface JournalDetail {
  journal: Journal
  lines: JournalLine[]
  notCounted: Array<{
    itemNumber: string
    bookQty: number
    unit: string
    value: number
  }>
}

export interface Sheet {
  id: string
  zone_id: string
  pass_no: 'PASS_1' | 'PASS_2'
  status: SheetStatus
  counter_name: string
  started_at: string | null
  ended_at: string | null
  evidence_path: string | null
  extraction_confidence: number | null
  lineCount: number
  countedLines: number
  /**
   * Lines the model read and a human then typed over.
   *
   * The reason a multi-sheet scan skips this sheet by default: that review is
   * the most expensive step in the chain, and re-reading the paper would undo
   * it silently.
   */
  correctedLines: number
}

/** Outcome of reading a scan that holds several counting sheets. */
export interface MultiScanReport {
  pages: number
  sheetsProcessed: Array<{
    sheetId: string
    zoneCode: string
    passNo: number
    pages: number[]
    overwroteCorrections: number
    counted: number
    lowConfidence: string[]
    unexpected: unknown[]
    missing: string[]
    meanConfidence: number | null
  }>
  sheetsSkipped: Array<{
    sheetId: string
    zoneCode: string
    passNo: number
    pages: number[]
    correctedLines: number
    reason: string
  }>
  /** Pages whose footer could not be read — reported, never guessed. */
  unroutedPages: Array<{ page: number; read: string; note: string }>
}

/**
 * Which of the three printable documents a sheet can produce.
 *
 * `blank` prints empty rows only (a free-entry zone), `list` prints the article
 * list with the quantity column empty (the sheet handed to a counter), `filled`
 * prints the counted quantities (the record). Which ones apply to a given zone
 * right now is decided server-side and arrives as `Zone.printModes`.
 */
export type PrintMode = 'blank' | 'list' | 'filled'

export const PRINT_MODE_LABELS: Record<PrintMode, string> = {
  blank: 'Feuille vierge — sans références',
  list: 'Feuille à compter — sans quantités',
  filled: 'Relevé — avec les quantités',
}

export interface Zone {
  id: string
  code: string
  label: string
  sector: string
  display_order: number
  /** Independent counts this zone requires — carried by the zone, not the campaign. */
  passes: number
  /** True when the sheet is deliberately blank: the counter writes what they find. */
  free_entry: boolean
  manager_code: string
  /** Whether a negative counted quantity is accepted on this zone's sheets. */
  allow_negative: boolean
  status: ZoneStatus
  pendingArbitrations: number
  /** What this zone can be printed as right now, in the order to offer them. */
  printModes: PrintMode[]
  sheets: Sheet[]
}

export interface Manager {
  code: string
  label: string
  /** Identity forwarded by the platform; what resolves « mon périmètre ». */
  actor: string
  active: boolean
  display_order: number
  zoneCount: number
  journalCount: number
}

export interface ManagerOverview {
  managers: Manager[]
  warehouses: Array<{
    warehouseId: string
    managerCode: string
    journalCount: number
    /** `AUTRES`: assigns every warehouse nobody named explicitly. */
    isCatchAll: boolean
    known: boolean
  }>
  zones: Array<{
    id: string
    code: string
    label: string
    sector: string
    managerCode: string
  }>
}

/**
 * The part of the variance that is a move between bins rather than a loss.
 *
 * `grossValue` counts a moved pallet twice — short in one bin, over in the
 * other — which is what drags the IRA down without anything being lost.
 * `netValue` is the per-reference reading, and the difference between the two
 * is the transfer.
 */
export interface TransferAnalysis {
  netValue: number
  grossValue: number
  transferValue: number
  transferShare: number
  itemCount: number
  rows: Array<{
    itemNumber: string
    name: string
    netValue: number
    grossValue: number
    transferValue: number
    transferShare: number
    locations: number
  }>
}

export interface SheetLine {
  id: string
  sheet_id: string
  item_number: string
  section: CountSection
  qty_imported: number | null
  qty_manual: number | null
  qty: number | null
  isCounted: boolean
  unit: string
  source: DataSource
  confidence: number | null
  comment: string
  display_order: number
  name: string
  known: boolean
  /** Pass-1 quantity for the same (article, section); null on a pass-1 sheet. */
  qtyPass1: number | null
}

export interface Arbitration {
  id: string
  zone_id: string
  item_number: string
  section: CountSection
  qty_pass_1: number | null
  qty_pass_2: number | null
  qty_arbitrated: number | null
  decided_by: string | null
  decided_at: string | null
  comment: string
  name: string
  gap: number
  gapValue: number
  unitCost: number
  divergent: boolean
  needsDecision: boolean
  /** A quantity is pre-filled and waiting for somebody to confirm or change it. */
  isProposed: boolean
}

export interface ConsolidationLine {
  item_number: string
  qty: number
  unit: string
  qty_line_side: number
  qty_wip_ok: number
  qty_wip_exploded: number
  zone_codes: string[]
  name: string
  value: number
  hasWip: boolean
}

export interface WipBreakdownRow {
  zone_code: string
  parent_item: string
  parent_qty: number
  child_item: string
  qty_per_parent: number
  child_qty: number
  depth: number
}

export interface WipWithoutBom {
  lineId: string
  sheetId: string
  passNo: string
  zoneId: string
  zoneCode: string
  itemNumber: string
  name: string
  itemType: ItemType
  qty: number
  unit: string
  knownItem: boolean
}

export interface FieldSpec {
  name: string
  label: string
  type: 'string' | 'number' | 'integer' | 'date' | 'datetime' | 'boolean' | 'enum'
  required: boolean
  aliases: string[]
  choices: string[]
  default: unknown
  help: string
  width: number
}

export interface GridContract {
  key: string
  title: string
  description: string
  hint: string
  naturalKey: string[]
  fields: FieldSpec[]
  examples: Array<Record<string, unknown>>
}

export interface ImportResult {
  target: string
  rowsReceived: number
  rowsAccepted: number
  rowsRejected: number
  ok: boolean
  errors: Array<{ line: number; column: string; value: string | null; message: string }>
  warnings: Array<{ line: number; column: string; value: string | null; message: string }>
  truncatedErrors: number
  missingColumns: string[]
  unknownColumns: string[]
  duplicateKeys: string[]
  batchId: string | null
  details: Record<string, unknown>
  duplicateOf?: {
    importedAt: string
    importedBy: string
    filename: string
    rowsAccepted: number
  }
}

export interface ImportPreview {
  contract: string
  rowsReceived: number
  rowsAccepted: number
  rowsRejected: number
  missingColumns: string[]
  unknownColumns: string[]
  duplicateKeys: string[]
  errors: Array<{ line: number; column: string; value: string | null; message: string }>
  truncatedErrors: number
  sample: Array<Record<string, unknown>>
}

export interface AuditEvent {
  id: string
  at: string
  actor: string
  action: string
  entity_type: string
  entity_id: string
  summary: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface AssignableCause {
  code: string
  label: string
  family: string
  description: string
  display_order: number
  active: boolean
}

export interface CauseSplit {
  rows: Array<{
    code: string | null
    label: string
    family?: string
    value: number
    absValue: number
    items: number
    share: number
  }>
  unassignedShare: number
}

export interface Analytics {
  available: boolean
  reason?: string
  abcXyz?: {
    summary: Array<{
      segment: string
      items: number
      book_value: number
      abs_variance_value: number
    }>
    items: Array<Record<string, unknown>>
  }
  pareto?: Array<Record<string, unknown>>
  anomalies?: {
    method: string
    contamination: number
    features: string[]
    flagged: Array<Record<string, unknown>>
  }
  clusters?: {
    n: number
    silhouette: number | null
    profiles: Array<{
      cluster: number
      items: number
      lines: number
      total_abs_variance: number
      total_book_value: number
      median_variance_ratio: number | null
      label: string
    }>
  }
  recountPriority?: Array<{
    item_number: string
    warehouse_id?: string
    location_id?: string
    variance_value: number
    abs_variance_value: number
    variance_ratio: number | null
    wip_share?: number
    movement_count?: number
    p_counting_error: number
    recount_expected_value: number
  }>
  dataQuality?: {
    benford: {
      digits: number[]
      observed: number[]
      expected: number[]
      chiSquare: number
      pValue: number
      sampleSize: number
      conclusion: string
    }
    digitPreference: {
      sampleSize: number
      buckets: Record<string, number>
      roundingIndex: number
      conclusion: string
    }
  }
}

export interface TransitionReadiness {
  current: CampaignStatus
  target: CampaignStatus
  allowed: boolean
  ready: boolean
  blockers: Finding[]
}

export interface Health {
  status: string
  ready: boolean
  version: string
  env: string
  lakebaseConfigured: boolean
  warehouseConfigured: boolean
  llmEndpoint: string
  startupError: string | null
}

export interface Me {
  actor: string
  authenticated: boolean
  source: string
}

/**
 * The campaign assistant.
 *
 * `contextBlocks` names what the answer was built from. Showing it is what lets
 * somebody calibrate their trust in a given answer instead of taking the whole
 * surface on faith — or dismissing it wholesale after one bad reply.
 */
export interface AssistantTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface AssistantAnswer {
  answer: string
  tokensUsed: number
  contextBlocks: string[]
  attachmentsRead: string[]
  /** What the assistant will and will not answer, in its own words. */
  scopeNote: string
}
