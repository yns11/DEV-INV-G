/** Le stock ERP chargé pour la campagne, avant gel. */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GRID_ROW_CEILING, api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { moneyShort, qty, percent } from '../lib/format'
import { ImportPanel } from '../components/ImportPanel'
import { DataGrid, type Column } from '../components/DataGrid'
import { Alert, AsyncBoundary, Button, Card, Icons, useErrorToast, useToast } from '../components/ui'
import { TOP_STOCK_LINES } from './preparation.shared'

// --------------------------------------------------------------------------- //
// Book stock
// --------------------------------------------------------------------------- //

export function BookStockTab({
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
  // La valeur du stock est concentrée : une poignée de lignes en portent
  // l'essentiel, et ce sont celles qu'on recompte en priorité.
  const [top, setTop] = useState(false)
  const query = useQuery({
    queryKey: ['book-stock', campaignId, top],
    queryFn: () =>
      api.bookStock(campaignId, {
        limit: GRID_ROW_CEILING,
        top: top ? TOP_STOCK_LINES : undefined,
      }),
  })

  const freeze = useMutation({
    mutationFn: () => api.freezeBookStock(campaignId),
    onSuccess: () => {
      void queryClient.invalidateQueries()
      toast.success('Stock ERP gelé', 'Les écarts sont désormais reproductibles.')
    },
    onError: (error) => showError(error, 'Gel impossible'),
  })

  const frozen = overview.campaign.book_stock_frozen_at !== null
  const columns: Column[] = [
    { key: 'item_number', label: 'Article', width: 170 },
    { key: 'warehouse_id', label: 'Entrepôt', width: 120 },
    { key: 'location_id', label: 'Emplacement', width: 150 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 130,
      render: (row) => <span className="num">{qty(Number(row.qty ?? 0))}</span>,
      value: (row) => Number(row.qty ?? 0),
    },
    { key: 'unit', label: 'Unité', width: 80 },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => <span className="num">{moneyShort(Number(row.value ?? 0))}</span>,
      value: (row) => Number(row.value ?? 0),
    },
  ]

  return (
    <div className="stack">
      {frozen ? (
        <Alert tone="success" title="Stock ERP gelé">
          Tout écart calculé aujourd’hui restera recalculable à l’identique.
        </Alert>
      ) : (
        <Alert
          tone="warning"
          title="Stock ERP non gelé"
          actions={
            <Button
              variant="primary"
              size="sm"
              icon={<Icons.lock size={13} />}
              disabled={query.data?.total === 0 || freeze.isPending}
              onClick={() => freeze.mutate()}
            >
              Geler le stock ERP
            </Button>
          }
        >
          Chargez la photo du stock puis gelez-la. Le chargement crée aussi le
          référentiel entrepôts/emplacements et un journal de comptage par
          emplacement actif.
        </Alert>
      )}

      <ImportPanel
        campaignId={campaignId}
        contract={contract}
        target="book_stock"
        disabled={!overview.permissions.bookStock || frozen}
        disabledReason={
          frozen
            ? undefined
            : 'Le chargement du stock ERP se fait pendant la phase de comptage.'
        }
        onImported={() => void queryClient.invalidateQueries()}
      />

      <Card
        title="Stock ERP"
        message={
          top && query.data?.topShare != null
            ? `Ces ${query.data.total} ligne(s) portent ${percent(query.data.topShare)} de la valeur du stock ERP (${moneyShort(query.data.totalValue)}).`
            : undefined
        }
        flush
      >
        <AsyncBoundary query={query} isEmpty={(d) => d.rows.length === 0}>
          {(data) => (
            <DataGrid
              columns={columns}
              rows={data.rows}
              toolbar={
                <button
                  className={`chip${top ? ' chip--active' : ''}`}
                  title={`Les ${TOP_STOCK_LINES} couples article / entrepôt / emplacement les plus lourds en valeur.`}
                  onClick={() => setTop((value) => !value)}
                >
                  Top {TOP_STOCK_LINES}
                </button>
              }
              exportTitle="Stock ERP"
              campaignId={campaignId}
              getRowId={(row, index) =>
                `${row.item_number}-${row.warehouse_id}-${row.location_id}-${index}`
              }
              searchPlaceholder="Filtrer par article, entrepôt, emplacement…"
              maxHeight={560}
              footer={
                <span>
                  {data.total.toLocaleString('fr-FR')} ligne(s)
                  {data.total > data.rows.length && ` — ${data.rows.length} affichées`}
                </span>
              }
            />
          )}
        </AsyncBoundary>
      </Card>
    </div>
  )
}
