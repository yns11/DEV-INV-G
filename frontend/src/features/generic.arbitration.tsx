/** Les écarts entre deux comptages, et la décision qui les tranche. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Arbitration, Overview } from '../lib/types'
import { SECTION_LABELS, moneyShort, qty, signedNum } from '../lib/format'
import { useFocusMode } from '../lib/focus'
import { DataGrid, type Column } from '../components/DataGrid'
import { Alert, Badge, Button, Card, useErrorToast, useToast } from '../components/ui'

// --------------------------------------------------------------------------- //
// Arbitration
// --------------------------------------------------------------------------- //

/**
 * La quantité que chaque ligne affiche, tapée ou remplie.
 *
 * Elle vit **ici**, au-dessus du tableau, et non dans chaque ligne. C'est ce
 * qui permet à « Tout le n°1 » de remplir les quarante champs sans rien
 * enregistrer, et à « Valider tout » de poster exactement ce que l'utilisateur
 * a sous les yeux. Tant que chaque ligne gardait sa propre saisie, le bouton de
 * lot ne pouvait que redemander au serveur ce qu'il pensait, lui, être la bonne
 * quantité — et le serveur ne voyait pas les champs.
 */
type Draft = Record<string, string>

const numeric = (value: string): number | null => {
  const cleaned = value.trim().replace(',', '.')
  if (cleaned === '') return null
  const parsed = Number(cleaned)
  return Number.isNaN(parsed) ? null : parsed
}

/** La quantité proposée d'office : le second comptage, le plus tardif. */
const suggested = (row: Arbitration): string =>
  String(Number(row.qty_arbitrated ?? row.qty_pass_2 ?? 0))

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
  const [draft, setDraft] = useState<Draft>({})

  const [focus] = useFocusMode()
  const zones = useQuery({
    queryKey: ['zones', campaignId, focus],
    queryFn: () => api.zones(campaignId, focus ? { focus: true } : {}),
  })
  // `divergentOnly` : une ligne sur laquelle les deux équipes s'accordent
  // n'appelle aucune décision, et sur une zone de quatre cents références elle
  // enterrait les neuf qui en appellent une.
  const query = useQuery({
    queryKey: ['arbitrations', campaignId, zoneFilter],
    queryFn: () => api.arbitrations(campaignId, zoneFilter || undefined, true),
  })

  const rows = useMemo(() => query.data ?? [], [query.data])
  const pending = useMemo(() => rows.filter((row) => row.needsDecision), [rows])
  const editable = overview.permissions.countSheets

  const afterWrite = () => {
    setDraft({})
    void queryClient.invalidateQueries()
  }

  const decide = useMutation({
    mutationFn: ({ id, qty }: { id: string; qty: number }) =>
      api.decideArbitration(campaignId, id, qty),
    onSuccess: () => {
      afterWrite()
      toast.success('Arbitrage enregistré')
    },
    onError: (error) => showError(error, 'Arbitrage impossible'),
  })

  const decideAll = useMutation({
    mutationFn: (decisions: { id: string; qty: number }[]) =>
      api.decideArbitrations(campaignId, zoneFilter, decisions),
    onSuccess: (result) => {
      afterWrite()
      toast.success(
        `${result.decided} écart(s) tranché(s)`,
        result.skipped
          ? `${result.skipped} ligne(s) laissée(s) ouverte(s) : aucune quantité à retenir.`
          : undefined,
      )
    },
    onError: (error) => showError(error, 'Arbitrage en lot impossible'),
  })

  /** Remplir les champs, sans rien enregistrer. */
  const fillAll = (pick: (row: Arbitration) => number | null) => {
    setDraft((current) => {
      const next = { ...current }
      for (const row of pending) {
        const value = pick(row)
        next[row.id] = value === null ? '' : String(Number(value))
      }
      return next
    })
  }

  const valueOf = (row: Arbitration): string =>
    draft[row.id] ?? suggested(row)

  const submitAll = () => {
    const decisions: { id: string; qty: number }[] = []
    for (const row of pending) {
      const parsed = numeric(valueOf(row))
      if (parsed !== null && parsed >= 0) decisions.push({ id: row.id, qty: parsed })
    }
    decideAll.mutate(decisions)
  }

  const columns: Column<Arbitration>[] = [
    {
      key: 'zoneCode',
      label: 'Zone',
      width: 130,
      filter: 'choice',
      render: (row) => <span className="mono">{row.zoneCode}</span>,
    },
    {
      key: 'item_number',
      label: 'Article',
      width: 190,
      filter: 'text',
      render: (row) => (
        <>
          <div className="mono">{row.item_number}</div>
          <div className="subtle truncate" style={{ maxWidth: 190 }}>{row.name}</div>
        </>
      ),
    },
    {
      key: 'section',
      label: 'Section',
      width: 140,
      filter: 'choice',
      choiceLabel: (value) => SECTION_LABELS[value as never] ?? value,
      value: (row) => row.section,
      render: (row) => (
        <Badge tone={row.section === 'WIP' ? 'warning' : 'neutral'}>
          {SECTION_LABELS[row.section] ?? row.section}
        </Badge>
      ),
    },
    {
      key: 'qty_pass_1',
      label: 'Comptage n°1',
      width: 130,
      numeric: true,
      filter: 'range',
      // Une référence absente d'un passage y vaut **zéro**, pas « inconnu » :
      // les deux passages portent le même document. Le serveur le dit déjà ;
      // le tiret n'a donc plus lieu d'être.
      render: (row) => qty(row.qty_pass_1 ?? 0),
    },
    {
      key: 'qty_pass_2',
      label: 'Comptage n°2',
      width: 130,
      numeric: true,
      filter: 'range',
      render: (row) => qty(row.qty_pass_2 ?? 0),
    },
    {
      key: 'gap',
      label: 'Écart',
      width: 110,
      numeric: true,
      filter: 'range',
      render: (row) => (
        <span className={row.gap === 0 ? 'neutral' : row.gap > 0 ? 'pos' : 'neg'}>
          {signedNum(row.gap)}
        </span>
      ),
    },
    {
      key: 'gapValue',
      label: 'Impact',
      width: 110,
      numeric: true,
      filter: 'range',
      render: (row) => moneyShort(row.gapValue),
    },
    {
      key: 'decision',
      label: 'Quantité retenue',
      width: 300,
      sortable: false,
      filter: false,
      sticky: 'right',
      value: (row) => (row.needsDecision ? valueOf(row) : row.qty_arbitrated),
      render: (row) =>
        row.needsDecision && editable ? (
          <div className="row" style={{ gap: 'var(--space-1)' }}>
            <input
              className="input num"
              style={{ width: 92, padding: '4px 8px' }}
              inputMode="decimal"
              value={valueOf(row)}
              onChange={(event) =>
                setDraft((d) => ({ ...d, [row.id]: event.target.value }))
              }
              aria-label={`Quantité arbitrée ${row.item_number}`}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setDraft((d) => ({ ...d, [row.id]: String(Number(row.qty_pass_1 ?? 0)) }))
              }
              title="Reprendre le comptage n°1"
            >
              n°1
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setDraft((d) => ({ ...d, [row.id]: String(Number(row.qty_pass_2 ?? 0)) }))
              }
              title="Reprendre le comptage n°2"
            >
              n°2
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={(numeric(valueOf(row)) ?? -1) < 0 || decide.isPending}
              onClick={() =>
                decide.mutate({ id: row.id, qty: numeric(valueOf(row)) ?? 0 })
              }
            >
              Valider
            </Button>
          </div>
        ) : row.qty_arbitrated !== null ? (
          <span className="subtle">
            <strong>{qty(row.qty_arbitrated)}</strong> · {row.decided_by ?? ''}
          </span>
        ) : (
          <Badge tone="success">accord</Badge>
        ),
    },
  ]

  return (
    <div className="stack">
      <div className="chips">
        <button
          className={`chip${zoneFilter === '' ? ' chip--active' : ''}`}
          onClick={() => { setZoneFilter(''); setDraft({}) }}
        >
          Toutes les zones
        </button>
        {(zones.data ?? [])
          .filter((zone) => zone.pendingArbitrations > 0 || zone.id === zoneFilter)
          .map((zone) => (
            <button
              key={zone.id}
              className={`chip${zoneFilter === zone.id ? ' chip--active' : ''}`}
              onClick={() => { setZoneFilter(zone.id); setDraft({}) }}
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
              {/* Ces deux-là **remplissent**, ils ne valident pas. La règle se
                  décide une fois — « le second comptage fait foi, la première
                  équipe comptait sous la pluie » — et les quarante quantités
                  qui en découlent se posent dans les champs, où on les relit. */}
              <Button
                size="sm"
                onClick={() => fillAll((row) => row.qty_pass_1 ?? 0)}
                title="Poser le comptage n°1 dans chaque champ, sans valider"
              >
                Tout le n°1
              </Button>
              <Button
                size="sm"
                onClick={() => fillAll((row) => row.qty_pass_2 ?? 0)}
                title="Poser le comptage n°2 dans chaque champ, sans valider"
              >
                Tout le n°2
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={decideAll.isPending}
                onClick={submitAll}
                title="Valider les quantités affichées"
              >
                Valider tout
              </Button>
            </span>
          }
        >
          Le comptage n°2 est le plus tardif et le mieux informé : c’est lui qui
          est proposé d’office dans chaque champ. « Tout le n°1 » et « Tout le
          n°2 » <strong>remplissent seulement</strong> — relisez, corrigez si
          besoin, puis validez, une par une ou d’un coup avec « Valider tout »,
          qui enregistre les quantités affichées. Tant qu’une ligne n’est pas
          validée, la consolidation l’ignore.
        </Alert>
      )}

      <Card
        title="Écarts entre comptage n°1 et n°2"
        message="Seuls les désaccords sont listés, décisions requises d’abord puis par impact en euros."
        flush
      >
        <DataGrid<Arbitration>
          rows={rows}
          columns={columns}
          getRowId={(row) => row.id}
          maxHeight={620}
          emptyTitle="Aucun écart entre les deux comptages"
          emptyBody="Les deux équipes ont trouvé les mêmes quantités."
          exportTitle="Arbitrages"
          campaignId={campaignId}
        />
      </Card>
    </div>
  )
}

export const __test__ = { numeric, suggested }
