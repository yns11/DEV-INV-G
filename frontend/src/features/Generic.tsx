/**
 * GENERIQUE: zones, double counting, arbitration and consolidation.
 *
 * This is the screen that replaces `Compil GENERIQUE.xlsx` — its 40 tabs, its
 * Power Query chain and the copy/paste into the ERP.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api, downloads } from '../lib/api'
import type {
  Arbitration,
  ConsolidationLine,
  Overview,
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
  numShort,
  percent,
  signedNum,
  label as toLabel,
} from '../lib/format'
import { CompositionBar } from '../components/charts'
import { DataGrid, SourceBadge, type Column } from '../components/DataGrid'
import {
  Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Field, Icons, Modal, Skeleton, Tabs, useDownload, useErrorToast, useToast,
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

export function Generic() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useState<Tab>('zones')

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <Alert tone="info" title="Un emplacement ERP, des dizaines de zones physiques">
        {overview.campaign.config.generic_warehouse} /{' '}
        {overview.campaign.config.generic_location} couvre bords de ligne, zones de
        picking, qualité, métrologie et laboratoires. Chaque zone est comptée deux fois
        par deux équipes indépendantes, arbitrée, puis l’ensemble est consolidé en un
        seul journal INVV.
      </Alert>

      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'zones', label: 'Zones & feuilles', count: overview.genericProgress.zones },
          {
            id: 'arbitration',
            label: 'Arbitrages',
            count: overview.genericProgress.pendingArbitrations || null,
          },
          { id: 'consolidation', label: 'Consolidation' },
        ]}
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
  const startDownload = useDownload()
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [creating, setCreating] = useState(false)
  const [openSheet, setOpenSheet] = useState<{ zone: Zone; sheet: Sheet } | null>(null)

  const query = useQuery({
    queryKey: ['zones', campaignId],
    queryFn: () => api.zones(campaignId),
    refetchInterval: 45_000,
  })

  const transition = useMutation({
    mutationFn: ({ sheetId, target, counterName }: {
      sheetId: string
      target: SheetStatus
      counterName?: string
    }) => api.transitionSheet(campaignId, sheetId, target, counterName),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Statut de la feuille mis à jour')
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
        <Button
          icon={<Icons.printer size={14} />}
          onClick={() => startDownload(downloads.allCountingSheets(campaignId, 1))}
        >
          Imprimer toutes les feuilles n°1
        </Button>
        <Button
          icon={<Icons.printer size={14} />}
          onClick={() => startDownload(downloads.allCountingSheets(campaignId, 2))}
        >
          Feuilles n°2
        </Button>
      </div>

      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={280} />}
        isEmpty={(zones) => zones.length === 0}
        empty={
          <Card>
            <EmptyState
              title="Aucune zone"
              action={
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Créer la première zone
                </Button>
              }
            >
              Créez une zone par aire physique (bord de ligne, picking, métrologie…).
              Chaque zone reçoit automatiquement deux feuilles de comptage.
            </EmptyState>
          </Card>
        }
      >
        {(zones) => (
          <div className="grid grid--2">
            {zones.map((zone) => (
              <Card
                key={zone.id}
                title={
                  <span className="row" style={{ gap: 'var(--space-3)' }}>
                    <span className="truncate">{zone.label || zone.code}</span>
                    <Badge tone={ZONE_TONE[zone.status] ?? 'neutral'} dot>
                      {toLabel(ZONE_STATUS_LABELS, zone.status)}
                    </Badge>
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
                      <span className="spacer" />
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Icons.printer size={13} />}
                        onClick={() => startDownload(downloads.countingSheet(campaignId, sheet.id))}
                        aria-label="Imprimer"
                        title="Imprimer cette feuille"
                      />
                      <Button
                        size="sm"
                        onClick={() => setOpenSheet({ zone, sheet })}
                      >
                        Ouvrir
                      </Button>
                      {editable && NEXT_SHEET_STATUS[sheet.status] && (
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={transition.isPending}
                          onClick={() =>
                            transition.mutate({
                              sheetId: sheet.id,
                              target: NEXT_SHEET_STATUS[sheet.status]!,
                            })
                          }
                        >
                          {NEXT_SHEET_LABEL[sheet.status]}
                        </Button>
                      )}
                    </div>
                  ))}
                  {zone.pendingArbitrations > 0 && (
                    <Alert tone="warning" title={`${zone.pendingArbitrations} écart(s) à arbitrer`}>
                      Les deux comptages divergent. La consolidation restera bloquée
                      tant qu’une quantité n’aura pas été retenue.
                    </Alert>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </AsyncBoundary>

      {creating && (
        <CreateZoneModal campaignId={campaignId} onClose={() => setCreating(false)} />
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

const NEXT_SHEET_STATUS: Partial<Record<SheetStatus, SheetStatus>> = {
  PENDING: 'COUNTING',
  COUNTING: 'ENCODING',
  ENCODING: 'DONE',
}

const NEXT_SHEET_LABEL: Partial<Record<SheetStatus, string>> = {
  PENDING: 'Remettre au compteur',
  COUNTING: 'Feuille rendue',
  ENCODING: 'Terminer l’encodage',
}

function CreateZoneModal({
  campaignId,
  onClose,
}: {
  campaignId: string
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState({ code: '', label: '', sector: '' })

  const mutation = useMutation({
    mutationFn: () => api.createZone(campaignId, form),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Zone créée', 'Ses deux feuilles de comptage sont prêtes.')
      onClose()
    },
    onError: (error) => showError(error, 'Création impossible'),
  })

  return (
    <Modal
      title="Nouvelle zone GENERIQUE"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={!form.code.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            Créer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Code de la zone" hint="Ex. FI ASSY M3.1, PICKING TRANSALLIANCE…">
          <input
            className="input"
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
        </Field>
        <Field label="Libellé">
          <input
            className="input"
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
          />
        </Field>
        <Field label="Secteur" hint="Sert au dispatch des feuilles imprimées.">
          <input
            className="input"
            value={form.sector}
            onChange={(event) => setForm({ ...form, sector: event.target.value })}
          />
        </Field>
      </div>
    </Modal>
  )
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
  const startDownload = useDownload()
  const [draft, setDraft] = useState<Array<Record<string, unknown>> | null>(null)
  const [scanning, setScanning] = useState(false)

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
              <span className="num">{numShort(Number(row.qty))}</span>
            ) : (
              <span className="subtle" title="Vide ≠ zéro : la ligne n’a pas été comptée">
                non compté
              </span>
            ),
      value: (row) => (row.qty === null ? null : Number(row.qty)),
    },
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
          <Button
            icon={<Icons.printer size={14} />}
            onClick={() => startDownload(downloads.countingSheet(campaignId, sheet.id))}
          >
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

            <Alert tone="info" title="Règle de saisie">
              Une case vide signifie <strong>non compté</strong> et sera traitée comme
              telle. Pour déclarer une absence de stock, saisissez explicitement 0.
            </Alert>

            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row, index) => String(row.id ?? index)}
              editable={editable && Boolean(draft)}
              onRowsChange={setDraft}
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

  const zones = useQuery({ queryKey: ['zones', campaignId], queryFn: () => api.zones(campaignId) })
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

  const acceptAll = useMutation({
    mutationFn: (zoneId: string) => api.acceptPass2(campaignId, zoneId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.decided} arbitrage(s) résolu(s)`,
        'Le comptage n°2 a été retenu ; chaque décision est tracée à votre nom.',
      )
    },
    onError: (error) => showError(error, 'Arbitrage groupé impossible'),
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
              variant="primary"
              disabled={acceptAll.isPending}
              onClick={() => acceptAll.mutate(zoneFilter)}
            >
              Retenir le comptage n°2 partout
            </Button>
          }
        >
          Le comptage n°2 est le plus tardif et le mieux informé ; l’adopter en bloc
          reste une décision explicite, enregistrée ligne par ligne dans l’audit.
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
              <td className="num">{row.qty_pass_1 === null ? '—' : numShort(row.qty_pass_1)}</td>
              <td className="num">{row.qty_pass_2 === null ? '—' : numShort(row.qty_pass_2)}</td>
              <td className={`num ${row.gap === 0 ? 'neutral' : row.gap > 0 ? 'pos' : 'neg'}`}>
                {signedNum(row.gap)}
              </td>
              <td className="num">{moneyShort(row.gapValue)}</td>
              <td className="num">
                {row.qty_arbitrated !== null ? (
                  <strong>{numShort(row.qty_arbitrated)}</strong>
                ) : (
                  <span className="subtle">à décider</span>
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
  const [value, setValue] = useState<string>(
    row.qty_pass_2 !== null ? String(row.qty_pass_2) : '',
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
                      {numShort(row.qty)} {row.unit}
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
                  {data.zonesSkipped.join(', ')} — elles ne contribueront pas tant que
                  leurs deux comptages ne seront pas validés.
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
                <dd className="num">{numShort(data.totalQty)}</dd>
                <dt>Zones incluses</dt>
                <dd>{data.zonesIncluded.join(', ') || '—'}</dd>
              </dl>
            </div>
          )}
        </AsyncBoundary>
      </Card>

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
        {(data) => <ConsolidationResult lines={data.lines} onExploreWip={setWipItem} />}
      </AsyncBoundary>

      {wipItem && (
        <WipModal campaignId={campaignId} itemNumber={wipItem} onClose={() => setWipItem(null)} />
      )}
    </div>
  )
}

function ConsolidationResult({
  lines,
  onExploreWip,
}: {
  lines: ConsolidationLine[]
  onExploreWip: (itemNumber: string) => void
}) {
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
      render: (row) => <strong className="num">{numShort(row.qty)}</strong>,
      value: (row) => row.qty,
    },
    {
      key: 'qty_line_side',
      label: 'Bord de ligne',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{numShort(row.qty_line_side)}</span>,
      value: (row) => row.qty_line_side,
    },
    {
      key: 'qty_wip_ok',
      label: 'WIP assemblé',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{numShort(row.qty_wip_ok)}</span>,
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
            {numShort(row.qty_wip_exploded)}
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
          format={numShort}
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
          getRowId={(row) => row.item_number}
          searchPlaceholder="Filtrer par article…"
          maxHeight={560}
          initialSort={{ key: 'value', direction: 'desc' }}
        />
      </Card>
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
                    <td className="num">{numShort(row.parent_qty)}</td>
                    <td className="num">{numShort(row.qty_per_parent)}</td>
                    <td className="num">
                      <strong>{numShort(row.child_qty)}</strong>
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
