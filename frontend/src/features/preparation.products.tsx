/** Les produits fabriqués : de quel assemblage chaque référence fait partie.
 *
 * Trois découpes cohabitent, et celle-ci ne répartit rien. Le périmètre répartit
 * des emplacements entre gestionnaires, le portefeuille répartit des articles
 * entre personnes ; le produit fabriqué, lui, sert à **rapprocher**.
 *
 * Deux références du même produit dont les écarts se compensent à peu près ne
 * sont pas deux anomalies indépendantes : c'est la signature d'une inversion au
 * comptage — un plus ici, un moins là, sur deux pièces qui se ressemblent et
 * voisinent sur le même assemblage. Ni la catégorie, ni le programme, ni
 * l'emplacement ne rapprochent ces deux lignes.
 *
 * Le tableau se charge par le même panneau d'import que toutes les autres
 * grilles — fichier, collage, saisie — parce que c'est ainsi qu'il arrive
 * réellement : depuis le tableur où il est tenu.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { DASH } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import {
  AsyncBoundary,
  Badge,
  Button,
  Card,
  Icons,
  Skeleton,
  useErrorToast,
  useToast,
} from '../components/ui'

type Row = Record<string, unknown>

export function ProductsTab({
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

  // Même garde que les gestionnaires, leurs périmètres et les portefeuilles :
  // une clé de lecture posée sur la campagne, qui bouge jusqu'à la clôture.
  const editable = overview.permissions.managers

  const query = useQuery({
    queryKey: ['products', campaignId],
    queryFn: () => api.products(campaignId),
  })

  const clear = useMutation({
    mutationFn: () => api.clearProducts(campaignId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['products', campaignId] })
      toast.success(`${result.removed} rattachement(s) retiré(s)`)
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  const columns: Column<Row>[] = [
    { key: 'itemNumber', label: 'Référence', width: 200 },
    { key: 'name', label: 'Désignation', width: 320 },
    { key: 'product', label: 'Produit fabriqué', width: 260 },
    {
      key: 'known',
      label: '',
      width: 150,
      sortable: false,
      // Une référence rattachée mais absente du référentiel est le cas qu'on
      // veut voir : le tableau se charge souvent avant les articles, et une
      // coquille y reste invisible jusqu'au jour où le filtre ne rend rien.
      render: (row) =>
        row.known ? null : <Badge tone="warning">hors référentiel</Badge>,
      value: (row) => (row.known ? 1 : 0),
    },
  ]

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <AsyncBoundary query={query} skeleton={<Skeleton height={120} />}>
        {(data) => (
          <Card
            title="Répartition"
            message="Combien de références par produit, et combien n’en ont aucun."
          >
            <div className="row-wrap" style={{ gap: 'var(--space-3)' }}>
              {data.byProduct.length === 0 && (
                <span className="subtle">Aucune référence rattachée pour l’instant.</span>
              )}
              {data.byProduct.map((entry) => (
                <Badge key={entry.product} tone="neutral">
                  {entry.product} — {entry.items.toLocaleString('fr-FR')}
                </Badge>
              ))}
              {data.unassigned > 0 && (
                <Badge tone="warning">
                  {data.unassigned.toLocaleString('fr-FR')} sans produit
                </Badge>
              )}
            </div>
          </Card>
        )}
      </AsyncBoundary>

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="products"
        disabled={!editable}
        disabledReason="La campagne est close : ses produits fabriqués ne changent plus."
        onImported={() => {
          // Les écarts portent le produit dans leur ligne : sans cette
          // invalidation, le filtre de la vue Écarts continuerait de proposer
          // la liste d'avant le chargement.
          void queryClient.invalidateQueries()
        }}
      />

      <Card
        title="Rattachements"
        message="Une ligne par référence. Charger à nouveau déplace les références citées et laisse les autres en place ; un produit vide détache la référence."
        actions={
          editable && (query.data?.rows.length ?? 0) > 0 ? (
            <Button
              variant="danger"
              size="sm"
              icon={<Icons.trash size={13} />}
              disabled={clear.isPending}
              onClick={() => clear.mutate()}
            >
              Tout détacher
            </Button>
          ) : null
        }
        flush
      >
        <AsyncBoundary
          query={query}
          skeleton={<Skeleton height={240} />}
          isEmpty={(data) => data.rows.length === 0}
          empty={
            <div style={{ padding: 'var(--space-4)' }}>
              <span className="subtle">
                Aucun rattachement. Chargez le tableau ci-dessus : une colonne
                pour la référence, une pour le produit fabriqué. {DASH}
              </span>
            </div>
          }
        >
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows as unknown as Row[]}
              exportTitle="Produits fabriqués"
              campaignId={campaignId}
              getRowId={(row) => String(row.itemNumber)}
              searchPlaceholder="Filtrer par référence, désignation, produit…"
              maxHeight={520}
              footer={
                <span>
                  {data.rows.length.toLocaleString('fr-FR')} référence(s) ·{' '}
                  {data.byProduct.length} produit(s)
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}
