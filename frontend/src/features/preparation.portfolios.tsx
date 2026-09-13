/** Les portefeuilles : quelles références sont à qui, et qui les filtre.
 *
 * L'application répartissait déjà des **emplacements** — c'est « mon
 * périmètre », porté par les gestionnaires. Elle ne répartissait pas les
 * **articles**, et ce n'est pas la même découpe : un acheteur suit ses
 * références partout où elles sont, quel que soit l'entrepôt qui les range.
 *
 * Sur une campagne de cinq cents références, l'écran des écarts montrait donc à
 * chacun le travail de tout le monde, et la première chose à faire était d'y
 * retrouver les siennes.
 *
 * Le tableau se charge par le même panneau d'import que toutes les autres
 * grilles — fichier, collage, saisie — parce que c'est ainsi qu'il arrive
 * réellement : depuis le tableur où il est tenu.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMutation } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { DASH } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import {
  Alert,
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

export function PortfoliosTab({
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

  // Même garde que les gestionnaires et leurs deux périmètres : c'est la même
  // nature de décision — qui s'occupe de quoi — et elle bouge aux mêmes
  // moments, jusqu'à la clôture comprise.
  const editable = overview.permissions.managers

  const query = useQuery({
    queryKey: ['portfolios', campaignId],
    queryFn: () => api.portfolios(campaignId),
  })

  const clear = useMutation({
    mutationFn: () => api.clearPortfolios(campaignId),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['portfolios', campaignId] })
      toast.success(`${result.removed} attribution(s) retirée(s)`)
    },
    onError: (error) => showError(error, 'Suppression impossible'),
  })

  const columns: Column<Row>[] = [
    { key: 'itemNumber', label: 'Référence', width: 200 },
    { key: 'name', label: 'Désignation', width: 320 },
    { key: 'actor', label: 'Suivie par', width: 280 },
    {
      key: 'known',
      label: '',
      width: 150,
      sortable: false,
      // Une référence attribuée mais absente du référentiel est le cas qu'on
      // veut voir : le portefeuille se charge souvent avant les articles, et
      // une coquille y reste invisible jusqu'au jour où le filtre ne rend rien.
      render: (row) =>
        row.known ? null : <Badge tone="warning">hors référentiel</Badge>,
      value: (row) => (row.known ? 1 : 0),
    },
  ]

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      <Alert tone="info" title="Un portefeuille n’est pas une habilitation">
        Il filtre l’affichage — la bascule <strong>Mes références</strong> sur le
        stock ERP, l’écart backflush et les écarts. Chacun garde le droit d’agir
        sur toutes les références, comme un gestionnaire garde celui d’agir hors
        de son périmètre. L’adresse est celle avec laquelle la personne se
        connecte.
      </Alert>

      <AsyncBoundary query={query} skeleton={<Skeleton height={120} />}>
        {(data) => (
          <Card
            title="Répartition"
            message="Combien de références chacun suit, et combien n’ont pas encore de propriétaire."
          >
            <div className="row-wrap" style={{ gap: 'var(--space-3)' }}>
              {data.byActor.length === 0 && (
                <span className="subtle">Aucune référence attribuée pour l’instant.</span>
              )}
              {data.byActor.map((entry) => (
                <Badge key={entry.actor} tone="neutral">
                  {entry.actor} — {entry.items.toLocaleString('fr-FR')}
                </Badge>
              ))}
              {data.unassigned > 0 && (
                <Badge tone="warning">
                  {data.unassigned.toLocaleString('fr-FR')} sans propriétaire
                </Badge>
              )}
            </div>
          </Card>
        )}
      </AsyncBoundary>

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="portfolios"
        disabled={!editable}
        disabledReason="La campagne est close : ses portefeuilles ne changent plus."
        onImported={() => {
          void queryClient.invalidateQueries({ queryKey: ['portfolios', campaignId] })
        }}
      />

      <Card
        title="Attributions"
        message="Une ligne par référence. Charger à nouveau met à jour les références citées et laisse les autres en place ; une adresse vide retire l’attribution."
        actions={
          editable && (query.data?.rows.length ?? 0) > 0 ? (
            <Button
              variant="danger"
              size="sm"
              icon={<Icons.trash size={13} />}
              disabled={clear.isPending}
              onClick={() => clear.mutate()}
            >
              Tout retirer
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
                Aucune attribution. Chargez le tableau ci-dessus : une colonne
                pour la référence, une pour l’adresse e-mail. {DASH}
              </span>
            </div>
          }
        >
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows as unknown as Row[]}
              exportTitle="Portefeuilles"
              campaignId={campaignId}
              getRowId={(row) => String(row.itemNumber)}
              searchPlaceholder="Filtrer par référence, désignation, adresse…"
              maxHeight={520}
              footer={
                <span>
                  {data.rows.length.toLocaleString('fr-FR')} attribution(s) ·{' '}
                  {data.byActor.length} personne(s)
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}
