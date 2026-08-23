/** Les causes assignées aux écarts, et la répartition qui en découle. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { AssignableCause, Overview } from '../lib/types'
import { moneyShort, qty, percent, signClass, signedMoney } from '../lib/format'
import { CompositionBar } from '../components/charts'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { AsyncBoundary, Badge, Button, Card, Icons, Skeleton, useErrorToast, useToast } from '../components/ui'

export const BUCKET_LABELS: Record<string, string> = {
  multiplesOf10: 'Multiples de 10',
  multiplesOf50: 'Multiples de 50',
  multiplesOf100: 'Multiples de 100',
  endingIn5: 'Terminant par 5',
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
          {(rows) => (
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
          )}
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
