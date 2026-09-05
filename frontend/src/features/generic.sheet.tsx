/** Une feuille de comptage ouverte : ses lignes, sa saisie, son scan. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { SheetScanReport, Sheet, Zone } from '../lib/types'
import { SECTION_HINTS, SOURCE_LABELS, ZONE_STATUS_LABELS, qty, label as toLabel } from '../lib/format'
import { sectionColumn, sectionLabel } from './sectionColumn'
import { DataGrid, SourceBadge, type Column } from '../components/DataGrid'
import { PasteArea } from '../components/PasteArea'
import { PrintModal } from '../components/PrintModal'
import { parseSheetLines } from '../lib/pasteSheetLines'
import { AsyncBoundary, Badge, Button, Card, Icons, Modal, Skeleton, useErrorToast, useToast } from '../components/ui'
import { ZONE_TONE } from './generic.zones'
import { ScanProgress } from './generic.scan'
import { PRINTED_SECTIONS } from './generic.layout'

/**
 * Une ligne qui porte un article — par opposition à un intertitre ou à une
 * ligne vide.
 *
 * Ces deux-là vivent dans la même liste que les articles, et c'est voulu : ce
 * qu'il faut conserver d'un intertitre, c'est **sa place**. Mais ils ne portent
 * ni référence, ni quantité, ni unité : leur offrir un champ de saisie invite à
 * les transformer en articles.
 */
export function isArticle(row: Record<string, unknown>): boolean {
  return String(row.line_kind ?? 'ARTICLE') === 'ARTICLE'
}

/**
 * Ce qui part au serveur pour une cellule de quantité.
 *
 * Un nombre quand c'en est un, et **le texte tel quel** sinon. C'est ce second
 * cas qui compte : la cellule accepte « 3*48+7 », et `Number()` en fait `NaN`,
 * que `JSON.stringify` écrit `null`. La formule ne devenait donc pas une erreur
 * — elle devenait une case vide, sur une ligne que quelqu'un venait de compter.
 *
 * Ce n'est pas au navigateur de décider si l'opération est acceptable : le
 * réglage vit sur la campagne, et le refus doit pouvoir le nommer. Le texte
 * traverse, le serveur tranche.
 *
 * Vide reste vide : on ne compte pas zéro parce qu'on n'a pas compté.
 */
export function quantityToSend(value: unknown): number | string | null {
  if (value === null || value === undefined || value === '') return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value).trim()
  if (text === '') return null
  const asNumber = Number(text.replace(',', '.'))
  return Number.isFinite(asNumber) ? asNumber : text
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
          // Le genre et le texte de l'intertitre repartent tels quels. Sans
          // eux, enregistrer la saisie transformait chaque intertitre en ligne
          // d'article sans référence — c'est-à-dire en ligne à jeter : la
          // feuille perdait sa forme au premier « Enregistrer ».
          lineKind: String(row.line_kind ?? 'ARTICLE'),
          label: String(row.label ?? ''),
          qty: quantityToSend(row.qty),
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

  // Les lignes rangées par section, dans l'ordre du papier. Le document *est*
  // groupé par section — l'impression le fait, l'aperçu de préparation aussi —
  // et l'écran de saisie le montre donc pareil.
  const bySection = useMemo(() => {
    const groups: Record<string, Array<Record<string, unknown>>> = {}
    for (const section of PRINTED_SECTIONS) groups[section] = []
    for (const row of rows) {
      const key = String(row.section ?? 'LINE_SIDE')
      ;(groups[key] ??= []).push(row)
    }
    return groups
  }, [rows])

  /** Une section modifiée, les deux autres inchangées, l'ordre conservé. */
  const replaceSection = (
    section: string, next: Array<Record<string, unknown>>,
  ) =>
    setDraft(
      PRINTED_SECTIONS.flatMap((s) => (s === section ? next : bySection[s] ?? [])),
    )

  const columns: Column[] = [
    {
      key: 'item_number',
      label: 'Référence',
      width: 170,
      editable: true,
      appliesTo: isArticle,
    },
    {
      key: 'name',
      label: 'Désignation',
      width: 240,
      editable: false,
      // L'intertitre s'écrit ici, en toutes lettres. La feuille de papier le
      // porte à cet endroit précis, et c'est ce qui permet à celui qui recopie
      // de suivre la page ligne à ligne au lieu de chercher où il en est.
      render: (row) =>
        String(row.line_kind ?? 'ARTICLE') === 'SUBSECTION' ? (
          <strong>{String(row.label ?? '')}</strong>
        ) : String(row.line_kind ?? 'ARTICLE') === 'SPACER' ? (
          <span className="subtle">— ligne vide —</span>
        ) : (
          <span>{String(row.name ?? '')}</span>
        ),
      value: (row) =>
        String(row.line_kind ?? 'ARTICLE') === 'ARTICLE'
          ? String(row.name ?? '')
          : String(row.label ?? ''),
    },
    sectionColumn({
      editable: true,
      appliesTo: isArticle,
      render: draft
        ? undefined
        : (row) => (
            <Badge
              tone={row.section === 'WIP' ? 'warning' : row.section === 'WIP_OK' ? 'info' : 'neutral'}
              title={SECTION_HINTS[String(row.section)]}
            >
              {sectionLabel(row.section)}
            </Badge>
          ),
    }),
    {
      key: 'qty',
      label: 'Comptage',
      numeric: true,
      width: 130,
      editable: true,
      appliesTo: isArticle,
      render: draft
        ? undefined
        : (row) =>
            isArticle(row) ? (
              // Une case vide vaut zéro : la ligne est sur la feuille parce
              // qu'on s'attend à trouver la référence dans la zone, et n'y
              // avoir rien trouvé est un écart à expliquer, pas un silence.
              //
              // L'opération sous le résultat quand il y en avait une. Sans
              // elle, « 151 » calculé et « 151 » tapé seraient identiques à
              // l'écran, et la seule chose que cette fonctionnalité apporte
              // par-dessus une calculatrice — pouvoir recompter — disparaîtrait
              // à l'affichage.
              <span className="stack" style={{ gap: 0 }}>
                <span className="num">{qty(Number(row.qty ?? 0))}</span>
                {row.qty_formula ? (
                  <span className="subtle mono" title="Écrit sur la feuille">
                    {String(row.qty_formula)}
                  </span>
                ) : null}
              </span>
            ) : null,
      value: (row) => (isArticle(row) ? Number(row.qty ?? 0) : null),
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
            appliesTo: isArticle,
            render: (row: Record<string, unknown>) => {
              if (!isArticle(row)) return null
              const first = Number((row.qtyPass1 as number | null) ?? 0)
              const second = Number(row.qty ?? 0)
              const diverges = second !== first
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
              isArticle(row) ? Number((row.qtyPass1 as number | null) ?? 0) : null,
          } satisfies Column,
        ]
      : []),
    { key: 'unit', label: 'Unité', width: 90, editable: true, appliesTo: isArticle },
    {
      key: 'source',
      label: 'Source',
      width: 190,
      editable: false,
      appliesTo: isArticle,
      choiceLabel: (value) => toLabel(SOURCE_LABELS, value),
      render: (row) => (
        <SourceBadge
          source={String(row.source)}
          confidence={row.confidence as number | null}
        />
      ),
      value: (row) => String(row.source),
    },
    {
      key: 'comment',
      label: 'Commentaire',
      width: 220,
      editable: true,
      appliesTo: isArticle,
    },
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
      width={1392}
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
                  <PasteArea
                    value={pasteText}
                    aria-label="Coller plusieurs lignes depuis Excel"
                    onChange={setPasteText}
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

            {/* Une grille par section, et non une grille filtrable par
                colonne. Les trois tableaux du papier sont trois tableaux, avec
                trois en-têtes et trois règles de comptage : les mettre bout à
                bout ne les distinguait que par une colonne, au milieu de dix
                autres, sur une feuille de cent lignes. C'est exactement la
                confusion — compter un en-cours comme une pièce de bord de
                ligne — que les sections existent pour empêcher. */}
            {PRINTED_SECTIONS.map((section) => (
              <Card
                key={section}
                title={sectionLabel(section)}
                message={SECTION_HINTS[section]}
                flush
              >
                <DataGrid
                  columns={columns}
                  rows={bySection[section] ?? []}
                  exportTitle={`Feuille — ${sectionLabel(section)}`}
                  campaignId={campaignId}
                  getRowId={(row, index) => String(row.id ?? `${section}-${index}`)}
                  rowClassName={(row) =>
                    String(row.line_kind ?? 'ARTICLE') === 'ARTICLE'
                      ? undefined
                      : 'row--layout'
                  }
                  editable={editable && Boolean(draft)}
                  onRowsChange={(next) => replaceSection(section, next)}
                  onPaste={editable ? appendPasted : undefined}
                  searchPlaceholder="Filtrer les lignes…"
                  maxHeight={454}
                  toolbar={
                    !draft && editable && section === PRINTED_SECTIONS[0] ? (
                      <Button size="sm" onClick={() => setDraft(rows)}>
                        Modifier les lignes
                      </Button>
                    ) : null
                  }
                />
              </Card>
            ))}

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
