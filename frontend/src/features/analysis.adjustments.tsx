/** Les ajustements qui partent vers l'ERP. */

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { GridContract, Overview } from '../lib/types'
import { DASH, qty, signClass, signedMoney } from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { BreakdownModal, DrillCell, type BreakdownAspect } from '../components/BreakdownModal'
import { ImportPanel } from '../components/ImportPanel'
import { AsyncBoundary, Card, EmptyState } from '../components/ui'

// --------------------------------------------------------------------------- //
// Adjustments
// --------------------------------------------------------------------------- //

export function AdjustmentsTab({
  campaignId,
  overview,
}: {
  campaignId: string
  overview: Overview
}) {
  const queryClient = useQueryClient()
  const contracts = useQuery({ queryKey: ['contracts'], queryFn: api.contracts })
  const contract: GridContract | undefined = contracts.data?.find((c) => c.key === 'adjustments')
  const query = useQuery({
    queryKey: ['adjustments', campaignId],
    queryFn: () => api.adjustments(campaignId),
  })
  // Un ajustement se juge contre le stock qu'il déplace : la référence s'ouvre
  // sur le physique de l'emplacement concerné, ce mouvement-ci compris.
  const [drill, setDrill] = useState<
    { itemNumber: string; aspect: BreakdownAspect; warehouseId: string; locationId: string } | null
  >(null)

  const columns: Column[] = [
    {
      key: 'item_number',
      label: 'Article',
      width: 170,
      render: (row) => (
        <DrillCell
          onOpen={() =>
            setDrill({
              itemNumber: String(row.item_number ?? ''),
              aspect: 'physical',
              warehouseId: String(row.warehouse_id ?? ''),
              locationId: String(row.location_id ?? ''),
            })
          }
        >
          <span className="mono">{String(row.item_number ?? DASH)}</span>
        </DrillCell>
      ),
      value: (row) => String(row.item_number ?? ''),
    },
    { key: 'physical_date', label: 'Date', width: 120 },
    { key: 'kind', label: 'Nature', width: 130 },
    { key: 'journal_number', label: 'Journal', width: 140 },
    {
      key: 'qty',
      label: 'Quantité',
      numeric: true,
      width: 120,
      render: (row) => (
        <span className={`num ${signClass(Number(row.qty))}`}>{qty(Number(row.qty))}</span>
      ),
      value: (row) => Number(row.qty),
    },
    {
      key: 'value',
      label: 'Valeur',
      numeric: true,
      width: 140,
      render: (row) => (
        <span className={`num ${signClass(Number(row.value))}`}>
          {signedMoney(Number(row.value))}
        </span>
      ),
      value: (row) => Number(row.value),
    },
    { key: 'warehouse_id', label: 'Entrepôt', width: 110 },
    { key: 'location_id', label: 'Emplacement', width: 140 },
    { key: 'comment', label: 'Commentaire', width: 220 },
  ]

  return (
    <div className="stack">
      {contract && (
        <ImportPanel
          campaignId={campaignId}
          contract={contract}
          target="adjustments"
          disabled={!overview.permissions.adjustments}
          disabledReason="Les ajustements sont modifiables pendant la phase d’analyse uniquement."
          onImported={() => void queryClient.invalidateQueries()}
        />
      )}

      <Card
        title="Mouvements et ajustements"
        message="Négatif = diminution de stock. Chaque mouvement s’ajoute au comptage pour former le stock physique."
        flush
      >
        <AsyncBoundary
          query={query}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState title="Aucun ajustement">
              Chargez l’export des transactions de stock, ou saisissez les ajustements
              postés dans l’ERP après analyse.
            </EmptyState>
          }
        >
          {(rows) => (
            <DataGrid
              columns={columns}
              rows={rows}
              exportTitle="Ajustements"
              campaignId={campaignId}
              getRowId={(row, index) => String(row.id ?? index)}
              searchPlaceholder="Filtrer les mouvements…"
              maxHeight={560}
            />
          )}
        </AsyncBoundary>
      </Card>

      {drill && (
        <BreakdownModal
          campaignId={campaignId}
          itemNumber={drill.itemNumber}
          aspect={drill.aspect}
          warehouseId={drill.warehouseId}
          locationId={drill.locationId}
          onClose={() => setDrill(null)}
        />
      )}
    </div>
  )
}
