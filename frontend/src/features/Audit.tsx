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
import { AsyncBoundary, Badge, Card, EmptyState, Skeleton, Tabs } from '../components/ui'
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
      <Tabs<Tab>
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
          <div className="table-wrap" style={{ maxHeight: 680 }}>
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 175 }}>Horodatage</th>
                  <th style={{ width: 210 }}>Auteur</th>
                  <th style={{ width: 165 }}>Action</th>
                  <th style={{ width: 160 }}>Entité</th>
                  <th>Résumé</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.id}>
                    <td>
                      <div className="num">{dateTime(event.at)}</div>
                      <div className="subtle">{relativeTime(event.at)}</div>
                    </td>
                    <td className="truncate">{event.actor}</td>
                    <td>
                      <Badge tone={ACTION_TONE[event.action] ?? 'neutral'}>
                        {toLabel(AUDIT_ACTION_LABELS, event.action)}
                      </Badge>
                    </td>
                    <td className="mono subtle">{event.entity_type}</td>
                    <td>{event.summary}</td>
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
            exportTitle="Journal d’audit"
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
