/**
 * The campaign workspace: header, phase stepper, navigation and phase gate.
 *
 * The header is on every screen, so its data comes from a single `/overview`
 * call. The navigation disables what the current phase has frozen, using the
 * exact same permission payload the backend enforces — UI and API can never
 * disagree about what is editable.
 */

import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useParams } from 'react-router-dom'
import { api, downloads } from '../lib/api'
import type { CampaignStatus, Overview, Permissions } from '../lib/types'
import {
  CAMPAIGN_STATUS_LABELS,
  date as fmtDate,
  moneyShort,
  numShort,
  percent,
  signClass,
  signedMoney,
  signedNum,
  label as toLabel,
} from '../lib/format'
import { useFocusMode } from '../lib/focus'
import {
  Alert, AsyncBoundary, Badge, Button, Carousel, ErrorState, Icons, Kpi, Modal, Progress, Skeleton, Stepper, Switch, useDownload, useErrorToast, useToast,
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
  badge?: (overview: Overview, focus: boolean) => number | null
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
    // Under focus the badge counts the perimeter, not the campaign: a "6" over
    // a list of four is the kind of small lie that makes people stop trusting
    // the numbers next to it.
    badge: (o, focus) =>
      focus
        ? o.perimeter.journalCount || null
        : o.journalProgress.total - o.journalProgress.complete || null,
  },
  {
    to: 'generique',
    label: 'GENERIQUE',
    icon: 'grid',
    enabled: () => true,
    badge: (o, focus) =>
      focus
        ? o.perimeter.zoneCount || null
        : o.genericProgress.pendingArbitrations || null,
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
  const [focus] = useFocusMode()
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
          const badge = section.badge?.(overview, focus)
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
  const { campaign, perimeter } = overview
  const [transitionTarget, setTransitionTarget] = useState<CampaignStatus | null>(null)
  const [focus] = useFocusMode()
  const next = NEXT_PHASE[campaign.status]
  const empty = perimeter.journalCount === 0 && perimeter.zoneCount === 0

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
          <FocusSwitch overview={overview} />
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

      <KpiCarousel overview={overview} />

      {focus && (
        <Alert
          tone={perimeter.resolved && !empty ? 'info' : 'warning'}
          title={
            !perimeter.resolved
              ? 'Vous n’êtes pas déclaré comme gestionnaire'
              : empty
                ? 'Aucun objet ne vous est affecté'
                : `Mon périmètre — ${perimeter.managerLabel || perimeter.managerCode}`
          }
        >
          {!perimeter.resolved ? (
            <>
              Votre identité n’est rattachée à aucun des cinq gestionnaires de cette
              campagne, donc le filtre ne laisse rien passer. Déclarez-la dans
              Préparation → Gestionnaires, ou désactivez « Mon périmètre ».
            </>
          ) : empty ? (
            <>
              Aucun entrepôt ni aucune zone n’est rattaché à{' '}
              {perimeter.managerLabel || perimeter.managerCode}. Les listes sont vides
              parce que votre périmètre l’est, pas parce que la campagne l’est.
            </>
          ) : (
            <>
              {perimeter.journalCount} journal(aux) et {perimeter.zoneCount} zone(s)
              vous sont affectés
              {perimeter.catchAll && ' (dont les entrepôts sans affectation explicite)'}.{' '}
              <strong>Le focus est un filtre, pas une habilitation</strong> : vous
              gardez le droit d’agir hors de votre périmètre, il suffit de couper
              l’interrupteur pour tout revoir.
            </>
          )}
        </Alert>
      )}

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

/**
 * The « Mon périmètre » switch.
 *
 * It carries its own counts, because the one thing a filter must never do is
 * leave the user unable to tell "nothing is assigned to me" from "nothing
 * exists". The banner under the header says which of the two it is.
 */
/**
 * The campaign's figures, a board at a time.
 *
 * Progress first — on inventory day the only question is what is still open —
 * then the money, then the dossier. The stock and variance boards need the book
 * stock to be frozen; before that they would show five dashes, so they are not
 * offered at all rather than offered empty.
 */
function KpiCarousel({ overview }: { overview: Overview }) {
  const { campaign, journalProgress, genericProgress, counts, perimeter } = overview
  const hasBookStock = campaign.book_stock_frozen_at !== null

  const kpis = useQuery({
    queryKey: ['kpis', campaign.id],
    queryFn: () => api.kpis(campaign.id),
    enabled: hasBookStock,
  })

  const slides: Array<{ id: string; label: string; content: ReactNode }> = [
    {
      id: 'progress',
      label: 'Avancement',
      content: (
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
                  { label: 'Terminés', value: journalProgress.complete, color: 'var(--success)' },
                  { label: 'En cours', value: journalProgress.running, color: 'var(--accent)' },
                  { label: 'En attente', value: journalProgress.pending, color: 'var(--bg-active)' },
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
                <dt>Gestionnaires affectés</dt>
                <dd className="num">
                  {perimeter.resolved
                    ? perimeter.managerLabel || perimeter.managerCode
                    : '—'}
                </dd>
                <dt>Moteur de calcul</dt>
                <dd className="mono">v{campaign.engine_version}</dd>
              </dl>
            </div>
          </div>
        </div>
      ),
    },
  ]

  if (hasBookStock) {
    slides.push({
      id: 'stock',
      label: 'Stock et écarts',
      content: (
        <AsyncBoundary
          query={kpis}
          skeleton={
            <div className="grid grid--kpi">
              {Array.from({ length: 5 }, (_, i) => (
                <div key={i} className="kpi">
                  <Skeleton height={52} />
                </div>
              ))}
            </div>
          }
        >
          {(data) => (
            <div className="grid grid--kpi">
              <Kpi
                label="Stock livre"
                value={moneyShort(data.bookValue)}
                compare={<span className="num">{numShort(data.bookQty)} unités</span>}
                hero
              />
              <Kpi
                label="Écart net"
                value={signedMoney(data.netVarianceValue)}
                tone={signClass(data.netVarianceValue) as 'pos' | 'neg' | 'neutral'}
                compare={<span className="num">{signedNum(data.netVarianceQty)} unités</span>}
                hint="Somme signée : les surplus compensent les manques. Répond à « avons-nous gagné ou perdu ? »"
              />
              <Kpi
                label="Écart brut"
                value={moneyShort(data.grossVarianceValue)}
                tone="neg"
                compare={<span>{data.materialLineCount} ligne(s) au-delà des seuils</span>}
                hint="Somme des écarts en valeur absolue. Répond à « combien nous sommes-nous trompés ? »"
              />
              <Kpi
                label="Fiabilité brute"
                value={percent(data.grossReliabilityValue, 2)}
                compare={
                  <span>
                    nette <strong className="num">{percent(data.netReliabilityValue, 2)}</strong>
                  </span>
                }
                hint="1 − Σ|écart €| / Σ stock livre €. La nette, compensée, est toujours plus flatteuse."
              />
              <Kpi
                label="IRA"
                value={percent(data.ira, 2)}
                compare={
                  <span className="num">
                    {data.accurateLineCount.toLocaleString('fr-FR')} /{' '}
                    {data.lineCount.toLocaleString('fr-FR')} exacts
                  </span>
                }
                hint="Part des couples article/emplacement dont l’écart tient dans la tolérance. Standard WMS."
              />
            </div>
          )}
        </AsyncBoundary>
      ),
    })
    slides.push({
      id: 'coverage',
      label: 'Couverture du comptage',
      content: (
        <AsyncBoundary query={kpis} skeleton={<Skeleton height={120} />}>
          {(data) => (
            <div className="grid grid--kpi">
              <Kpi
                label="Lignes analysées"
                value={data.lineCount.toLocaleString('fr-FR')}
                compare={<span>couples article / emplacement</span>}
                hero
              />
              <Kpi
                label="Comptés sans stock livre"
                value={data.countedOnlyCount.toLocaleString('fr-FR')}
                tone={data.countedOnlyCount ? 'neg' : 'neutral'}
                compare={<span>stock trouvé là où l’ERP n’en voyait aucun</span>}
                hint="À vérifier avant ajustement : souvent un emplacement mal saisi."
              />
              <Kpi
                label="Jamais comptés"
                value={data.bookOnlyCount.toLocaleString('fr-FR')}
                tone={data.bookOnlyCount ? 'neg' : 'neutral'}
                compare={<span>seront soldés à zéro à la clôture</span>}
                hint="Du stock livre existe sans aucun comptage en face."
              />
              <Kpi
                label="Écart résiduel"
                value={moneyShort(data.residualValue)}
                compare={<span>après ajustements postés</span>}
                hint="Ce qui reste inexpliqué une fois les mouvements de correction pris en compte."
              />
            </div>
          )}
        </AsyncBoundary>
      ),
    })
  }

  return <Carousel slides={slides} storageKey={`campaign.${campaign.id}`} />
}

function FocusSwitch({ overview }: { overview: Overview }) {
  const [focus, setFocus] = useFocusMode()
  const { perimeter } = overview
  const total = perimeter.journalCount + perimeter.zoneCount
  return (
    <Switch
      checked={focus}
      onChange={setFocus}
      title={
        perimeter.resolved
          ? 'Filtre d’affichage : vos actions restent les mêmes dans les deux modes.'
          : 'Votre identité n’est rattachée à aucun gestionnaire de cette campagne.'
      }
      label={
        <span className="row" style={{ gap: 'var(--space-2)' }}>
          Mon périmètre
          <Badge tone={total > 0 ? 'accent' : 'neutral'}>
            {perimeter.resolved ? total : 0}
          </Badge>
        </span>
      }
    />
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
