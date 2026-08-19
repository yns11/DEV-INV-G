/**
 * The campaign workspace: page head, indicators and phase gate.
 *
 * Navigation lives in the sidebar (see `components/CampaignNav`), so this file
 * no longer draws a bar of any kind. What is left is what belongs above the
 * content: what screen you are on, what the campaign's figures are, and the two
 * actions that apply everywhere — export the dossier, move to the next phase.
 *
 * The header's data comes from a single `/overview` call, shared with the
 * sidebar through the query cache. The permission payload it carries is the
 * same one the backend enforces, so UI and API can never disagree about what is
 * editable.
 */

import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Outlet, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, downloads } from '../lib/api'
import type { CampaignStatus, Overview } from '../lib/types'
import {
  CAMPAIGN_STATUS_LABELS,
  moneyShort,
  qty,
  percent,
  signClass,
  signedMoney,
  signedNum,
  label as toLabel,
} from '../lib/format'
import { useFocusMode } from '../lib/focus'
import { UTILITIES, labelOf, sectionFor } from '../lib/navigation'
import {
  Alert, AsyncBoundary, Badge, Button, Carousel, ErrorState, Icons, Kpi, Modal,
  Skeleton, Switch, useDownload, useErrorToast, useToast,
} from '../components/ui'

const NEXT_PHASE: Partial<Record<CampaignStatus, CampaignStatus>> = {
  PREPARATION: 'COUNTING',
  COUNTING: 'ANALYSIS',
  ANALYSIS: 'CLOSED',
}

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
        <Skeleton height={72} />
        <Skeleton height={320} />
      </div>
    )
  }
  if (query.isError || !query.data) {
    return <ErrorState error={query.error} onRetry={query.refetch} />
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <CampaignHeader overview={query.data} />
      <Outlet context={query.data} />
    </div>
  )
}

function CampaignHeader({ overview }: { overview: Overview }) {
  const startDownload = useDownload()
  const location = useLocation()
  const navigate = useNavigate()
  const { campaign } = overview
  const [transitionTarget, setTransitionTarget] = useState<CampaignStatus | null>(null)
  const [focus] = useFocusMode()
  const next = NEXT_PHASE[campaign.status]
  const section = sectionFor(location.pathname, campaign.id)

  return (
    <header className="stack" style={{ gap: 'var(--space-4)' }}>
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div className="stack" style={{ gap: 'var(--space-1)' }}>
          <h1 className="page-head__title" style={{ marginBottom: 0 }}>
            {section ? labelOf(section, overview) : campaign.code}
          </h1>
          <p className="page-head__lede">{section?.lede ?? campaign.label}</p>
        </div>
        <div className="row-wrap">
          <FocusSwitch overview={overview} />
          {/* L'assistant et l'audit s'ouvrent à propos de ce qu'on est en train
              de faire, depuis n'importe quel écran. Ils appartiennent donc à la
              barre d'actions, pas à l'arborescence des étapes — où ils
              formaient un groupe qu'il fallait traverser pour atteindre la
              première. */}
          {UTILITIES.map((utility) => {
            const Icon = Icons[utility.icon]
            const here = location.pathname.endsWith(`/${utility.to}`)
            return (
              <Button
                key={utility.to}
                variant={here ? 'primary' : 'secondary'}
                icon={<Icon size={14} />}
                title={utility.lede}
                onClick={() => navigate(`/campagnes/${campaign.id}/${utility.to}`)}
              >
                {utility.short}
              </Button>
            )
          })}
          <Button
            icon={<Icons.download size={14} />}
            title="Le dossier complet de la campagne, en un classeur Excel"
            onClick={() => startDownload(downloads.campaignWorkbook(campaign.id))}
          >
            Exporter
          </Button>
          {next && (
            <Button
              variant="primary"
              icon={<Icons.chevronRight size={14} />}
              onClick={() => setTransitionTarget(next)}
            >
              Passer en {toLabel(CAMPAIGN_STATUS_LABELS, next).toLowerCase()}
            </Button>
          )}
        </div>
      </div>

      <KpiCarousel overview={overview} />

      {focus && <PerimeterNote overview={overview} />}

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
 * What the focus filter is currently hiding.
 *
 * One line, not a paragraph — but never absent, because the one thing a filter
 * must not do is leave somebody unable to tell "nothing is assigned to me" from
 * "nothing exists".
 */
function PerimeterNote({ overview }: { overview: Overview }) {
  const { perimeter } = overview
  const empty = perimeter.journalCount === 0 && perimeter.zoneCount === 0

  if (!perimeter.resolved) {
    return (
      <Alert tone="warning" title="Vous n’êtes déclaré comme gestionnaire d’aucun périmètre">
        Déclarez-vous dans Référentiels &amp; seuils → Gestionnaires, ou coupez « Mon
        périmètre ».
      </Alert>
    )
  }
  if (empty) {
    return (
      <Alert tone="warning" title="Aucun objet ne vous est affecté">
        Les listes sont vides parce que votre périmètre l’est, pas la campagne.
      </Alert>
    )
  }
  return (
    <Alert tone="info" title={`Mon périmètre — ${perimeter.managerLabel || perimeter.managerCode}`}>
      {perimeter.journalCount} journal(aux), {perimeter.zoneCount} zone(s)
      {perimeter.catchAll && ' (dont les entrepôts non affectés)'}. Filtre d’affichage :
      vos droits sont inchangés.
    </Alert>
  )
}

/**
 * The campaign's figures, a board at a time.
 *
 * Progress first — on inventory day the only question is what is still open —
 * then the money, then the coverage. Every board is a row of the same KPI card,
 * so switching slides moves the numbers and nothing else; a first slide built
 * out of taller cards made the whole strip jump on every arrow press.
 *
 * The stock and variance boards need the book stock to be frozen; before that
 * they would show five dashes, so they are not offered at all rather than
 * offered empty.
 */
function KpiCarousel({ overview }: { overview: Overview }) {
  const { campaign, journalProgress, genericProgress, counts } = overview
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
        <div className="grid grid--kpi">
          <Kpi
            label="Journaux de comptage"
            value={percent(journalProgress.ratio)}
            compare={
              <span className="num">
                {journalProgress.complete} / {journalProgress.total} terminés
              </span>
            }
            hero
          />
          <Kpi
            label="Zones GENERIQUE"
            value={percent(genericProgress.ratio)}
            compare={
              <span className="num">
                {genericProgress.done} / {genericProgress.zones} terminées
              </span>
            }
          />
          <Kpi
            label="Arbitrages en attente"
            value={genericProgress.pendingArbitrations.toLocaleString('fr-FR')}
            tone={genericProgress.pendingArbitrations ? 'neg' : 'neutral'}
            compare={<span>écarts entre les deux comptages</span>}
          />
          {/* Le gestionnaire ne figure plus ici : cette troisième ligne
              rendait la carte — et donc toute la planche — plus haute que les
              autres, et le carrousel changeait de taille à chaque flèche. Il
              est déjà nommé par l'interrupteur « Mon périmètre », qui est
              l'endroit d'où il se change. */}
          <Kpi
            label="Articles au dossier"
            value={counts.items.toLocaleString('fr-FR')}
            compare={
              <span className="num">
                {counts.bookStockLines.toLocaleString('fr-FR')} lignes de stock ERP
              </span>
            }
          />
          <Kpi
            label="Journaux en cours"
            value={journalProgress.running.toLocaleString('fr-FR')}
            tone={journalProgress.running ? 'neg' : 'neutral'}
            compare={
              <span className="num">
                {journalProgress.pending.toLocaleString('fr-FR')} pas encore ouverts
              </span>
            }
            hint="Saisis mais pas encore postés à l’ERP."
          />
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
                label="Stock ERP"
                value={moneyShort(data.bookValue)}
                compare={<span className="num">{qty(data.bookQty)} unités</span>}
                hero
              />
              <Kpi
                label="Stock physique"
                value={moneyShort(data.physicalValue)}
                compare={<span className="num">{qty(data.physicalQty)} unités</span>}
                hint="Ce qui a été compté, plus les mouvements postés depuis. C’est ce total-là que l’écart oppose au stock ERP."
              />
              <Kpi
                label="Écart net"
                value={signedMoney(data.netVarianceValue)}
                tone={signClass(data.netVarianceValue) as 'pos' | 'neg' | 'neutral'}
                compare={<span className="num">{signedNum(data.netVarianceQty)} unités</span>}
                hint="Somme signée : les surplus compensent les manques."
              />
              <Kpi
                label="Écart brut"
                value={moneyShort(data.grossVarianceValue)}
                tone="neg"
                compare={<span>{data.materialLineCount} ligne(s) hors seuils</span>}
                hint="Somme des écarts en valeur absolue."
              />
              <Kpi
                label="Fiabilité brute"
                value={percent(data.grossReliabilityValue, 2)}
                compare={
                  <span>
                    nette <strong className="num">{percent(data.netReliabilityValue, 2)}</strong>
                  </span>
                }
                hint="1 − Σ|écart €| / Σ stock ERP €. La nette, compensée, flatte."
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
                hint="Part des couples article/emplacement dans la tolérance."
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
                label="Comptés sans stock ERP"
                value={data.countedOnlyCount.toLocaleString('fr-FR')}
                tone={data.countedOnlyCount ? 'neg' : 'neutral'}
                compare={<span>trouvé là où l’ERP ne voyait rien</span>}
                hint="Souvent un emplacement mal saisi. À vérifier avant ajustement."
              />
              <Kpi
                label="Jamais comptés"
                value={data.bookOnlyCount.toLocaleString('fr-FR')}
                tone={data.bookOnlyCount ? 'neg' : 'neutral'}
                compare={<span>seront soldés à zéro à la clôture</span>}
              />
              <Kpi
                label="Ajustements postés"
                value={moneyShort(data.adjustedValue)}
                compare={<span>déjà compris dans l’écart</span>}
                hint={`Les mouvements postés après le comptage : ils s’ajoutent à lui pour former le stock physique. Le comptage seul montrait ${moneyShort(data.countedVarianceValue)}.`}
              />
              <Kpi
                label="Lignes hors seuils"
                value={data.materialLineCount.toLocaleString('fr-FR')}
                tone={data.materialLineCount ? 'neg' : 'neutral'}
                compare={<span>à analyser une par une</span>}
                hint="Celles dont l’écart dépasse le seuil de leur type d’article."
              />
            </div>
          )}
        </AsyncBoundary>
      ),
    })
  }

  return <Carousel slides={slides} alignColumns storageKey={`campaign.${campaign.id}`} />
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
          ? 'Filtre d’affichage : vos droits sont les mêmes dans les deux modes.'
          : 'Vous n’êtes rattaché à aucun gestionnaire de cette campagne.'
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
      width={640}
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
            {mutation.isPending ? 'En cours…' : 'Confirmer'}
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
          <Alert tone="success" title="Prérequis remplis" />
        )}

        {freezes.length > 0 && (
          <Alert tone="warning" title="Ce qui sera figé définitivement">
            <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
              {freezes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </Alert>
        )}

        {target === 'CLOSED' && (
          <Alert tone="danger" title="Irréversible">
            Une campagne clôturée ne se rouvre pas. Pour poursuivre, dupliquez-la.
          </Alert>
        )}
      </div>
    </Modal>
  )
}

const FREEZE_NOTES: Partial<Record<CampaignStatus, string[]>> = {
  COUNTING: [
    'Articles, nomenclatures et seuils.',
    'Les zones GENERIQUE restent créables pendant le comptage.',
  ],
  ANALYSIS: [
    'Journaux de comptage et leurs lignes.',
    'Feuilles GENERIQUE, arbitrages et consolidation.',
    'Référentiel emplacements.',
  ],
  CLOSED: [
    'Ajustements, causes, commentaires, et l’écart backflush.',
    'La comparaison entre deux campagnes reste ouverte : elle ne change aucun chiffre de celle-ci, et c’est une fois close qu’on la fait.',
    'Exports et audit restent lisibles.',
  ],
}
