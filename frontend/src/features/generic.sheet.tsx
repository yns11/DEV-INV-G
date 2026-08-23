/** Une feuille de comptage ouverte : ses lignes, sa saisie, son scan. */

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SheetScanReport, Sheet, Zone } from '../lib/types'
import { SECTION_HINTS, SECTION_LABELS, SOURCE_LABELS, ZONE_STATUS_LABELS, qty, label as toLabel } from '../lib/format'
import { DataGrid, SourceBadge, type Column } from '../components/DataGrid'
import { PrintModal } from '../components/PrintModal'
import { parseSheetLines } from '../lib/pasteSheetLines'
import { Alert, AsyncBoundary, Badge, Button, Card, Icons, Modal, Skeleton, useErrorToast, useToast } from '../components/ui'
import { ZONE_TONE } from './generic.zones'
import { ScanProgress } from './generic.scan'

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
export function SheetModal({
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
        // La version lue à l'ouverture. Le serveur refuse si la feuille a bougé
        // entre-temps, plutôt que d'effacer ce que l'autre vient d'y saisir.
        Number(query.data?.sheet?.row_version) || undefined,
      ),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setDraft(null)
      toast.success(`${result.written} ligne(s) enregistrée(s)`)
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  // --- le scan, mené comme un travail suivi ---------------------------------
  //
  // Le dépôt rend un identifiant, pas un rapport : la lecture d'une feuille dure
  // de dix secondes à plus d'une minute, et l'attendre dans la requête ne
  // laissait rien à regarder — un bouton grisé qui ne distingue pas un travail
  // qui avance d'un appel qui a calé. On interroge donc le travail, comme le
  // fait déjà le scan multi-feuilles.
  const [jobId, setJobId] = useState<string | null>(null)

  // À l'ouverture, on cherche un scan déjà en cours sur cette feuille. Sans
  // cela, un rafraîchissement pendant la lecture rend la feuille inerte et
  // invite à relancer un travail qui tourne déjà.
  useQuery({
    queryKey: ['sheet-scan-job', campaignId, sheet.id],
    queryFn: async () => {
      const running = await api.sheetScanJob(campaignId, sheet.id)
      if (running && !running.isDone) {
        setJobId(running.id)
        setScanning(true)
      }
      return running
    },
    staleTime: Infinity,
  })

  const job = useQuery({
    queryKey: ['scan-job', campaignId, jobId],
    queryFn: () => api.scanJob(campaignId, jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.isDone ? false : 2000),
  })

  const scan = useMutation({
    mutationFn: (file: File) => api.scanSheet(campaignId, sheet.id, file),
    onSuccess: (queued) => setJobId(queued.id),
    onError: (error) => {
      setScanning(false)
      showError(error, 'Dépôt du scan impossible')
    },
  })

  // Le travail est terminé : on annonce le résultat une fois, et on recharge.
  const finished = job.data?.isDone ?? false
  const announced = useRef<string | null>(null)
  useEffect(() => {
    const state = job.data
    if (!finished || !state || announced.current === state.id) return
    announced.current = state.id
    setScanning(false)
    setJobId(null)
    if (state.status === 'FAILED') {
      toast.push({
        tone: 'danger',
        title: 'La lecture du scan n’a pas abouti',
        body: state.error || 'Raison inconnue.',
      })
      return
    }
    void queryClient.invalidateQueries()
    setDraft(null)
    const report = state.report as SheetScanReport
    const low = report.lowConfidence ?? []
    const unexpected = report.unexpected ?? []
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
  }, [finished, job.data, queryClient, toast])

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
      choiceLabel: (value) => toLabel(SECTION_LABELS, value),
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
      choiceLabel: (value) => toLabel(SOURCE_LABELS, value),
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
          {/* L'état affiché est celui de la **zone** : la feuille n'en a plus,
              et c'est la zone qu'on déclare terminée. */}
          <Badge tone={ZONE_TONE[zone.status] ?? 'neutral'} dot>
            {toLabel(ZONE_STATUS_LABELS, zone.status)}
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

            {/* Ce que fait la lecture, pendant qu'elle le fait. Un bouton grisé
                ne distingue pas un travail qui avance d'un appel qui a calé, et
                cette lecture-là dure jusqu'à une minute. */}
            {scanning && (
              <Card>
                <ScanProgress state={job.data} />
              </Card>
            )}

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
