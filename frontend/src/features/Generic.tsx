/**
 * GENERIQUE: zones, double counting, arbitration and consolidation.
 *
 * This is the screen that replaces `Compil GENERIQUE.xlsx` — its 40 tabs, its
 * Power Query chain and the copy/paste into the ERP.
 */

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import { useSubSection } from '../lib/subsection'
import type {
  Arbitration,
  ConsolidationLine,
  Finding,
  MultiScanReport,
  Overview,
  ScanJob,
  PrintMode,
  Sheet,
  SheetStatus,
  Zone,
} from '../lib/types'
import {
  SECTION_HINTS,
  SECTION_LABELS,
  SHEET_STATUS_LABELS,
  ZONE_STATUS_LABELS,
  moneyShort,
  qty,
  percent,
  signedNum,
  label as toLabel,
} from '../lib/format'
import { CompositionBar } from '../components/charts'
import { DataGrid, SourceBadge, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { PrintModal } from '../components/PrintModal'
import { SubSectionTabs } from '../components/SubSectionTabs'
import { parseSheetLines } from '../lib/pasteSheetLines'
import { useFocusMode } from '../lib/focus'
import { CreateZoneModal } from './zones'
import {
  Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Icons, Modal, Progress,
  Skeleton, useErrorToast, useToast,
} from '../components/ui'

type Tab = 'zones' | 'arbitration' | 'consolidation'

const ZONE_TONE: Record<string, string> = {
  PENDING: 'neutral',
  PASS_1_RUNNING: 'accent',
  PASS_2_RUNNING: 'accent',
  ARBITRATION: 'warning',
  DONE: 'success',
}

const SHEET_TONE: Record<SheetStatus, string> = {
  PENDING: 'neutral',
  COUNTING: 'accent',
  ENCODING: 'warning',
  DONE: 'success',
}

const TABS: Tab[] = ['zones', 'arbitration', 'consolidation']

/**
 * Où en est une zone, du point de vue de qui distribue le papier.
 *
 * Le statut de la zone répond à « peut-elle être consolidée ? » ; celui-ci
 * répond à « qu'est-ce qui se passe dessus en ce moment ? », qui est la
 * question qu'on se pose le jour J devant quarante zones. Les deux ne se
 * déduisent pas l'un de l'autre : une zone en arbitrage et une zone dont
 * personne n'a encore pris la feuille sont toutes deux « pas finies », et ce
 * n'est pas la même chose à faire.
 */
type ZoneStage =
  | 'pending'
  | 'count_1'
  | 'encode_1'
  | 'count_2'
  | 'encode_2'
  | 'done'

const STAGE_LABELS: Array<{ id: ZoneStage; label: string; hint: string }> = [
  {
    id: 'pending',
    label: 'En attente',
    hint: 'Aucune feuille en cours : à démarrer, entre deux comptages, ou en attente d’arbitrage.',
  },
  { id: 'count_1', label: '1er comptage en cours', hint: 'La feuille n°1 est sur le terrain.' },
  { id: 'encode_1', label: '1er encodage en cours', hint: 'La feuille n°1 est rentrée, sa saisie est ouverte.' },
  { id: 'count_2', label: '2ème comptage en cours', hint: 'La feuille n°2 est sur le terrain.' },
  { id: 'encode_2', label: '2ème encodage en cours', hint: 'La feuille n°2 est rentrée, sa saisie est ouverte.' },
  { id: 'done', label: 'Terminé', hint: 'Comptages rendus, écarts arbitrés : la zone entre dans la consolidation.' },
]

/**
 * L'étape d'une zone, la plus avancée d'abord.
 *
 * L'ordre des tests est ce qui compte : une zone dont la feuille n°2 est
 * partie alors que la saisie de la n°1 traîne encore est affichée sur son
 * comptage n°2 — c'est ce qui est en jeu maintenant.
 */
function stageOf(zone: Zone): ZoneStage {
  if (zone.status === 'DONE') return 'done'
  const statusOf = (pass: Sheet['pass_no']) =>
    zone.sheets.find((sheet) => sheet.pass_no === pass)?.status
  const pass1 = statusOf('PASS_1')
  const pass2 = statusOf('PASS_2')
  if (pass2 === 'ENCODING') return 'encode_2'
  if (pass2 === 'COUNTING') return 'count_2'
  if (pass1 === 'ENCODING') return 'encode_1'
  if (pass1 === 'COUNTING') return 'count_1'
  return 'pending'
}

export function Generic() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useSubSection<Tab>('zones', TABS)

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <SubSectionTabs
        section="compil"
        overview={overview}
        value={tab}
        onChange={setTab}
      />
      {tab === 'zones' && <ZonesTab campaignId={campaignId} overview={overview} />}
      {tab === 'arbitration' && <ArbitrationTab campaignId={campaignId} overview={overview} />}
      {tab === 'consolidation' && (
        <ConsolidationTab campaignId={campaignId} overview={overview} />
      )}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Zones
// --------------------------------------------------------------------------- //

function ZonesTab({ campaignId, overview }: { campaignId: string; overview: Overview }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [creating, setCreating] = useState(false)
  const [printing, setPrinting] = useState(false)
  const [printSheet, setPrintSheet] = useState<{ sheetId: string; zone: Zone } | null>(null)
  const [multiScan, setMultiScan] = useState<File | null>(null)
  const [scanning, setScanning] = useState(false)
  const [openSheet, setOpenSheet] = useState<{ zone: Zone; sheet: Sheet } | null>(null)
  const [stage, setStage] = useState<ZoneStage | ''>('')

  const [focus] = useFocusMode()
  // A zone created here needs its manager straight away: with the focus switch
  // on, a zone assigned to nobody would disappear from the list of the very
  // person who just created it.
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })
  const query = useQuery({
    queryKey: ['zones', campaignId, focus],
    // The filtering happens on the server: what the perimeter excludes never
    // reaches this browser at all.
    queryFn: () => api.zones(campaignId, focus ? { focus: true } : {}),
    refetchInterval: 45_000,
  })

  // A batch print offers the modes at least one zone can actually produce, and
  // says how many sheets each would yield — the two are the same question.
  const batch = useMemo(() => {
    const counts = { blank: 0, list: 0, filled: 0 } as Record<PrintMode, number>
    for (const zone of query.data ?? []) {
      for (const mode of zone.printModes ?? []) counts[mode] = (counts[mode] ?? 0) + 1
    }
    const modes = (['list', 'blank', 'filled'] as PrintMode[]).filter(
      (m) => (counts[m] ?? 0) > 0,
    )
    return { modes, counts }
  }, [query.data])

  // Combien de zones par étape, et lesquelles sont à l'écran. Les deux se
  // calculent d'un coup : une pilule qui annoncerait un nombre différent de ce
  // qu'elle affiche une fois cliquée serait pire que pas de pilule du tout.
  const { byStage, visible } = useMemo(() => {
    const zones = query.data ?? []
    const tally = {} as Record<ZoneStage, number>
    for (const zone of zones) {
      const id = stageOf(zone)
      tally[id] = (tally[id] ?? 0) + 1
    }
    return {
      byStage: tally,
      visible: stage === '' ? zones : zones.filter((z) => stageOf(z) === stage),
    }
  }, [query.data, stage])

  const transition = useMutation({
    mutationFn: ({ sheetId, target, counterName }: {
      sheetId: string
      target: SheetStatus
      counterName?: string
    }) => api.transitionSheet(campaignId, sheetId, target, counterName),
    onSuccess: (_result, { target }) => {
      void queryClient.invalidateQueries()
      toast.success(
        `Feuille ${toLabel(SHEET_STATUS_LABELS, target).toLowerCase()}`,
        target === 'ENCODING'
          ? 'Ouvrez-la pour saisir les quantités relevées.'
          : undefined,
      )
    },
    onError: (error) => showError(error, 'Transition impossible'),
  })

  const editable = overview.permissions.countSheets

  return (
    <div className="stack">
      <div className="row-wrap">
        <Button
          variant="primary"
          icon={<Icons.plus size={14} />}
          disabled={!overview.permissions.zones}
          onClick={() => setCreating(true)}
        >
          Créer une zone
        </Button>
        <Button icon={<Icons.printer size={14} />} onClick={() => setPrinting(true)}>
          Imprimer les feuilles
        </Button>
        <label className="btn btn--secondary" style={{ cursor: 'pointer' }}>
          <Icons.sparkles size={14} />
          {scanning ? 'Lecture en cours…' : 'Importer un scan multi-feuilles'}
          <input
            type="file"
            hidden
            accept="application/pdf,image/*"
            disabled={!editable || scanning}
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) setMultiScan(file)
              event.target.value = ''
            }}
          />
        </label>
      </div>

      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={280} />}
        isEmpty={(zones) => zones.length === 0}
        empty={
          <Card>
            <EmptyState
              title={focus ? 'Aucune zone dans votre périmètre' : 'Aucune zone'}
              action={
                focus ? null : (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    Créer la première zone
                  </Button>
                )
              }
            >
              {focus
                ? 'Aucune zone ne vous est affectée. Coupez « Mon périmètre » pour voir toute la campagne — vous gardez le droit d’y agir.'
                : 'Créez une zone par aire physique (bord de ligne, picking, métrologie…), ou chargez la grille « Feuilles de comptage » en préparation pour les créer avec leur liste d’articles.'}
            </EmptyState>
          </Card>
        }
      >
        {(zones) => (
          <div className="stack">
            <div className="chips">
              <button
                className={`chip${stage === '' ? ' chip--active' : ''}`}
                onClick={() => setStage('')}
              >
                Tous <span className="num">{zones.length}</span>
              </button>
              {STAGE_LABELS.map(({ id, label, hint }) => (
                <button
                  key={id}
                  className={`chip${stage === id ? ' chip--active' : ''}`}
                  title={hint}
                  onClick={() => setStage(stage === id ? '' : id)}
                >
                  {label} <span className="num">{byStage[id] ?? 0}</span>
                </button>
              ))}
            </div>

            {visible.length === 0 ? (
              <Card>
                <EmptyState
                  title="Aucune zone à cette étape"
                  action={
                    <Button variant="ghost" onClick={() => setStage('')}>
                      Voir toutes les zones
                    </Button>
                  }
                />
              </Card>
            ) : (
              <div className="grid grid--2">
                {visible.map((zone) => (
                  <Card
                    key={zone.id}
                    title={
                      <span className="row" style={{ gap: 'var(--space-3)' }}>
                        <span className="truncate">{zone.label || zone.code}</span>
                        <Badge tone={ZONE_TONE[zone.status] ?? 'neutral'} dot>
                          {toLabel(ZONE_STATUS_LABELS, zone.status)}
                        </Badge>
                        {zone.passes === 1 && (
                          <Badge tone="warning" title="Un seul comptage : aucun arbitrage possible">
                            comptage unique
                          </Badge>
                        )}
                        {zone.free_entry && (
                          <Badge tone="info" title="Feuille volontairement vide : le compteur écrit ce qu’il trouve">
                            saisie libre
                          </Badge>
                        )}
                      </span>
                    }
                    message={zone.sector || undefined}
                  >
                    <div className="stack" style={{ gap: 'var(--space-3)' }}>
                      {zone.sheets.map((sheet) => (
                        <div
                          key={sheet.id}
                          className="row-wrap"
                          style={{
                            padding: 'var(--space-3)',
                            background: 'var(--bg-inset)',
                            borderRadius: 'var(--radius-md)',
                          }}
                        >
                          <strong style={{ fontSize: 'var(--text-sm)', minWidth: 90 }}>
                            Comptage n°{sheet.pass_no === 'PASS_1' ? 1 : 2}
                          </strong>
                          <Badge tone={SHEET_TONE[sheet.status]}>
                            {toLabel(SHEET_STATUS_LABELS, sheet.status)}
                          </Badge>
                          <span className="subtle num">
                            {sheet.countedLines} / {sheet.lineCount} lignes comptées
                          </span>
                          {sheet.counter_name && (
                            <span className="subtle">· {sheet.counter_name}</span>
                          )}
                          {sheet.extraction_confidence !== null && (
                            <Badge tone={sheet.extraction_confidence < 0.75 ? 'danger' : 'neutral'}>
                              IA {percent(sheet.extraction_confidence)}
                            </Badge>
                          )}
                          {sheet.correctedLines > 0 && (
                            <Badge
                              tone="success"
                              title="Un scan multi-feuilles préservera cette feuille plutôt que d’écraser ces corrections."
                            >
                              {sheet.correctedLines} corrigée(s) à la main
                            </Badge>
                          )}
                          <span className="spacer" />
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Icons.printer size={13} />}
                            onClick={() => setPrintSheet({ sheetId: sheet.id, zone })}
                            aria-label="Imprimer"
                            title="Imprimer cette feuille — vierge ou remplie"
                          />
                          {/* La saisie n'a lieu qu'à l'encodage. Proposer
                              « Ouvrir » avant, c'est inviter à remplir la
                              feuille pendant que le compteur est encore en
                              train de la remplir sur le papier ; après, c'est
                              rouvrir sans le dire ce que quelqu'un a validé —
                              « Modifier » le dit. L'impression, elle, reste
                              accessible à tout moment. */}
                          {sheet.status === 'ENCODING' && (
                            <Button
                              size="sm"
                              onClick={() => setOpenSheet({ zone, sheet })}
                            >
                              Ouvrir
                            </Button>
                          )}
                          {editable && (
                            <Button
                              size="sm"
                              variant="primary"
                              disabled={transition.isPending}
                              onClick={() =>
                                transition.mutate({
                                  sheetId: sheet.id,
                                  target: SHEET_ACTION[sheet.status].target,
                                })
                              }
                            >
                              {SHEET_ACTION[sheet.status].label}
                            </Button>
                          )}
                        </div>
                      ))}
                      {zone.pendingArbitrations > 0 && (
                        <Alert tone="warning" title={`${zone.pendingArbitrations} écart(s) à arbitrer`}>
                          La consolidation reste bloquée tant qu’une quantité n’est pas
                          retenue.
                        </Alert>
                      )}
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}
      </AsyncBoundary>

      {creating && (
        <CreateZoneModal
          campaignId={campaignId}
          managers={managers.data?.managers ?? []}
          onClose={() => setCreating(false)}
        />
      )}
      {printing && (
        <PrintModal
          campaignId={campaignId}
          modes={batch.modes}
          zonesByMode={batch.counts}
          onClose={() => setPrinting(false)}
        />
      )}
      {printSheet && (
        <PrintModal
          campaignId={campaignId}
          sheetId={printSheet.sheetId}
          modes={printSheet.zone.printModes}
          onClose={() => setPrintSheet(null)}
        />
      )}
      {multiScan && (
        <MultiScanModal
          campaignId={campaignId}
          file={multiScan}
          zones={query.data ?? []}
          onBusy={setScanning}
          onClose={() => setMultiScan(null)}
        />
      )}
      {openSheet && (
        <SheetModal
          campaignId={campaignId}
          zone={openSheet.zone}
          sheet={openSheet.sheet}
          editable={editable}
          onClose={() => setOpenSheet(null)}
        />
      )}
    </div>
  )
}

/**
 * Le bouton bleu d'une feuille : ce qu'il fait, et comment il s'appelle.
 *
 * Il porte l'action suivante, jamais l'état courant — « Commencer le comptage »
 * tant que rien n'a commencé, « Valider » quand la saisie est faite. Une
 * feuille terminée n'est pas close pour autant : « Modifier » la ramène en
 * encodage, et le couple Modifier / Valider se répète autant de fois qu'il le
 * faut. C'est le seul bouton bleu de la ligne, donc le seul endroit où
 * quelqu'un a besoin de regarder pour savoir quoi faire.
 */
const SHEET_ACTION: Record<SheetStatus, { target: SheetStatus; label: string }> = {
  PENDING: { target: 'COUNTING', label: 'Commencer le comptage' },
  COUNTING: { target: 'ENCODING', label: 'Commencer l’encodage' },
  ENCODING: { target: 'DONE', label: 'Valider' },
  DONE: { target: 'ENCODING', label: 'Modifier' },
}

function SheetModal({
  campaignId,
  zone,
  sheet,
  editable,
  onClose,
}: {
  campaignId: string
  zone: Zone
  sheet: Sheet
  editable: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Array<Record<string, unknown>> | null>(null)
  const [pasteText, setPasteText] = useState('')
  const [scanning, setScanning] = useState(false)
  const [printing, setPrinting] = useState(false)

  const query = useQuery({
    queryKey: ['sheet', campaignId, sheet.id],
    queryFn: () => api.sheet(campaignId, sheet.id),
  })

  const save = useMutation({
    mutationFn: (rows: Array<Record<string, unknown>>) =>
      api.saveSheetLines(
        campaignId,
        sheet.id,
        rows.map((row, index) => ({
          id: row.id ?? null,
          itemNumber: String(row.item_number ?? ''),
          section: String(row.section ?? 'LINE_SIDE'),
          qty:
            row.qty === null || row.qty === undefined || row.qty === ''
              ? null
              : Number(String(row.qty).replace(',', '.')),
          unit: String(row.unit ?? 'PCE'),
          comment: String(row.comment ?? ''),
          displayOrder: index,
        })),
        true,
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setDraft(null)
      toast.success(`${result.written} ligne(s) enregistrée(s)`)
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const scan = useMutation({
    mutationFn: (file: File) => api.scanSheet(campaignId, sheet.id, file),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setDraft(null)
      setScanning(false)
      const report = result.report as Record<string, unknown>
      const low = (report.lowConfidence as string[]) ?? []
      const unexpected = (report.unexpected as unknown[]) ?? []
      toast.push({
        tone: low.length || unexpected.length ? 'warning' : 'success',
        title: `Scan lu : ${report.counted} quantité(s) extraite(s)`,
        body: [
          low.length ? `${low.length} valeur(s) à confiance faible` : null,
          unexpected.length ? `${unexpected.length} lecture(s) hors liste attendue` : null,
          'Vérifiez et validez avant de terminer l’encodage.',
        ]
          .filter(Boolean)
          .join(' · '),
      })
    },
    onError: (error) => {
      setScanning(false)
      showError(error, 'Extraction impossible')
    },
  })

  const rows = draft ?? (query.data?.lines as Array<Record<string, unknown>>) ?? []

  /**
   * Append a pasted block to the draft.
   *
   * Appended, never substituted: a paste is an addition to what the counter
   * already has in front of them, and silently replacing their work would be
   * the one unrecoverable mistake here. Nothing is written until "Enregistrer",
   * so the grid stays the place where the result is checked.
   */
  const appendPasted = (text: string) => {
    const { lines, rejected, headerSkipped } = parseSheetLines(text)
    if (lines.length === 0) {
      toast.error(
        'Aucune ligne exploitable dans ce collage',
        'Chaque ligne doit contenir une référence article.',
      )
      return
    }
    const base = draft ?? rows
    setDraft([
      ...base,
      ...lines.map((line, index) => ({
        item_number: line.item_number,
        section: line.section,
        unit: line.unit,
        qty: line.qty,
        // Same provenance as a hand-typed line — because that is what it is.
        // Left unset, the grid rendered "undefined" in the Source column.
        source: 'MANUAL',
        display_order: base.length + index,
      })),
    ])
    setPasteText('')
    toast.success(
      `${lines.length} ligne(s) ajoutée(s)`,
      [
        headerSkipped ? 'ligne d’en-tête ignorée' : '',
        rejected.length ? `${rejected.length} ligne(s) sans référence ignorée(s)` : '',
        'rien n’est enregistré avant « Enregistrer »',
      ]
        .filter(Boolean)
        .join(' · '),
    )
  }

  const isPass2 = sheet.pass_no === 'PASS_2'

  const columns: Column[] = [
    { key: 'item_number', label: 'Référence', width: 170, editable: true },
    { key: 'name', label: 'Désignation', width: 240, editable: false },
    {
      key: 'section',
      label: 'Section',
      width: 160,
      editable: true,
      choices: ['LINE_SIDE', 'WIP', 'WIP_OK'],
      render: draft
        ? undefined
        : (row) => (
            <Badge
              tone={row.section === 'WIP' ? 'warning' : row.section === 'WIP_OK' ? 'info' : 'neutral'}
              title={SECTION_HINTS[String(row.section)]}
            >
              {SECTION_LABELS[String(row.section)] ?? String(row.section)}
            </Badge>
          ),
      value: (row) => String(row.section),
    },
    {
      key: 'qty',
      label: 'Comptage',
      numeric: true,
      width: 130,
      editable: true,
      render: draft
        ? undefined
        : (row) =>
            row.isCounted ? (
              <span className="num">{qty(Number(row.qty))}</span>
            ) : (
              <span className="subtle" title="Vide ≠ zéro : la ligne n’a pas été comptée">
                non compté
              </span>
            ),
      value: (row) => (row.qty === null ? null : Number(row.qty)),
    },
    // Screen only. The printed sheet must never carry the first count, or the
    // second one stops being independent — but on screen, seeing the
    // disagreement while typing is what turns encoding into a check.
    ...(isPass2
      ? [
          {
            key: 'qtyPass1',
            label: 'Comptage n°1',
            numeric: true,
            width: 140,
            editable: false,
            render: (row: Record<string, unknown>) => {
              const first = row.qtyPass1 as number | null
              if (first === null || first === undefined) {
                return <span className="subtle">non compté</span>
              }
              const second = row.qty === null || row.qty === undefined
                ? null
                : Number(row.qty)
              const diverges = second !== null && second !== first
              return (
                <span className={`num${diverges ? ' neg' : ''}`} title={
                  diverges
                    ? 'Les deux comptages divergent : un arbitrage sera demandé.'
                    : undefined
                }>
                  {qty(first)}
                </span>
              )
            },
            value: (row: Record<string, unknown>) =>
              row.qtyPass1 === null || row.qtyPass1 === undefined
                ? null
                : Number(row.qtyPass1),
          } satisfies Column,
        ]
      : []),
    { key: 'unit', label: 'Unité', width: 90, editable: true },
    {
      key: 'source',
      label: 'Source',
      width: 190,
      editable: false,
      render: (row) => (
        <SourceBadge
          source={String(row.source)}
          confidence={row.confidence as number | null}
        />
      ),
      value: (row) => String(row.source),
    },
    { key: 'comment', label: 'Commentaire', width: 220, editable: true },
  ]

  return (
    <Modal
      title={
        <span className="row" style={{ gap: 'var(--space-3)' }}>
          {zone.label || zone.code} — comptage n°{sheet.pass_no === 'PASS_1' ? 1 : 2}
          <Badge tone={SHEET_TONE[sheet.status]}>
            {toLabel(SHEET_STATUS_LABELS, sheet.status)}
          </Badge>
        </span>
      }
      onClose={onClose}
      width={1180}
      footer={
        <>
          <Button icon={<Icons.printer size={14} />} onClick={() => setPrinting(true)}>
            Imprimer
          </Button>
          <span className="spacer" />
          {draft && (
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Annuler les modifications
            </Button>
          )}
          <Button
            variant="primary"
            disabled={!draft || save.isPending}
            onClick={() => draft && save.mutate(draft)}
          >
            {save.isPending ? 'Enregistrement…' : 'Enregistrer'}
          </Button>
        </>
      }
    >
      <AsyncBoundary query={query} skeleton={<Skeleton height={320} />}>
        {() => (
          <div className="stack">
            <div className="row-wrap">
              <label className="btn btn--secondary btn--sm" style={{ cursor: 'pointer' }}>
                <Icons.sparkles size={14} />
                {scanning ? 'Lecture en cours…' : 'Importer un scan (PDF ou image)'}
                <input
                  type="file"
                  hidden
                  accept="application/pdf,image/*"
                  disabled={!editable || scanning}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) {
                      setScanning(true)
                      scan.mutate(file)
                    }
                    event.target.value = ''
                  }}
                />
              </label>
              <span className="subtle">
                Le modèle lit la feuille en s’appuyant sur la liste d’articles
                pré-imprimée : une référence absente de cette liste est signalée, jamais
                acceptée.
              </span>
            </div>

            <Alert tone="info" title="Case vide = non compté">
              Pour déclarer une absence de stock, saisissez explicitement 0.
              {isPass2 && ' La colonne « Comptage n°1 » n’est affichée qu’à l’écran.'}
            </Alert>

            {editable && (
              <details open={rows.length === 0}>
                <summary
                  style={{
                    cursor: 'pointer',
                    fontSize: 'var(--text-sm)',
                    fontWeight: 'var(--weight-medium)',
                  }}
                >
                  Coller plusieurs lignes depuis Excel
                </summary>
                <div className="stack" style={{ marginTop: 'var(--space-3)' }}>
                  <textarea
                    className="textarea mono"
                    value={pasteText}
                    onChange={(event) => setPasteText(event.target.value)}
                    placeholder={
                      'Un article par ligne. L’ordre des colonnes est libre :\n' +
                      'article, unité et section sont reconnus à leur contenu.\n\n' +
                      'P-00324093\tPCE\tWIP\n' +
                      'P-00311002\tBord de ligne'
                    }
                  />
                  <div className="row">
                    <Button
                      variant="primary"
                      size="sm"
                      disabled={!pasteText.trim()}
                      onClick={() => appendPasted(pasteText)}
                    >
                      Ajouter les lignes collées
                    </Button>
                    {pasteText && (
                      <Button variant="ghost" size="sm" onClick={() => setPasteText('')}>
                        Effacer
                      </Button>
                    )}
                    <span className="subtle">
                      Section par défaut : bord de ligne · unité : PCE · quantités
                      laissées vides
                    </span>
                  </div>
                </div>
              </details>
            )}

            <DataGrid
              columns={columns}
              rows={rows}
              exportTitle="Arbitrages"
              campaignId={campaignId}
              getRowId={(row, index) => String(row.id ?? index)}
              editable={editable && Boolean(draft)}
              onRowsChange={setDraft}
              onPaste={editable ? appendPasted : undefined}
              searchPlaceholder="Filtrer les lignes…"
              maxHeight={420}
              toolbar={
                !draft && editable ? (
                  <Button size="sm" onClick={() => setDraft(rows)}>
                    Modifier les lignes
                  </Button>
                ) : null
              }
            />

            {printing && (
              <PrintModal
                campaignId={campaignId}
                sheetId={sheet.id}
                modes={zone.printModes}
                onClose={() => setPrinting(false)}
              />
            )}
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Arbitration
// --------------------------------------------------------------------------- //

function ArbitrationTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [zoneFilter, setZoneFilter] = useState<string>('')

  const [focus] = useFocusMode()
  const zones = useQuery({
    queryKey: ['zones', campaignId, focus],
    queryFn: () => api.zones(campaignId, focus ? { focus: true } : {}),
  })
  const query = useQuery({
    queryKey: ['arbitrations', campaignId, zoneFilter],
    queryFn: () => api.arbitrations(campaignId, zoneFilter || undefined),
  })

  const decide = useMutation({
    mutationFn: ({ id, qty }: { id: string; qty: number }) =>
      api.decideArbitration(campaignId, id, qty),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Arbitrage enregistré')
    },
    onError: (error) => showError(error, 'Arbitrage impossible'),
  })

  const prefillAll = useMutation({
    mutationFn: (zoneId: string) => api.prefillWithPass2(campaignId, zoneId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.proposed} quantité(s) pré-remplie(s)`,
        'Rien n’est validé : relisez chaque ligne, corrigez si besoin, puis validez.',
      )
    },
    onError: (error) => showError(error, 'Pré-remplissage impossible'),
  })

  const rows = query.data ?? []
  const pending = rows.filter((row) => row.needsDecision)
  const editable = overview.permissions.countSheets

  return (
    <div className="stack">
      <div className="chips">
        <button
          className={`chip${zoneFilter === '' ? ' chip--active' : ''}`}
          onClick={() => setZoneFilter('')}
        >
          Toutes les zones
        </button>
        {(zones.data ?? [])
          .filter((zone) => zone.pendingArbitrations > 0 || zone.id === zoneFilter)
          .map((zone) => (
            <button
              key={zone.id}
              className={`chip${zoneFilter === zone.id ? ' chip--active' : ''}`}
              onClick={() => setZoneFilter(zone.id)}
            >
              {zone.code}
              {zone.pendingArbitrations > 0 && (
                <span className="num">{zone.pendingArbitrations}</span>
              )}
            </button>
          ))}
      </div>

      {zoneFilter && editable && pending.length > 0 && (
        <Alert
          tone="warning"
          title={`${pending.length} écart(s) à arbitrer sur cette zone`}
          actions={
            <Button
              size="sm"
              disabled={prefillAll.isPending}
              onClick={() => prefillAll.mutate(zoneFilter)}
            >
              Pré-remplir avec le comptage n°2
            </Button>
          }
        >
          Le comptage n°2 est le plus tardif et le mieux informé, donc c’est le
          point de départ raisonnable. Le pré-remplissage <strong>ne valide
          rien</strong> : il pose la quantité dans le champ, vous la relisez, la
          corrigez si besoin, puis vous validez. Tant qu’une ligne n’est pas
          validée, la consolidation l’ignore.
        </Alert>
      )}

      <Card
        title="Écarts entre comptage n°1 et n°2"
        message="Triés par décision requise puis par impact en euros : le désaccord le plus coûteux d’abord."
        flush
      >
        <AsyncBoundary
          query={query}
          isEmpty={(list) => list.length === 0}
          empty={
            <EmptyState title="Aucun écart entre les deux comptages" icon={<Icons.check size={20} />}>
              Les deux équipes ont trouvé les mêmes quantités.
            </EmptyState>
          }
        >
          {(list) => <ArbitrationTable rows={list} editable={editable} onDecide={decide.mutate} />}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

function ArbitrationTable({
  rows,
  editable,
  onDecide,
}: {
  rows: Arbitration[]
  editable: boolean
  onDecide: (input: { id: string; qty: number }) => void
}) {
  return (
    <div className="table-wrap" style={{ maxHeight: 620 }}>
      <table className="data">
        <thead>
          <tr>
            <th>Article</th>
            <th>Section</th>
            <th className="num">Comptage n°1</th>
            <th className="num">Comptage n°2</th>
            <th className="num">Écart</th>
            <th className="num">Impact</th>
            <th className="num" style={{ width: 150 }}>Quantité retenue</th>
            <th style={{ width: 190 }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <div className="mono">{row.item_number}</div>
                <div className="subtle truncate" style={{ maxWidth: 200 }}>
                  {row.name}
                </div>
              </td>
              <td>
                <Badge tone={row.section === 'WIP' ? 'warning' : 'neutral'}>
                  {SECTION_LABELS[row.section] ?? row.section}
                </Badge>
              </td>
              <td className="num">{row.qty_pass_1 === null ? '—' : qty(row.qty_pass_1)}</td>
              <td className="num">{row.qty_pass_2 === null ? '—' : qty(row.qty_pass_2)}</td>
              <td className={`num ${row.gap === 0 ? 'neutral' : row.gap > 0 ? 'pos' : 'neg'}`}>
                {signedNum(row.gap)}
              </td>
              <td className="num">{moneyShort(row.gapValue)}</td>
              <td className="num">
                {row.qty_arbitrated === null ? (
                  <span className="subtle">à décider</span>
                ) : row.isProposed ? (
                  <span className="subtle" title="Pré-rempli, pas encore validé">
                    {qty(row.qty_arbitrated)} · proposé
                  </span>
                ) : (
                  <strong>{qty(row.qty_arbitrated)}</strong>
                )}
              </td>
              <td>
                {row.needsDecision && editable ? (
                  <ArbitrationActions row={row} onDecide={onDecide} />
                ) : row.qty_arbitrated !== null ? (
                  <span className="subtle">
                    {row.decided_by ?? ''} {row.comment && `· ${row.comment}`}
                  </span>
                ) : (
                  <Badge tone="success">accord</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ArbitrationActions({
  row,
  onDecide,
}: {
  row: Arbitration
  onDecide: (input: { id: string; qty: number }) => void
}) {
  // A pre-filled quantity is what the user asked to see in the box; falling
  // back to pass 2 keeps the shortcut available when nothing was pre-filled.
  // Round-tripped through Number so the six stored decimals do not land in a
  // field somebody is about to read and retype.
  const initial = row.qty_arbitrated ?? row.qty_pass_2
  const [value, setValue] = useState<string>(
    initial === null || initial === undefined ? '' : String(Number(initial)),
  )
  return (
    <div className="row" style={{ gap: 'var(--space-1)' }}>
      <input
        className="input num"
        style={{ width: 92, padding: '4px 8px' }}
        inputMode="decimal"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-label="Quantité arbitrée"
      />
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setValue(String(row.qty_pass_2 ?? ''))}
        title="Préremplir avec le comptage n°2"
      >
        n°2
      </Button>
      <Button
        size="sm"
        variant="primary"
        disabled={value.trim() === '' || Number.isNaN(Number(value.replace(',', '.')))}
        onClick={() => onDecide({ id: row.id, qty: Number(value.replace(',', '.')) })}
      >
        Valider
      </Button>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Consolidation
// --------------------------------------------------------------------------- //

function ConsolidationTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [wipItem, setWipItem] = useState<string | null>(null)

  const preview = useQuery({
    queryKey: ['consolidation-preview', campaignId],
    queryFn: () => api.consolidationPreview(campaignId),
  })
  const current = useQuery({
    queryKey: ['consolidation', campaignId],
    queryFn: () => api.consolidation(campaignId),
  })
  const orphans = useQuery({
    queryKey: ['wip-without-bom', campaignId],
    queryFn: () => api.wipWithoutBom(campaignId),
  })

  const run = useMutation({
    mutationFn: () => api.runConsolidation(campaignId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `Consolidation effectuée : ${result.lines} article(s)`,
        `${result.zonesIncluded.length} zone(s) incluses. Le journal GENERIQUE est prêt à exporter.`,
      )
    },
    onError: (error) => showError(error, 'Consolidation impossible'),
  })

  const reclassify = useMutation({
    mutationFn: (lineIds: string[]) => api.reclassifyWip(campaignId, lineIds, 'WIP_OK'),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.updated} ligne(s) reclassée(s) en « WIP assemblé »`,
        'Ces assemblages seront comptés tels quels au lieu d’être éclatés.',
      )
    },
    onError: (error) => showError(error, 'Reclassement impossible'),
  })

  const blocking = preview.data?.findings.filter((f) => f.severity === 'BLOCKER') ?? []
  const orphanRows = orphans.data ?? []

  return (
    <div className="stack">
      {orphanRows.length > 0 && (
        <Alert
          tone="danger"
          title={`${orphanRows.length} ligne(s) WIP sans nomenclature`}
          actions={
            overview.permissions.countSheets && (
              <Button
                size="sm"
                variant="primary"
                disabled={reclassify.isPending}
                onClick={() => reclassify.mutate(orphanRows.map((row) => row.lineId))}
              >
                Compter ces assemblages tels quels
              </Button>
            )
          }
        >
          Ces assemblages sont comptés en WIP mais n’ont aucune nomenclature : les
          éclater ferait disparaître la quantité comptée. Comme les nomenclatures sont
          gelées pendant le comptage, la résolution est de les reclasser en{' '}
          <strong>WIP assemblé</strong>.
          <div className="table-wrap" style={{ maxHeight: 180, marginTop: 'var(--space-2)' }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Article</th>
                  <th className="num">Quantité</th>
                </tr>
              </thead>
              <tbody>
                {orphanRows.slice(0, 15).map((row) => (
                  <tr key={row.lineId}>
                    <td>{row.zoneCode}</td>
                    <td className="mono">{row.itemNumber}</td>
                    <td className="num">
                      {qty(row.qty)} {row.unit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Alert>
      )}

      <Card
        title="Consolidation GENERIQUE"
        message={
          preview.data
            ? `${preview.data.zonesIncluded.length} zone(s) prête(s), ${preview.data.zonesSkipped.length} en attente. ${blocking.length} point(s) bloquant(s).`
            : undefined
        }
        actions={
          <Button
            variant="primary"
            icon={<Icons.refresh size={14} />}
            disabled={!overview.permissions.countSheets || run.isPending || blocking.length > 0}
            onClick={() => run.mutate()}
          >
            {run.isPending ? 'Consolidation…' : 'Consolider et alimenter le journal'}
          </Button>
        }
      >
        <AsyncBoundary query={preview} skeleton={<Skeleton height={160} />}>
          {(data) => (
            <div className="stack">
              {data.zonesSkipped.length > 0 && (
                <Alert tone="warning" title={`${data.zonesSkipped.length} zone(s) non terminée(s)`}>
                  {data.zonesSkipped.join(', ')} — sans contribution tant que leurs
                  comptages ne sont pas validés.
                </Alert>
              )}
              {blocking.length > 0 && (
                <Alert tone="danger" title={`${blocking.length} point(s) bloquant(s)`}>
                  <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                    {blocking.slice(0, 8).map((finding, index) => (
                      <li key={index}>{finding.message}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              <dl className="kv">
                <dt>Articles au journal (aperçu)</dt>
                <dd className="num">{data.lines.length.toLocaleString('fr-FR')}</dd>
                <dt>Quantité totale</dt>
                <dd className="num">{qty(data.totalQty)}</dd>
                <dt>Zones incluses</dt>
                <dd>{data.zonesIncluded.join(', ') || '—'}</dd>
              </dl>
            </div>
          )}
        </AsyncBoundary>
      </Card>

      <ConsolidationExceptions findings={preview.data?.findings ?? []} />

      <AsyncBoundary
        query={current}
        skeleton={<Skeleton height={280} />}
        isEmpty={(data) => data.lines.length === 0}
        empty={
          <Card title="Journal consolidé">
            <EmptyState title="Aucune consolidation enregistrée">
              Lancez la consolidation lorsque toutes les zones sont terminées. Le
              résultat alimente le journal INVV de {overview.campaign.config.generic_warehouse}{' '}
              / {overview.campaign.config.generic_location}.
            </EmptyState>
          </Card>
        }
      >
        {(data) => (
          <ConsolidationResult
            campaignId={campaignId}
            lines={data.lines}
            onExploreWip={setWipItem}
          />
        )}
      </AsyncBoundary>

      {wipItem && (
        <WipModal campaignId={campaignId} itemNumber={wipItem} onClose={() => setWipItem(null)} />
      )}
    </div>
  )
}

/**
 * Ce que la consolidation a écarté, ou ajouté de sa propre initiative.
 *
 * Trois listes que le journal seul ne montre pas, et qui sont précisément
 * celles qu'on doit pouvoir relire :
 *
 *  - un produit fini compté en bord de ligne est une erreur de section, et sa
 *    quantité n'entre pas dans le stock — il faut aller chercher la feuille ;
 *  - compté en WIP assemblé il est noté à titre indicatif, ses composants étant
 *    déjà comptés par l'éclatement ;
 *  - un article que l'ERP porte et que personne n'a compté est soldé à zéro,
 *    et cette décision-là mérite d'être vue avant d'être postée.
 *
 * Chacune est une pilule plutôt qu'un tableau de plus, parce que les trois
 * partagent les mêmes colonnes et qu'empilées elles se noieraient.
 */
const EXCEPTION_PILLS: Array<{ code: string; label: string; hint: string }> = [
  {
    code: 'FINISHED_ON_LINE_SIDE',
    label: 'Produits finis en bord de ligne',
    hint: 'Erreur de section : la quantité n’est pas retenue. À corriger sur la feuille.',
  },
  {
    code: 'FINISHED_IN_WIP_OK',
    label: 'Produits finis en WIP assemblé',
    hint: 'Noté à titre indicatif : les composants sont déjà comptés par l’éclatement.',
  },
  {
    code: 'UNCOUNTED_WITH_BOOK_STOCK',
    label: 'Soldés à zéro',
    hint: 'Stock ERP en GENERIQUE que personne n’a compté : le journal le solde explicitement.',
  },
]

function ConsolidationExceptions({ findings }: { findings: Finding[] }) {
  const [code, setCode] = useState(EXCEPTION_PILLS[0]!.code)
  const counts = useMemo(() => {
    const tally: Record<string, number> = {}
    for (const finding of findings) {
      tally[finding.code] = (tally[finding.code] ?? 0) + 1
    }
    return tally
  }, [findings])

  const total = EXCEPTION_PILLS.reduce((sum, p) => sum + (counts[p.code] ?? 0), 0)
  if (total === 0) return null

  const rows = findings.filter((f) => f.code === code)
  const active = EXCEPTION_PILLS.find((p) => p.code === code)

  return (
    <Card
      title="Signalements de la consolidation"
      message={active?.hint}
      flush
    >
      <div className="chips" style={{ padding: '0 var(--space-4)' }}>
        {EXCEPTION_PILLS.map((pill) => (
          <button
            key={pill.code}
            className={`chip${code === pill.code ? ' chip--active' : ''}`}
            title={pill.hint}
            onClick={() => setCode(pill.code)}
          >
            {pill.label} <span className="num">{counts[pill.code] ?? 0}</span>
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState title="Rien à signaler dans cette catégorie" />
      ) : (
        <div className="table-wrap" style={{ maxHeight: 320 }}>
          <table className="data">
            <thead>
              <tr>
                <th style={{ width: 170 }}>Article</th>
                <th style={{ width: 170 }}>Zone</th>
                <th style={{ width: 190 }}>Feuille</th>
                <th className="num" style={{ width: 130 }}>Quantité</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 300).map((finding, index) => (
                <tr key={`${finding.item_number}-${index}`}>
                  <td className="mono">{finding.item_number || '—'}</td>
                  <td>{String(finding.context?.zone ?? '—')}</td>
                  <td>{String(finding.context?.sheets || '—')}</td>
                  <td className="num">
                    {qty(Number(finding.context?.qty ?? finding.context?.bookQty ?? 0))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function ConsolidationResult({
  campaignId,
  lines,
  onExploreWip,
}: {
  campaignId: string
  lines: ConsolidationLine[]
  onExploreWip: (itemNumber: string) => void
}) {
  // Les trois colonnes d'origine s'ouvrent comme le WIP le faisait déjà : une
  // quantité qu'on ne peut pas expliquer est une quantité qu'on ne peut pas
  // défendre, et c'est en réunion que la question se pose.
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect } | null
  >(null)
  const totals = useMemo(() => {
    return lines.reduce(
      (acc, line) => ({
        lineSide: acc.lineSide + line.qty_line_side,
        wipOk: acc.wipOk + line.qty_wip_ok,
        wipExploded: acc.wipExploded + line.qty_wip_exploded,
      }),
      { lineSide: 0, wipOk: 0, wipExploded: 0 },
    )
  }, [lines])

  const columns: Column<ConsolidationLine>[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 260 },
    {
      key: 'qty',
      label: 'Quantité totale',
      numeric: true,
      width: 150,
      render: (row) => (
        <DrillCell
          disabled={row.qty === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'counted' })}
        >
          <strong className="num">{qty(row.qty)}</strong>
        </DrillCell>
      ),
      value: (row) => row.qty,
    },
    {
      key: 'qty_line_side',
      label: 'Bord de ligne',
      numeric: true,
      width: 140,
      render: (row) => (
        <DrillCell
          disabled={row.qty_line_side === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'line_side' })}
        >
          <span className="num">{qty(row.qty_line_side)}</span>
        </DrillCell>
      ),
      value: (row) => row.qty_line_side,
    },
    {
      key: 'qty_wip_ok',
      label: 'WIP assemblé',
      numeric: true,
      width: 140,
      render: (row) => (
        <DrillCell
          disabled={row.qty_wip_ok === 0}
          onOpen={() => setDrill({ itemNumber: row.item_number, aspect: 'wip_ok' })}
        >
          <span className="num">{qty(row.qty_wip_ok)}</span>
        </DrillCell>
      ),
      value: (row) => row.qty_wip_ok,
    },
    {
      key: 'qty_wip_exploded',
      label: 'WIP éclaté',
      numeric: true,
      width: 150,
      render: (row) =>
        row.hasWip ? (
          <button
            className="btn btn--ghost btn--sm num"
            onClick={() => onExploreWip(row.item_number)}
            title="Voir de quoi se compose ce WIP"
          >
            {qty(row.qty_wip_exploded)}
            <Icons.chevronRight size={12} />
          </button>
        ) : (
          <span className="subtle">—</span>
        ),
      value: (row) => row.qty_wip_exploded,
    },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{moneyShort(row.value)}</span>,
      value: (row) => row.value,
    },
    {
      key: 'zone_codes',
      label: 'Zones',
      width: 200,
      render: (row) => <span className="subtle truncate">{row.zone_codes.join(', ')}</span>,
      value: (row) => row.zone_codes.join(','),
    },
  ]

  return (
    <div className="stack">
      <Card
        title="Origine des quantités consolidées"
        message="Le WIP éclaté n’est plus une valeur agrégée opaque : chaque quantité est traçable jusqu’à l’assemblage qui l’a produite."
      >
        <CompositionBar
          format={qty}
          segments={[
            { label: 'Bord de ligne', value: totals.lineSide, color: 'var(--cat-1)' },
            { label: 'WIP assemblé', value: totals.wipOk, color: 'var(--cat-2)' },
            { label: 'WIP éclaté en composants', value: totals.wipExploded, color: 'var(--cat-4)' },
          ]}
        />
      </Card>

      <Card title="Journal consolidé" flush>
        <DataGrid
          columns={columns}
          rows={lines}
          exportTitle="Consolidation"
          campaignId={campaignId}
          getRowId={(row) => row.item_number}
          searchPlaceholder="Filtrer par article…"
          maxHeight={560}
          initialSort={{ key: 'value', direction: 'desc' }}
        />
      </Card>

      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}

function WipModal({
  campaignId,
  itemNumber,
  onClose,
}: {
  campaignId: string
  itemNumber: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['wip', campaignId, itemNumber],
    queryFn: () => api.wipBreakdown(campaignId, itemNumber),
  })

  return (
    <Modal title={`Composition du WIP — ${itemNumber}`} onClose={onClose} width={780}>
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={180} />}
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState title="Aucune décomposition disponible" />}
      >
        {(rows) => (
          <div className="table-wrap" style={{ maxHeight: 420 }}>
            <table className="data">
              <thead>
                <tr>
                  <th>Zone</th>
                  <th>Assemblage compté</th>
                  <th className="num">Qté assemblage</th>
                  <th className="num">Qté / assemblage</th>
                  <th className="num">Quantité apportée</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index}>
                    <td>{row.zone_code || '—'}</td>
                    <td className="mono">{row.parent_item}</td>
                    <td className="num">{qty(row.parent_qty)}</td>
                    <td className="num">{qty(row.qty_per_parent)}</td>
                    <td className="num">
                      <strong>{qty(row.child_qty)}</strong>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Printing
// --------------------------------------------------------------------------- //

/**
 * The print dialog.
 *
 * Three jobs share one document, and the difference between them is worth
 * spelling out rather than hiding behind three buttons: the blank form handed
 * to a counter, the record of what came back, and the free-entry sheet with
 * nothing pre-printed at all.
 */

/**
 * Reading a whole stack of sheets in one go.
 *
 * The pages are routed to their sheets by the identifier the application itself
 * printed in the footer. Two outcomes are surfaced loudly because both are ones
 * a silent import would bury: a page nobody could attribute, and a sheet whose
 * AI reading somebody has already corrected by hand.
 */
/**
 * L'avancement d'une lecture de pile, en clair.
 *
 * Six minutes de silence sont indistinguables d'une panne : c'est l'étape en
 * cours et le compteur de feuilles qui font la différence, pas le pourcentage
 * seul — « 0 % » pendant deux minutes de rendu n'apprend rien, « Préparation
 * des pages » si.
 */
function ScanProgress({ state }: { state: ScanJob | undefined }) {
  if (!state) return <p className="subtle">Mise en file…</p>
  const running = state.status === 'RUNNING' || state.status === 'QUEUED'
  return (
    <div className="stack">
      <div className="row">
        <Badge tone={running ? 'info' : 'success'}>{state.step || 'En file'}</Badge>
        {state.totalPages > 0 && (
          <span className="subtle">{state.totalPages} page(s)</span>
        )}
        {state.sheetsTotal > 0 && (
          <span className="subtle">
            {state.sheetsDone}/{state.sheetsTotal} feuille(s) lue(s)
          </span>
        )}
      </div>
      <Progress
        total={Math.max(state.sheetsTotal, 1)}
        segments={[
          {
            label: 'Feuilles lues',
            value: state.sheetsDone,
            color: 'var(--accent)',
          },
        ]}
        caption={running ? `${state.percent} %` : null}
      />
    </div>
  )
}


function MultiScanModal({
  campaignId,
  file,
  zones,
  onBusy,
  onClose,
}: {
  campaignId: string
  file: File
  zones: Zone[]
  onBusy: (busy: boolean) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const showError = useErrorToast()
  const [jobId, setJobId] = useState<string | null>(null)

  const atRisk = zones.flatMap((zone) =>
    zone.sheets
      .filter((sheet) => sheet.correctedLines > 0)
      .map((sheet) => ({ zone, sheet })),
  )

  // Le dépôt rend un travail, pas un rapport : la lecture d'une pile de cent
  // feuilles dure des minutes, et l'attendre dans la requête de chargement
  // faisait couper la passerelle avant la fin.
  const scan = useMutation({
    mutationFn: (overwrite: boolean) =>
      api.scanMultipleSheets(campaignId, file, overwrite),
    onMutate: () => onBusy(true),
    onSuccess: (queued) => setJobId(queued.id),
    onError: (error) => {
      onBusy(false)
      showError(error, 'Dépôt du scan impossible')
      onClose()
    },
  })

  // Tant que le travail tourne, on redemande où il en est. Deux secondes : assez
  // souvent pour que la barre bouge, assez rare pour ne pas peser sur une base
  // qui écrit en même temps cent feuilles de comptage.
  const job = useQuery({
    queryKey: ['scan-job', campaignId, jobId],
    queryFn: () => api.scanJob(campaignId, jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.isDone ? false : 2000),
  })

  const finished = job.data?.isDone ?? false
  useEffect(() => {
    if (!finished) return
    onBusy(false)
    void queryClient.invalidateQueries()
  }, [finished, onBusy, queryClient])

  // --- la lecture est en cours : on montre où elle en est ---------------------
  if (jobId && !finished) {
    const state = job.data
    return (
      <Modal title="Lecture du scan en cours" onClose={onClose} width={620}>
        <div className="stack">
          <p>
            <strong className="mono">{file.name}</strong> — vous pouvez fermer
            cette fenêtre : la lecture continue et les feuilles se remplissent au
            fur et à mesure.
          </p>
          <ScanProgress state={state} />
        </div>
      </Modal>
    )
  }

  // --- terminé en échec ------------------------------------------------------
  if (finished && job.data?.status === 'FAILED') {
    return (
      <Modal
        title="Scan multi-feuilles — échec"
        onClose={onClose}
        width={620}
        footer={
          <Button variant="primary" onClick={onClose}>
            Fermer
          </Button>
        }
      >
        <Alert tone="danger" title="La lecture n’a pas abouti">
          {job.data.error || 'Raison inconnue.'}
        </Alert>
      </Modal>
    )
  }

  const report = (finished ? job.data?.report : null) as MultiScanReport | null

  if (report) {
    return (
      <Modal
        title="Scan multi-feuilles — résultat"
        onClose={onClose}
        width={840}
        footer={
          <Button variant="primary" onClick={onClose}>
            Fermer
          </Button>
        }
      >
        <div className="stack">
          <dl className="kv">
            <dt>Pages lues</dt>
            <dd className="num">{report.pages}</dd>
            <dt>Feuilles renseignées</dt>
            <dd className="num">{report.sheetsProcessed.length}</dd>
            <dt>Feuilles préservées</dt>
            <dd className="num">{report.sheetsSkipped.length}</dd>
            <dt>Pages non attribuées</dt>
            <dd className="num">{report.unroutedPages.length}</dd>
          </dl>

          {report.sheetsProcessed.length > 0 && (
            <div className="table-wrap" style={{ maxHeight: 240 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Feuille</th>
                    <th className="num">Pages</th>
                    <th className="num">Quantités lues</th>
                    <th className="num">Confiance</th>
                    <th>À vérifier</th>
                  </tr>
                </thead>
                <tbody>
                  {report.sheetsProcessed.map((sheet) => (
                    <tr key={sheet.sheetId}>
                      <td>
                        {sheet.zoneCode} — n°{sheet.passNo}
                      </td>
                      <td className="num">{sheet.pages.join(', ')}</td>
                      <td className="num">{sheet.counted}</td>
                      <td className="num">
                        {sheet.meanConfidence === null
                          ? '—'
                          : percent(sheet.meanConfidence)}
                      </td>
                      <td className="subtle">
                        {[
                          sheet.lowConfidence.length
                            ? `${sheet.lowConfidence.length} valeur(s) douteuse(s)`
                            : '',
                          sheet.missing.length
                            ? `${sheet.missing.length} non lue(s)`
                            : '',
                          sheet.overwroteCorrections
                            ? `${sheet.overwroteCorrections} correction(s) écrasée(s)`
                            : '',
                        ]
                          .filter(Boolean)
                          .join(' · ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {report.sheetsSkipped.length > 0 && (
            <Alert
              tone="success"
              title={`${report.sheetsSkipped.length} feuille(s) préservée(s)`}
            >
              Valeurs lues par l’IA puis corrigées à la main : elles n’ont pas été
              relues.
              <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
                {report.sheetsSkipped.map((sheet) => (
                  <li key={sheet.sheetId}>
                    {sheet.zoneCode} — n°{sheet.passNo} · {sheet.correctedLines}{' '}
                    ligne(s) corrigée(s)
                  </li>
                ))}
              </ul>
            </Alert>
          )}

          {report.unroutedPages.length > 0 && (
            <Alert
              tone="warning"
              title={`${report.unroutedPages.length} page(s) non attribuée(s)`}
            >
              Pied de page illisible : signalées plutôt que devinées. Ouvrez la
              feuille concernée et importez ces pages une par une.
              <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
                {report.unroutedPages.map((page) => (
                  <li key={page.page}>
                    Page {page.page} — {page.note}
                  </li>
                ))}
              </ul>
            </Alert>
          )}
        </div>
      </Modal>
    )
  }

  return (
    <Modal
      title="Importer un scan de plusieurs feuilles"
      onClose={scan.isPending ? () => {} : onClose}
      width={700}
      footer={
        <>
          <Button variant="ghost" disabled={scan.isPending} onClick={onClose}>
            Annuler
          </Button>
          {atRisk.length > 0 && (
            <Button
              variant="danger"
              disabled={scan.isPending}
              onClick={() => scan.mutate(true)}
            >
              Lire et écraser les corrections
            </Button>
          )}
          <Button
            variant="primary"
            disabled={scan.isPending}
            onClick={() => scan.mutate(false)}
          >
            {scan.isPending ? 'Lecture en cours…' : 'Lire le scan'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <p>
          <strong className="mono">{file.name}</strong> — chaque page sera rattachée
          à sa feuille par l’identifiant que l’application a imprimé en pied de page.
          Une page dont le pied est illisible est signalée, jamais devinée.
        </p>

        {atRisk.length > 0 ? (
          <Alert
            tone="warning"
            title={`${atRisk.length} feuille(s) portent des corrections humaines`}
          >
            Elles seront <strong>préservées</strong> par défaut. « Lire et écraser »
            les relit quand même — à n’utiliser que si le scan est plus récent que
            les corrections.
            <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }}>
              {atRisk.slice(0, 8).map(({ zone, sheet }) => (
                <li key={sheet.id}>
                  {zone.code} — comptage n°{sheet.pass_no === 'PASS_1' ? 1 : 2} ·{' '}
                  {sheet.correctedLines} ligne(s) corrigée(s)
                </li>
              ))}
            </ul>
          </Alert>
        ) : (
          <Alert tone="info" title="Aucune correction humaine en jeu">
            Aucune feuille ne porte de valeur IA corrigée à la main.
          </Alert>
        )}
      </div>
    </Modal>
  )
}
