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
import { api, download, downloads } from '../lib/api'
import type { Overview } from '../lib/types'
import {
  AUDIT_ACTION_LABELS,
  DASH,
  dateTime,
  label as toLabel,
  relativeTime,
} from '../lib/format'
import {
  AsyncBoundary,
  Badge,
  Card,
  EmptyState,
  Icons,
  Skeleton,
  ViewTabs,
} from '../components/ui'
import { DataGrid, type Column } from '../components/DataGrid'

type Tab = 'events' | 'imports' | 'scans'

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
          { id: 'scans', label: 'Scans archivés' },
        ]}
      />
      {tab === 'events' && <Events campaignId={campaignId} />}
      {tab === 'imports' && <Imports campaignId={campaignId} />}
      {tab === 'scans' && <Scans campaignId={campaignId} />}
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
    {
      key: 'filename',
      label: 'Fichier',
      width: 260,
      // Le nom devient le lien quand l'original a été conservé. Un chargement
      // par collage ou une lecture ERP n'a pas de fichier d'origine : son nom
      // reste du texte, ce qui dit la différence sans avoir à l'écrire.
      render: (row) =>
        row.archived ? (
          <button
            className="drill filelink"
            title="Télécharger le fichier tel qu'il a été reçu"
            onClick={() =>
              void download(downloads.importEvidence(campaignId, String(row.id)))
            }
          >
            <Icons.download size={12} />
            {String(row.filename || 'pièce jointe')}
          </button>
        ) : (
          <span>{String(row.filename || DASH)}</span>
        ),
      value: (row) => String(row.filename ?? ''),
    },
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

/** Ce que pèse une pièce, dit comme on le dit à l'oral. */
function weight(bytes: number): string {
  if (!bytes) return DASH
  if (bytes < 1024) return `${bytes} o`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} ko`
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} Mo`
}

/**
 * Les scans archivés — l'onglet qui rend l'archive consultable.
 *
 * Chaque scan déposé est conservé *avant* d'être lu : c'est la pièce qui
 * justifie les quantités, et sans elle une valeur contestée six mois plus tard
 * n'a plus rien derrière elle, la feuille manuscrite étant repartie à l'atelier.
 *
 * Le fichier se téléchargeait déjà, mais seulement pour qui connaissait
 * l'identifiant de la feuille qui le porte : rien ne disait ce que l'archive
 * contenait, ni même si elle contenait quelque chose. Une archive qu'on ne peut
 * pas énumérer ne se contrôle pas, et c'est pourtant tout son objet.
 *
 * **Une ligne par document, pas par feuille.** Une pile déposée d'un coup est un
 * seul document, et les dix feuilles qu'on y a lues pointent dessus ; les lister
 * une par une rendrait dix fois le même PDF sous dix noms.
 */
function Scans({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['archived-scans', campaignId],
    queryFn: () => api.archivedScans(campaignId),
  })

  const columns: Column[] = [
    {
      key: 'archivedAt',
      label: 'Déposé le',
      width: 180,
      render: (row) => <span className="num">{dateTime(String(row.archivedAt))}</span>,
      value: (row) => String(row.archivedAt),
    },
    {
      key: 'filename',
      label: 'Fichier',
      width: 300,
      // Le nom devient le lien, comme dans l'historique des imports : le même
      // geste pour la même chose, et un seul chemin de téléchargement — celui
      // qui passe par la feuille, donc par la barrière de campagne.
      render: (row) =>
        row.sheetId ? (
          <button
            className="drill filelink"
            title="Télécharger le scan tel qu'il a été déposé"
            onClick={() =>
              void download(downloads.sheetEvidence(campaignId, String(row.sheetId)))
            }
          >
            <Icons.download size={12} />
            {String(row.filename || 'scan')}
          </button>
        ) : (
          // La pièce reste, ses feuilles non : la zone a été supprimée. On le
          // montre plutôt que de faire disparaître une pièce de l'archive.
          <span title="Les feuilles qu'il justifiait ont été supprimées">
            {String(row.filename || DASH)}
          </span>
        ),
      value: (row) => String(row.filename ?? ''),
    },
    {
      key: 'sheets',
      label: 'Feuilles justifiées',
      width: 320,
      render: (row) => {
        const sheets = (row.sheets as string[] | undefined) ?? []
        if (sheets.length === 0) return <span className="subtle">{DASH}</span>
        return (
          <span title={sheets.join(' · ')}>
            {sheets.length > 3
              ? `${sheets.slice(0, 3).join(' · ')} +${sheets.length - 3}`
              : sheets.join(' · ')}
          </span>
        )
      },
      value: (row) => ((row.sheets as string[] | undefined) ?? []).join(' '),
    },
    {
      key: 'sheetCount',
      label: 'Feuilles',
      numeric: true,
      width: 110,
      value: (row) => Number(row.sheetCount),
    },
    {
      key: 'sizeBytes',
      label: 'Poids',
      numeric: true,
      width: 110,
      render: (row) => <span className="num">{weight(Number(row.sizeBytes))}</span>,
      value: (row) => Number(row.sizeBytes),
    },
    {
      key: 'sha256',
      label: 'Empreinte',
      width: 180,
      // Le chemin dit *où*, l'empreinte dit *lequel*. Les douze premiers
      // caractères suffisent à l'œil ; l'infobulle porte les soixante-quatre,
      // qui sont ce qu'on recopie dans un rapport.
      render: (row) =>
        row.sha256 ? (
          <span className="num subtle" title={String(row.sha256)}>
            {String(row.sha256).slice(0, 12)}…
          </span>
        ) : (
          <span className="subtle">{DASH}</span>
        ),
      value: (row) => String(row.sha256 ?? ''),
    },
  ]

  return (
    <Card
      title="Scans archivés"
      message="Chaque scan est conservé avant d’être lu : c’est la pièce qui justifie les quantités lues par le modèle. Une pile déposée d’un coup est un seul document, et justifie toutes les feuilles qu’on y a lues."
      flush
    >
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton count={6} height={20} />}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="Aucun scan archivé">
            Les quantités de cette campagne ont été saisies à la main, ou ses
            feuilles ont été lues avant la mise en service de l’archive.
          </EmptyState>
        }
      >
        {(rows) => (
          <DataGrid
            columns={columns}
            rows={rows}
            exportTitle="Scans archivés"
            campaignId={campaignId}
            getRowId={(row, index) => String(row.sha256 ?? index)}
            searchPlaceholder="Filtrer par fichier, zone, empreinte…"
            maxHeight={620}
            initialSort={{ key: 'archivedAt', direction: 'desc' }}
            footer={
              <span>
                {rows.length} pièce(s) ·{' '}
                {weight(
                  rows.reduce((n, r) => n + Number(r.sizeBytes ?? 0), 0),
                )}{' '}
                au total
              </span>
            }
          />
        )}
      </AsyncBoundary>
    </Card>
  )
}
