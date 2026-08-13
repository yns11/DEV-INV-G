/**
 * Audit trail and import history — the "who changed what, when" screen.
 *
 * The specification asks for an audit event on every action and status change.
 * The table is append-only in the database (UPDATE and DELETE are no-ops), so
 * what this screen shows is, by construction, what happened.
 */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useOutletContext } from 'react-router-dom'
import { api } from '../lib/api'
import type { Overview } from '../lib/types'
import { AUDIT_ACTION_LABELS, dateTime, label as toLabel, relativeTime } from '../lib/format'
import { AsyncBoundary, Badge, Card, EmptyState, Skeleton, ViewTabs } from '../components/ui'
import { DataGrid, type Column } from '../components/DataGrid'

type Tab = 'events' | 'imports'

const ACTION_TONE: Record<string, string> = {
  CREATE: 'success',
  UPDATE: 'accent',
  DELETE: 'danger',
  STATUS_CHANGE: 'info',
  IMPORT: 'accent',
  EXPORT: 'neutral',
  FREEZE: 'warning',
  CONSOLIDATE: 'info',
  ARBITRATE: 'warning',
}

export function Audit() {
  const overview = useOutletContext<Overview>()
  const campaignId = overview.campaign.id
  const [tab, setTab] = useState<Tab>('events')

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <ViewTabs<Tab>
        value={tab}
        onChange={setTab}
        tabs={[
          { id: 'events', label: 'Journal d’audit' },
          { id: 'imports', label: 'Historique des imports' },
        ]}
      />
      {tab === 'events' ? <Events campaignId={campaignId} /> : <Imports campaignId={campaignId} />}
    </div>
  )
}

function Events({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['audit', campaignId],
    queryFn: () => api.audit(campaignId, { limit: 500 }),
  })

  // Une grille et non un tableau écrit à la main : c'est ce qui donne au journal
  // la recherche, le tri et l'export que l'historique des imports avait déjà.
  // C'est aussi celui des deux qu'on transmet — « qui a validé cet écart, et
  // quand » est une question qui se pose en réunion, pas devant l'écran.
  const columns: Column[] = [
    {
      key: 'at',
      label: 'Horodatage',
      width: 190,
      render: (row) => (
        <div>
          <div className="num">{dateTime(String(row.at))}</div>
          <div className="subtle">{relativeTime(String(row.at))}</div>
        </div>
      ),
      value: (row) => String(row.at),
    },
    { key: 'actor', label: 'Auteur', width: 210 },
    {
      key: 'action',
      label: 'Action',
      width: 165,
      render: (row) => (
        <Badge tone={ACTION_TONE[String(row.action)] ?? 'neutral'}>
          {toLabel(AUDIT_ACTION_LABELS, String(row.action))}
        </Badge>
      ),
      value: (row) => String(row.action),
    },
    { key: 'entity_type', label: 'Entité', width: 160 },
    { key: 'summary', label: 'Résumé', width: 420 },
  ]

  return (
    <Card
      title="Journal d’audit"
      message="Chaque action et chaque changement de statut produit un évènement immuable, attribué à son auteur."
      flush
    >
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton count={8} height={20} />}
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState title="Aucun évènement" />}
      >
        {(rows) => (
          <DataGrid
            columns={columns}
            rows={rows as unknown as Array<Record<string, unknown>>}
            exportTitle="Journal d’audit"
            campaignId={campaignId}
            getRowId={(row, index) => String(row.id ?? index)}
            searchPlaceholder="Filtrer par auteur, action, résumé…"
            maxHeight={660}
            initialSort={{ key: 'at', direction: 'desc' }}
          />
        )}
      </AsyncBoundary>
    </Card>
  )
}

function Imports({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['import-history', campaignId],
    queryFn: () => api.importHistory(campaignId),
  })

  const columns: Column[] = [
    {
      key: 'imported_at',
      label: 'Date',
      width: 180,
      render: (row) => <span className="num">{dateTime(String(row.imported_at))}</span>,
      value: (row) => String(row.imported_at),
    },
    { key: 'target', label: 'Cible', width: 200 },
    { key: 'filename', label: 'Fichier', width: 260 },
    {
      key: 'rows_received',
      label: 'Reçues',
      numeric: true,
      width: 110,
      value: (row) => Number(row.rows_received),
    },
    {
      key: 'rows_accepted',
      label: 'Acceptées',
      numeric: true,
      width: 120,
      render: (row) => (
        <span className="num pos">{Number(row.rows_accepted).toLocaleString('fr-FR')}</span>
      ),
      value: (row) => Number(row.rows_accepted),
    },
    {
      key: 'rows_rejected',
      label: 'Rejetées',
      numeric: true,
      width: 110,
      render: (row) => (
        <span className={`num ${Number(row.rows_rejected) ? 'neg' : 'subtle'}`}>
          {Number(row.rows_rejected).toLocaleString('fr-FR')}
        </span>
      ),
      value: (row) => Number(row.rows_rejected),
    },
    { key: 'imported_by', label: 'Par', width: 200 },
  ]

  return (
    <Card
      title="Historique des imports"
      message="Provenance de chaque chargement en masse : fichier, empreinte, volumes acceptés et rejetés."
      flush
    >
      <AsyncBoundary
        query={query}
        isEmpty={(rows) => rows.length === 0}
        empty={<EmptyState title="Aucun import enregistré" />}
      >
        {(rows) => (
          <DataGrid
            columns={columns}
            rows={rows}
            exportTitle="Historique des imports"
            campaignId={campaignId}
            getRowId={(row, index) => String(row.id ?? index)}
            searchPlaceholder="Filtrer les imports…"
            maxHeight={620}
            initialSort={{ key: 'imported_at', direction: 'desc' }}
          />
        )}
      </AsyncBoundary>
    </Card>
  )
}
