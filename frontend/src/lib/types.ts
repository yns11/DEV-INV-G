/**
 * Wire types shared with the FastAPI backend.
 *
 * Hand-written rather than generated, because the surface is small and stable
 * and a hand-written type can carry the documentation that matters (what a KPI
 * actually means, why two reliability figures exist).
 */

export type CampaignStatus = 'PREPARATION' | 'COUNTING' | 'ANALYSIS' | 'CLOSED'
export type JournalStatus = 'PENDING' | 'IN_PROGRESS' | 'POSTED' | 'BOOK_ENFORCED'
/**
 * Trois états, dont deux se déduisent des quantités relevées.
 *
 * Une feuille de comptage n'a plus d'état propre : elle en avait quatre,
 * qu'il fallait faire avancer à la main deux fois par zone sans qu'aucune
 * écriture n'en dépende.
 */
export type ZoneStatus = 'PENDING' | 'IN_PROGRESS' | 'DONE'
export type CountSection = 'LINE_SIDE' | 'WIP' | 'WIP_OK'
export type ItemType =
  | 'COMPONENT' | 'SEMI_FINISHED' | 'FINISHED' | 'PACKAGING' | 'UNKNOWN'
export type LocationStatus = 'ACTIVE' | 'DISABLED'
export type Severity = 'BLOCKER' | 'WARNING' | 'INFO'
export type DataSource =
  | 'ERP_IMPORT' | 'FILE_IMPORT' | 'MANUAL' | 'SCAN_AI'
  | 'CONSOLIDATION' | 'ARBITRATION' | 'SYSTEM'

/** Une page de campagnes, avec de quoi savoir si elle en cache d'autres. */
export interface CampaignPage {
  items: Campaign[]
  total: number
  offset: number
}

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
  /** Ouverts tant que la campagne l'est ; la clôture les fige. */
  backflush: boolean
  stockFlow: boolean
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

/**
 * Ce que l'utilisateur est vis-à-vis de cette campagne.
 *
 * Pas un rôle global : la même personne pilote sa campagne et ne fait que lire
 * celle du trimestre précédent. `permissions` porte déjà le résultat — tout y
 * est faux pour un lecteur —, mais un écran entièrement grisé ne se distingue
 * pas d'une campagne clôturée. C'est ce bloc qui permet de dire laquelle des
 * deux, et à qui s'adresser.
 */
export interface Access {
  role: 'OWNER' | 'MANAGER' | 'READER'
  canWrite: boolean
  isOwner: boolean
  owner: string
}

export interface Overview {
  campaign: Campaign
  permissions: Permissions
  access: Access
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
  /**
   * Which steps are open, and why the others are not.
   *
   * Computed by the same function the API guard uses, so the interface can grey
   * out a step with the exact sentence a write would have been refused with.
   */
  sequence: {
    unlocked: Record<string, boolean>
    blockedBy: Record<string, string>
  }
}

/**
 * Whether the referential can be read straight from the ERP.
 *
 * `available` is false when no SQL warehouse is attached; the screen then says
 * why instead of offering a button that can only fail.
 */
export interface ErpSource {
  available: boolean
  reason: string | null
  /**
   * `uc` reads the silver tables live; `mirror` reads a local copy refreshed by
   * a scheduled job — the fallback when the application's service principal
   * cannot be granted access to the ERP's catalog.
   */
  source: 'uc' | 'mirror'
  tables: { items: string; boms: string }
  /** Age of the local copy, per grid. Null when reading the ERP live. */
  mirror: Record<string, { rows: number | null; syncedAt: string | null; stale: boolean | null }> | null
}

export interface Threshold {
  item_type: ItemType
  value_abs_eur: string | number
  qty_relative: string | number | null
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
  /** Le stock physique — compté plus mouvements postés : le terme de l'écart. */
  physicalQty: number | null
  physicalValue: number | null
  netVarianceQty: number | null
  netVarianceValue: number | null
  grossVarianceQty: number | null
  grossVarianceValue: number | null
  /**
   * L'écart tel que le comptage seul le montrait, et ce que les ajustements
   * ont posté depuis. Leur somme vaut `netVarianceValue` : le stock physique —
   * compté plus mouvements — est la référence, ces deux-là en sont la lecture
   * détaillée.
   */
  countedVarianceValue: number | null
  adjustedValue: number | null
  /** Ce que le backflush explique, et ce qui reste. */
  backflushShareValue: number | null
  unexplainedValue: number | null
  grossUnexplainedValue: number | null
  /** L'écart d'inventaire des seuls articles mesurés : les trois se soustraient. */
  backflushVarianceValue: number | null
  backflushExplanationRate: number | null
  backflushLineCount: number
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
  /**
   * Le stock physique — compté plus les mouvements postés après — et l'écart
   * qu'il creuse face à l'ERP gelé. C'est *la* référence : `countedVariance*`
   * garde à côté ce que le comptage seul montrait, avant ajustements.
   */
  physicalQty: number
  physicalValue: number
  varianceQty: number
  varianceValue: number
  adjustedQty: number
  adjustedValue: number
  countedVarianceQty: number
  countedVarianceValue: number
  /** Écart backflush brut, dans la convention backflush (théorique − réel). */
  backflushQty: number
  /** Le même, dans la convention d'inventaire : c'est lui qu'on soustrait. */
  backflushShareQty: number
  backflushShareValue: number
  unexplainedQty: number
  unexplainedValue: number
  explanationRate: number | null
  /** Distingue « mesuré et nul » de « jamais mesuré ». */
  backflushMeasured: boolean
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
  countedVarianceValue: number
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

/**
 * Un contrôle et son nombre d'occurrences.
 *
 * Les occurrences elles-mêmes ne voyagent qu'une fois, dans `findings` : on les
 * retrouve en filtrant sur `code`. Deux copies d'une même liste finiraient par
 * ne plus dire le même nombre.
 */
export interface FindingGroup {
  code: string
  label: string
  severity: Severity
  count: number
}

export interface ControlsPayload {
  summary: {
    total: number
    bySeverity: Record<string, number>
    byCode: Record<string, number>
    hasBlocker: boolean
  }
  groups: FindingGroup[]
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
  /**
   * Pages no sheet could be matched to — reported, never guessed. `read` is
   * what the model transcribed off the footer, which tells a damaged strip
   * apart from a page that simply belongs to another campaign.
   */
  unroutedPages: Array<{ page: number; read: string; note: string }>
  /** Une feuille que le modèle n'a pas pu lire. Nommée, jamais tue. */
  sheetsFailed?: Array<{
    sheetId: string
    zoneCode: string
    passNo: number
    pages: number[]
    reason: string
  }>
  /** Où le temps est passé. Absent des scans lus avant l'instrumentation. */
  timings?: Record<string, number | string>
}

/**
 * La lecture d'une pile scannée, suivie pendant qu'elle se fait.
 *
 * Le dépôt rend immédiatement ce travail en `QUEUED` ; l'écran l'interroge
 * jusqu'à `isDone`, et lit alors `report`. Sans ce suivi, six minutes de
 * traitement sont indistinguables d'une panne — et c'est précisément ce que
 * faisait la version qui attendait le rapport dans la requête de dépôt.
 */
/**
 * Ce que rend la lecture d'**une** feuille.
 *
 * Les listes portent des étiquettes prêtes à lire — « MASS-1 » ou
 * « MASS-1 [WIP non déclaré] » quand la référence figure deux fois sur la
 * feuille et qu'il faut dire laquelle vérifier.
 */
export interface SheetScanReport {
  linesExtracted: number
  counted: number
  pagesRead: number
  meanConfidence: number | null
  counterName: string
  startedAt: string | null
  endedAt: string | null
  lowConfidence: string[]
  missing: string[]
  unexpected: Array<{ text?: string; qty?: unknown; note?: string }>
  tokensUsed?: number
}

export interface ScanJob {
  id: string
  /**
   * Renseigné = le scan d'une feuille ; nul = une pile multi-feuilles.
   *
   * Les deux chemins partagent la table, le suivi et l'écran d'avancement :
   * ce qui les sépare est la lecture, et c'est ce champ qui la désigne.
   */
  sheetId: string | null
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  step: string
  filename: string
  totalPages: number
  pagesRouted: number
  sheetsTotal: number
  sheetsDone: number
  percent: number
  /** Multi-feuilles : `MultiScanReport`. Feuille seule : le rapport d'extraction. */
  report: MultiScanReport | SheetScanReport | Record<string, never>
  error: string
  createdBy: string
  createdAt: string | null
  startedAt: string | null
  finishedAt: string | null
  /** Vrai quand il n'y a plus rien à attendre — succès comme échec. */
  isDone: boolean
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
  /** Quand la zone a été déclarée terminée, et par qui. Null = encore ouverte. */
  closed_at: string | null
  closed_by: string
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
    items: Array<Record<string, unknown>>
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
  /** Which framing produced this answer — profiles can be switched mid-thread. */
  profile: string
  tokensUsed: number
  contextBlocks: string[]
  attachmentsRead: string[]
  /** What the assistant will and will not answer, in its own words. */
  scopeNote: string
}

/**
 * A framing of the assistant, served by the API rather than hard-coded here.
 *
 * `context` says how much of the campaign travels with a question: `none` (the
 * open profile — answers rest on nothing from the application), `digest`, or
 * `full`. `maxQuestionChars` of 0 means no ceiling.
 */
export interface AssistantProfile {
  key: string
  label: string
  description: string
  scopeNote: string
  context: 'none' | 'digest' | 'full'
  maxQuestionChars: number
  maxAnswerTokens: number
  temperature: number
}

export interface AssistantProfiles {
  /** The profile used when the request names none — the deployment default. */
  active: string
  profiles: AssistantProfile[]
}


// --------------------------------------------------------------------------- //
// Écart backflush
// --------------------------------------------------------------------------- //

/**
 * La période sur laquelle l'écart a été lu, et la fraîcheur de la source.
 *
 * Voyage avec les lignes plutôt que d'être demandée à part : un chiffre de
 * backflush sans ses bornes n'est pas interprétable, et deux réponses séparées
 * finissent par se contredire à l'écran.
 */
export interface BackflushPeriod {
  periodStart: string
  periodEnd: string
  weeks: number
  sourceLoadedAt: string | null
  refreshedAt: string | null
  items: number
}

export interface BackflushRow {
  itemNumber: string
  name: string
  itemType: string
  category: string
  program: string
  unit: string
  unitCost: number
  netQty: number
  underConsumedQty: number
  overConsumedQty: number
  theoreticalQty: number
  actualQty: number
  parentCount: number
  weekCount: number
  backflushShareQty: number
  backflushShareValue: number
  typeEcart: string
  /** `null` tant que l'article n'a pas été compté : « non comparé » n'est pas 0. */
  varianceQty: number | null
  varianceValue: number | null
  unexplainedQty: number | null
  unexplainedValue: number | null
  explanationRate: number | null
  compared: boolean
}

export interface BackflushView {
  period: BackflushPeriod | null
  kpis: Kpis
  rows: BackflushRow[]
}

// --------------------------------------------------------------------------- //
// Réconciliation entre deux campagnes
// --------------------------------------------------------------------------- //

export interface StockFlowCandidate {
  id: string
  code: string
  label: string
  countDate: string
  status: string
  weeks: number
}

export interface StockFlowRun {
  id: string
  campaignId: string
  baselineCampaignId: string
  periodStart: string
  periodEnd: string
  weeks: number
  scrapLoaded: boolean
  erpRefreshedAt: string | null
  /** Quand chaque étape a été lue dans l'ERP. */
  receiptsRefreshedAt: string | null
  shipmentsRefreshedAt: string | null
  scrapRefreshedAt: string | null
  baselineCode?: string
  baselineLabel?: string
  baselineCountDate?: string
  campaignCode?: string
  campaignCountDate?: string
}

/** D'où vient une quantité de la comparaison. */
export type FlowSource = 'ERP' | 'FILE' | 'MANUAL'

/** Où en est chaque étape de chargement. */
export interface StockFlowStep {
  kind: string
  label: string
  items: number
  totalQty: number
  loaded: boolean
  optional: boolean
  /**
   * Les provenances présentes dans l'étape, et la date de sa dernière lecture
   * ERP. Quatre étapes affichant un nombre se ressemblent ; « lu dans l'ERP il
   * y a deux minutes » et « corrigé à la main » ne se défendent pas pareil.
   */
  sources: FlowSource[]
  refreshedAt: string | null
  /** La sous-section qui détaille cette étape. */
  view: string
}

/** Une ligne d'étape chargée, telle que la grille éditable la montre. */
export interface StockFlowInputRow {
  itemNumber: string
  name: string
  unit: string
  qty: number
  source: FlowSource
}

/** Une ligne de l'instantané production / consommation théorique. */
export interface StockFlowErpRow {
  itemNumber: string
  name: string
  unit: string
  producedQty: number
  consumedQty: number
  source: FlowSource
}

/** Ce que renvoie une lecture ERP d'une étape. */
export interface StockFlowErpRead {
  kind: string
  label: string
  /** Retenus : lus *et* présents au référentiel de la campagne. */
  items: number
  /** Lus dans l'ERP, avant filtrage. */
  rowsRead: number
  outOfScope: number
  totalQty: number
  /** Somme signée telle que l'ERP la donne : négative = sortie nette. */
  netQty: number
  periodStart: string
  periodEnd: string
  source: string
}

export interface StockFlowSaveResult {
  rows: number
  /** Les références absentes du référentiel, nommées plutôt qu'ignorées. */
  unknown: string[]
  unknownCount: number
}

/** Un maillon de la chaîne, du stock initial au stock compté final. */
export interface StockFlowChainStep {
  key: string
  label: string
  qty: number
  value: number
  sign: number
  terminal: boolean
}

export interface StockFlowRow {
  itemNumber: string
  name: string
  unit: string
  unitCost: number
  openingQty: number
  receivedQty: number
  producedQty: number
  shippedQty: number
  consumedQty: number
  scrappedQty: number
  expectedQty: number
  closingQty: number
  varianceQty: number
  varianceValue: number
  varianceRatio: number | null
  hasOpening: boolean
  hasClosing: boolean
  complete: boolean
}

/**
 * Quels stocks encadrent les flux — « physique » veut dire compté ajusté.
 *
 * Un paramètre de *lecture* : les quantités chargées et l'instantané ERP gelé
 * ne bougent pas, si bien que les quatre combinaisons sont quatre vues d'une
 * même comparaison et non quatre comparaisons.
 */
export type StockBasis = 'PHYSICAL' | 'BOOK'

export interface StockFlowBasis {
  opening: StockBasis
  closing: StockBasis
  /** « Physique » / « ERP » — la forme courte des pastilles. */
  openingLabel: string
  closingLabel: string
  /** « Stock physique » / « Stock ERP » — la forme qui tient dans une phrase. */
  openingStockLabel: string
  closingStockLabel: string
  label: string
}

export interface StockFlowKpis {
  lineCount: number
  completeCount: number
  incompleteCount: number
  matchedCount: number
  expectedValue: number
  closingValue: number
  netVarianceValue: number
  grossVarianceValue: number
  netReliability: number | null
  grossReliability: number | null
}

export interface StockFlowReport {
  run: StockFlowRun
  basis: StockFlowBasis
  steps: StockFlowStep[]
  kpis: StockFlowKpis
  chain: StockFlowChainStep[]
  rows: StockFlowRow[]
}
