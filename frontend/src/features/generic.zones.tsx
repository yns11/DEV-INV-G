/** Les zones GENERIQUE et leurs feuilles : ce qui reste à compter, et par qui. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Overview, PrintMode, Sheet, ZoneStatus, Zone } from '../lib/types'
import { ZONE_STATUS_LABELS, percent, label as toLabel } from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { PrintModal } from '../components/PrintModal'
import { useFocusMode } from '../lib/focus'
import { CreateZoneModal } from './zones'
import { Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Icons, Skeleton, useErrorToast, useToast } from '../components/ui'
import { MultiScanModal } from './generic.scan'
import { SheetModal } from './generic.sheet'

export const ZONE_TONE: Record<string, string> = {
  PENDING: 'neutral',
  IN_PROGRESS: 'accent',
  DONE: 'success',
}

/**
 * Les trois états d'une zone, dans l'ordre du déroulement.
 *
 * Il y en avait six, calculés à partir du statut des deux feuilles — 1er
 * comptage en cours, 1er encodage en cours, 2ème comptage, 2ème encodage,
 * arbitrage requis, terminé — et six pastilles à parcourir pour savoir où en
 * était une campagne de quarante zones. Deux de ces six ne se distinguaient
 * que par un bouton que quelqu'un avait pensé à cliquer.
 */
const ZONE_STAGES: Array<{ id: ZoneStatus; label: string; hint: string }> = [
  {
    id: 'PENDING',
    label: 'À compter',
    hint: 'Aucune quantité relevée dans cette zone.',
  },
  {
    id: 'IN_PROGRESS',
    label: 'En cours',
    hint: 'Des quantités sont saisies ; la zone n’est pas déclarée terminée.',
  },
  {
    id: 'DONE',
    label: 'Terminée',
    hint: 'Déclarée finie : elle entre dans la consolidation.',
  },
]

/**
 * Cartes ou lignes — le même choix que sur « Toutes les campagnes ».
 *
 * Les deux lectures sont légitimes et aucune ne gagne en général : la carte
 * porte les feuilles d'une zone côte à côte et se lit bien à dix zones, la
 * grille trie, filtre et totalise et tient encore à quatre-vingts. Le choix est
 * donc celui de l'utilisateur, et il est retenu — une préférence d'affichage
 * qui se réinitialise à chaque visite est une préférence que l'application fait
 * redire au lieu de la tenir.
 *
 * **Une ligne par feuille, pas par zone.** C'est la feuille qui porte un état,
 * un compteur et une action ; une ligne de zone devrait dédoubler chaque
 * colonne ou renvoyer aux cartes pour agir, ce qui retirerait à la grille son
 * intérêt. La colonne « Zone » les regroupe, et le tri par zone rend la lecture
 * par zone.
 */
export type ZoneDisplay = 'cards' | 'list'

const ZONE_DISPLAY_KEY = 'campagnes-inventaire.zones.display'

export function readZoneDisplay(): ZoneDisplay {
  try {
    return window.localStorage.getItem(ZONE_DISPLAY_KEY) === 'list' ? 'list' : 'cards'
  } catch {
    return 'cards'
  }
}

/** Une feuille et la zone qui la porte, mises à plat pour la grille. */
export interface SheetRow extends Record<string, unknown> {
  id: string
  zone: Zone
  sheet: Sheet
}

// --------------------------------------------------------------------------- //
// Zones
// --------------------------------------------------------------------------- //

export function ZonesTab({ campaignId, overview }: { campaignId: string; overview: Overview }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [creating, setCreating] = useState(false)
  const [printing, setPrinting] = useState(false)
  const [printSheet, setPrintSheet] = useState<{ sheetId: string; zone: Zone } | null>(null)
  const [multiScan, setMultiScan] = useState<File | null>(null)
  const [scanning, setScanning] = useState(false)
  const [openSheet, setOpenSheet] = useState<{ zone: Zone; sheet: Sheet } | null>(null)
  const [stage, setStage] = useState<ZoneStatus | ''>('')
  const [display, setDisplay] = useState<ZoneDisplay>(readZoneDisplay)

  const chooseDisplay = (next: ZoneDisplay) => {
    setDisplay(next)
    try {
      window.localStorage.setItem(ZONE_DISPLAY_KEY, next)
    } catch {
      // Navigation privée ou stockage plein : le choix vaut pour la session,
      // ce qui reste mieux que de refuser de l'appliquer.
    }
  }

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
    const tally = {} as Record<ZoneStatus, number>
    for (const zone of zones) {
      tally[zone.status] = (tally[zone.status] ?? 0) + 1
    }
    return {
      byStage: tally,
      visible: stage === '' ? zones : zones.filter((z) => z.status === stage),
    }
  }, [query.data, stage])

  // La seule décision d'état du parcours. Elle a remplacé quatre transitions
  // par feuille, qu'il fallait faire avancer à la main sans qu'aucune écriture
  // n'en dépende.
  const closure = useMutation({
    mutationFn: ({ zoneId, closed }: { zoneId: string; closed: boolean }) =>
      api.setZoneClosed(campaignId, zoneId, closed),
    onSuccess: (_result, { closed }) => {
      void queryClient.invalidateQueries()
      toast.success(
        closed ? 'Zone terminée' : 'Zone rouverte',
        closed
          ? 'Elle entre dans la consolidation.'
          : 'Les quantités redeviennent modifiables.',
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
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
              {ZONE_STAGES.map(({ id, label, hint }) => (
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

            <div className="row-wrap">
              <span className="subtle num">
                {visible.length} / {zones.length} zone(s)
              </span>
              <span className="spacer" />
              <div className="row" style={{ gap: 0 }}>
                <Button
                  variant={display === 'cards' ? 'primary' : 'ghost'}
                  size="sm"
                  icon={<Icons.dashboard size={14} />}
                  title="Affichage en icônes"
                  onClick={() => chooseDisplay('cards')}
                >
                  Icônes
                </Button>
                <Button
                  variant={display === 'list' ? 'primary' : 'ghost'}
                  size="sm"
                  icon={<Icons.grid size={14} />}
                  title="Affichage en liste — triable, filtrable, exportable"
                  onClick={() => chooseDisplay('list')}
                >
                  Liste
                </Button>
              </div>
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
            ) : display === 'list' ? (
              <ZoneTable
                zones={visible}
                editable={editable}
                busy={closure.isPending}
                onOpen={setOpenSheet}
                onPrint={setPrintSheet}
                onClose={(zoneId, closed) => closure.mutate({ zoneId, closed })}
              />
            ) : (
              <div className="grid grid--2">
                {visible.map((zone) => (
                  <Card
                    key={zone.id}
                    title={
                      // Le nom d'abord : trois badges à côté d'un titre non
                      // prioritaire réduisaient « Zone MÉTROLOGIE » à « Z.. ».
                      <span
                        className="row-wrap"
                        style={{ gap: 'var(--space-2)', rowGap: 'var(--space-1)' }}
                      >
                        <span className="truncate" style={{ minWidth: '8ch', flex: '1 1 auto' }}>
                          {zone.label || zone.code}
                        </span>
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
                      {/* Une feuille tient sur une ligne : son numéro, ce
                          qu'elle porte, et deux boutons. Elle affichait aussi
                          son propre statut et le bouton qui le faisait avancer,
                          ce qui la mettait sur deux lignes pour une donnée que
                          personne ne lisait. */}
                      {zone.sheets.map((sheet) => (
                        <div
                          key={sheet.id}
                          className="row"
                          style={{
                            padding: 'var(--space-2) var(--space-3)',
                            background: 'var(--bg-inset)',
                            borderRadius: 'var(--radius-md)',
                            gap: 'var(--space-2)',
                          }}
                        >
                          <strong
                            className="truncate"
                            style={{ fontSize: 'var(--text-sm)', minWidth: 92 }}
                          >
                            Comptage n°{sheet.pass_no === 'PASS_1' ? 1 : 2}
                          </strong>
                          <span className="subtle num">
                            {sheet.countedLines} / {sheet.lineCount} lignes
                          </span>
                          {sheet.counter_name && (
                            <span className="subtle truncate">· {sheet.counter_name}</span>
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
                              {sheet.correctedLines} corrigée(s)
                            </Badge>
                          )}
                          <span className="spacer" />
                          {/* Toujours actif pendant le comptage. Il l'était
                              auparavant au seul état « encodage en cours », ce
                              qui obligeait à cliquer deux boutons avant de
                              pouvoir saisir la première quantité. */}
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Icons.pencil size={13} />}
                            disabled={!editable}
                            onClick={() => setOpenSheet({ zone, sheet })}
                            aria-label="Ouvrir la feuille"
                            title={
                              editable
                                ? 'Ouvrir la feuille pour saisir ou scanner'
                                : 'Les quantités ne sont modifiables qu’en phase Comptage'
                            }
                          />
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<Icons.printer size={13} />}
                            onClick={() => setPrintSheet({ sheetId: sheet.id, zone })}
                            aria-label="Imprimer"
                            title="Imprimer cette feuille — vierge ou remplie"
                          />
                        </div>
                      ))}
                      {zone.pendingArbitrations > 0 && (
                        <Alert tone="warning" title={`${zone.pendingArbitrations} écart(s) à arbitrer`}>
                          La consolidation reste bloquée tant qu’une quantité n’est pas
                          retenue.
                        </Alert>
                      )}
                      {/* La seule décision d'état du parcours, et elle porte
                          sur la zone. Rouvrir ne se refuse jamais : c'est le
                          geste qui répare une clôture trop rapide. */}
                      {editable && (
                        <div className="row">
                          <span className="spacer" />
                          <Button
                            size="sm"
                            variant={zone.status === 'DONE' ? 'ghost' : 'primary'}
                            icon={
                              zone.status === 'DONE' ? (
                                <Icons.undo size={13} />
                              ) : (
                                <Icons.check size={13} />
                              )
                            }
                            disabled={closure.isPending}
                            onClick={() =>
                              closure.mutate({
                                zoneId: zone.id,
                                closed: zone.status !== 'DONE',
                              })
                            }
                            title={
                              zone.status === 'DONE'
                                ? 'Rouvrir cette zone pour corriger une quantité'
                                : 'Déclarer cette zone terminée : elle entre dans la consolidation'
                            }
                          >
                            {zone.status === 'DONE' ? 'Rouvrir' : 'Terminer la zone'}
                          </Button>
                        </div>
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

export function ZoneTable({
  zones,
  editable,
  busy,
  onOpen,
  onPrint,
  onClose,
}: {
  zones: Zone[]
  editable: boolean
  busy: boolean
  onOpen: (row: { zone: Zone; sheet: Sheet }) => void
  onPrint: (row: { sheetId: string; zone: Zone }) => void
  onClose: (zoneId: string, closed: boolean) => void
}) {
  const rows: SheetRow[] = zones.flatMap((zone) =>
    zone.sheets.map((sheet) => ({ id: sheet.id, zone, sheet })),
  )

  const columns: Column<SheetRow>[] = [
    {
      key: 'zone',
      label: 'Zone',
      width: 200,
      render: (row) => (
        <span className="truncate" title={row.zone.code}>
          {row.zone.label || row.zone.code}
        </span>
      ),
      value: (row) => row.zone.label || row.zone.code,
    },
    {
      key: 'sector',
      label: 'Secteur',
      width: 130,
      filter: 'choice',
      value: (row) => row.zone.sector || '',
    },
    {
      key: 'zoneStatus',
      label: 'État de la zone',
      width: 150,
      filter: 'choice',
      choiceLabel: (value) => toLabel(ZONE_STATUS_LABELS, value),
      render: (row) => (
        <Badge tone={ZONE_TONE[row.zone.status] ?? 'neutral'} dot>
          {toLabel(ZONE_STATUS_LABELS, row.zone.status)}
        </Badge>
      ),
      value: (row) => row.zone.status,
    },
    {
      key: 'pass',
      label: 'Comptage',
      width: 110,
      filter: 'choice',
      value: (row) => (row.sheet.pass_no === 'PASS_1' ? 'n°1' : 'n°2'),
    },
    {
      key: 'countedLines',
      label: 'Lignes comptées',
      numeric: true,
      width: 150,
      render: (row) => (
        <span className="num">
          {row.sheet.countedLines} / {row.sheet.lineCount}
        </span>
      ),
      value: (row) => row.sheet.countedLines,
    },
    {
      key: 'counter',
      label: 'Compteur',
      width: 150,
      value: (row) => row.sheet.counter_name || '',
    },
    {
      key: 'confidence',
      label: 'Confiance IA',
      numeric: true,
      width: 130,
      render: (row) =>
        row.sheet.extraction_confidence === null ? (
          <span className="subtle">—</span>
        ) : (
          <Badge tone={row.sheet.extraction_confidence < 0.75 ? 'danger' : 'neutral'}>
            {percent(row.sheet.extraction_confidence)}
          </Badge>
        ),
      value: (row) => row.sheet.extraction_confidence ?? 0,
    },
    {
      key: 'corrected',
      label: 'Corrigées à la main',
      numeric: true,
      width: 160,
      help: 'Un scan multi-feuilles préserve ces feuilles plutôt que d’écraser les corrections.',
      value: (row) => row.sheet.correctedLines,
    },
    {
      key: 'arbitrations',
      label: 'À arbitrer',
      numeric: true,
      width: 120,
      help: 'La consolidation reste bloquée tant qu’une quantité n’est pas retenue.',
      value: (row) => row.zone.pendingArbitrations,
    },
    {
      key: 'actions',
      label: '',
      width: 130,
      sortable: false,
      filter: false,
      sticky: 'right',
      render: (row) => {
        const done = row.zone.status === 'DONE'
        return (
          <span className="row" style={{ gap: 'var(--space-2)' }}>
            <Button
              size="sm"
              variant="ghost"
              icon={<Icons.pencil size={13} />}
              disabled={!editable}
              onClick={() => onOpen(row)}
              aria-label="Ouvrir la feuille"
              title={
                editable
                  ? 'Ouvrir la feuille pour saisir ou scanner'
                  : 'Les quantités ne sont modifiables qu’en phase Comptage'
              }
            />
            <Button
              size="sm"
              variant="ghost"
              icon={<Icons.printer size={13} />}
              onClick={() => onPrint({ sheetId: row.sheet.id, zone: row.zone })}
              aria-label="Imprimer"
              title="Imprimer cette feuille — vierge ou remplie"
            />
            {/* Porte sur la **zone**, pas sur la feuille : les deux lignes
                d'une zone à deux comptages montrent donc le même état, et
                l'une ou l'autre la termine. */}
            {editable && (
              <Button
                size="sm"
                variant={done ? 'ghost' : 'secondary'}
                icon={done ? <Icons.undo size={13} /> : <Icons.check size={13} />}
                disabled={busy}
                onClick={() => onClose(row.zone.id, !done)}
                aria-label={done ? 'Rouvrir la zone' : 'Terminer la zone'}
                title={
                  done
                    ? `Rouvrir la zone ${row.zone.code}`
                    : `Déclarer la zone ${row.zone.code} terminée`
                }
              />
            )}
          </span>
        )
      },
    },
  ]

  return (
    <Card>
      <DataGrid
        columns={columns}
        rows={rows}
        getRowId={(row) => row.id}
        searchable
        searchPlaceholder="Filtrer par zone, secteur ou compteur…"
        emptyTitle="Aucune feuille"
        maxHeight={640}
      />
    </Card>
  )
}
