/** Les écarts entre deux comptages, et la décision qui les tranche. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Arbitration, Overview } from '../lib/types'
import { SECTION_LABELS, moneyShort, qty, signedNum } from '../lib/format'
import { useFocusMode } from '../lib/focus'
import { Alert, AsyncBoundary, Badge, Button, Card, EmptyState, Icons, useErrorToast, useToast } from '../components/ui'

// --------------------------------------------------------------------------- //
// Arbitration
// --------------------------------------------------------------------------- //

export function ArbitrationTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [zoneFilter, setZoneFilter] = useState<string>('')

  const [focus] = useFocusMode()
  const zones = useQuery({
    queryKey: ['zones', campaignId, focus],
    queryFn: () => api.zones(campaignId, focus ? { focus: true } : {}),
  })
  const query = useQuery({
    queryKey: ['arbitrations', campaignId, zoneFilter],
    queryFn: () => api.arbitrations(campaignId, zoneFilter || undefined),
  })

  const decide = useMutation({
    mutationFn: ({ id, qty }: { id: string; qty: number }) =>
      api.decideArbitration(campaignId, id, qty),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Arbitrage enregistré')
    },
    onError: (error) => showError(error, 'Arbitrage impossible'),
  })

  const decideAll = useMutation({
    mutationFn: ({ zoneId, choice }: {
      zoneId: string; choice: 'PASS_1' | 'PASS_2' | 'PROPOSED'
    }) => api.decideArbitrations(campaignId, zoneId, choice),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.decided} écart(s) tranché(s)`,
        result.skipped
          ? `${result.skipped} ligne(s) laissée(s) ouverte(s) : aucune quantité à retenir.`
          : undefined,
      )
    },
    onError: (error) => showError(error, 'Arbitrage en lot impossible'),
  })

  const prefillAll = useMutation({
    mutationFn: (zoneId: string) => api.prefillWithPass2(campaignId, zoneId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      toast.success(
        `${result.proposed} quantité(s) pré-remplie(s)`,
        'Rien n’est validé : relisez chaque ligne, corrigez si besoin, puis validez.',
      )
    },
    onError: (error) => showError(error, 'Pré-remplissage impossible'),
  })

  const rows = query.data ?? []
  const pending = rows.filter((row) => row.needsDecision)
  const editable = overview.permissions.countSheets

  return (
    <div className="stack">
      <div className="chips">
        <button
          className={`chip${zoneFilter === '' ? ' chip--active' : ''}`}
          onClick={() => setZoneFilter('')}
        >
          Toutes les zones
        </button>
        {(zones.data ?? [])
          .filter((zone) => zone.pendingArbitrations > 0 || zone.id === zoneFilter)
          .map((zone) => (
            <button
              key={zone.id}
              className={`chip${zoneFilter === zone.id ? ' chip--active' : ''}`}
              onClick={() => setZoneFilter(zone.id)}
            >
              {zone.code}
              {zone.pendingArbitrations > 0 && (
                <span className="num">{zone.pendingArbitrations}</span>
              )}
            </button>
          ))}
      </div>

      {zoneFilter && editable && pending.length > 0 && (
        <Alert
          tone="warning"
          title={`${pending.length} écart(s) à arbitrer sur cette zone`}
          actions={
            <span className="row-wrap" style={{ gap: 'var(--space-2)' }}>
              <Button
                size="sm"
                disabled={prefillAll.isPending}
                onClick={() => prefillAll.mutate(zoneFilter)}
              >
                Pré-remplir avec le n°2
              </Button>
              {/* La règle se décide une fois, pas quarante. Quand on sait
                  laquelle des deux équipes a compté dans de bonnes conditions,
                  la répéter ligne à ligne n'ajoute aucun jugement — juste des
                  occasions de se tromper de champ. */}
              <Button
                size="sm"
                disabled={decideAll.isPending}
                onClick={() =>
                  decideAll.mutate({ zoneId: zoneFilter, choice: 'PASS_1' })
                }
                title="Retenir partout le comptage n°1"
              >
                Tout le n°1
              </Button>
              <Button
                size="sm"
                disabled={decideAll.isPending}
                onClick={() =>
                  decideAll.mutate({ zoneId: zoneFilter, choice: 'PASS_2' })
                }
                title="Retenir partout le comptage n°2"
              >
                Tout le n°2
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={decideAll.isPending}
                onClick={() =>
                  decideAll.mutate({ zoneId: zoneFilter, choice: 'PROPOSED' })
                }
                title="Valider les quantités déjà proposées"
              >
                Valider tout
              </Button>
            </span>
          }
        >
          Le comptage n°2 est le plus tardif et le mieux informé, donc c’est le
          point de départ raisonnable. Le pré-remplissage <strong>ne valide
          rien</strong> : il pose la quantité dans le champ, vous la relisez, la
          corrigez si besoin, puis vous validez — une par une, ou toutes d’un
          coup avec « Valider tout ». Tant qu’une ligne n’est pas validée, la
          consolidation l’ignore.
        </Alert>
      )}

      <Card
        title="Écarts entre comptage n°1 et n°2"
        message="Triés par décision requise puis par impact en euros : le désaccord le plus coûteux d’abord."
        flush
      >
        <AsyncBoundary
          query={query}
          isEmpty={(list) => list.length === 0}
          empty={
            <EmptyState title="Aucun écart entre les deux comptages" icon={<Icons.check size={20} />}>
              Les deux équipes ont trouvé les mêmes quantités.
            </EmptyState>
          }
        >
          {(list) => <ArbitrationTable rows={list} editable={editable} onDecide={decide.mutate} />}
        </AsyncBoundary>
      </Card>
    </div>
  )
}

function ArbitrationTable({
  rows,
  editable,
  onDecide,
}: {
  rows: Arbitration[]
  editable: boolean
  onDecide: (input: { id: string; qty: number }) => void
}) {
  return (
    <div className="table-wrap" style={{ maxHeight: 620 }}>
      <table className="data">
        <thead>
          <tr>
            <th>Article</th>
            <th>Section</th>
            <th className="num">Comptage n°1</th>
            <th className="num">Comptage n°2</th>
            <th className="num">Écart</th>
            <th className="num">Impact</th>
            <th className="num" style={{ width: 150 }}>Quantité retenue</th>
            <th style={{ width: 190 }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>
                <div className="mono">{row.item_number}</div>
                <div className="subtle truncate" style={{ maxWidth: 200 }}>
                  {row.name}
                </div>
              </td>
              <td>
                <Badge tone={row.section === 'WIP' ? 'warning' : 'neutral'}>
                  {SECTION_LABELS[row.section] ?? row.section}
                </Badge>
              </td>
              <td className="num">{row.qty_pass_1 === null ? '—' : qty(row.qty_pass_1)}</td>
              <td className="num">{row.qty_pass_2 === null ? '—' : qty(row.qty_pass_2)}</td>
              <td className={`num ${row.gap === 0 ? 'neutral' : row.gap > 0 ? 'pos' : 'neg'}`}>
                {signedNum(row.gap)}
              </td>
              <td className="num">{moneyShort(row.gapValue)}</td>
              <td className="num">
                {row.qty_arbitrated === null ? (
                  <span className="subtle">à décider</span>
                ) : row.isProposed ? (
                  <span className="subtle" title="Pré-rempli, pas encore validé">
                    {qty(row.qty_arbitrated)} · proposé
                  </span>
                ) : (
                  <strong>{qty(row.qty_arbitrated)}</strong>
                )}
              </td>
              <td>
                {row.needsDecision && editable ? (
                  <ArbitrationActions row={row} onDecide={onDecide} />
                ) : row.qty_arbitrated !== null ? (
                  <span className="subtle">
                    {row.decided_by ?? ''} {row.comment && `· ${row.comment}`}
                  </span>
                ) : (
                  <Badge tone="success">accord</Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ArbitrationActions({
  row,
  onDecide,
}: {
  row: Arbitration
  onDecide: (input: { id: string; qty: number }) => void
}) {
  // A pre-filled quantity is what the user asked to see in the box; falling
  // back to pass 2 keeps the shortcut available when nothing was pre-filled.
  // Round-tripped through Number so the six stored decimals do not land in a
  // field somebody is about to read and retype.
  const initial = row.qty_arbitrated ?? row.qty_pass_2
  const [value, setValue] = useState<string>(
    initial === null || initial === undefined ? '' : String(Number(initial)),
  )
  return (
    <div className="row" style={{ gap: 'var(--space-1)' }}>
      <input
        className="input num"
        style={{ width: 92, padding: '4px 8px' }}
        inputMode="decimal"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-label="Quantité arbitrée"
      />
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setValue(String(row.qty_pass_1 ?? ''))}
        title="Reprendre le comptage n°1"
      >
        n°1
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => setValue(String(row.qty_pass_2 ?? ''))}
        title="Reprendre le comptage n°2"
      >
        n°2
      </Button>
      <Button
        size="sm"
        variant="primary"
        disabled={value.trim() === '' || Number.isNaN(Number(value.replace(',', '.')))}
        onClick={() => onDecide({ id: row.id, qty: Number(value.replace(',', '.')) })}
      >
        Valider
      </Button>
    </div>
  )
}
