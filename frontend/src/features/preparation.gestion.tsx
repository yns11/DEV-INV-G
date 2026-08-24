/** Qui compte quoi, et à partir de quel montant un écart compte. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Manager, Overview, Threshold } from '../lib/types'
import { ITEM_TYPE_LABELS, moneyShort, percent } from '../lib/format'
import { ZonesAdminGrid } from './zones'
import { Alert, AsyncBoundary, Badge, Button, Card, Skeleton, useErrorToast, useToast } from '../components/ui'

// --------------------------------------------------------------------------- //
// Paramètres
// --------------------------------------------------------------------------- //

/**
 * « Accepter des formules dans les comptages ».
 *
 * Devant trois palettes de quarante-huit et un fond de bac de sept, un compteur
 * écrit `3*48+7` — et c'est la bonne façon de compter : le calcul reste devant
 * les yeux de qui relira, ce qu'un « 151 » nu ne permet plus. L'application ne
 * savait lire qu'un nombre : la saisie refusait la ligne, le scan rendait une
 * case vide sur une feuille pourtant ni vierge ni douteuse.
 *
 * Réglage, et non comportement d'office : une usine qui veut que ses feuilles
 * portent un nombre et un seul a raison de l'exiger. Ce qui ne se défendait
 * pas, c'est que le refus parlait d'une quantité illisible sans jamais dire
 * qu'un réglage existait.
 *
 * Ouvert plus longtemps que les seuils, et pour une raison précise : les seuils
 * gèlent à l'entrée en comptage parce qu'ils décident ce qui sera signalé comme
 * exception ; celui-ci décide seulement de ce qu'un champ accepte, et le besoin
 * apparaît le jour de l'inventaire.
 */
function FormulaSetting({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const editable = overview.permissions.settings
  const allowed = overview.campaign.config.allow_formulas

  const save = useMutation({
    mutationFn: (allowFormulas: boolean) =>
      api.saveSettings(campaignId, { allowFormulas }),
    onSuccess: (_data, allowFormulas) => {
      void queryClient.invalidateQueries()
      toast.success(
        allowFormulas
          ? 'Les formules sont acceptées dans les comptages'
          : 'Les formules ne sont plus acceptées',
      )
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  return (
    <Card
      title="Accepter des formules dans les comptages"
      message="Une quantité peut s’écrire comme l’opération qui la produit. Elle est évaluée comme dans un tableur, et le texte d’origine est conservé à côté du résultat."
    >
      <div className="stack" style={{ gap: 'var(--space-3)' }}>
        <label className="row" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={allowed}
            disabled={!editable || save.isPending}
            onChange={(e) => save.mutate(e.target.checked)}
          />
          <span>
            <strong>{allowed ? 'Activé' : 'Désactivé'}</strong> —{' '}
            {allowed
              ? 'les opérations saisies ou lues sur un scan sont calculées.'
              : 'seuls des nombres sont acceptés ; une opération est refusée.'}
          </span>
        </label>

        <div className="subtle">
          Accepté : <code>3*48+7</code>, <code>=(10+2)/4</code>,{' '}
          <code>2,5*4</code>, <code>1 200 + 30</code>. Les quatre opérations, les
          parenthèses et le signe moins — rien d’autre.
        </div>

        {!editable && (
          <Alert tone="info" title="Réglage gelé">
            Les comptages de cette campagne sont terminés : ce réglage ne change
            plus ce qu’ils contiennent.
          </Alert>
        )}
      </div>
    </Card>
  )
}

// --------------------------------------------------------------------------- //
// Thresholds
// --------------------------------------------------------------------------- //

/**
 * Les paramètres de la campagne : ce qu'elle accepte, puis à partir de quand
 * un écart compte.
 *
 * L'onglet s'appelait « Seuils » et ne portait qu'eux. Le premier réglage venu
 * qui n'était pas un seuil n'avait donc aucun endroit où aller — et un onglet
 * nommé d'après son unique contenu ne peut pas en accueillir un second sans
 * mentir sur ce qu'il contient.
 *
 * L'ordre n'est pas neutre. Les formules décident de ce qu'un champ de saisie
 * accepte, et la question se pose le jour de l'inventaire, devant la première
 * feuille qui porte « 3*48+7 ». Les seuils décident de ce qui sera signalé à
 * l'analyse, trois semaines plus tard. Le plus urgent est en tête.
 */
export function SettingsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Threshold[] | null>(null)

  const query = useQuery({
    queryKey: ['thresholds', campaignId],
    queryFn: () => api.thresholds(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Threshold[]) =>
      api.saveThresholds(
        campaignId,
        rows.map((row) => ({
          itemType: row.item_type,
          valueAbsEur: Number(row.value_abs_eur),
          qtyRelative: row.qty_relative === null ? null : Number(row.qty_relative),
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['thresholds', campaignId] })
      setDraft(null)
      toast.success('Seuils enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: keyof Threshold, value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value === '' ? null : value }
    setDraft(next)
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
    <FormulaSetting campaignId={campaignId} overview={overview} />
    <Card
      title="Seuils de matérialité"
      message="Un écart est « matériel » lorsqu’il franchit toutes les barrières de son type, jamais une seule."
      actions={
        editable && draft ? (
          <>
            <Button variant="ghost" onClick={() => setDraft(null)}>
              Annuler
            </Button>
            <Button
              variant="primary"
              disabled={save.isPending}
              onClick={() => save.mutate(rows)}
            >
              Enregistrer
            </Button>
          </>
        ) : null
      }
      flush
    >
      {!editable && (
        <div style={{ padding: 'var(--space-4)' }}>
          <Alert tone="info" title="Seuils gelés">
            Figés au passage en comptage, pour que les exceptions signalées restent
            les mêmes jusqu’à l’analyse.
          </Alert>
        </div>
      )}

      <AsyncBoundary query={query} isEmpty={() => false}>
        {() => (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 160 }}>Type d’article</th>
                  <th className="num" title="Écart en valeur absolue au-delà duquel la ligne est une exception">
                    Valeur absolue (€)
                  </th>
                  <th className="num" title="|Δqté| / qté ERP au-delà duquel la ligne est une exception">
                    Écart relatif
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={row.item_type}>
                    <td>
                      <strong>{ITEM_TYPE_LABELS[row.item_type] ?? row.item_type}</strong>
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={String(row.value_abs_eur ?? '')}
                          onChange={(e) => update(index, 'value_abs_eur', e.target.value)}
                        />
                      ) : (
                        moneyShort(Number(row.value_abs_eur))
                      )}
                    </td>
                    <td className="editable num">
                      {editable ? (
                        <input
                          className="num"
                          inputMode="decimal"
                          value={row.qty_relative === null ? '' : String(row.qty_relative)}
                          placeholder="désactivé"
                          onChange={(e) => update(index, 'qty_relative', e.target.value)}
                        />
                      ) : row.qty_relative === null ? (
                        <span className="subtle">désactivé</span>
                      ) : (
                        percent(Number(row.qty_relative), 2)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </AsyncBoundary>
    </Card>
    </div>
  )
}

// --------------------------------------------------------------------------- //
// Managers and perimeters
// --------------------------------------------------------------------------- //

/**
 * Les postes de gestionnaire, tels que le serveur les énumère.
 *
 * The identity column is the load-bearing one: it is what lets the server
 * answer "who is asking?" when a screen requests `focus=true`, so the browser
 * never has to name a manager — and never receives what the filter excluded.
 */
export function ManagersTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [draft, setDraft] = useState<Manager[] | null>(null)

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const save = useMutation({
    mutationFn: (rows: Manager[]) =>
      api.saveManagers(
        campaignId,
        rows.map((row, index) => ({
          code: row.code,
          label: row.label,
          actor: row.actor,
          active: row.active,
          displayOrder: index,
        })),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      setDraft(null)
      toast.success('Gestionnaires enregistrés')
    },
    onError: (error) => showError(error, 'Enregistrement impossible'),
  })

  const rows = draft ?? query.data?.managers ?? []
  const editable = overview.permissions.thresholds

  const update = (index: number, key: 'label' | 'actor', value: string) => {
    const next = [...rows]
    const target = next[index]
    if (!target) return
    next[index] = { ...target, [key]: value }
    setDraft(next)
  }

  return (
    <div className="stack">
      <Alert tone="info" title="Un périmètre, pas une habilitation">
        Une affectation ne restreint aucune action : c’est le filtre « Mon périmètre ».
        Chacun garde le droit d’agir partout.
      </Alert>

      <Card
        title="Gestionnaires de la campagne"
        message="L’identité est celle transmise par l’authentification (votre adresse e-mail). C’est elle qui résout « Mon périmètre » côté serveur."
        actions={
          editable && draft ? (
            <>
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Annuler
              </Button>
              <Button
                variant="primary"
                disabled={save.isPending}
                onClick={() => save.mutate(rows)}
              >
                Enregistrer
              </Button>
            </>
          ) : null
        }
        flush
      >
        {!editable && (
          <div style={{ padding: 'var(--space-4)' }}>
            <Alert tone="info" title="Gestionnaires gelés">
              Figés au passage en comptage, comme le reste de la configuration.
            </Alert>
          </div>
        )}
        <AsyncBoundary query={query} skeleton={<Skeleton height={220} />}>
          {() => (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 160 }}>Poste</th>
                    <th>Libellé</th>
                    <th>Identité (e-mail)</th>
                    <th className="num" style={{ width: 110 }}>Entrepôts</th>
                    <th className="num" style={{ width: 100 }}>Journaux</th>
                    <th className="num" style={{ width: 90 }}>Zones</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.code}>
                      <td className="mono subtle">{row.code}</td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.label}
                            placeholder="Nom du poste"
                            onChange={(e) => update(index, 'label', e.target.value)}
                          />
                        ) : (
                          row.label || <span className="subtle">—</span>
                        )}
                      </td>
                      <td className="editable">
                        {editable ? (
                          <input
                            value={row.actor}
                            inputMode="email"
                            placeholder="prenom.nom@exemple.fr"
                            onChange={(e) => update(index, 'actor', e.target.value)}
                          />
                        ) : row.actor ? (
                          <span className="mono">{row.actor}</span>
                        ) : (
                          <span className="subtle">poste inoccupé</span>
                        )}
                      </td>
                      <td className="num">
                        {
                          (query.data?.warehouses ?? []).filter(
                            (w) => w.managerCode === row.code,
                          ).length
                        }
                      </td>
                      <td className="num">{row.journalCount}</td>
                      <td className="num">{row.zoneCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

/** Warehouses — and therefore their counting journals — assigned to a manager. */
export function JournalScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()

  const query = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  const assign = useMutation({
    mutationFn: (input: { warehouseId: string; managerCode: string }) =>
      api.assignWarehouses(campaignId, [input]),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Affectation enregistrée')
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const editable = overview.permissions.thresholds
  const managers = query.data?.managers ?? []

  return (
    <div className="stack">
      <Alert tone="info" title="Un journal suit son entrepôt">
        Un journal suit son entrepôt. La ligne <strong>AUTRES</strong> rattache d’un
        coup tous les entrepôts sans affectation explicite — sans elle, un entrepôt
        découvert par un import tomberait hors de tout périmètre.
      </Alert>

      <Card title="Affectation des entrepôts" flush>
        <AsyncBoundary query={query} skeleton={<Skeleton height={240} />}>
          {(data) => (
            <div className="table-wrap" style={{ maxHeight: 560 }}>
              <table className="data">
                <thead>
                  <tr>
                    <th style={{ width: 200 }}>Entrepôt</th>
                    <th className="num" style={{ width: 130 }}>Journaux</th>
                    <th style={{ width: 260 }}>Gestionnaire</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.warehouses.map((warehouse) => (
                    <tr key={warehouse.warehouseId}>
                      <td>
                        <span className="mono">{warehouse.warehouseId}</span>
                        {warehouse.isCatchAll && (
                          <>
                            {' '}
                            <Badge tone="info">fourre-tout</Badge>
                          </>
                        )}
                      </td>
                      <td className="num">
                        {warehouse.isCatchAll ? (
                          <span className="subtle">—</span>
                        ) : (
                          warehouse.journalCount
                        )}
                      </td>
                      <td className="editable">
                        <select
                          className="input"
                          disabled={!editable || assign.isPending}
                          value={warehouse.managerCode}
                          onChange={(event) =>
                            assign.mutate({
                              warehouseId: warehouse.warehouseId,
                              managerCode: event.target.value,
                            })
                          }
                        >
                          <option value="">— aucun —</option>
                          {managers.map((manager) => (
                            <option key={manager.code} value={manager.code}>
                              {manager.label || manager.code}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="subtle">
                        {!warehouse.known && !warehouse.isCatchAll
                          ? 'aucun journal pour l’instant'
                          : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

/** GENERIQUE zones assigned to a manager, in bulk over a selection. */
export function ZoneScopeTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const managers = useQuery({
    queryKey: ['managers', campaignId],
    queryFn: () => api.managers(campaignId),
  })

  return (
    <div className="stack">
      <Alert tone="info" title="Rattacher les zones à leur gestionnaire">
        Sélectionnez des zones, puis choisissez un gestionnaire dans la barre d’outils.
      </Alert>

      <ZonesAdminGrid
        campaignId={campaignId}
        editable={overview.permissions.zones}
        managers={managers.data?.managers ?? []}
      />
    </div>
  )
}
