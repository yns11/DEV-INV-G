/** Les causes assignées aux écarts, et la répartition qui en découle. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AssignableCause, Overview } from '../lib/types'
import { moneyShort, qty, percent, signClass, signedMoney } from '../lib/format'
import { CompositionBar } from '../components/charts'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { AsyncBoundary, Badge, Button, Card, Field, Icons, SearchInput, Skeleton, useErrorToast, useToast } from '../components/ui'

export const BUCKET_LABELS: Record<string, string> = {
  multiplesOf10: 'Multiples de 10',
  multiplesOf50: 'Multiples de 50',
  multiplesOf100: 'Multiples de 100',
  endingIn5: 'Terminant par 5',
}

// --------------------------------------------------------------------------- //
// Filtres
// --------------------------------------------------------------------------- //

/**
 * Ce sur quoi on restreint la liste d'affectation.
 *
 * L'écran sert à faire descendre « part sans cause affectée » à zéro. Sur deux
 * cents lignes, celles qui restent à traiter sont noyées parmi celles qui sont
 * déjà faites, et il n'y avait aucun moyen de ne voir que les premières —
 * ni même de retrouver une référence précise, la seule recherche de
 * l'application vivant dans `DataGrid`, que ce tableau n'utilise pas.
 */
export interface CauseFilters {
  /** Référence ou désignation, insensible à la casse et aux espaces. */
  text: string
  /** `''` toutes · `none` non affectées · `any` affectées · un code de cause. */
  cause: string
  /** `''` toutes · `with` avec proposition IA · `pending` proposition non suivie. */
  ai: string
  /** `''` tous · `pos` excédents · `neg` manquants. */
  sign: string
}

export const NO_CAUSE_FILTER: CauseFilters = { text: '', cause: '', ai: '', sign: '' }

/** La ligne telle que ce tableau la lit — le strict nécessaire aux filtres. */
interface FilterableRow {
  itemNumber: string
  name?: string | null
  causeCode?: string | null
  aiSuggestedCause?: string | null
  varianceValue?: number | null
}

/**
 * Une ligne passe-t-elle les filtres ?
 *
 * À part du composant, et exportée, parce que c'est la seule partie de cet
 * écran qui décide quelque chose. Les combinaisons — « sans cause **et** avec
 * une proposition IA », qui est la file de travail réelle — sont exactement ce
 * qu'un contrôle doit pouvoir vérifier sans monter un DOM.
 *
 * Les filtres se composent en **et** : chacun retire, aucun n'ajoute.
 */
export function matchesCause(row: FilterableRow, filters: CauseFilters): boolean {
  const needle = filters.text.trim().toLowerCase()
  if (needle) {
    const hay = `${row.itemNumber} ${row.name ?? ''}`.toLowerCase()
    if (!hay.includes(needle)) return false
  }

  const cause = row.causeCode ?? null
  if (filters.cause === 'none' && cause !== null) return false
  if (filters.cause === 'any' && cause === null) return false
  if (filters.cause && !['none', 'any'].includes(filters.cause) && cause !== filters.cause) {
    return false
  }

  const suggested = row.aiSuggestedCause ?? null
  if (filters.ai === 'with' && suggested === null) return false
  // « À valider » : l'IA propose quelque chose que la décision humaine ne
  // reprend pas. C'est la liste sur laquelle on clique « Accepter », et elle
  // n'existe pas si l'on ne peut pas retirer les lignes déjà entérinées.
  if (filters.ai === 'pending' && (suggested === null || suggested === cause)) return false

  const value = row.varianceValue ?? 0
  if (filters.sign === 'pos' && value <= 0) return false
  if (filters.sign === 'neg' && value >= 0) return false

  return true
}

/**
 * La barre de filtres du tableau d'affectation.
 *
 * Le décompte « X sur Y » n'est pas décoratif : une liste filtrée qui ne dit
 * pas qu'elle l'est se lit comme la liste entière, et c'est ainsi qu'on croit
 * avoir tout traité. Il est à côté du bouton qui remet tout, pour que le
 * constat et le geste soient au même endroit.
 */
function CauseFilterBar({
  filters,
  onChange,
  causes,
  shown,
  total,
}: {
  filters: CauseFilters
  onChange: (filters: CauseFilters) => void
  causes: AssignableCause[]
  shown: number
  total: number
}) {
  const set = <K extends keyof CauseFilters>(key: K, value: CauseFilters[K]) =>
    onChange({ ...filters, [key]: value })
  const filtering = shown !== total

  return (
    <div className="stack" style={{ gap: 'var(--space-2)', padding: 'var(--space-4)' }}>
      <div className="filters-row">
        <div className="field--wide">
          <Field label="Recherche">
            <SearchInput
              value={filters.text}
              onChange={(value) => set('text', value)}
              placeholder="Référence ou désignation…"
            />
          </Field>
        </div>
        <Field label="Cause retenue">
          <select
            className="select"
            value={filters.cause}
            onChange={(e) => set('cause', e.target.value)}
          >
            <option value="">Toutes</option>
            {/* En tête, parce que c'est la file de travail : l'écran existe
                pour faire descendre la part sans cause à zéro. */}
            <option value="none">— non affectées —</option>
            <option value="any">Affectées, quelle qu’elle soit</option>
            {causes.map((cause) => (
              <option key={cause.code} value={cause.code}>
                {cause.code} — {cause.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Proposition IA">
          <select
            className="select"
            value={filters.ai}
            onChange={(e) => set('ai', e.target.value)}
          >
            <option value="">Toutes</option>
            <option value="pending">À valider</option>
            <option value="with">Avec proposition</option>
          </select>
        </Field>
        <Field label="Sens de l’écart">
          <select
            className="select"
            value={filters.sign}
            onChange={(e) => set('sign', e.target.value)}
          >
            <option value="">Tous</option>
            <option value="pos">Excédents</option>
            <option value="neg">Manquants</option>
          </select>
        </Field>
      </div>
      <div className="row-wrap" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
        <span className="subtle">
          {filtering
            ? `${shown.toLocaleString('fr-FR')} ligne(s) affichée(s) sur ${total.toLocaleString('fr-FR')}`
            : `${total.toLocaleString('fr-FR')} ligne(s)`}
        </span>
        {filtering && (
          <Button size="sm" variant="ghost" onClick={() => onChange(NO_CAUSE_FILTER)}>
            Réinitialiser les filtres
          </Button>
        )}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Causes
// --------------------------------------------------------------------------- //

export function CausesTab({ campaignId, overview }: { campaignId: string; overview: Overview }) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  // Affecter une cause sans pouvoir regarder d'où sort l'écart, c'est deviner.
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect } | null
  >(null)
  const [filters, setFilters] = useState<CauseFilters>(NO_CAUSE_FILTER)

  const split = useQuery({
    queryKey: ['cause-split', campaignId],
    queryFn: () => api.causeSplit(campaignId),
  })
  const causes = useQuery({
    queryKey: ['causes', campaignId],
    queryFn: () => api.causes(campaignId),
  })
  const variances = useQuery({
    queryKey: ['variances', campaignId, 'causes'],
    queryFn: () => api.variances(campaignId, { limit: 200 }),
  })

  const suggest = useMutation({
    mutationFn: () => api.suggestCauses(campaignId, 40),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.suggestions} proposition(s) générée(s)`,
        'Les propositions IA sont stockées à côté de la décision humaine, jamais à sa place.',
      )
    },
    onError: (error) => showError(error, 'Génération impossible'),
  })

  const save = useMutation({
    mutationFn: ({ itemNumber, causeCode }: { itemNumber: string; causeCode: string | null }) =>
      api.saveVarianceAnalysis(campaignId, itemNumber, {
        causeCode,
        comment: '',
        accepted: causeCode !== null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Cause enregistrée')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const editable = overview.permissions.analysis

  return (
    <div className="stack">
      <AsyncBoundary query={split} skeleton={<Skeleton height={200} />}>
        {(data) => (
          <Card
            title="Répartition des écarts par cause"
            message={
              data.unassignedShare > 0
                ? `${percent(data.unassignedShare)} de l’écart absolu reste sans cause affectée — c’est ce qui alimente le plan d’action de la prochaine campagne.`
                : 'Tous les écarts significatifs ont une cause affectée.'
            }
            actions={
              editable && (
                <Button
                  icon={<Icons.sparkles size={14} />}
                  disabled={suggest.isPending}
                  onClick={() => suggest.mutate()}
                >
                  {suggest.isPending ? 'Analyse IA…' : 'Proposer des causes par IA'}
                </Button>
              )
            }
          >
            <CompositionBar
              segments={data.rows.map((row) => ({
                label: row.label,
                value: row.absValue,
                color: row.code === null ? 'var(--fg-subtle)' : undefined,
              }))}
              format={moneyShort}
            />
          </Card>
        )}
      </AsyncBoundary>

      <Card
        title="Affectation des causes"
        message="Une proposition IA n’est jamais écrite dans la colonne de décision : elle est affichée à côté, avec sa justification, et vous l’acceptez ou non."
        flush
      >
        <AsyncBoundary query={variances} isEmpty={(rows) => rows.length === 0}>
          {(all) => {
          const rows = all.filter((row) => matchesCause(row, filters))
          return (
            <>
            <CauseFilterBar
              filters={filters}
              onChange={setFilters}
              causes={causes.data ?? []}
              shown={rows.length}
              total={all.length}
            />
            <div className="table-wrap" style={{ maxHeight: 620 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th>Article</th>
                    <th className="num">Compté</th>
                    <th className="num">Physique</th>
                    <th className="num">Écart</th>
                    <th style={{ width: 260 }}>Cause retenue</th>
                    <th>Proposition IA</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.itemNumber}>
                      <td>
                        <div className="mono">{row.itemNumber}</div>
                        <div className="subtle truncate" style={{ maxWidth: 200 }}>
                          {row.name}
                        </div>
                      </td>
                      <td className="num">
                        <DrillCell
                          disabled={row.countedQty === 0}
                          onOpen={() =>
                            setDrill({ itemNumber: row.itemNumber, aspect: 'counted' })
                          }
                        >
                          <span className="num">{qty(row.countedQty)}</span>
                        </DrillCell>
                      </td>
                      <td className="num">
                        <DrillCell
                          disabled={row.physicalQty === 0}
                          onOpen={() =>
                            setDrill({ itemNumber: row.itemNumber, aspect: 'physical' })
                          }
                        >
                          <span className="num">{qty(row.physicalQty)}</span>
                        </DrillCell>
                      </td>
                      <td className={`num ${signClass(row.varianceValue)}`}>
                        <DrillCell
                          disabled={row.varianceQty === 0}
                          onOpen={() =>
                            setDrill({ itemNumber: row.itemNumber, aspect: 'variance' })
                          }
                        >
                          {signedMoney(row.varianceValue)}
                        </DrillCell>
                      </td>
                      <td>
                        <select
                          className="select"
                          value={row.causeCode ?? ''}
                          disabled={!editable || save.isPending}
                          onChange={(event) =>
                            save.mutate({
                              itemNumber: row.itemNumber,
                              causeCode: event.target.value || null,
                            })
                          }
                        >
                          <option value="">— non affectée —</option>
                          {(causes.data ?? []).map((cause: AssignableCause) => (
                            <option key={cause.code} value={cause.code}>
                              {cause.code} — {cause.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        {row.aiSuggestedCause ? (
                          <div className="stack" style={{ gap: 'var(--space-1)' }}>
                            <span className="row" style={{ gap: 'var(--space-2)' }}>
                              <Badge tone="accent">
                                {row.aiSuggestedCause}
                                {row.aiConfidence !== null &&
                                  ` · ${Math.round(row.aiConfidence * 100)} %`}
                              </Badge>
                              {editable && row.causeCode !== row.aiSuggestedCause && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() =>
                                    save.mutate({
                                      itemNumber: row.itemNumber,
                                      causeCode: row.aiSuggestedCause,
                                    })
                                  }
                                >
                                  Accepter
                                </Button>
                              )}
                            </span>
                            {row.aiRationale && (
                              <span className="subtle">{row.aiRationale}</span>
                            )}
                          </div>
                        ) : (
                          <span className="subtle">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rows.length === 0 && (
              <div style={{ padding: 'var(--space-4)' }}>
                <span className="subtle">
                  Aucune ligne ne correspond à ces filtres.{' '}
                  <Button size="sm" variant="ghost" onClick={() => setFilters(NO_CAUSE_FILTER)}>
                    Tout afficher
                  </Button>
                </span>
              </div>
            )}
            </>
          )
          }}
        </AsyncBoundary>
      </Card>

      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}
