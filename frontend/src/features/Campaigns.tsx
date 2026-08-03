/** Campaign list, creation and duplication — the entry point of the product. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import type { Campaign } from '../lib/types'
import {
  CAMPAIGN_STATUS_LABELS,
  date as fmtDate,
  label as toLabel,
  relativeTime,
} from '../lib/format'
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
  TableSkeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

export function CampaignsPage() {
  const [creating, setCreating] = useState(false)
  const [cloning, setCloning] = useState<Campaign | null>(null)
  const query = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api.listCampaigns(true),
  })

  const grouped = useMemo(() => {
    const rows = query.data ?? []
    return {
      active: rows.filter((c) => c.status !== 'CLOSED'),
      closed: rows.filter((c) => c.status === 'CLOSED'),
    }
  }, [query.data])

  return (
    <div className="stack" style={{ gap: 'var(--space-5)' }}>
      <header className="page-head">
        <div>
          <h1 className="page-head__title">Campagnes d’inventaire</h1>
          <p className="page-head__lede">
            Chaque campagne est un dossier figé : référentiels, stock livre, comptages,
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
        isEmpty={(rows) => rows.length === 0}
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
            <CampaignGrid
              title="Campagnes en cours"
              campaigns={grouped.active}
              onClone={setCloning}
            />
            {grouped.closed.length > 0 && (
              <CampaignGrid
                title="Campagnes clôturées"
                campaigns={grouped.closed}
                onClone={setCloning}
              />
            )}
          </div>
        )}
      </AsyncBoundary>

      {creating && <CreateCampaignModal onClose={() => setCreating(false)} />}
      {cloning && <CloneCampaignModal source={cloning} onClose={() => setCloning(null)} />}
    </div>
  )
}

function CampaignGrid({
  title,
  campaigns,
  onClone,
}: {
  title: string
  campaigns: Campaign[]
  onClone: (campaign: Campaign) => void
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
                <dt>Stock livre</dt>
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
              <div className="row" style={{ marginTop: 'auto' }}>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<Icons.copy size={13} />}
                  onClick={() => onClone(campaign)}
                >
                  Dupliquer
                </Button>
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
          livre se charge au début du <strong>Comptage</strong>.
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
          <strong>Aucune donnée de comptage n’est copiée</strong> : ni stock livre, ni
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
