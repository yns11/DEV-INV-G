/**
 * Counting journals — the operational screen of inventory day.
 *
 * Genre: operational worklist. Ordering is by *what is not done yet*, because
 * on the day the only question is "which locations are still open?".
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api, download, downloads } from '../lib/api'
import { compositeKey, splitCompositeKey } from '../lib/rowKey'
import type { GridContract, Journal, JournalStatus, Overview } from '../lib/types'
import {
  JOURNAL_STATUS_LABELS,
  moneyShort,
  qty,
  label as toLabel,
} from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, SourceBadge, type Column } from '../components/DataGrid'
import { useFocusMode } from '../lib/focus'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Icons,
  Modal,
  Skeleton,
  ViewTabs,
  useErrorToast,
  useToast,
} from '../components/ui'

type Tab = 'journals' | 'import' | 'locations' | 'controls'

const STATUS_TONE: Record<JournalStatus, string> = {
  PENDING: 'neutral',
  IN_PROGRESS: 'accent',
  POSTED: 'success',
  BOOK_ENFORCED: 'info',
}

export function Counting() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useState<Tab>('journals')

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const journalContract: GridContract | undefined = contracts.data?.find(
    (c) => c.key === 'count_journal_lines',
  )

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <ViewTabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'journals', label: 'Journaux', count: overview.journalProgress.total },
          { id: 'import', label: 'Import ERP' },
          { id: 'locations', label: 'Entrepôts & emplacements' },
          { id: 'controls', label: 'Contrôles' },
        ]}
      />

      {tab === 'journals' && <JournalsTab campaignId={campaignId} overview={overview} />}
      {tab === 'import' && journalContract && (
        <ImportPanel
          campaignId={campaignId}
          contract={journalContract}
          target="count_journal_lines"
          disabled={!overview.permissions.countJournals}
          disabledReason="Les journaux de comptage sont gelés depuis le passage en phase d’analyse."
          extraActions={
            <Badge tone="info">
              Un rechargement ne détruit jamais une correction manuelle
            </Badge>
          }
        />
      )}
      {tab === 'locations' && <LocationsTab campaignId={campaignId} overview={overview} />}
      {tab === 'controls' && <ControlsTab campaignId={campaignId} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Journals
// --------------------------------------------------------------------------- //

function JournalsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [openJournal, setOpenJournal] = useState<Journal | null>(null)
  const [statusFilter, setStatusFilter] = useState<JournalStatus | 'ALL'>('ALL')

  const [focus] = useFocusMode()
  const query = useQuery({
    queryKey: ['journals', campaignId, focus],
    // Server-side: a journal outside the perimeter is not hidden here, it is
    // never sent. Filtering in the browser would still ship the site's whole
    // counting state to every workstation.
    queryFn: () => api.journals(campaignId, focus ? { focus: true } : {}),
    refetchInterval: 45_000,
  })

  const setStatus = useMutation({
    mutationFn: ({ ids, status }: { ids: string[]; status: JournalStatus }) =>
      api.setJournalStatus(campaignId, ids, status),
    onSuccess: (result, variables) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} journal(aux) mis à jour`,
        variables.status === 'BOOK_ENFORCED'
          ? 'Leur quantité comptée est désormais celle du stock ERP : écart nul par construction.'
          : undefined,
      )
    },
    onError: (error) => showError(error, 'Changement de statut impossible'),
  })

  const rows = useMemo(() => {
    const all = query.data ?? []
    return statusFilter === 'ALL' ? all : all.filter((j) => j.status === statusFilter)
  }, [query.data, statusFilter])

  const counts = useMemo(() => {
    const all = query.data ?? []
    return {
      ALL: all.length,
      PENDING: all.filter((j) => j.status === 'PENDING').length,
      IN_PROGRESS: all.filter((j) => j.status === 'IN_PROGRESS').length,
      POSTED: all.filter((j) => j.status === 'POSTED').length,
      BOOK_ENFORCED: all.filter((j) => j.status === 'BOOK_ENFORCED').length,
    }
  }, [query.data])

  const columns: Column<Journal>[] = [
    { key: 'warehouse_id', label: 'Entrepôt', width: 120 },
    { key: 'location_id', label: 'Emplacement', width: 160 },
    {
      key: 'kind',
      label: 'Type',
      width: 100,
      render: (row) => (
        <Badge tone="neutral" title={row.kind === 'INVE' ? 'Inventaire par étiquette (scan)' : 'Inventaire vrac (saisie)'}>
          {row.kind}
        </Badge>
      ),
      value: (row) => row.kind,
    },
    {
      key: 'status',
      label: 'Statut',
      width: 170,
      render: (row) => (
        <span className="row" style={{ gap: 'var(--space-1)' }}>
          <Badge tone={STATUS_TONE[row.status]} dot>
            {toLabel(JOURNAL_STATUS_LABELS, row.status)}
          </Badge>
          {row.auto_created && <Badge tone="warning">auto</Badge>}
        </span>
      ),
      value: (row) => row.status,
    },
    { key: 'journal_number', label: 'N° ERP', width: 140 },
    {
      key: 'lineCount',
      label: 'Lignes',
      numeric: true,
      width: 90,
      render: (row) => <span className="num">{row.lineCount}</span>,
      value: (row) => row.lineCount,
    },
    {
      key: 'countedQty',
      label: 'Qté comptée',
      numeric: true,
      width: 130,
      render: (row) => <span className="num">{qty(row.countedQty)}</span>,
      value: (row) => row.countedQty,
    },
    {
      key: 'overriddenLines',
      label: 'Corrigées',
      numeric: true,
      width: 110,
      render: (row) =>
        row.overriddenLines ? (
          <Badge tone="accent">{row.overriddenLines}</Badge>
        ) : (
          <span className="subtle">—</span>
        ),
      value: (row) => row.overriddenLines,
    },
    {
      key: 'actions',
      label: '',
      width: 100,
      sortable: false,
      render: (row) => (
        <div className="row" style={{ gap: 'var(--space-1)' }}>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOpenJournal(row)}
            title="Ouvrir le journal"
          >
            Ouvrir
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon={<Icons.download size={13} />}
            onClick={() => download(downloads.journal(campaignId, row.id))}
            title="Exporter au format d’import ERP"
            aria-label="Exporter"
          />
        </div>
      ),
    },
  ]

  const editable = overview.permissions.countJournals

  return (
    <div className="stack">
      <div className="chips">
        {(['ALL', 'PENDING', 'IN_PROGRESS', 'POSTED', 'BOOK_ENFORCED'] as const).map((status) => (
          <button
            key={status}
            className={`chip${statusFilter === status ? ' chip--active' : ''}`}
            onClick={() => setStatusFilter(status)}
          >
            {status === 'ALL' ? 'Tous' : toLabel(JOURNAL_STATUS_LABELS, status)}
            <span className="num">{counts[status]}</span>
          </button>
        ))}
      </div>

      {selected.size > 0 && editable && (
        <Alert tone="info" title={`${selected.size} journal(aux) sélectionné(s)`}>
          <div className="row-wrap" style={{ marginTop: 'var(--space-2)' }}>
            <Button
              size="sm"
              variant="primary"
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus.mutate({ ids: [...selected], status: 'POSTED' })
              }
            >
              Marquer comme postés
            </Button>
            <Button
              size="sm"
              disabled={setStatus.isPending}
              onClick={() =>
                setStatus.mutate({ ids: [...selected], status: 'BOOK_ENFORCED' })
              }
              title="Pour les emplacements inventoriés avant la date du stock ERP"
            >
              Forcer au stock ERP
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Désélectionner
            </Button>
          </div>
        </Alert>
      )}

      <Card
        title="Journaux de comptage"
        message="Un journal par emplacement actif. Un emplacement déjà inventorié peut être aligné sur le stock ERP, son écart devenant nul."
        flush
      >
        <AsyncBoundary
          query={query}
          isEmpty={() => rows.length === 0}
          empty={
            <EmptyState
              title={focus ? 'Aucun journal dans votre périmètre' : 'Aucun journal'}
            >
              {focus
                ? 'Aucun entrepôt ne vous est affecté. Coupez « Mon périmètre » pour voir toute la campagne — vous gardez le droit d’y agir.'
                : 'Les journaux sont créés automatiquement au chargement du stock ERP, un par emplacement actif.'}
            </EmptyState>
          }
        >
          {() => (
            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row) => row.id}
              selectable={editable}
              selected={selected}
              onSelectedChange={setSelected}
              searchPlaceholder="Filtrer par entrepôt, emplacement, n° de journal…"
              maxHeight={560}
              initialSort={{ key: 'status', direction: 'asc' }}
            />
          )}
        </AsyncBoundary>
      </Card>

      {openJournal && (
        <JournalModal
          campaignId={campaignId}
          journal={openJournal}
          editable={editable}
          onClose={() => setOpenJournal(null)}
        />
      )}
    </div>
  )
}

function JournalModal({
  campaignId,
  journal,
  editable,
  onClose,
}: {
  campaignId: string
  journal: Journal
  editable: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const query = useQuery({
    queryKey: ['journal', campaignId, journal.id],
    queryFn: () => api.journal(campaignId, journal.id),
  })

  const saveLine = useMutation({
    mutationFn: (body: { lineId: string; itemNumber: string; qty: number | null }) =>
      api.saveJournalLine(campaignId, journal.id, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['journal', campaignId, journal.id] })
      void queryClient.invalidateQueries({ queryKey: ['journals', campaignId] })
      toast.success('Ligne corrigée')
    },
    onError: (error) => showError(error, 'Correction impossible'),
  })

  return (
    <Modal
      title={
        <span className="row" style={{ gap: 'var(--space-3)' }}>
          {journal.warehouse_id} / {journal.location_id}
          <Badge tone={STATUS_TONE[journal.status]}>
            {toLabel(JOURNAL_STATUS_LABELS, journal.status)}
          </Badge>
        </span>
      }
      onClose={onClose}
      width={1000}
      footer={
        <Button
          icon={<Icons.download size={14} />}
          onClick={() => download(downloads.journal(campaignId, journal.id))}
        >
          Exporter pour l’ERP
        </Button>
      }
    >
      <AsyncBoundary query={query} skeleton={<Skeleton height={280} />}>
        {(data) => (
          <div className="stack">
            {data.notCounted.length > 0 && (
              <Alert
                tone="warning"
                title={`${data.notCounted.length} article(s) du stock ERP non comptés`}
              >
                Ils seront soldés à zéro à la clôture. Total en jeu :{' '}
                <strong>
                  {moneyShort(data.notCounted.reduce((sum, row) => sum + row.value, 0))}
                </strong>
                .
                <div className="table-wrap" style={{ maxHeight: 170, marginTop: 'var(--space-2)' }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Article</th>
                        <th className="num">Stock ERP</th>
                        <th className="num">Valeur</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.notCounted.slice(0, 20).map((row) => (
                        <tr key={row.itemNumber}>
                          <td className="mono">{row.itemNumber}</td>
                          <td className="num">
                            {qty(row.bookQty)} {row.unit}
                          </td>
                          <td className="num">{moneyShort(row.value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Alert>
            )}

            <div className="table-wrap" style={{ maxHeight: 420 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Article</th>
                    <th className="num">Importé</th>
                    <th className="num">Corrigé</th>
                    <th className="num">Retenu</th>
                    <th className="num">Stock ERP</th>
                    <th className="num">Écart</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((line) => (
                    <tr key={line.id}>
                      <td className="mono">{line.item_number}</td>
                      <td className="num subtle">
                        {line.qty_imported === null ? '—' : qty(line.qty_imported)}
                      </td>
                      <td className="editable num">
                        {editable && journal.status !== 'POSTED' ? (
                          <input
                            className="num"
                            inputMode="decimal"
                            defaultValue={line.qty_manual ?? ''}
                            placeholder="—"
                            onBlur={(event) => {
                              const raw = event.target.value.trim()
                              const next = raw === '' ? null : Number(raw.replace(',', '.'))
                              if (next === (line.qty_manual ?? null)) return
                              if (next !== null && Number.isNaN(next)) return
                              saveLine.mutate({
                                lineId: line.id,
                                itemNumber: line.item_number,
                                qty: next,
                              })
                            }}
                          />
                        ) : line.qty_manual === null ? (
                          <span className="subtle">—</span>
                        ) : (
                          qty(line.qty_manual)
                        )}
                      </td>
                      <td className="num">
                        <strong>{qty(line.qty)}</strong>
                      </td>
                      <td className="num subtle">{qty(line.bookQty)}</td>
                      <td className={`num ${line.varianceQty === 0 ? 'neutral' : line.varianceQty > 0 ? 'pos' : 'neg'}`}>
                        {qty(line.varianceQty)}
                      </td>
                      <td>
                        <SourceBadge
                          source={line.effectiveSource}
                          overridden={line.isOverridden}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {journal.status === 'POSTED' && (
              <Alert tone="info" title="Journal posté">
                Pour corriger une ligne, dépostez le journal dans l’ERP puis rechargez
                l’export : les corrections manuelles déjà saisies seront préservées.
              </Alert>
            )}
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}

// --------------------------------------------------------------------------- //
// Locations
// --------------------------------------------------------------------------- //

function LocationsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const query = useQuery({
    queryKey: ['locations', campaignId],
    queryFn: () => api.locations(campaignId),
  })

  const setStatus = useMutation({
    mutationFn: ({ keys, status }: { keys: Array<{ warehouseId: string; locationId: string }>; status: 'ACTIVE' | 'DISABLED' }) =>
      api.setLocationStatus(campaignId, keys, status),
    onSuccess: (result, variables) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} emplacement(s) ${variables.status === 'ACTIVE' ? 'réactivé(s)' : 'désactivé(s)'}`,
        variables.status === 'DISABLED'
          ? `${result.journalsRemoved} journal(aux) supprimé(s) ; ces emplacements sortent totalement du périmètre.`
          : `${result.journalsCreated} journal(aux) recréé(s).`,
      )
    },
    onError: (error) => showError(error, 'Changement de statut impossible'),
  })

  const columns: Column[] = [
    { key: 'warehouse_id', label: 'Entrepôt', width: 130 },
    { key: 'location_id', label: 'Emplacement', width: 170 },
    { key: 'zone', label: 'Zone logistique', width: 180 },
    {
      key: 'type',
      label: 'Type',
      width: 130,
      render: (row) => (
        <Badge tone="neutral">
          {row.type === 'LABEL' ? 'Étiquettes' : row.type === 'BULK' ? 'Vrac' : '—'}
        </Badge>
      ),
      value: (row) => String(row.type),
    },
    {
      key: 'status',
      label: 'Statut',
      width: 130,
      render: (row) => (
        <Badge tone={row.status === 'ACTIVE' ? 'success' : 'neutral'} dot>
          {row.status === 'ACTIVE' ? 'Actif' : 'Désactivé'}
        </Badge>
      ),
      value: (row) => String(row.status),
    },
    {
      key: 'journalStatus',
      label: 'Journal',
      width: 160,
      render: (row) =>
        row.hasJournal ? (
          <Badge tone={STATUS_TONE[row.journalStatus as JournalStatus] ?? 'neutral'}>
            {toLabel(JOURNAL_STATUS_LABELS, String(row.journalStatus))}
          </Badge>
        ) : (
          <span className="subtle">aucun</span>
        ),
      value: (row) => String(row.journalStatus ?? ''),
    },
  ]

  const editable = overview.permissions.locations

  return (
    <div className="stack">
      <Alert tone="info" title="Périmètre de comptage">
        Construit automatiquement à partir du stock ERP. Désactiver un emplacement
        supprime son journal et le sort de tous les indicateurs.
      </Alert>

      {selected.size > 0 && editable && (
        <div className="row-wrap">
          <Button
            size="sm"
            variant="danger"
            disabled={setStatus.isPending}
            onClick={() =>
              setStatus.mutate({
                keys: [...selected].map((key) => {
                  const [warehouseId, locationId] = splitCompositeKey(key)
                  return { warehouseId, locationId }
                }),
                status: 'DISABLED',
              })
            }
          >
            Désactiver {selected.size} emplacement(s)
          </Button>
          <Button
            size="sm"
            disabled={setStatus.isPending}
            onClick={() =>
              setStatus.mutate({
                keys: [...selected].map((key) => {
                  const [warehouseId, locationId] = splitCompositeKey(key)
                  return { warehouseId, locationId }
                }),
                status: 'ACTIVE',
              })
            }
          >
            Réactiver
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
            Désélectionner
          </Button>
        </div>
      )}

      <Card title="Entrepôts et emplacements" flush>
        <AsyncBoundary query={query} isEmpty={(d) => d.locations.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.locations}
              getRowId={(row) => compositeKey(row.warehouse_id, row.location_id)}
              selectable={editable}
              selected={selected}
              onSelectedChange={setSelected}
              searchPlaceholder="Filtrer par entrepôt, emplacement, zone…"
              maxHeight={600}
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Controls
// --------------------------------------------------------------------------- //

function ControlsTab({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['counting-controls', campaignId],
    queryFn: () => api.countingControls(campaignId),
  })

  return (
    <Card
      title="Contrôles sur les journaux"
      message="Article inconnu, unité incohérente, comptage négatif, doublon, emplacement désactivé — vérifiés en continu plutôt qu’en post-mortem."
      flush
    >
      <AsyncBoundary
        query={query}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="Aucune anomalie" icon={<Icons.check size={20} />}>
            Les journaux de comptage sont cohérents avec le référentiel.
          </EmptyState>
        }
      >
        {(rows) => (
          <DataGrid
            columns={[
              {
                key: 'severity',
                label: 'Sévérité',
                width: 120,
                render: (row) => (
                  <Badge tone={row.severity === 'BLOCKER' ? 'danger' : 'warning'}>
                    {row.severity === 'BLOCKER' ? 'Bloquant' : 'Avertissement'}
                  </Badge>
                ),
                value: (row) => String(row.severity),
              },
              { key: 'code', label: 'Code', width: 200 },
              { key: 'item_number', label: 'Article', width: 160 },
              { key: 'warehouse_id', label: 'Entrepôt', width: 110 },
              { key: 'location_id', label: 'Emplacement', width: 140 },
              { key: 'message', label: 'Constat', width: 420 },
            ]}
            rows={rows as unknown as Array<Record<string, unknown>>}
            getRowId={(_, index) => String(index)}
            searchPlaceholder="Filtrer les contrôles…"
            maxHeight={560}
          />
        )}
      </AsyncBoundary>
    </Card>
  )
}
