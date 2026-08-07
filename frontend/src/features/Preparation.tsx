/** Referentials, materiality thresholds and the book-stock snapshot. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { GRID_ROW_CEILING, api } from '../lib/api'
import type { GridContract, Manager, Overview, Threshold } from '../lib/types'
import { ITEM_TYPE_LABELS, moneyShort, numShort, percent } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { ZonesAdminGrid } from './zones'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  Icons,
  Skeleton,
  Tabs,
  useErrorToast,
  useToast,
} from '../components/ui'

type Tab =
  | 'items'
  | 'boms'
  | 'book_stock'
  | 'count_sheets'
  | 'thresholds'
  | 'managers'
  | 'journal_scope'
  | 'zone_scope'

export function Preparation() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useState<Tab>('items')

  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract = (key: string): GridContract | undefined =>
    contracts.data?.find((c) => c.key === key)

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <Tabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'items', label: 'Articles', count: overview.counts.items },
          { id: 'boms', label: 'Nomenclatures' },
          { id: 'book_stock', label: 'Stock livre', count: overview.counts.bookStockLines },
          { id: 'count_sheets', label: 'Feuilles de comptage' },
          { id: 'thresholds', label: 'Seuils' },
          { id: 'managers', label: 'Gestionnaires' },
          { id: 'journal_scope', label: 'Affectation journaux' },
          { id: 'zone_scope', label: 'Affectation zones' },
        ]}
      />

      {contracts.isPending && <Skeleton height={240} />}

      {tab === 'items' && contract('items') && (
        <ItemsTab campaignId={campaignId} contract={contract('items')!} overview={overview} />
      )}
      {tab === 'boms' && contract('boms') && (
        <BomsTab campaignId={campaignId} contract={contract('boms')!} overview={overview} />
      )}
      {tab === 'book_stock' && contract('book_stock') && (
        <BookStockTab
          campaignId={campaignId}
          contract={contract('book_stock')!}
          overview={overview}
        />
      )}
      {tab === 'count_sheets' && contract('count_sheets') && (
        <CountSheetsTab
          campaignId={campaignId}
          contract={contract('count_sheets')!}
          overview={overview}
        />
      )}
      {tab === 'thresholds' && <ThresholdsTab campaignId={campaignId} overview={overview} />}
      {tab === 'managers' && <ManagersTab campaignId={campaignId} overview={overview} />}
      {tab === 'journal_scope' && (
        <JournalScopeTab campaignId={campaignId} overview={overview} />
      )}
      {tab === 'zone_scope' && <ZoneScopeTab campaignId={campaignId} overview={overview} />}
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Articles
// --------------------------------------------------------------------------- //

function ItemsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const query = useQuery({
    queryKey: ['items', campaignId, search],
    queryFn: () => api.items(campaignId, { limit: GRID_ROW_CEILING, search: search || undefined }),
  })

  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'name', label: 'Désignation', width: 280 },
    {
      key: 'item_type',
      label: 'Type',
      width: 130,
      render: (row) => (
        <Badge tone="neutral">
          {ITEM_TYPE_LABELS[String(row.item_type)] ?? String(row.item_type)}
        </Badge>
      ),
      value: (row) => String(row.item_type),
    },
    { key: 'category', label: 'Catégorie', width: 130 },
    { key: 'program', label: 'Programme', width: 120 },
    { key: 'unit', label: 'Unité', width: 80 },
    {
      key: 'stdPrice',
      label: 'Prix standard',
      numeric: true,
      width: 140,
      render: (row) => moneyShort(Number(row.stdPrice ?? 0)),
      value: (row) => Number(row.stdPrice ?? 0),
    },
    {
      key: 'exclusions',
      label: 'Exclusion',
      width: 160,
      render: (row) => {
        const values = (row.exclusions as string[] | undefined) ?? []
        if (values.length === 0) return <span className="subtle">—</span>
        return (
          <span className="row" style={{ gap: 'var(--space-1)' }}>
            {values.map((value) => (
              <Badge key={value} tone={value === 'ALL' ? 'danger' : 'warning'}>
                {EXCLUSION_LABELS[value] ?? value}
              </Badge>
            ))}
          </span>
        )
      },
      value: (row) => ((row.exclusions as string[] | undefined) ?? []).join(','),
    },
  ]

  return (
    <div className="stack">
      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="items"
        disabled={!overview.permissions.items}
        disabledReason="Le référentiel articles est gelé depuis le passage en phase de comptage."
        onImported={() => void queryClient.invalidateQueries({ queryKey: ['items'] })}
      />

      <Card
        title="Référentiel articles de la campagne"
        message="Trois niveaux d’exclusion : hors périmètre complet, hors GENERIQUE, ou ignoré dans les nomenclatures."
        flush
      >
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              getRowId={(row) => String(row.item_number)}
              searchPlaceholder="Filtrer par référence, désignation…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} article(s) au référentiel
                  {data.total > data.rows.length && ` — ${data.rows.length} affichés`}
                </span>
              }
              toolbar={
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Icons.search size={13} />}
                  onClick={() => setSearch('')}
                  disabled={!search}
                >
                  Réinitialiser la recherche serveur
                </Button>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

const EXCLUSION_LABELS: Record<string, string> = {
  ALL: 'Hors périmètre',
  GENERIC: 'Hors GENERIQUE',
  BOM: 'Ignoré en BOM',
}

// --------------------------------------------------------------------------- //
// Bills of materials
// --------------------------------------------------------------------------- //

function BomsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const health = useQuery({
    queryKey: ['bom-health', campaignId],
    queryFn: () => api.bomHealth(campaignId),
  })
  const links = useQuery({
    queryKey: ['boms', campaignId],
    queryFn: () => api.boms(campaignId),
  })

  const columns: Column[] = [
    { key: 'parent_item', label: 'Assemblage', width: 180 },
    { key: 'child_item', label: 'Composant', width: 180 },
    {
      key: 'qtyPer',
      label: 'Qté par assemblage',
      numeric: true,
      width: 170,
      render: (row) => <span className="num">{numShort(Number(row.qtyPer ?? 0))}</span>,
      value: (row) => Number(row.qtyPer ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 90 },
  ]

  return (
    <div className="stack">
      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="boms"
        disabled={!overview.permissions.boms}
        disabledReason="Les nomenclatures sont gelées depuis le passage en phase de comptage."
        onImported={() => {
          void queryClient.invalidateQueries({ queryKey: ['boms'] })
          void queryClient.invalidateQueries({ queryKey: ['bom-health'] })
        }}
      />

      <AsyncBoundary query={health} skeleton={<Skeleton height={140} />}>
        {(data) => (
          <Card
            title="Santé des nomenclatures"
            message={
              data.cycles.length > 0
                ? `${data.cycles.length} cycle(s) détecté(s) : l’éclatement du WIP est impossible tant qu’ils subsistent.`
                : `${data.linkCount.toLocaleString('fr-FR')} liens couvrant ${data.parentCount} assemblage(s), sans cycle.`
            }
          >
            <div className="stack">
              {data.cycles.length > 0 && (
                <Alert tone="danger" title="Cycles de nomenclature">
                  <ul style={{ margin: 0, paddingLeft: '1.1rem' }} className="mono">
                    {data.cycles.slice(0, 6).map((cycle) => (
                      <li key={cycle}>{cycle}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              {data.findings.length > 0 ? (
                <div className="table-wrap" style={{ maxHeight: 260 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th style={{ width: 110 }}>Sévérité</th>
                        <th style={{ width: 170 }}>Article</th>
                        <th>Constat</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.findings.slice(0, 60).map((finding, index) => (
                        <tr key={index}>
                          <td>
                            <Badge tone={finding.severity === 'BLOCKER' ? 'danger' : 'warning'}>
                              {finding.severity === 'BLOCKER' ? 'Bloquant' : 'Avertissement'}
                            </Badge>
                          </td>
                          <td className="mono">{finding.item_number || '—'}</td>
                          <td>{finding.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Alert tone="success" title="Aucune anomalie structurelle détectée" />
              )}
            </div>
          </Card>
        )}
      </AsyncBoundary>

      <Card title="Liens de nomenclature" flush>
        <AsyncBoundary query={links} isEmpty={(rows) => rows.length === 0}>
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              getRowId={(row, index) => `${row.parent_item}-${row.child_item}-${index}`}
              searchPlaceholder="Filtrer par assemblage ou composant…"
              maxHeight={520}
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Book stock
// --------------------------------------------------------------------------- //

function BookStockTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const query = useQuery({
    queryKey: ['book-stock', campaignId],
    queryFn: () => api.bookStock(campaignId, { limit: GRID_ROW_CEILING }),
  })

  const freeze = useMutation({
    mutationFn: () => api.freezeBookStock(campaignId),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Stock livre gelé', 'Les écarts sont désormais reproductibles.')
    },
    onError: (error) => showError(error, 'Gel impossible'),
  })

  const frozen = overview.campaign.book_stock_frozen_at !== null
  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'warehouse_id', label: 'Entrepôt', width: 120 },
    { key: 'location_id', label: 'Emplacement', width: 150 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 130,
      render: (row) => <span className="num">{numShort(Number(row.qty ?? 0))}</span>,
      value: (row) => Number(row.qty ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 80 },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{moneyShort(Number(row.value ?? 0))}</span>,
      value: (row) => Number(row.value ?? 0),
    },
  ]

  return (
    <div className="stack">
      {frozen ? (
        <Alert tone="success" title="Stock livre gelé">
          Le snapshot est figé : tout écart calculé aujourd’hui sera recalculable à
          l’identique dans six mois.
        </Alert>
      ) : (
        <Alert
          tone="warning"
          title="Stock livre non gelé"
          actions={
            <Button
              variant="primary"
              size="sm"
              icon={<Icons.lock size={13} />}
              disabled={query.data?.total === 0 || freeze.isPending}
              onClick={() => freeze.mutate()}
            >
              Geler le stock livre
            </Button>
          }
        >
          Chargez l’export ERP puis gelez-le. Le chargement crée aussi le référentiel
          entrepôts/emplacements et un journal de comptage par emplacement actif.
        </Alert>
      )}

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="book_stock"
        disabled={!overview.permissions.bookStock || frozen}
        disabledReason={
          frozen
            ? undefined
            : 'Le chargement du stock livre se fait pendant la phase de comptage.'
        }
        onImported={() => void queryClient.invalidateQueries()}
      />

      <Card title="Stock livre (snapshot ERP)" flush>
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              getRowId={(row, index) =>
                `${row.item_number}-${row.warehouse_id}-${row.location_id}-${index}`
              }
              searchPlaceholder="Filtrer par article, entrepôt, emplacement…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} ligne(s)
                  {data.total > data.rows.length && ` — ${data.rows.length} affichées`}
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Prepared counting sheets
// --------------------------------------------------------------------------- //

function CountSheetsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  return (
    <div className="stack">
      {overview.permissions.countSheets && (
      <Alert tone="info" title="Décider quoi compter, avant le jour J">
        Une ligne par couple <strong>feuille / article</strong>. Une feuille inconnue
        est créée avec ses passages ; une feuille connue est complétée, jamais
        recréée. Les lignes sont posées sur <strong>les deux comptages</strong>,
        quantités vides : ne pré-remplir que le n°1 rendrait le n°2 aveugle.
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
          Un article absent du référentiel produit une erreur de ligne : il n’est
          <strong> jamais</strong> créé à la volée. Chargez-le d’abord dans l’onglet
          Articles.
        </Alert>
      )}

      <ZonesAdminGrid
        campaignId={campaignId}
        editable={overview.permissions.zones}
        managers={managers.data?.managers ?? []}
      />
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Thresholds
// --------------------------------------------------------------------------- //

function ThresholdsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Threshold[] | null>(null)

  const query = useQuery({
    queryKey: ['thresholds', campaignId],
    queryFn: () => api.thresholds(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Threshold[]) =>
      api.saveThresholds(
        campaignId,
        rows.map((row) => ({
          itemType: row.item_type,
          valueAbsEur: Number(row.value_abs_eur),
          qtyRelative: row.qty_relative === null ? null : Number(row.qty_relative),
          qtyAbsFloor: Number(row.qty_abs_floor),
          iraTolerance: Number(row.ira_tolerance),
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['thresholds', campaignId] })
      setDraft(null)
      toast.success('Seuils enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: keyof Threshold, value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value === '' ? null : value }
    setDraft(next)
  }

  return (
    <Card
      title="Seuils de matérialité"
      message="Un écart est « matériel » lorsqu’il franchit toutes les barrières configurées de son type. Exiger la conjonction — et non l’une ou l’autre — garde la liste d’exceptions à une taille exploitable le jour J."
      actions={
        editable && draft ? (
          <>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Annuler
            </Button>
            <Button
              variant="primary"
              disabled={save.isPending}
              onClick={() => save.mutate(rows)}
            >
              Enregistrer
            </Button>
          </>
        ) : null
      }
      flush
    >
      {!editable && (
        <div style={{ padding: 'var(--space-4)' }}>
          <Alert tone="info" title="Seuils gelés">
            Les seuils sont figés depuis le passage en phase de comptage, pour que les
            exceptions signalées pendant le comptage soient les mêmes qu’à l’analyse.
          </Alert>
        </div>
      )}

      <AsyncBoundary query={query} isEmpty={() => false}>
        {() => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 160 }}>Type d’article</th>
                  <th className="num" title="Écart en valeur absolue au-delà duquel la ligne est une exception">
                    Valeur absolue (€)
                  </th>
                  <th className="num" title="|Δqté| / qté livre au-delà duquel la ligne est une exception">
                    Écart relatif
                  </th>
                  <th className="num" title="En deçà de cette quantité, jamais d’exception">
                    Plancher quantité
                  </th>
                  <th className="num" title="Tolérance utilisée par l’indicateur IRA">
                    Tolérance IRA
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.item_type}>
                    <td>
                      <strong>{ITEM_TYPE_LABELS[row.item_type] ?? row.item_type}</strong>
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={String(row.value_abs_eur ?? '')}
                          onChange={(e) => update(index, 'value_abs_eur', e.target.value)}
                        />
                      ) : (
                        moneyShort(Number(row.value_abs_eur))
                      )}
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={row.qty_relative === null ? '' : String(row.qty_relative)}
                          placeholder="désactivé"
                          onChange={(e) => update(index, 'qty_relative', e.target.value)}
                        />
                      ) : row.qty_relative === null ? (
                        <span className="subtle">désactivé</span>
                      ) : (
                        percent(Number(row.qty_relative), 2)
                      )}
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={String(row.qty_abs_floor ?? '')}
                          onChange={(e) => update(index, 'qty_abs_floor', e.target.value)}
                        />
                      ) : (
                        numShort(Number(row.qty_abs_floor))
                      )}
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={String(row.ira_tolerance ?? '')}
                          onChange={(e) => update(index, 'ira_tolerance', e.target.value)}
                        />
                      ) : (
                        percent(Number(row.ira_tolerance), 2)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncBoundary>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Managers and perimeters
// --------------------------------------------------------------------------- //

/**
 * The five manager slots.
 *
 * The identity column is the load-bearing one: it is what lets the server
 * answer "who is asking?" when a screen requests `focus=true`, so the browser
 * never has to name a manager — and never receives what the filter excluded.
 */
function ManagersTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Manager[] | null>(null)

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Manager[]) =>
      api.saveManagers(
        campaignId,
        rows.map((row, index) => ({
          code: row.code,
          label: row.label,
          actor: row.actor,
          active: row.active,
          displayOrder: index,
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      setDraft(null)
      toast.success('Gestionnaires enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data?.managers ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: 'label' | 'actor', value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value }
    setDraft(next)
  }

  return (
    <div className="stack">
      <Alert tone="info" title="Un périmètre, pas une habilitation">
        Affecter un entrepôt ou une zone à un gestionnaire ne restreint aucune
        action : c’est un filtre d’affichage, activé par l’interrupteur
        « Mon périmètre » de la barre supérieure. Chacun garde le droit d’agir
        partout — indispensable quand il faut couvrir un collègue à 6 h du matin.
      </Alert>

      <Card
        title="Gestionnaires de la campagne"
        message="L’identité est celle transmise par l’authentification (votre adresse e-mail). C’est elle qui résout « Mon périmètre » côté serveur."
        actions={
          editable && draft ? (
            <>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Annuler
              </Button>
              <Button
                variant="primary"
                disabled={save.isPending}
                onClick={() => save.mutate(rows)}
              >
                Enregistrer
              </Button>
            </>
          ) : null
        }
        flush
      >
        {!editable && (
          <div style={{ padding: 'var(--space-4)' }}>
            <Alert tone="info" title="Référentiel gelé">
              Les gestionnaires sont figés depuis le passage en phase de comptage,
              comme le reste de la configuration de campagne.
            </Alert>
          </div>
        )}
        <AsyncBoundary query={query} skeleton={<Skeleton height={220} />}>
          {() => (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 160 }}>Poste</th>
                    <th>Libellé</th>
                    <th>Identité (e-mail)</th>
                    <th className="num" style={{ width: 110 }}>Entrepôts</th>
                    <th className="num" style={{ width: 100 }}>Journaux</th>
                    <th className="num" style={{ width: 90 }}>Zones</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.code}>
                      <td className="mono subtle">{row.code}</td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.label}
                            placeholder="Nom du poste"
                            onChange={(e) => update(index, 'label', e.target.value)}
                          />
                        ) : (
                          row.label || <span className="subtle">—</span>
                        )}
                      </td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.actor}
                            inputMode="email"
                            placeholder="prenom.nom@exemple.fr"
                            onChange={(e) => update(index, 'actor', e.target.value)}
                          />
                        ) : row.actor ? (
                          <span className="mono">{row.actor}</span>
                        ) : (
                          <span className="subtle">poste inoccupé</span>
                        )}
                      </td>
                      <td className="num">
                        {
                          (query.data?.warehouses ?? []).filter(
                            (w) => w.managerCode === row.code,
                          ).length
                        }
                      </td>
                      <td className="num">{row.journalCount}</td>
                      <td className="num">{row.zoneCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

/** Warehouses — and therefore their counting journals — assigned to a manager. */
function JournalScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const assign = useMutation({
    mutationFn: (input: { warehouseId: string; managerCode: string }) =>
      api.assignWarehouses(campaignId, [input]),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Affectation enregistrée')
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const editable = overview.permissions.thresholds
  const managers = query.data?.managers ?? []

  return (
    <div className="stack">
      <Alert tone="info" title="Un journal suit son entrepôt">
        Un journal de comptage est dans le périmètre d’un gestionnaire lorsque son
        entrepôt lui est affecté. La ligne <strong>AUTRES</strong> n’est pas un
        entrepôt : elle rattache d’un coup tous ceux qui n’ont pas d’affectation
        explicite — sinon un entrepôt découvert par un nouvel import de stock livre
        tomberait hors de tout périmètre sans que personne ne le voie.
      </Alert>

      <Card title="Affectation des entrepôts" flush>
        <AsyncBoundary query={query} skeleton={<Skeleton height={240} />}>
          {(data) => (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 200 }}>Entrepôt</th>
                    <th className="num" style={{ width: 130 }}>Journaux</th>
                    <th style={{ width: 260 }}>Gestionnaire</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.warehouses.map((warehouse) => (
                    <tr key={warehouse.warehouseId}>
                      <td>
                        <span className="mono">{warehouse.warehouseId}</span>
                        {warehouse.isCatchAll && (
                          <>
                            {' '}
                            <Badge tone="info">fourre-tout</Badge>
                          </>
                        )}
                      </td>
                      <td className="num">
                        {warehouse.isCatchAll ? (
                          <span className="subtle">—</span>
                        ) : (
                          warehouse.journalCount
                        )}
                      </td>
                      <td className="editable">
                        <select
                          className="input"
                          disabled={!editable || assign.isPending}
                          value={warehouse.managerCode}
                          onChange={(event) =>
                            assign.mutate({
                              warehouseId: warehouse.warehouseId,
                              managerCode: event.target.value,
                            })
                          }
                        >
                          <option value="">— aucun —</option>
                          {managers.map((manager) => (
                            <option key={manager.code} value={manager.code}>
                              {manager.label || manager.code}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="subtle">
                        {!warehouse.known && !warehouse.isCatchAll
                          ? 'aucun journal pour l’instant'
                          : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

/** GENERIQUE zones assigned to a manager, in bulk over a selection. */
function ZoneScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  return (
    <div className="stack">
      <Alert tone="info" title="Rattacher les feuilles à leur gestionnaire">
        Sélectionnez des zones, puis choisissez un gestionnaire dans la barre
        d’outils. Comme partout, l’affectation ne restreint rien : elle nourrit le
        filtre « Mon périmètre ».
      </Alert>

      <ZonesAdminGrid
        campaignId={campaignId}
        editable={overview.permissions.zones}
        managers={managers.data?.managers ?? []}
      />
    </div>
  )
}
