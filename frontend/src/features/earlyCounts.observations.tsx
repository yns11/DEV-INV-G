/**
 * Ce qu'un précomptage laisse à regarder : ses dérives et ses étiquettes.
 *
 * Les deux listes étaient des vues du dispositif de comptage avancé, et chacune
 * portait des issues à trancher. Elles n'en portent plus, et elles ont changé
 * de place pour cette raison : ce qui se regarde sans se décider a sa place
 * dans les Contrôles, avec les autres constats.
 *
 * **Pourquoi il n'y a plus rien à trancher.** Un journal de précomptage est
 * posté dans l'ERP *avant* que la photo du jour J ne soit prise, et cette photo
 * l'a donc déjà intégré. La correction de l'inventaire n'est pas perdue : elle
 * a été enregistrée plus tôt, dans l'ERP, avant la campagne. Ce qui subsiste
 * après ce réalignement n'est pas un écart mais ce qui a bougé entre les deux
 * dates — une sortie, une réception, une correction saisie entre-temps.
 *
 * Aucune action requise, donc, et aucun constat bloquant. Les deux listes sont
 * des indices : elles disent où aller voir, à qui veut y aller.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Drift, LabelAlert } from '../lib/types'
import { DASH, qty, signedMoney, signedNum } from '../lib/format'
import { DataGrid, type Column } from '../components/DataGrid'
import { Alert, AsyncBoundary, Card, EmptyState, Skeleton } from '../components/ui'

// --------------------------------------------------------------------------
// Dérives
// --------------------------------------------------------------------------

export function DriftsTab({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['drifts', campaignId],
    queryFn: () => api.drifts(campaignId),
  })

  const columns: Column<Drift>[] = [
    { key: 'warehouseId', label: 'Entrepôt', width: 110 },
    { key: 'locationId', label: 'Emplacement', width: 150 },
    { key: 'itemNumber', label: 'Référence', width: 170 },
    {
      key: 'qtyCountedT0',
      label: 'Compté au précomptage',
      width: 180,
      numeric: true,
      render: (row) => qty(row.qtyCountedT0),
    },
    {
      key: 'qtyErpJ',
      label: 'ERP jour J',
      width: 130,
      numeric: true,
      render: (row) => qty(row.qtyErpJ),
    },
    {
      key: 'driftQty',
      label: 'Dérive',
      width: 130,
      numeric: true,
      render: (row) => signedNum(row.driftQty),
    },
    {
      key: 'driftValue',
      label: 'Valeur',
      width: 130,
      numeric: true,
      render: (row) => signedMoney(row.driftValue),
    },
  ]

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={240} />}
      isEmpty={(rows) => rows.length === 0}
      empty={
        <EmptyState title="Aucune dérive">
          Aucun emplacement scellé, ou le stock ERP du jour J n’a pas encore été
          chargé — et c’est lui qui sert de comparaison.
        </EmptyState>
      }
    >
      {(drifts) => (
        <Card
          title="Dérives des emplacements précomptés"
          message="Stock ERP du jour J moins ce que le précomptage avait compté. Attendue nulle : poster le journal du précomptage a réaligné l’ERP avant que la photo du jour J ne soit prise."
          flush
        >
          <Alert tone="info" title="À regarder, pas à trancher">
            Ce qui reste ici a bougé entre le précomptage et le jour J : une
            sortie, une réception, une correction saisie entre-temps. Rien n’est
            à décider et rien n’est bloqué — l’écart d’inventaire, lui, se mesure
            contre le stock ERP du jour J, qui est la référence unique.
          </Alert>
          <DataGrid<Drift>
            rows={drifts}
            columns={columns}
            getRowId={(row) => row.id}
            exportTitle="Dérives"
            campaignId={campaignId}
          />
        </Card>
      )}
    </AsyncBoundary>
  )
}

// --------------------------------------------------------------------------
// Étiquettes
// --------------------------------------------------------------------------

/**
 * Ce qui a été retiré de la liste des étiquettes comptées ailleurs, et pourquoi.
 *
 * Deux journaux passés sur le même emplacement scellé y produisaient une ligne
 * par étiquette, avec « ATP / SF1 » dans les deux colonnes d'emplacement — la
 * pièce n'a pas bougé, et il n'y a alors aucun déplacement à montrer. Elles
 * noyaient les vrais déplacements, seuls à regarder.
 *
 * Les retirer en silence cacherait pourtant un fait : deux journaux ont compté
 * le même emplacement, parfois avec des quantités différentes, et un seul est
 * retenu. C'est ce que ce bandeau dit — au-dessus de la frontière asynchrone,
 * parce qu'il doit rester visible quand la liste, elle, est vide.
 */
function RecountedInPlaceNotice({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['recounted-in-place', campaignId],
    queryFn: () => api.recountedInPlace(campaignId),
  })
  const rows = query.data ?? []
  if (rows.length === 0) return null
  const labels = rows.reduce((sum, row) => sum + row.labelCount, 0)
  return (
    <Alert tone="info" title="Emplacements recomptés sur place">
      {rows.length} emplacement(s) scellés, {labels} étiquette(s) : un second
      journal les a comptés au même endroit. Ce n’est pas un déplacement, et seul
      le journal qui possède l’emplacement est retenu.
      <ul>
        {rows.map((row) => (
          <li
            key={`${row.sealedWarehouseId}-${row.sealedLocationId}-${row.otherJournalNumber}`}
          >
            {row.sealedWarehouseId} / {row.sealedLocationId} — retenu{' '}
            {row.ownerJournalNumber || DASH}, ignoré {row.otherJournalNumber} (
            {row.labelCount} étiquette(s))
          </li>
        ))}
      </ul>
    </Alert>
  )
}

export function LabelsTab({ campaignId }: { campaignId: string }) {
  const query = useQuery({
    queryKey: ['label-alerts', campaignId],
    queryFn: () => api.labelAlerts(campaignId),
  })

  const rowId = (row: LabelAlert) =>
    `${row.labelId}-${row.itemNumber}-${row.otherJournalNumber}`

  const columns: Column<LabelAlert>[] = [
    { key: 'labelId', label: 'Étiquette', width: 150 },
    { key: 'itemNumber', label: 'Référence', width: 170 },
    {
      key: 'sealedLocationId',
      label: 'Emplacement scellé',
      width: 190,
      render: (row) => `${row.sealedWarehouseId} / ${row.sealedLocationId}`,
    },
    {
      key: 'otherLocationId',
      label: 'Comptée aussi en',
      width: 190,
      render: (row) => `${row.otherWarehouseId} / ${row.otherLocationId}`,
    },
    { key: 'otherJournalNumber', label: 'Dans le journal', width: 150 },
    {
      key: 'otherQtyCounted',
      label: 'Quantité',
      width: 110,
      numeric: true,
      render: (row) => qty(row.otherQtyCounted),
    },
  ]

  return (
    <div className="stack">
      <RecountedInPlaceNotice campaignId={campaignId} />
      <AsyncBoundary
        query={query}
        skeleton={<Skeleton height={200} />}
        isEmpty={(rows) => rows.length === 0}
        empty={
          <EmptyState title="Aucune étiquette signalée">
            Aucune étiquette d’un emplacement scellé ne se retrouve comptée à un
            autre emplacement.
          </EmptyState>
        }
      >
        {(alerts) => (
          <Card
            title="Étiquettes scellées comptées ailleurs"
            message="Ce que la dérive ne voit pas. Une pièce sortie d’un emplacement scellé sans transaction ERP laisse une dérive nulle ; si elle est re-scannée ailleurs, son étiquette apparaît dans un second journal."
            flush
          >
            <Alert tone="info" title="À regarder, pas à trancher">
              La liste ne retire rien d’aucun comptage : elle dit où une pièce a
              reparu, à qui veut aller voir. Une pièce comptée deux fois se règle
              sur le terrain, pas en excluant une ligne d’une agrégation.
            </Alert>
            <DataGrid<LabelAlert>
              rows={alerts}
              columns={columns}
              getRowId={rowId}
              exportTitle="Étiquettes signalées"
              campaignId={campaignId}
            />
          </Card>
        )}
      </AsyncBoundary>
    </div>
  )
}
