/** Les nomenclatures : quel assemblage se décompose en quels composants. */

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GRID_ROW_CEILING, api } from '../lib/api'
import { compositeKey, splitCompositeKey } from '../lib/rowKey'
import type { GridContract, Overview } from '../lib/types'
import { qty } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { FindingGroups } from '../components/Findings'
import { Alert, AsyncBoundary, Badge, Button, Card, ConfirmDelete, Field, Icons, Modal, Skeleton, Switch, useErrorToast, useToast } from '../components/ui'
import { StockedFilter } from './preparation.shared'

/**
 * Les trois lectures d'une nomenclature.
 *
 * « Produits fabriqués » change de grain volontairement : une ligne par
 * assemblage et non par lien. C'est la liste de ce que l'usine sait encore
 * construire, elle se lit en dizaines là où les liens se comptent en milliers,
 * et la donner sous forme de liens obligerait à la dédoublonner de tête.
 */
type BomView = 'all' | 'active' | 'parents'

const BOM_VIEWS: Array<{ id: BomView; label: string; hint: string }> = [
  { id: 'all', label: 'Tous les liens', hint: 'Toutes les versions, en vigueur ou retirées.' },
  {
    id: 'active',
    label: 'Liens en vigueur',
    hint: 'Les seuls que la consolidation éclate.',
  },
  {
    id: 'parents',
    label: 'Produits fabriqués en vigueur',
    hint: 'Un assemblage par ligne : ceux qui portent au moins un lien en vigueur.',
  },
]

const PARENT_COLUMNS: Column[] = [
  { key: 'parent', label: 'Assemblage', width: 200 },
  { key: 'name', label: 'Désignation', width: 320 },
  {
    key: 'children',
    label: 'Composants en vigueur',
    numeric: true,
    width: 180,
  },
]

// --------------------------------------------------------------------------- //
// Bills of materials
// --------------------------------------------------------------------------- //

export function BomsTab({
  campaignId,
  contract,
  overview,
}: {
  campaignId: string
  contract: GridContract
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const toast = useToast()
  const showError = useErrorToast()
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null)
  const [counted, setCounted] = useState(false)
  //: Le lien dont la suppression est en attente de confirmation. Un lien de
  //: nomenclature se recharge depuis l'ERP en dix secondes : le dialogue le dit
  //: plutôt que de poser la même question que pour une zone entière.
  const [removingLink, setRemovingLink] = useState<
    { parent: string; child: string } | null
  >(null)
  const editable = overview.permissions.boms
  const [selectedLinks, setSelectedLinks] = useState<Set<string>>(new Set())
  const [bomView, setBomView] = useState<BomView>('all')
  const health = useQuery({
    queryKey: ['bom-health', campaignId],
    queryFn: () => api.bomHealth(campaignId),
  })
  const links = useQuery({
    queryKey: ['boms', campaignId, counted],
    queryFn: () =>
      api.boms(campaignId, {
        limit: GRID_ROW_CEILING,
        counted: counted || undefined,
      }),
  })

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ['boms'] })
    void queryClient.invalidateQueries({ queryKey: ['bom-health'] })
  }

  const remove = useMutation({
    mutationFn: ({ parent, child }: { parent: string; child: string }) =>
      api.deleteBomLink(campaignId, parent, child),
    onSuccess: () => {
      refresh()
      toast.success('Lien supprimé')
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  // Un changement de version arrive par lot — c'est toute la recette d'un
  // assemblage qu'on remplace, pas une de ses lignes — donc c'est l'opération.
  const activate = useMutation({
    mutationFn: (active: boolean) =>
      api.setBomActivation(
        campaignId,
        [...selectedLinks].map((key) => {
          const [parentItem, childItem] = splitCompositeKey(key)
          return { parentItem: parentItem!, childItem: childItem! }
        }),
        active,
      ),
    onSuccess: (result, active) => {
      refresh()
      setSelectedLinks(new Set())
      toast.success(
        `${result.updated} lien(s) ${active ? 'remis en vigueur' : 'retirés'}`,
        result.unchanged > 0 ? `${result.unchanged} l’étaient déjà.` : undefined,
      )
    },
    onError: (error) => showError(error, 'Changement impossible'),
  })

  // Les trois lectures d'une nomenclature. « Produits fabriqués » change de
  // grain : une ligne par assemblage, pas par lien — c'est la liste de ce que
  // l'usine sait encore construire, et elle se lit en dizaines quand les liens
  // se comptent en milliers.
  const { visibleLinks, parentRows, counts, hidden } = useMemo(() => {
    const rows = links.data?.rows ?? []
    const active = rows.filter((r) => r.active !== false)
    const byParent = new Map<string, { parent: string; name: string; children: number }>()
    for (const row of active) {
      const parent = String(row.parent_item)
      const entry = byParent.get(parent) ?? {
        parent,
        name: String(row.parentName ?? ''),
        children: 0,
      }
      entry.children += 1
      byParent.set(parent, entry)
    }
    const parents = [...byParent.values()].sort((a, b) =>
      a.parent.localeCompare(b.parent, 'fr'),
    )
    return {
      visibleLinks: bomView === 'active' ? active : rows,
      parentRows: parents,
      counts: { all: rows.length, active: active.length, parents: parents.length },
      // Les comptes des pastilles portent sur ce qui est chargé ; celui-ci
      // porte sur ce qui existe. Les confondre ferait dire « 20 000 liens » à
      // une nomenclature qui en a soixante mille.
      hidden: Math.max(0, (links.data?.total ?? rows.length) - rows.length),
    }
  }, [links.data, bomView])

  const columns: Column[] = [
    { key: 'parent_item', label: 'Assemblage', width: 170 },
    // Les désignations, parce qu'une nomenclature lue en références seules
    // oblige à ouvrir le référentiel à chaque ligne pour savoir de quoi il
    // s'agit — et c'est là que les erreurs de relecture se glissent.
    { key: 'parentName', label: 'Désignation assemblage', width: 240 },
    { key: 'child_item', label: 'Composant', width: 170 },
    { key: 'childName', label: 'Désignation composant', width: 240 },
    {
      key: 'qtyPer',
      label: 'Qté par assemblage',
      numeric: true,
      width: 170,
      render: (row) => <span className="num">{qty(Number(row.qtyPer ?? 0))}</span>,
      value: (row) => Number(row.qtyPer ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 90, filter: 'choice' },
    {
      key: 'active',
      label: 'Version',
      width: 110,
      // Deux valeurs, donc une liste — et la somme d'une version ne veut rien
      // dire, mais la colonne n'étant pas numérique elle ne totalise déjà pas.
      filter: 'choice',
      render: (row) =>
        row.active === false ? (
          <Badge tone="warning">Inactive</Badge>
        ) : (
          <Badge tone="success">En vigueur</Badge>
        ),
      value: (row) => (row.active === false ? 'Inactive' : 'En vigueur'),
    },
    {
      key: 'edit',
      label: '',
      width: 92,
      sortable: false,
      render: (row) => (
        <span className="row" style={{ gap: 'var(--space-1)' }}>
          <Button
            variant="ghost"
            size="sm"
            title={editable ? 'Modifier ce lien' : 'Nomenclatures gelées'}
            disabled={!editable}
            onClick={() => setEditing(row)}
          >
            <Icons.sliders size={14} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title={editable ? 'Supprimer ce lien' : 'Nomenclatures gelées'}
            disabled={!editable || remove.isPending}
            onClick={() =>
              setRemovingLink({
                parent: String(row.parent_item),
                child: String(row.child_item),
              })
            }
          >
            <Icons.trash size={14} />
          </Button>
        </span>
      ),
    },
  ]

  return (
    <div className="stack">
      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="boms"
        disabled={!overview.permissions.boms}
        disabledReason="Les nomenclatures sont gelées depuis le passage en phase de comptage."
        onImported={() => {
          void queryClient.invalidateQueries({ queryKey: ['boms'] })
          void queryClient.invalidateQueries({ queryKey: ['bom-health'] })
        }}
      />

      <AsyncBoundary query={health} skeleton={<Skeleton height={140} />}>
        {(data) => (
          <Card
            title="Santé des nomenclatures"
            message={
              data.cycles.length > 0
                ? `${data.cycles.length} cycle(s) détecté(s) : l’éclatement du WIP est impossible tant qu’ils subsistent.`
                : `${data.linkCount.toLocaleString('fr-FR')} liens couvrant ${data.parentCount} assemblage(s), sans cycle.`
            }
          >
            <div className="stack">
              {data.cycles.length > 0 && (
                <Alert tone="danger" title="Cycles de nomenclature">
                  <ul style={{ margin: 0, paddingLeft: '1.1rem' }} className="mono">
                    {data.cycles.slice(0, 6).map((cycle) => (
                      <li key={cycle}>{cycle}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              <FindingGroups
                groups={data.groups}
                findings={data.findings}
                emptyLabel="Aucune anomalie structurelle détectée"
              />
            </div>
          </Card>
        )}
      </AsyncBoundary>

      <Card title="Liens de nomenclature" flush>
        <div className="chips" style={{ padding: '0 var(--space-4)' }}>
          {BOM_VIEWS.map(({ id, label, hint }) => (
            <button
              key={id}
              className={`chip${bomView === id ? ' chip--active' : ''}`}
              title={hint}
              onClick={() => {
                setBomView(id)
                setSelectedLinks(new Set())
              }}
            >
              {label} <span className="num">{counts[id === 'parents' ? 'parents' : id]}</span>
            </button>
          ))}
        </div>

        {bomView === 'parents' ? (
          <DataGrid
            columns={PARENT_COLUMNS}
            rows={parentRows}
            exportTitle="Produits fabriqués"
            campaignId={campaignId}
            getRowId={(row) => row.parent}
            searchPlaceholder="Filtrer par assemblage…"
            maxHeight={520}
            emptyTitle="Aucun assemblage en vigueur"
            footer={
              <span>
                Assemblages portant au moins un lien en vigueur — ce que l’usine
                sait construire aujourd’hui.
              </span>
            }
          />
        ) : (
        <AsyncBoundary query={links} isEmpty={() => visibleLinks.length === 0}>
          {() => (
            <DataGrid
              columns={columns}
              rows={visibleLinks}
              exportTitle="Nomenclatures"
              campaignId={campaignId}
              selectable={editable}
              selected={selectedLinks}
              onSelectedChange={setSelectedLinks}
              getRowId={(row) => compositeKey(row.parent_item, row.child_item)}
              searchPlaceholder="Filtrer par assemblage ou composant…"
              maxHeight={520}
              toolbar={
                <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
                  <StockedFilter value={counted} onChange={setCounted} />
                  {editable && selectedLinks.size > 0 && (
                    <>
                      <Button
                        size="sm"
                        disabled={activate.isPending}
                        onClick={() => activate.mutate(true)}
                      >
                        Activer
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={activate.isPending}
                        onClick={() => activate.mutate(false)}
                      >
                        Désactiver
                      </Button>
                    </>
                  )}
                </div>
              }
              footer={
                <span>
                  {counted &&
                    'Liens dont l’assemblage ou le composant est stocké ou compté. '}
                  {hidden > 0 &&
                    `${hidden.toLocaleString('fr-FR')} lien(s) non chargé(s) — filtrez sur un assemblage ou exportez.`}
                </span>
              }
            />
          )}
        </AsyncBoundary>
        )}
      </Card>

      {editing && (
        <BomLinkEditModal
          campaignId={campaignId}
          row={editing}
          onSaved={refresh}
          onClose={() => setEditing(null)}
        />
      )}
      {removingLink && (
        <ConfirmDelete
          what={`le lien ${removingLink.parent} → ${removingLink.child}`}
          consequences={[
            'Ce composant cessera d’être éclaté sous cet assemblage lors ' +
              'de la consolidation.',
          ]}
          reversible
          pending={remove.isPending}
          onClose={() => setRemovingLink(null)}
          onConfirm={() => {
            remove.mutate(removingLink)
            setRemovingLink(null)
          }}
        />
      )}
    </div>
  )
}

/**
 * Correcting one bill-of-materials edge.
 *
 * Parent and child are shown and fixed: they *are* the edge. Changing one is
 * deleting a link and creating another, which the grid already offers — and
 * doing it silently here would leave the original in place.
 *
 * A wrong quantity per assembly is invisible until consolidation explodes a WIP
 * and produces a component count nobody can explain, so it is worth being able
 * to fix in one field.
 */
function BomLinkEditModal({
  campaignId,
  row,
  onSaved,
  onClose,
}: {
  campaignId: string
  row: Record<string, unknown>
  onSaved: () => void
  onClose: () => void
}) {
  const toast = useToast()
  const showError = useErrorToast()
  const parent = String(row.parent_item)
  const child = String(row.child_item)
  const [qtyPer, setQtyPer] = useState(String(row.qtyPer ?? ''))
  const [unit, setUnit] = useState(String(row.unit ?? 'PCE'))
  const [active, setActive] = useState(row.active !== false)

  const parsed = Number(qtyPer.replace(',', '.'))
  const invalid = !Number.isFinite(parsed) || parsed <= 0

  const save = useMutation({
    mutationFn: () =>
      api.updateBomLink(campaignId, {
        parentItem: parent,
        childItem: child,
        qtyPer: parsed,
        unit,
        active,
      }),
    onSuccess: () => {
      onSaved()
      toast.success(`Lien ${parent} → ${child} modifié`)
      onClose()
    },
    onError: (error) => showError(error, 'Modification impossible'),
  })

  return (
    <Modal
      title={`${parent} → ${child}`}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={save.isPending || invalid}
            onClick={() => save.mutate()}
          >
            Enregistrer
          </Button>
        </>
      }
    >
      <div className="stack">
        <Field
          label="Quantité par assemblage"
          hint="Combien de composants un assemblage consomme."
          error={invalid ? 'Nombre strictement positif attendu.' : undefined}
        >
          <input
            className="input num"
            inputMode="decimal"
            value={qtyPer}
            onChange={(event) => setQtyPer(event.target.value)}
          />
        </Field>
        <Field label="Unité">
          <input
            className="input"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
          />
        </Field>
        {/* Une version retirée reste chargée — c'est ce qui distingue « recette
            périmée » de « aucune recette » — mais elle n'est pas éclatée. La
            remettre en vigueur ici évite un aller-retour par l'ERP quand c'est
            le statut, et non la structure, qui était faux. */}
        <Switch
          checked={active}
          onChange={setActive}
          label="Version en vigueur (seules celles-ci sont éclatées)"
        />
      </div>
    </Modal>
  )
}
