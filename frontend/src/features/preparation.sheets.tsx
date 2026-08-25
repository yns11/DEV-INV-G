/** Les feuilles de comptage préparées zone par zone. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GRID_ROW_CEILING, api } from '../lib/api'
import type { GridContract, Overview, PrintMode, Zone } from '../lib/types'
import { qty } from '../lib/format'
import { sectionColumn } from './sectionColumn'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { PrintModal } from '../components/PrintModal'
import { ZonesAdminGrid } from './zones'
import { Alert, AsyncBoundary, Button, Card, ConfirmDelete, EmptyState, Icons, useErrorToast, useToast } from '../components/ui'
import { DRAFT_PREFIX, rowKey } from './preparation.shared'

// --------------------------------------------------------------------------- //
// Prepared counting sheets
// --------------------------------------------------------------------------- //

export function CountSheetsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const [printing, setPrinting] = useState(false)
  const [printZones, setPrintZones] = useState<string[] | null>(null)
  const [sheetView, setSheetView] = useState<'zones' | 'lines'>('zones')
  const [zoneFilter, setZoneFilter] = useState('')
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })
  const zones = useQuery({
    queryKey: ['zones', campaignId, false],
    queryFn: () => api.zones(campaignId, {}),
  })

  // Which documents the whole set can produce, and how many sheets each would
  // yield. Computed from the zones themselves — the print matrix lives on the
  // server and arrives as `zone.printModes`, so this screen never guesses.
  const batch = useMemo(() => {
    const counts = { blank: 0, list: 0, filled: 0 } as Record<PrintMode, number>
    for (const zone of zones.data ?? []) {
      for (const mode of zone.printModes ?? []) counts[mode] = (counts[mode] ?? 0) + 1
    }
    const modes = (['list', 'blank', 'filled'] as PrintMode[]).filter(
      (m) => (counts[m] ?? 0) > 0,
    )
    return { modes, counts }
  }, [zones.data])

  return (
    <div className="stack">
      {/* Printing belongs here, not three screens away under the counting
          phase: the sheets are handed out *before* anyone counts, and having to
          go looking for the button in GENERIQUE was the reason people printed
          from the wrong screen. */}
      <Card
        title="Feuilles à imprimer"
        message="Préparez et sortez le papier avant la journée de comptage."
        actions={
          <Button
            variant="primary"
            icon={<Icons.printer size={14} />}
            disabled={batch.modes.length === 0}
            title={
              batch.modes.length === 0
                ? 'Créez d’abord au moins une zone.'
                : undefined
            }
            onClick={() => setPrinting(true)}
          >
            Imprimer toutes les feuilles
          </Button>
        }
      >
        <p className="muted">
          {batch.modes.length === 0
            ? 'Aucune zone pour l’instant : les feuilles apparaîtront ici dès qu’une zone existe.'
            : `${zones.data?.length ?? 0} zone(s) prêtes à être imprimées.`}
        </p>
      </Card>

      {overview.permissions.countSheets && (
      <Alert tone="info" title="Une ligne par couple feuille / article">
        Une feuille inconnue est créée, une feuille connue complétée. Les lignes sont
        posées sur <strong>les deux comptages</strong>, quantités vides.
      </Alert>
      )}

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="count_sheets"
        disabled={!overview.permissions.countSheets}
        disabledReason="Les feuilles de comptage sont gelées depuis le passage en phase d’analyse."
        onImported={() => void queryClient.invalidateQueries()}
      />

      {overview.permissions.countSheets && (
        <Alert tone="warning" title="Le référentiel articles fait foi">
          Un article absent du référentiel est rejeté, jamais créé à la volée.
        </Alert>
      )}

      <div className="chips">
        {(['zones', 'lines'] as const).map((id) => (
          <button
            key={id}
            className={`chip${sheetView === id ? ' chip--active' : ''}`}
            title={
              id === 'zones'
                ? 'Une ligne par zone : le nombre de comptages, le gestionnaire, l’impression.'
                : 'Une ligne par article à compter, toutes zones confondues — pour corriger une référence posée au mauvais endroit.'
            }
            onClick={() => {
              setSheetView(id)
              if (id === 'zones') setZoneFilter('')
            }}
          >
            {id === 'zones' ? 'Zones et feuilles' : 'Toutes les lignes'}
          </button>
        ))}
      </div>

      {sheetView === 'zones' ? (
        <ZonesAdminGrid
          campaignId={campaignId}
          editable={overview.permissions.zones}
          // La suppression s'arrête au passage en comptage, où les feuilles
          // portent des quantités relevées. Le serveur applique la même règle.
          deletable={
            overview.permissions.zones &&
            overview.campaign.status === 'PREPARATION'
          }
          managers={managers.data?.managers ?? []}
          onPrint={(selection) => setPrintZones(selection.map((z) => z.id))}
          onOpen={(zone) => {
            setZoneFilter(zone.id)
            setSheetView('lines')
          }}
        />
      ) : (
        <SheetLinesView
          campaignId={campaignId}
          zones={zones.data ?? []}
          zoneId={zoneFilter}
          onZoneChange={setZoneFilter}
          editable={overview.permissions.countSheets}
        />
      )}

      {(printing || printZones !== null) && (
        <PrintModal
          campaignId={campaignId}
          modes={batch.modes}
          zonesByMode={printZones === null ? batch.counts : undefined}
          zoneIds={printZones ?? undefined}
          onClose={() => {
            setPrinting(false)
            setPrintZones(null)
          }}
        />
      )}
    </div>
  )
}

/**
 * Toutes les lignes de feuilles, à plat.
 *
 * Les écrans par zone répondent à « qu'y a-t-il sur cette feuille ? ». Celui-ci
 * répond à « où cette référence apparaît-elle ? », qui est la question quand une
 * ligne a été posée sur la mauvaise zone ou qu'une famille entière doit être
 * retirée de quinze feuilles. Mêmes lignes, une seule liste : la correction
 * coûte une modification au lieu de quinze navigations.
 *
 * Les quantités n'y sont pas éditables — elles ne le sont qu'au comptage, et
 * c'est le serveur qui le décide. Ce qui se règle ici, c'est *ce qu'il y a à
 * compter*, pas ce qui a été trouvé.
 */
function SheetLinesView({
  campaignId,
  zones,
  zoneId,
  onZoneChange,
  editable,
}: {
  campaignId: string
  zones: Zone[]
  zoneId: string
  onZoneChange: (next: string) => void
  editable: boolean
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [draft, setDraft] = useState<Record<string, unknown>[] | null>(null)
  //: Vrai quand la suppression de la sélection attend confirmation. Ces lignes
  //: sont le travail de préparation — une liste qu'on a élaguée article par
  //: article — et le fichier d'origine ne les rendra pas telles quelles.
  const [removingLines, setRemovingLines] = useState(false)

  const query = useQuery({
    queryKey: ['sheet-lines', campaignId, zoneId],
    queryFn: () =>
      api.sheetLines(campaignId, zoneId || undefined, { limit: GRID_ROW_CEILING }),
  })

  const refresh = () => {
    setDraft(null)
    setSelected(new Set())
    void queryClient.invalidateQueries({ queryKey: ['sheet-lines'] })
    void queryClient.invalidateQueries({ queryKey: ['zones'] })
  }

  const remove = useMutation({
    mutationFn: (lineIds: string[]) => api.deleteSheetLines(campaignId, lineIds),
    onSuccess: (result) => {
      refresh()
      toast.success(`${result.deleted} ligne(s) supprimée(s)`)
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  /**
   * Supprimer la sélection.
   *
   * Une ligne ajoutée à la grille et pas encore enregistrée n'existe que dans
   * ce navigateur : la « supprimer » c'est la retirer du brouillon, et
   * l'envoyer au serveur reviendrait à lui demander d'effacer quelque chose
   * qu'il n'a jamais vu.
   */
  const deleteSelection = () => {
    const saved = [...selected].filter((id) => !id.startsWith(DRAFT_PREFIX))
    const drafts = selected.size - saved.length
    if (drafts > 0 && draft) {
      setDraft(
        draft.filter(
          (row, index) => !selected.has(rowKey(row, index)) || Boolean(row.id),
        ),
      )
    }
    if (saved.length === 0) {
      setSelected(new Set())
      toast.success(`${drafts} ligne(s) non enregistrée(s) retirée(s)`)
      return
    }
    remove.mutate(saved)
  }

  // Une sauvegarde par feuille : l'endpoint travaille feuille par feuille, et
  // la grille à plat en couvre plusieurs. Regrouper ici évite d'inventer un
  // endpoint « lignes de partout » dont personne ne saurait dire ce qu'il gèle.
  //
  // Une ligne neuve n'a pas de feuille : elle va sur *toutes* celles de la zone
  // filtrée, comme le fait l'import — une référence à compter l'est par les deux
  // équipes, sinon le second comptage n'a rien à comparer. C'est aussi pourquoi
  // on n'ajoute rien sans zone choisie : la ligne n'aurait nulle part où aller.
  const save = useMutation({
    mutationFn: async (rows: Record<string, unknown>[]) => {
      const targets = zones
        .filter((z) => z.id === zoneId)
        .flatMap((z) => z.sheets.map((s) => s.id))
      const bySheet = new Map<string, Record<string, unknown>[]>()
      for (const row of rows) {
        const sheetId = String(row.sheet_id ?? '')
        if (sheetId) {
          bySheet.set(sheetId, [...(bySheet.get(sheetId) ?? []), row])
          continue
        }
        if (!String(row.item_number ?? '').trim()) continue
        for (const target of targets) {
          bySheet.set(target, [...(bySheet.get(target) ?? []), { ...row, id: null }])
        }
      }
      let written = 0
      for (const [sheetId, sheetRows] of bySheet) {
        const result = await api.saveSheetLines(
          campaignId,
          sheetId,
          sheetRows.map((row) => ({
            id: row.id,
            itemNumber: String(row.item_number ?? ''),
            section: String(row.section ?? 'LINE_SIDE'),
            // La quantité repart telle quelle : ne pas la renvoyer l'effacerait,
            // et la modifier ici serait refusé par le serveur de toute façon.
            qty: row.qty ?? null,
            unit: String(row.unit ?? 'PCE'),
            comment: String(row.comment ?? ''),
          })),
        )
        written += result.written
      }
      return written
    },
    onSuccess: (written) => {
      refresh()
      toast.success(`${written} ligne(s) enregistrée(s)`)
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data?.rows ?? []
  //: Ce que le plafond laisse dehors. Compté sur la page reçue et non sur
  //: `rows`, qui porte le brouillon en cours d'édition : une ligne ajoutée à
  //: la main ferait sinon baisser le nombre de lignes « non chargées ».
  const loaded = query.data?.rows.length ?? 0
  const hidden = Math.max(0, (query.data?.total ?? loaded) - loaded)
  const columns: Column[] = [
    { key: 'zoneCode', label: 'Zone', width: 160, editable: false },
    { key: 'passNo', label: 'Comptage', numeric: true, width: 100, editable: false },
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 240, editable: false },
    sectionColumn({ width: 150 }),
    { key: 'unit', label: 'Unité', width: 90 },
    { key: 'comment', label: 'Commentaire', width: 220 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 120,
      editable: false,
      render: (row) =>
        row.qty === null || row.qty === undefined ? (
          <span className="subtle">non compté</span>
        ) : (
          <span className="num">{qty(Number(row.qty))}</span>
        ),
      value: (row) => (row.qty === null ? null : Number(row.qty)),
    },
  ]

  return (
    <Card
      title="Lignes de feuilles"
      message="Ce qu’il y a à compter, zone par zone. Les quantités se saisissent au comptage, pas ici."
      actions={
        <select
          className="input"
          style={{ width: 250 }}
          value={zoneId}
          onChange={(event) => {
            onZoneChange(event.target.value)
            setDraft(null)
            setSelected(new Set())
          }}
        >
          <option value="">Toutes les zones</option>
          {zones.map((zone) => (
            <option key={zone.id} value={zone.id}>
              {zone.label || zone.code}
            </option>
          ))}
        </select>
      }
      flush
    >
      <AsyncBoundary query={query} isEmpty={() => rows.length === 0}
        empty={
          <EmptyState title="Aucune ligne">
            Chargez la grille « Feuilles de comptage » ci-dessus, ou créez une zone
            en saisie libre.
          </EmptyState>
        }
      >
        {() => (
          <DataGrid
            columns={columns}
            rows={rows}
            exportTitle="Lignes de feuilles"
            campaignId={campaignId}
            getRowId={rowKey}
            selectable={editable}
            canAdd={Boolean(zoneId)}
            selected={selected}
            onSelectedChange={setSelected}
            editable={editable}
            onRowsChange={(next) => setDraft(next as Record<string, unknown>[])}
            searchPlaceholder="Filtrer par zone, article, désignation…"
            maxHeight={560}
            toolbar={
              <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
                {editable && selected.size > 0 && (
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<Icons.trash size={13} />}
                    disabled={remove.isPending}
                    onClick={() => setRemovingLines(true)}
                  >
                    Supprimer la sélection
                  </Button>
                )}
                {draft && (
                  <>
                    <Button
                      size="sm"
                      variant="primary"
                      disabled={save.isPending}
                      onClick={() => save.mutate(draft)}
                    >
                      Enregistrer
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                      Annuler
                    </Button>
                  </>
                )}
              </div>
            }
            footer={
              <span>
                {(draft ? rows.length : (query.data?.total ?? 0)).toLocaleString('fr-FR')} ligne(s)
                {zoneId ? ' sur cette zone' : ' sur toute la campagne'}
                {hidden > 0 &&
                  ` — ${loaded.toLocaleString('fr-FR')} affichées, filtrez sur une zone pour voir les autres`}
              </span>
            }
          />
        )}
      </AsyncBoundary>
      {removingLines && (
        <ConfirmDelete
          what={`${selected.size} ligne(s) de feuille`}
          consequences={[
            'Ces articles ne seront plus imprimés sur les feuilles de la zone.',
            'Les quantités déjà saisies sur ces lignes partent avec elles.',
          ]}
          pending={remove.isPending}
          onClose={() => setRemovingLines(false)}
          onConfirm={() => {
            deleteSelection()
            setRemovingLines(false)
          }}
        />
      )}
    </Card>
  )
}
