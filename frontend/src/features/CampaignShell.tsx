/**
 * The campaign workspace: header, phase stepper, navigation and phase gate.
 *
 * The header is on every screen, so its data comes from a single `/overview`
 * call. The navigation disables what the current phase has frozen, using the
 * exact same permission payload the backend enforces — UI and API can never
 * disagree about what is editable.
 */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useParams } from 'react-router-dom'
import { api, downloads } from '../lib/api'
import type { CampaignStatus, Overview, Permissions } from '../lib/types'
import {
  CAMPAIGN_STATUS_LABELS,
  date as fmtDate,
  label as toLabel,
  percent,
} from '../lib/format'
import {
  Alert, Badge, Button, ErrorState, Icons, Modal, Progress, Skeleton, Stepper, useDownload, useErrorToast, useToast,
} from '../components/ui'

const PHASES: Array<{ id: CampaignStatus; label: string }> = [
  { id: 'PREPARATION', label: 'Préparation' },
  { id: 'COUNTING', label: 'Comptage' },
  { id: 'ANALYSIS', label: 'Analyse & ajustements' },
  { id: 'CLOSED', label: 'Clôture' },
]

const NEXT_PHASE: Partial<Record<CampaignStatus, CampaignStatus>> = {
  PREPARATION: 'COUNTING',
  COUNTING: 'ANALYSIS',
  ANALYSIS: 'CLOSED',
}

/** Which phase each section belongs to, and what unlocks it. */
const SECTIONS: Array<{
  to: string
  label: string
  icon: keyof typeof Icons
  enabled: (permissions: Permissions, overview: Overview) => boolean
  badge?: (overview: Overview) => number | null
}> = [
  { to: '', label: 'Tableau de bord', icon: 'dashboard', enabled: () => true },
  {
    to: 'preparation',
    label: 'Référentiels & seuils',
    icon: 'layers',
    enabled: () => true,
  },
  {
    to: 'comptage',
    label: 'Journaux de comptage',
    icon: 'clipboard',
    enabled: (_p, o) => o.campaign.status !== 'PREPARATION',
    badge: (o) => o.journalProgress.total - o.journalProgress.complete || null,
  },
  {
    to: 'generique',
    label: 'GENERIQUE',
    icon: 'grid',
    enabled: () => true,
    badge: (o) => o.genericProgress.pendingArbitrations || null,
  },
  {
    to: 'analyse',
    label: 'Écarts & analyses',
    icon: 'chart',
    enabled: (_p, o) => o.campaign.book_stock_frozen_at !== null,
  },
  { to: 'audit', label: 'Journal d’audit', icon: 'history', enabled: () => true },
]

export function CampaignShell() {
  const { campaignId = '' } = useParams()
  const query = useQuery({
    queryKey: ['overview', campaignId],
    queryFn: () => api.overview(campaignId),
    // The counting screen changes minute by minute on inventory day.
    refetchInterval: 60_000,
  })

  if (query.isPending) {
    return (
      <div className="stack">
        <Skeleton height={80} />
        <Skeleton height={320} />
      </div>
    )
  }
  if (query.isError || !query.data) {
    return <ErrorState error={query.error} onRetry={query.refetch} />
  }

  const overview = query.data
  return (
    <div className="stack" style={{ gap: 'var(--space-5)' }}>
      <CampaignHeader overview={overview} />
      <nav className="tabs" aria-label="Sections de la campagne">
        {SECTIONS.map((section) => {
          const Icon = Icons[section.icon]
          const enabled = section.enabled(overview.permissions, overview)
          const badge = section.badge?.(overview)
          return (
            <NavLink
              key={section.to}
              to={section.to}
              end={section.to === ''}
              className={({ isActive }) =>
                `tab${isActive ? ' tab--active' : ''}${enabled ? '' : ' navlink--disabled'}`
              }
              aria-disabled={!enabled}
            >
              <span className="row" style={{ gap: 'var(--space-2)' }}>
                <Icon size={15} />
                {section.label}
                {badge ? <span className="tab__count num">{badge}</span> : null}
              </span>
            </NavLink>
          )
        })}
      </nav>
      <Outlet context={overview} />
    </div>
  )
}

function CampaignHeader({ overview }: { overview: Overview }) {
  const startDownload = useDownload()
  const { campaign, journalProgress, genericProgress, counts } = overview
  const [transitionTarget, setTransitionTarget] = useState<CampaignStatus | null>(null)
  const next = NEXT_PHASE[campaign.status]

  return (
    <header className="stack" style={{ gap: 'var(--space-4)' }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          <div className="row-wrap">
            <h1 className="page-head__title" style={{ marginBottom: 0 }}>
              {campaign.code}
            </h1>
            <Badge tone={campaign.status} dot>
              {toLabel(CAMPAIGN_STATUS_LABELS, campaign.status)}
            </Badge>
            {campaign.cloned_from_code && (
              <Badge tone="neutral">dupliquée de {campaign.cloned_from_code}</Badge>
            )}
          </div>
          <p className="page-head__lede">
            {campaign.label} — comptage du <strong>{fmtDate(campaign.count_date)}</strong>
            {campaign.book_stock_frozen_at && (
              <>
                {' '}
                · stock livre gelé le {fmtDate(campaign.book_stock_frozen_at)}
              </>
            )}
          </p>
        </div>
        <div className="row-wrap">
          <Button
            icon={<Icons.download size={14} />}
            onClick={() => startDownload(downloads.campaignWorkbook(campaign.id))}
          >
            Exporter le dossier
          </Button>
          {next && (
            <Button
              variant="primary"
              icon={<Icons.chevronRight size={14} />}
              onClick={() => setTransitionTarget(next)}
            >
              Passer à « {toLabel(CAMPAIGN_STATUS_LABELS, next)} »
            </Button>
          )}
        </div>
      </div>

      <Stepper steps={PHASES} current={campaign.status} />

      <div className="grid grid--3">
        <div className="card">
          <div className="card__body stack" style={{ gap: 'var(--space-3)' }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong style={{ fontSize: 'var(--text-sm)' }}>Avancement général</strong>
              <span className="num" style={{ fontWeight: 600 }}>
                {percent(journalProgress.ratio)}
              </span>
            </div>
            <Progress
              total={journalProgress.total}
              segments={[
                {
                  label: 'Terminés',
                  value: journalProgress.complete,
                  color: 'var(--success)',
                },
                {
                  label: 'En cours',
                  value: journalProgress.running,
                  color: 'var(--accent)',
                },
                {
                  label: 'En attente',
                  value: journalProgress.pending,
                  color: 'var(--bg-active)',
                },
              ]}
            />
            <p className="subtle">
              {journalProgress.complete} / {journalProgress.total} journaux de comptage
              postés ou forcés au stock livre.
            </p>
          </div>
        </div>

        <div className="card">
          <div className="card__body stack" style={{ gap: 'var(--space-3)' }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <strong style={{ fontSize: 'var(--text-sm)' }}>Avancement GENERIQUE</strong>
              <span className="num" style={{ fontWeight: 600 }}>
                {percent(genericProgress.ratio)}
              </span>
            </div>
            <Progress
              total={genericProgress.zones}
              segments={[
                { label: 'Zones terminées', value: genericProgress.done, color: 'var(--success)' },
                {
                  label: 'En cours',
                  value: genericProgress.zones - genericProgress.done,
                  color: 'var(--bg-active)',
                },
              ]}
            />
            <p className="subtle">
              {genericProgress.done} / {genericProgress.zones} zones
              {genericProgress.pendingArbitrations > 0 && (
                <>
                  {' '}
                  · <strong className="neg">
                    {genericProgress.pendingArbitrations} arbitrage(s) en attente
                  </strong>
                </>
              )}
            </p>
          </div>
        </div>

        <div className="card">
          <div className="card__body stack" style={{ gap: 'var(--space-3)' }}>
            <strong style={{ fontSize: 'var(--text-sm)' }}>Dossier de campagne</strong>
            <dl className="kv">
              <dt>Articles</dt>
              <dd className="num">{counts.items.toLocaleString('fr-FR')}</dd>
              <dt>Lignes de stock livre</dt>
              <dd className="num">{counts.bookStockLines.toLocaleString('fr-FR')}</dd>
              <dt>Moteur de calcul</dt>
              <dd className="mono">v{campaign.engine_version}</dd>
            </dl>
          </div>
        </div>
      </div>

      {transitionTarget && (
        <TransitionModal
          campaignId={campaign.id}
          current={campaign.status}
          target={transitionTarget}
          onClose={() => setTransitionTarget(null)}
        />
      )}
    </header>
  )
}

function TransitionModal({
  campaignId,
  current,
  target,
  onClose,
}: {
  campaignId: string
  current: CampaignStatus
  target: CampaignStatus
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const readiness = useQuery({
    queryKey: ['readiness', campaignId, target],
    queryFn: () => api.transitionReadiness(campaignId, target),
  })

  const mutation = useMutation({
    mutationFn: () => api.transition(campaignId, target),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success(`Campagne passée en « ${toLabel(CAMPAIGN_STATUS_LABELS, target)} »`)
      onClose()
    },
    onError: (error) => showError(error, 'Changement de statut impossible'),
  })

  const freezes = useMemo(() => FREEZE_NOTES[target] ?? [], [target])
  const blockers = readiness.data?.blockers ?? []
  const ready = readiness.data?.ready ?? false

  return (
    <Modal
      title={`${toLabel(CAMPAIGN_STATUS_LABELS, current)} → ${toLabel(CAMPAIGN_STATUS_LABELS, target)}`}
      onClose={onClose}
      width={680}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant={target === 'CLOSED' ? 'danger' : 'primary'}
            disabled={!ready || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'En cours…' : 'Confirmer le changement'}
          </Button>
        </>
      }
    >
      <div className="stack">
        {readiness.isPending && <Skeleton count={3} />}

        {blockers.length > 0 && (
          <Alert tone="danger" title={`${blockers.length} point(s) bloquant(s)`}>
            <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {blockers.map((blocker, index) => (
                <li key={index}>{blocker.message}</li>
              ))}
            </ul>
          </Alert>
        )}

        {ready && blockers.length === 0 && (
          <Alert tone="success" title="Tous les prérequis sont remplis" />
        )}

        {freezes.length > 0 && (
          <div className="card">
            <div className="card__body">
              <strong style={{ fontSize: 'var(--text-sm)' }}>
                Ce qui sera gelé définitivement
              </strong>
              <ul style={{ margin: 'var(--space-2) 0 0', paddingLeft: '1.1rem' }} className="muted">
                {freezes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {target === 'CLOSED' && (
          <Alert tone="warning" title="Cette action est irréversible">
            Une campagne clôturée ne peut pas être rouverte : c’est ce qui garantit que
            les chiffres publiés restent ceux qui ont été calculés. Pour poursuivre des
            travaux, dupliquez la campagne.
          </Alert>
        )}
      </div>
    </Modal>
  )
}

const FREEZE_NOTES: Partial<Record<CampaignStatus, string[]>> = {
  COUNTING: [
    'Le référentiel articles et les nomenclatures ne seront plus modifiables.',
    'Les seuils de matérialité seront figés.',
    'Les zones GENERIQUE et leurs feuilles restent créables pendant le comptage.',
  ],
  ANALYSIS: [
    'Les journaux de comptage et leurs lignes seront figés.',
    'Les feuilles GENERIQUE, les arbitrages et la consolidation seront figés.',
    'Le référentiel emplacements sera figé.',
    'Seuls les ajustements et l’analyse des écarts resteront modifiables.',
  ],
  CLOSED: [
    'Tout est figé : ajustements, causes, commentaires.',
    'Les exports et le journal d’audit restent consultables.',
  ],
}
