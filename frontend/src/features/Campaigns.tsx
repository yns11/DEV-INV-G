/** Campaign list, creation and duplication — the entry point of the product. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { Campaign, CampaignPage, CampaignStatus } from '../lib/types'
import {
  CAMPAIGN_STATUS_LABELS,
  date as fmtDate,
  label as toLabel,
  relativeTime,
} from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import {
  Alert,
  AsyncBoundary,
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  Icons,
  Modal,
  SearchInput,
  Switch,
  TableSkeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

/**
 * Cards or rows.
 *
 * Both readings are legitimate and neither wins in general: cards carry the
 * freeze state and read well at a dozen campaigns, a grid sorts and filters and
 * still reads at two hundred. So the choice is the user's, and it is remembered
 * — a display preference that resets on every visit is a preference the app
 * makes you re-state instead of one it holds.
 */
type Display = 'cards' | 'list'

const DISPLAY_KEY = 'campagnes-inventaire.campaigns.display'

/**
 * Combien de campagnes une page en rend.
 *
 * Cent tenaient dans l'écran des années durant ; ce qui manquait n'était pas
 * une borne plus haute mais de savoir qu'il y en avait davantage.
 */
const PAGE = 100


function readDisplay(): Display {
  try {
    return window.localStorage.getItem(DISPLAY_KEY) === 'list' ? 'list' : 'cards'
  } catch {
    return 'cards'
  }
}

/** Empty means « no filter » for every one of these. */
interface Filters {
  text: string
  status: CampaignStatus | ''
  owner: string
  from: string
  to: string
  mine: boolean
}

const NO_FILTER: Filters = { text: '', status: '', owner: '', from: '', to: '', mine: false }

function matches(campaign: Campaign, filters: Filters, me: string): boolean {
  const needle = filters.text.trim().toLowerCase()
  if (
    needle &&
    !campaign.code.toLowerCase().includes(needle) &&
    !(campaign.label ?? '').toLowerCase().includes(needle)
  ) {
    return false
  }
  if (filters.status && campaign.status !== filters.status) return false
  if (filters.owner && campaign.created_by !== filters.owner) return false
  // Dates compare as ISO strings, which sort chronologically — no parsing, and
  // no timezone to shift a count date by a day.
  if (filters.from && campaign.count_date < filters.from) return false
  if (filters.to && campaign.count_date > filters.to) return false
  if (filters.mine && campaign.created_by !== me) return false
  return true
}

export function CampaignsPage() {
  const [creating, setCreating] = useState(false)
  const [cloning, setCloning] = useState<Campaign | null>(null)
  const [deleting, setDeleting] = useState<Campaign | null>(null)
  const [display, setDisplayState] = useState<Display>(readDisplay)
  const [filters, setFilters] = useState<Filters>(NO_FILTER)
  // Le serveur borne la page. `limit` monte quand l'utilisateur demande la
  // suite : sans cela, les campagnes au-delà de la centième n'existaient plus
  // pour qui regardait l'écran, alors qu'elles étaient toujours en base.
  const [limit, setLimit] = useState(PAGE)
  const query = useQuery({
    queryKey: ['campaigns', limit],
    queryFn: () => api.listCampaigns(true, limit),
    // Garder la page précédente pendant que la suivante arrive : sans cela,
    // demander « les plus anciennes » vide la liste le temps de la requête.
    placeholderData: (previous: CampaignPage | undefined) => previous,
  })
  const page = query.data
  const loaded = page?.items ?? []
  const known = page?.total ?? 0
  const hidden = Math.max(0, known - loaded.length)
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const actor = me.data?.actor ?? ''

  const setDisplay = (value: Display) => {
    setDisplayState(value)
    try {
      window.localStorage.setItem(DISPLAY_KEY, value)
    } catch {
      /* the choice still holds for this session */
    }
  }

  const owners = useMemo(
    () => [...new Set(loaded.map((c) => c.created_by).filter(Boolean))].sort(),
    [loaded],
  )

  const shown = useMemo(
    () => loaded.filter((c) => matches(c, filters, actor)),
    [loaded, filters, actor],
  )
  const grouped = useMemo(
    () => ({
      active: shown.filter((c) => c.status !== 'CLOSED'),
      closed: shown.filter((c) => c.status === 'CLOSED'),
    }),
    [shown],
  )

  const filtering = JSON.stringify(filters) !== JSON.stringify(NO_FILTER)
  const total = loaded.length

  return (
    <div className="stack" style={{ gap: 'var(--space-5)' }}>
      <header className="page-head">
        <div>
          <h1 className="page-head__title">Campagnes d’inventaire</h1>
          <p className="page-head__lede">
            Chaque campagne est un dossier figé : référentiels, stock ERP, comptages,
            journaux et analyses sont versionnés ensemble et restent recalculables à
            l’identique.
          </p>
        </div>
        <Button variant="primary" icon={<Icons.plus size={15} />} onClick={() => setCreating(true)}>
          Nouvelle campagne
        </Button>
      </header>

      <AsyncBoundary
        query={query}
        skeleton={<TableSkeleton rows={4} cols={5} />}
        isEmpty={(page) => page.total === 0}
        empty={
          <Card>
            <EmptyState
              title="Aucune campagne"
              action={
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Créer la première campagne
                </Button>
              }
            >
              Créez une campagne pour préparer les référentiels, puis lancez le comptage
              le jour J. Une campagne suivante pourra être dupliquée de celle-ci en un clic.
            </EmptyState>
          </Card>
        }
      >
        {() => (
          <div className="stack" style={{ gap: 'var(--space-5)' }}>
            <CampaignFilters
              filters={filters}
              onChange={setFilters}
              owners={owners}
              display={display}
              onDisplayChange={setDisplay}
              shown={shown.length}
              total={total}
              hidden={hidden}
              onLoadMore={() => setLimit((current) => current + PAGE)}
              loading={query.isFetching}
            />

            {shown.length === 0 ? (
              <Card>
                <EmptyState
                  title="Aucune campagne ne correspond"
                  action={
                    <Button variant="secondary" onClick={() => setFilters(NO_FILTER)}>
                      Réinitialiser les filtres
                    </Button>
                  }
                >
                  {total} campagne(s) au total, aucune ne passe les filtres en cours.
                </EmptyState>
              </Card>
            ) : display === 'list' ? (
              <CampaignTable
                campaigns={shown}
                actor={actor}
                onClone={setCloning}
                onDelete={setDeleting}
              />
            ) : (
              <>
                <CampaignGrid
                  title={filtering ? 'Campagnes en cours (filtrées)' : 'Campagnes en cours'}
                  campaigns={grouped.active}
                  actor={actor}
                  onClone={setCloning}
                  onDelete={setDeleting}
                />
                <CampaignGrid
                  title="Campagnes clôturées"
                  campaigns={grouped.closed}
                  actor={actor}
                  onClone={setCloning}
                  onDelete={setDeleting}
                />
              </>
            )}
          </div>
        )}
      </AsyncBoundary>

      {creating && <CreateCampaignModal onClose={() => setCreating(false)} />}
      {cloning && <CloneCampaignModal source={cloning} onClose={() => setCloning(null)} />}
      {deleting && (
        <DeleteCampaignModal campaign={deleting} onClose={() => setDeleting(null)} />
      )}
    </div>
  )
}

function CampaignFilters({
  filters,
  onChange,
  owners,
  display,
  onDisplayChange,
  shown,
  total,
  hidden,
  onLoadMore,
  loading,
}: {
  filters: Filters
  onChange: (filters: Filters) => void
  owners: string[]
  display: Display
  onDisplayChange: (display: Display) => void
  shown: number
  total: number
  /** Campagnes existantes que cette page n'a pas encore chargées. */
  hidden: number
  onLoadMore: () => void
  loading: boolean
}) {
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    onChange({ ...filters, [key]: value })

  // Deux rangées, et la séparation n'est pas cosmétique : la première restreint
  // *quoi* est affiché, la seconde *comment*. Mêlées, les contrôles s'échangent
  // de place au gré des largeurs et rien ne se retrouve deux fois au même
  // endroit d'une visite à l'autre.
  //
  // La première rangée est une **grille**, pas une ligne qui déborde. En ligne,
  // chaque champ prenait la hauteur de son contenu : la recherche, sans
  // étiquette, se calait en bas pendant que les listes se calaient en haut, et
  // la seule aide de saisie de la rangée décalait sa colonne à elle seule. Une
  // grille pose les étiquettes sur une ligne et les champs sur une autre, quelle
  // que soit la largeur.
  return (
    <Card>
      <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div className="filters-row">
        <Field label="Recherche">
          <SearchInput
            value={filters.text}
            onChange={(value) => set('text', value)}
            placeholder="Code ou libellé…"
          />
        </Field>
        <Field label="Statut">
          <select
            className="select"
            value={filters.status}
            onChange={(e) => set('status', e.target.value as CampaignStatus | '')}
          >
            <option value="">Tous</option>
            {Object.entries(CAMPAIGN_STATUS_LABELS).map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Propriétaire">
          <select
            className="select"
            value={filters.owner}
            onChange={(e) => set('owner', e.target.value)}
          >
            <option value="">Tous</option>
            {owners.map((owner) => (
              <option key={owner} value={owner}>
                {owner}
              </option>
            ))}
          </select>
        </Field>
        {/* Les deux bornes dans un seul champ : deux étiquettes « du » et « au »
            l'une à côté de l'autre se lisent comme deux filtres indépendants. */}
        <div className="field--wide">
        <Field label="Date de comptage">
          <div className="row" style={{ gap: 'var(--space-2)' }}>
            <input
              className="input"
              type="date"
              aria-label="Comptage à partir du"
              value={filters.from}
              onChange={(e) => set('from', e.target.value)}
            />
            <span className="subtle">→</span>
            <input
              className="input"
              type="date"
              aria-label="Comptage jusqu’au"
              value={filters.to}
              onChange={(e) => set('to', e.target.value)}
            />
          </div>
        </Field>
        </div>
      </div>
      <div className="row-wrap">
        <Switch
          checked={filters.mine}
          onChange={(value) => set('mine', value)}
          label="Mes campagnes"
          title="N’afficher que les campagnes que vous avez créées."
        />
        <span className="spacer" />
        <span className="subtle num">
          {shown} / {total} campagne(s)
          {hidden > 0 && ` — ${hidden} plus ancienne(s) non chargée(s)`}
        </span>
        {hidden > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onLoadMore}
            disabled={loading}
            title={`Charger les ${Math.min(hidden, PAGE)} suivantes.`}
          >
            Charger les plus anciennes
          </Button>
        )}
        <div className="row" style={{ gap: 0 }}>
          <Button
            variant={display === 'cards' ? 'primary' : 'ghost'}
            size="sm"
            icon={<Icons.dashboard size={14} />}
            title="Affichage en icônes"
            onClick={() => onDisplayChange('cards')}
          >
            Icônes
          </Button>
          <Button
            variant={display === 'list' ? 'primary' : 'ghost'}
            size="sm"
            icon={<Icons.grid size={14} />}
            title="Affichage en liste"
            onClick={() => onDisplayChange('list')}
          >
            Liste
          </Button>
        </div>
      </div>
      </div>
    </Card>
  )
}

/**
 * Why a campaign cannot be deleted, or `null` when it can.
 *
 * Returned as a sentence rather than a boolean because it goes straight into
 * the button's tooltip: a control that is greyed out without saying why is a
 * control the user reports as broken.
 */
function deletionBlocker(campaign: Campaign, actor: string): string | null {
  if (!actor) return 'Identité inconnue : impossible de vérifier qui a créé cette campagne.'
  if (campaign.created_by !== actor) {
    return `Créée par ${campaign.created_by || 'un autre utilisateur'} : seul son auteur peut la supprimer.`
  }
  return null
}

function DeleteButton({
  campaign,
  actor,
  onDelete,
  compact = false,
}: {
  campaign: Campaign
  actor: string
  onDelete: (campaign: Campaign) => void
  /** Sans libellé, pour la grille où chaque pixel de colonne est disputé. */
  compact?: boolean
}) {
  const blocker = deletionBlocker(campaign, actor)
  return (
    <Button
      variant="ghost"
      size="sm"
      icon={<Icons.trash size={13} />}
      disabled={blocker !== null}
      title={blocker ?? `Supprimer ${campaign.code}`}
      onClick={() => onDelete(campaign)}
    >
      {compact ? null : 'Supprimer'}
    </Button>
  )
}

function CampaignGrid({
  title,
  campaigns,
  actor,
  onClone,
  onDelete,
}: {
  title: string
  campaigns: Campaign[]
  actor: string
  onClone: (campaign: Campaign) => void
  onDelete: (campaign: Campaign) => void
}) {
  if (campaigns.length === 0) return null
  return (
    <section className="stack">
      <h2 style={{ fontSize: 'var(--text-md)' }}>{title}</h2>
      <div className="grid grid--3">
        {campaigns.map((campaign) => (
          <article key={campaign.id} className="card">
            <div className="card__body stack" style={{ gap: 'var(--space-3)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <Badge tone={campaign.status} dot>
                  {toLabel(CAMPAIGN_STATUS_LABELS, campaign.status)}
                </Badge>
                <span className="subtle">{relativeTime(campaign.created_at)}</span>
              </div>
              <div>
                <Link
                  to={`/campagnes/${campaign.id}`}
                  style={{ fontSize: 'var(--text-lg)', fontWeight: 600, color: 'var(--fg)' }}
                >
                  {campaign.code}
                </Link>
                <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                  {campaign.label || 'Sans libellé'}
                </p>
              </div>
              <dl className="kv">
                <dt>Date de comptage</dt>
                <dd className="num">{fmtDate(campaign.count_date)}</dd>
                <dt>Propriétaire</dt>
                <dd className="truncate">{campaign.created_by || '—'}</dd>
                <dt>Stock ERP</dt>
                <dd>
                  {campaign.book_stock_frozen_at ? (
                    <span className="row" style={{ gap: 'var(--space-1)' }}>
                      <Icons.lock size={12} /> gelé le {fmtDate(campaign.book_stock_frozen_at)}
                    </span>
                  ) : (
                    <span className="subtle">non chargé</span>
                  )}
                </dd>
                {campaign.cloned_from_code && (
                  <>
                    <dt>Dupliquée de</dt>
                    <dd className="mono">{campaign.cloned_from_code}</dd>
                  </>
                )}
              </dl>
              {/* Se replie plutôt que de rogner : à quatre vignettes par ligne,
                  la carte devient assez étroite pour que « Ouvrir » sorte du
                  cadre, et un bouton à moitié visible ne se clique pas. */}
              <div className="row-wrap" style={{ marginTop: 'auto', gap: 'var(--space-2)' }}>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<Icons.copy size={13} />}
                  onClick={() => onClone(campaign)}
                >
                  Dupliquer
                </Button>
                <DeleteButton campaign={campaign} actor={actor} onDelete={onDelete} />
                <span className="spacer" />
                <Link className="btn btn--primary btn--sm" to={`/campagnes/${campaign.id}`}>
                  Ouvrir
                  <Icons.chevronRight size={13} />
                </Link>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}

function CampaignTable({
  campaigns,
  actor,
  onClone,
  onDelete,
}: {
  campaigns: Campaign[]
  actor: string
  onClone: (campaign: Campaign) => void
  onDelete: (campaign: Campaign) => void
}) {
  const columns: Column<Campaign>[] = [
    {
      key: 'code',
      label: 'Code',
      width: 160,
      sortable: true,
      render: (campaign) => (
        <Link className="mono" to={`/campagnes/${campaign.id}`}>
          {campaign.code}
        </Link>
      ),
      value: (campaign) => campaign.code,
    },
    { key: 'label', label: 'Libellé', width: 220, sortable: true },
    {
      key: 'status',
      label: 'Statut',
      width: 150,
      sortable: true,
      render: (campaign) => (
        <Badge tone={campaign.status} dot>
          {toLabel(CAMPAIGN_STATUS_LABELS, campaign.status)}
        </Badge>
      ),
      value: (campaign) => campaign.status,
    },
    {
      key: 'count_date',
      label: 'Date de comptage',
      width: 150,
      sortable: true,
      render: (campaign) => <span className="num">{fmtDate(campaign.count_date)}</span>,
      value: (campaign) => campaign.count_date,
    },
    // La date de création reste sur la carte : la grille est déjà large, et
    // c'est la date d'*inventaire* qui sert à retrouver une campagne.
    { key: 'created_by', label: 'Propriétaire', width: 180, sortable: true },
    {
      key: 'book_stock_frozen_at',
      label: 'Stock ERP',
      width: 140,
      render: (campaign) =>
        campaign.book_stock_frozen_at ? (
          <span className="row" style={{ gap: 'var(--space-1)' }}>
            <Icons.lock size={12} /> {fmtDate(campaign.book_stock_frozen_at)}
          </span>
        ) : (
          <span className="subtle">non chargé</span>
        ),
      value: (campaign) => campaign.book_stock_frozen_at,
    },
    {
      key: 'actions',
      label: '',
      width: 110,
      render: (campaign) => (
        <div className="row" style={{ gap: 'var(--space-1)' }}>
          <Button
            variant="ghost"
            size="sm"
            icon={<Icons.copy size={13} />}
            title={`Dupliquer ${campaign.code}`}
            onClick={() => onClone(campaign)}
          />
          <DeleteButton
            campaign={campaign}
            actor={actor}
            onDelete={onDelete}
            compact
          />
        </div>
      ),
      value: () => null,
    },
  ]

  return (
    <DataGrid
      columns={columns}
      rows={campaigns}
      getRowId={(campaign) => campaign.id}
      searchable={false}
      initialSort={{ key: 'count_date', direction: 'desc' }}
      emptyTitle="Aucune campagne"
    />
  )
}

function DeleteCampaignModal({
  campaign,
  onClose,
}: {
  campaign: Campaign
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const mutation = useMutation({
    mutationFn: () => api.deleteCampaign(campaign.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      toast.success(`Campagne ${campaign.code} supprimée`)
      onClose()
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  return (
    <Modal
      title={`Supprimer ${campaign.code} ?`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="danger"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Suppression…' : 'Supprimer la campagne'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <p>
          <strong>{campaign.code}</strong>
          {campaign.label ? ` — ${campaign.label}` : ''}, comptage du{' '}
          <span className="num">{fmtDate(campaign.count_date)}</span>.
        </p>
        <Alert tone="info" title="Suppression logique">
          La campagne quitte la liste et son code redevient disponible. Rien n’est
          effacé : comptages, journaux, ajustements et journal d’audit restent en base,
          et la suppression y est elle-même tracée.
        </Alert>
      </div>
    </Modal>
  )
}

function CreateCampaignModal({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const today = new Date().toISOString().slice(0, 10)
  const [form, setForm] = useState({ code: '', label: '', countDate: today })

  const mutation = useMutation({
    mutationFn: () => api.createCampaign(form),
    onSuccess: (campaign) => {
      void queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      toast.success(`Campagne ${campaign.code} créée`)
      navigate(`/campagnes/${campaign.id}`)
    },
    onError: (error) => showError(error, 'Création impossible'),
  })

  const valid = form.code.trim().length >= 3 && form.countDate

  return (
    <Modal
      title="Nouvelle campagne"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={!valid || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Création…' : 'Créer la campagne'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="Code de la campagne"
          hint="3 à 50 caractères : lettres, chiffres, tirets. Sert d’identifiant métier."
        >
          <input
            className="input mono"
            value={form.code}
            placeholder="INV-2026-06"
            onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
          />
        </Field>
        <Field label="Libellé">
          <input
            className="input"
            value={form.label}
            placeholder="Inventaire général juin 2026"
            onChange={(e) => setForm({ ...form, label: e.target.value })}
          />
        </Field>
        <Field label="Date de comptage" hint="Jour J de l’inventaire physique.">
          <input
            className="input"
            type="date"
            value={form.countDate}
            onChange={(e) => setForm({ ...form, countDate: e.target.value })}
          />
        </Field>
        <Alert tone="info" title="Et ensuite ?">
          La campagne démarre en <strong>Préparation</strong> : chargez les articles et
          les nomenclatures, réglez les seuils et créez les zones GENERIQUE. Le stock
          ERP se charge au début du <strong>Comptage</strong>.
        </Alert>
      </div>
    </Modal>
  )
}

function CloneCampaignModal({
  source,
  onClose,
}: {
  source: Campaign
  onClose: () => void
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState({
    code: '',
    label: '',
    countDate: new Date().toISOString().slice(0, 10),
    includeZones: true,
    includeSheetLines: true,
  })

  const mutation = useMutation({
    mutationFn: () => api.cloneCampaign({ sourceCampaignId: source.id, ...form }),
    onSuccess: (campaign) => {
      void queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      toast.success(
        `Campagne ${campaign.code} créée`,
        `Référentiels repris de ${source.code}.`,
      )
      navigate(`/campagnes/${campaign.id}`)
    },
    onError: (error) => showError(error, 'Duplication impossible'),
  })

  return (
    <Modal
      title={`Dupliquer ${source.code}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={form.code.trim().length < 3 || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? 'Duplication…' : 'Dupliquer'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Alert tone="info" title="Ce qui est repris">
          Les seuils, le référentiel articles, les nomenclatures, le référentiel
          entrepôts/emplacements (avec les emplacements désactivés) et, si vous le
          souhaitez, les zones GENERIQUE avec leurs listes d’articles pré-imprimées.
          <br />
          <strong>Aucune donnée de comptage n’est copiée</strong> : ni stock ERP, ni
          journaux, ni quantités, ni ajustements.
        </Alert>
        <Field label="Code de la nouvelle campagne">
          <input
            className="input mono"
            value={form.code}
            placeholder="INV-2026-12"
            onChange={(e) => setForm({ ...form, code: e.target.value.toUpperCase() })}
          />
        </Field>
        <Field label="Libellé">
          <input
            className="input"
            value={form.label}
            onChange={(e) => setForm({ ...form, label: e.target.value })}
          />
        </Field>
        <Field label="Date de comptage">
          <input
            className="input"
            type="date"
            value={form.countDate}
            onChange={(e) => setForm({ ...form, countDate: e.target.value })}
          />
        </Field>
        <label className="row">
          <input
            type="checkbox"
            checked={form.includeZones}
            onChange={(e) => setForm({ ...form, includeZones: e.target.checked })}
          />
          Reprendre les zones GENERIQUE et leurs feuilles
        </label>
        <label className="row">
          <input
            type="checkbox"
            checked={form.includeSheetLines}
            disabled={!form.includeZones}
            onChange={(e) => setForm({ ...form, includeSheetLines: e.target.checked })}
          />
          Reprendre les listes d’articles pré-imprimées (quantités vidées)
        </label>
      </div>
    </Modal>
  )
}
