/**
 * Zone administration, shared by Préparation and GENERIQUE.
 *
 * The same three actions are needed in both places for different reasons:
 * preparation builds the list of what will be counted, the counting day adds
 * the area nobody had listed. Keeping one implementation is what stops the two
 * screens from drifting into two different sets of defaults.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Manager, Zone } from '../lib/types'
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
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

export function CreateZoneModal({
  campaignId,
  managers = [],
  onClose,
}: {
  campaignId: string
  managers?: Manager[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [form, setForm] = useState({
    code: '',
    label: '',
    sector: '',
    passes: 2 as 1 | 2,
    managerCode: '',
  })

  const mutation = useMutation({
    mutationFn: () =>
      api.createZone(campaignId, {
        code: form.code,
        label: form.label,
        sector: form.sector,
        passes: form.passes,
        managerCode: form.managerCode,
        // No article list comes with this call, so the sheet is a free-entry
        // one until somebody loads one. Saying so keeps the preparation
        // controls from reporting it as an oversight.
        freeEntry: true,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success(
        'Zone créée',
        form.passes === 2
          ? 'Ses deux feuilles de comptage sont prêtes, en saisie libre.'
          : 'Sa feuille de comptage est prête, en saisie libre.',
      )
      onClose()
    },
    onError: (error) => showError(error, 'Création impossible'),
  })

  return (
    <Modal
      title="Nouvelle zone GENERIQUE"
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={!form.code.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            Créer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field label="Code de la zone" hint="Ex. FI ASSY M3.1, PICKING TRANSALLIANCE…">
          <input
            className="input"
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value })}
          />
        </Field>
        <Field label="Libellé">
          <input
            className="input"
            value={form.label}
            onChange={(event) => setForm({ ...form, label: event.target.value })}
          />
        </Field>
        <Field label="Secteur" hint="Sert au dispatch des feuilles imprimées.">
          <input
            className="input"
            value={form.sector}
            onChange={(event) => setForm({ ...form, sector: event.target.value })}
          />
        </Field>
        <Field
          label="Nombre de comptages"
          hint="Le double comptage est la règle ; le comptage unique s’assume zone par zone."
        >
          <select
            className="input"
            value={form.passes}
            onChange={(event) =>
              setForm({ ...form, passes: Number(event.target.value) as 1 | 2 })
            }
          >
            <option value={2}>2 — deux équipes indépendantes, puis arbitrage</option>
            <option value={1}>1 — un seul comptage, sans arbitrage possible</option>
          </select>
        </Field>
        {managers.length > 0 && (
          <Field label="Gestionnaire" hint="Sert au filtre « Mon périmètre ».">
            <select
              className="input"
              value={form.managerCode}
              onChange={(event) =>
                setForm({ ...form, managerCode: event.target.value })
              }
            >
              <option value="">— aucun —</option>
              {managers.map((manager) => (
                <option key={manager.code} value={manager.code}>
                  {manager.label || manager.code}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Alert tone="info" title="Feuille de saisie libre">
          Cette zone est créée sans liste d’articles pré-imprimée : le compteur écrit
          ce qu’il trouve. Chargez une liste depuis l’onglet « Feuilles de comptage »
          si elle doit être pré-remplie.
        </Alert>
      </div>
    </Modal>
  )
}

/**
 * The zones grid with its bulk actions.
 *
 * Selection-wide rather than row-by-row because that is how the work arrives:
 * "all of metrology counts once", "these twelve zones are Marie's".
 */
export function ZonesAdminGrid({
  campaignId,
  editable,
  managers = [],
  onPrint,
  onOpen,
}: {
  campaignId: string
  editable: boolean
  managers?: Manager[]
  /** Print the selected zones. Absent on screens where printing has no place. */
  onPrint?: (zones: Zone[]) => void
  /** Open one zone's article list for editing. */
  onOpen?: (zone: Zone) => void
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [creating, setCreating] = useState(false)

  // Same key shape as the GENERIQUE screen's unfiltered query, so the two
  // share a cache entry and one invalidation refreshes both. Administration is
  // deliberately never focus-filtered: you assign zones you do not yet own.
  const query = useQuery({
    queryKey: ['zones', campaignId, false],
    queryFn: () => api.zones(campaignId),
  })

  const setPasses = useMutation({
    mutationFn: (passes: 1 | 2) =>
      api.setZonePasses(campaignId, [...selected], passes),
    onSuccess: (result, passes) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} zone(s) à ${passes} comptage(s)`,
        passes === 1
          ? `${result.sheetsRemoved} feuille(s) n°2 supprimée(s).`
          : `${result.sheetsCreated} feuille(s) n°2 recréée(s), avec la même liste d’articles.`,
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
  })

  const setNegative = useMutation({
    mutationFn: (allowed: boolean) =>
      api.setZoneNegative(campaignId, [...selected], allowed),
    onSuccess: (result, allowed) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(
        `${result.updated} zone(s) mise(s) à jour`,
        allowed
          ? 'Les quantités négatives y sont désormais acceptées.'
          : 'Une quantité négative y sera de nouveau refusée à la saisie.',
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
  })

  const assign = useMutation({
    mutationFn: (managerCode: string) =>
      api.assignZones(campaignId, [...selected], managerCode),
    onSuccess: (result) => {
      void queryClient.invalidateQueries()
      setSelected(new Set())
      toast.success(`${result.updated} zone(s) affectée(s)`)
    },
    onError: (error) => showError(error, 'Affectation impossible'),
  })

  const byCode = new Map(managers.map((m) => [m.code, m]))
  const columns: Column<Zone>[] = [
    { key: 'code', label: 'Zone', width: 200 },
    { key: 'label', label: 'Libellé', width: 220 },
    { key: 'sector', label: 'Secteur', width: 150 },
    {
      key: 'passes',
      label: 'Comptages',
      numeric: true,
      width: 120,
      render: (row) => (
        <Badge tone={row.passes === 1 ? 'warning' : 'neutral'}>
          {row.passes === 1 ? '1 — unique' : '2 — double'}
        </Badge>
      ),
      value: (row) => row.passes,
    },
    {
      key: 'lines',
      label: 'Lignes pré-imprimées',
      numeric: true,
      width: 180,
      render: (row) => {
        const lines = row.sheets[0]?.lineCount ?? 0
        if (row.free_entry && lines === 0) {
          return <Badge tone="info">saisie libre</Badge>
        }
        if (lines === 0) {
          return (
            <Badge tone="warning" title="Ni liste d’articles, ni saisie libre déclarée">
              à préparer
            </Badge>
          )
        }
        return <span className="num">{lines}</span>
      },
      value: (row) => row.sheets[0]?.lineCount ?? 0,
    },
    {
      key: 'allow_negative',
      label: 'Négatifs',
      width: 120,
      render: (row) =>
        row.allow_negative ? (
          <Badge
            tone="warning"
            title="Une quantité négative est acceptée sur cette zone — feuille de correction."
          >
            autorisés
          </Badge>
        ) : (
          <span className="subtle">refusés</span>
        ),
      value: (row) => (row.allow_negative ? 1 : 0),
    },
    ...(onOpen
      ? [
          {
            key: 'open',
            label: '',
            width: 90,
            sortable: false,
            render: (row: Zone) => (
              <Button size="sm" onClick={() => onOpen(row)}>
                Ouvrir
              </Button>
            ),
          } satisfies Column<Zone>,
        ]
      : []),
    ...(managers.length > 0
      ? [
          {
            key: 'manager_code',
            label: 'Gestionnaire',
            width: 180,
            render: (row: Zone) =>
              row.manager_code ? (
                <span>{byCode.get(row.manager_code)?.label || row.manager_code}</span>
              ) : (
                <span className="subtle">—</span>
              ),
            value: (row: Zone) => row.manager_code,
          } satisfies Column<Zone>,
        ]
      : []),
  ]

  return (
    <Card
      title="Zones et feuilles de comptage"
      message="Une zone par aire physique. Le nombre de comptages et le gestionnaire se changent sur une sélection."
      actions={
        editable && (
          <Button
            variant="primary"
            size="sm"
            icon={<Icons.plus size={13} />}
            onClick={() => setCreating(true)}
          >
            Créer une zone
          </Button>
        )
      }
      flush
    >
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={240} />}
        isEmpty={(zones) => zones.length === 0}
        empty={
          <EmptyState
            title="Aucune zone"
            action={
              editable && (
                <Button variant="primary" onClick={() => setCreating(true)}>
                  Créer la première zone
                </Button>
              )
            }
          >
            Chargez la grille « Feuilles de comptage » ci-dessus pour créer les zones
            et leur liste d’articles d’un coup, ou créez une feuille de saisie libre.
          </EmptyState>
        }
      >
        {(zones) => (
          <DataGrid
              columns={columns}
              rows={zones}
              exportTitle="Zones"
              campaignId={campaignId}
              getRowId={(row) => row.id}
              selectable={editable || Boolean(onPrint)}
              selected={selected}
              onSelectedChange={setSelected}
              searchPlaceholder="Filtrer par zone, libellé, secteur…"
              maxHeight={520}
              toolbar={
                selected.size > 0 ? (
                  <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
                    {onPrint && (
                      <Button
                        size="sm"
                        variant="primary"
                        icon={<Icons.printer size={13} />}
                        onClick={() =>
                          onPrint(zones.filter((z) => selected.has(z.id)))
                        }
                      >
                        Imprimer la sélection
                      </Button>
                    )}
                    {editable && (
                    <>
                    <Button
                      size="sm"
                      disabled={setPasses.isPending}
                      onClick={() => setPasses.mutate(1)}
                    >
                      Un seul comptage
                    </Button>
                    <Button
                      size="sm"
                      disabled={setPasses.isPending}
                      onClick={() => setPasses.mutate(2)}
                    >
                      Double comptage
                    </Button>
                    <Button
                      size="sm"
                      disabled={setNegative.isPending}
                      title="Pour une feuille de correction : un retour à retrancher d’un comptage déjà posté."
                      onClick={() => setNegative.mutate(true)}
                    >
                      Autoriser les négatifs
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={setNegative.isPending}
                      onClick={() => setNegative.mutate(false)}
                    >
                      Refuser les négatifs
                    </Button>
                    {managers.length > 0 && (
                      <select
                        className="input"
                        style={{ width: 210 }}
                        value=""
                        disabled={assign.isPending}
                        onChange={(event) => {
                          if (event.target.value !== '') {
                            assign.mutate(
                              event.target.value === '__none__'
                                ? ''
                                : event.target.value,
                            )
                          }
                        }}
                      >
                        <option value="">Affecter à…</option>
                        <option value="__none__">— retirer l’affectation —</option>
                        {managers.map((manager) => (
                          <option key={manager.code} value={manager.code}>
                            {manager.label || manager.code}
                          </option>
                        ))}
                      </select>
                    )}
                    </>
                    )}
                  </div>
                ) : null
              }
            footer={
              <span>
                {zones.length} zone(s) ·{' '}
                {zones.filter((z) => z.passes === 1).length} à comptage unique ·{' '}
                {zones.filter((z) => z.free_entry).length} en saisie libre
              </span>
            }
          />
        )}
      </AsyncBoundary>

      {creating && (
        <CreateZoneModal
          campaignId={campaignId}
          managers={managers}
          onClose={() => setCreating(false)}
        />
      )}
    </Card>
  )
}
