/** Les causes assignées aux écarts, et la répartition qui en découle. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AssignableCause, Overview, VarianceRow } from '../lib/types'
import { moneyShort, qty, percent, signClass, signedMoney } from '../lib/format'
import { CompositionBar } from '../components/charts'
import { DataGrid, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { CauseDialog } from '../components/CauseDialog'
import { Markdown } from '../lib/markdown'
import {
  AsyncBoundary,
  Badge,
  Button,
  Card,
  Field,
  Icons,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

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
 * Trois questions, et aucune ne s'exprime comme un filtre de colonne. « Non
 * affectées » demande l'absence d'une valeur ; « à valider » compare deux
 * colonnes entre elles — ce que l'IA propose et ce que personne n'a encore
 * repris ; « manquants » lit le signe d'un nombre. Ce sont les trois files de
 * travail de cet écran, et elles vivent au-dessus de la grille.
 *
 * La recherche libre, elle, n'est plus ici : la grille a la sienne, comme
 * partout ailleurs dans l'application, et deux champs de recherche côte à côte
 * auraient obligé à deviner lequel cherche quoi.
 */
export interface CauseFilters {
  /** `''` toutes · `none` non affectées · `any` affectées · un code de cause. */
  cause: string
  /** `''` toutes · `with` avec proposition IA · `pending` proposition non suivie. */
  ai: string
  /** `''` tous · `pos` excédents · `neg` manquants. */
  sign: string
}

export const NO_CAUSE_FILTER: CauseFilters = { cause: '', ai: '', sign: '' }

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
 * Les trois files de travail, au-dessus de la grille.
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
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [assigning, setAssigning] = useState<VarianceRow[] | null>(null)

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

  /**
   * Une ligne, ses deux colonnes de décision, ensemble.
   *
   * Elle ne portait que la cause et postait `comment: ''`. Or le serveur fait
   * foi du formulaire, commentaire vidé compris — c'est ce qui permet
   * d'effacer un commentaire en effaçant le champ — donc changer une cause
   * depuis cette grille effaçait sans rien dire le commentaire écrit depuis la
   * vue Écarts. Le défaut ne se voyait pas ici : la colonne était en lecture
   * seule, et le texte disparaissait de la ligne d'à côté.
   *
   * Chaque champ envoie donc la valeur courante de l'autre. Ce qui part est
   * toujours la ligne entière telle qu'elle est à l'écran.
   */
  const save = useMutation({
    mutationFn: ({ itemNumber, causeCode, comment }: {
      itemNumber: string
      causeCode: string | null
      comment: string
    }) =>
      api.saveVarianceAnalysis(campaignId, itemNumber, {
        causeCode,
        comment,
        accepted: causeCode !== null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Ligne enregistrée')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const assign = useMutation({
    mutationFn: ({ items, cause, comment }: {
      items: string[]
      cause: string | null
      comment: string
    }) =>
      api.saveVarianceCauses(campaignId, {
        itemNumbers: items,
        causeCode: cause,
        comment,
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setAssigning(null)
      setSelected(new Set())
      toast.success(`${result.updated} écart(s) mis à jour`)
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const editable = overview.permissions.analysis
  const causeList = causes.data ?? []

  const columns: Column<VarianceRow>[] = [
    {
      key: 'itemNumber',
      label: 'Article',
      width: 210,
      render: (row) => (
        <div>
          <div className="mono">{row.itemNumber}</div>
          <div className="subtle truncate" style={{ maxWidth: 200 }}>
            {row.name}
          </div>
        </div>
      ),
      value: (row) => row.itemNumber,
    },
    // Comme sur la vue Écarts : montrées dans la cellule de l'article, et
    // filtrables sans prendre de colonne.
    { key: 'name', label: 'Désignation', filterOnly: true, filter: 'text',
      value: (row) => row.name },
    { key: 'manufacturedProduct', label: 'Produit fabriqué', filterOnly: true,
      filter: 'choice', choiceSingle: true, value: (row) => row.manufacturedProduct },
    // Le terme de comparaison, à gauche de ce qu'on lui oppose. Affecter une
    // cause sans voir ce que l'ERP annonçait revient à juger un écart sur sa
    // seule valeur : « −40 » ne se raconte pas de la même façon selon qu'il
    // manque 40 pièces sur 45 ou sur 4 000.
    {
      key: 'bookQty',
      label: 'Stock ERP',
      numeric: true,
      width: 120,
      render: (row) => (
        <DrillCell
          disabled={row.bookQty === 0}
          onOpen={() => setDrill({ itemNumber: row.itemNumber, aspect: 'book' })}
        >
          <span className="num">{qty(row.bookQty)}</span>
        </DrillCell>
      ),
      value: (row) => row.bookQty,
    },
    {
      key: 'countedQty',
      label: 'Compté',
      numeric: true,
      width: 120,
      render: (row) => (
        <DrillCell
          disabled={row.countedQty === 0}
          onOpen={() => setDrill({ itemNumber: row.itemNumber, aspect: 'counted' })}
        >
          <span className="num">{qty(row.countedQty)}</span>
        </DrillCell>
      ),
      value: (row) => row.countedQty,
    },
    {
      key: 'physicalQty',
      label: 'Physique',
      numeric: true,
      width: 120,
      render: (row) => (
        <DrillCell
          disabled={row.physicalQty === 0}
          onOpen={() => setDrill({ itemNumber: row.itemNumber, aspect: 'physical' })}
        >
          <span className="num">{qty(row.physicalQty)}</span>
        </DrillCell>
      ),
      value: (row) => row.physicalQty,
    },
    {
      key: 'varianceValue',
      label: 'Écart',
      numeric: true,
      width: 140,
      render: (row) => (
        <DrillCell
          disabled={row.varianceQty === 0}
          onOpen={() => setDrill({ itemNumber: row.itemNumber, aspect: 'variance' })}
        >
          <span className={`num ${signClass(row.varianceValue)}`}>
            {signedMoney(row.varianceValue)}
          </span>
        </DrillCell>
      ),
      value: (row) => row.varianceValue,
    },
    {
      key: 'causeCode',
      label: 'Cause retenue',
      width: 250,
      filter: 'choice',
      render: (row) => (
        <select
          className="select"
          value={row.causeCode ?? ''}
          disabled={!editable || save.isPending}
          onChange={(event) =>
            save.mutate({
              itemNumber: row.itemNumber,
              causeCode: event.target.value || null,
              comment: row.comment,
            })
          }
        >
          <option value="">— non affectée —</option>
          {causeList.map((cause) => (
            <option key={cause.code} value={cause.code}>
              {cause.code} — {cause.label}
            </option>
          ))}
        </select>
      ),
      value: (row) => row.causeCode ?? '',
    },
    {
      key: 'comment',
      label: 'Commentaire',
      width: 260,
      filter: 'text',
      render: (row) => (
        <input
          className="input"
          /* Non contrôlé, et remonté quand la valeur du serveur change.
             Contrôlé, chaque frappe rerendrait la grille entière ; non
             contrôlé sans clé, le champ garderait à l'écran ce qu'on y a tapé
             même après qu'une affectation en lot l'a remplacé. */
          key={`${row.itemNumber}:${row.comment}`}
          defaultValue={row.comment}
          disabled={!editable}
          placeholder="Ce qui a été constaté…"
          title={row.comment}
          /* Entrée enregistre sans quitter le clavier : on descend la colonne
             ligne à ligne, et aller chercher la souris entre deux saisies est
             ce qui fait qu'on n'en saisit qu'une. */
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.currentTarget.blur()
          }}
          onBlur={(event) => {
            const comment = event.target.value
            // Sortir d'un champ sans l'avoir touché n'est pas une écriture.
            // Sans ce garde, parcourir la grille au clavier réécrirait chaque
            // ligne et signerait chacune au journal d'audit.
            if (comment === row.comment) return
            save.mutate({
              itemNumber: row.itemNumber,
              causeCode: row.causeCode ?? null,
              comment,
            })
          }}
        />
      ),
      value: (row) => row.comment,
    },
    {
      key: 'aiSuggestedCause',
      label: 'Proposition IA',
      width: 260,
      filter: 'choice',
      render: (row) =>
        row.aiSuggestedCause ? (
          <div className="stack" style={{ gap: 'var(--space-1)' }}>
            <span className="row" style={{ gap: 'var(--space-2)' }}>
              <Badge tone="accent">
                {row.aiSuggestedCause}
                {row.aiConfidence !== null && ` · ${Math.round(row.aiConfidence * 100)} %`}
              </Badge>
              {editable && row.causeCode !== row.aiSuggestedCause && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    save.mutate({
                      itemNumber: row.itemNumber,
                      causeCode: row.aiSuggestedCause,
                      comment: row.comment,
                    })
                  }
                >
                  Accepter
                </Button>
              )}
            </span>
            {/* La justification vient du modèle comme le reste : elle passe
                par le même rendu, qui peint des nœuds React et jamais du HTML.
                La même chaîne sert aussi d'infobulle sur la vue Écarts, et
                celle-ci reste du texte nu — un attribut `title` ne se met pas
                en forme, ce n'est pas un oubli. */}
            {row.aiRationale && (
              <span className="subtle">
                <Markdown text={row.aiRationale} />
              </span>
            )}
          </div>
        ) : (
          <span className="subtle">—</span>
        ),
      value: (row) => row.aiSuggestedCause ?? '',
    },
  ]

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
                  causes={causeList}
                  shown={rows.length}
                  total={all.length}
                />
                <DataGrid
                  columns={columns}
                  rows={rows}
                  exportTitle="Causes"
                  campaignId={campaignId}
                  getRowId={(row) => row.itemNumber}
                  selectable={editable}
                  selected={selected}
                  onSelectedChange={setSelected}
                  toolbar={
                    selected.size > 0 ? (
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<Icons.clipboard size={13} />}
                        onClick={() =>
                          setAssigning(rows.filter((row) => selected.has(row.itemNumber)))
                        }
                      >
                        Affecter une cause
                      </Button>
                    ) : null
                  }
                  searchPlaceholder="Filtrer par article, désignation, commentaire…"
                  maxHeight={620}
                  initialSort={{ key: 'varianceValue', direction: 'asc' }}
                />
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
      {assigning && (
        <CauseDialog
          count={assigning.length}
          initialCause={assigning.length === 1 ? assigning[0]!.causeCode : null}
          initialComment={assigning.length === 1 ? assigning[0]!.comment : ''}
          causes={causeList}
          title={
            assigning.length === 1
              ? `Cause de l’écart — ${assigning[0]!.itemNumber}`
              : `Cause pour ${assigning.length} écarts`
          }
          pending={assign.isPending}
          onSubmit={(cause, comment) =>
            assign.mutate({
              items: assigning.map((row) => row.itemNumber),
              cause,
              comment,
            })
          }
          onClose={() => setAssigning(null)}
        />
      )}
    </div>
  )
}
