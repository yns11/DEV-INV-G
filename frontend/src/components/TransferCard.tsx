/**
 * Perte sèche ou simple transfert : ce que la différence entre les deux vues
 * mesure exactement.
 *
 * Sorti de la vue Écarts, où il n'était pas à sa place : cette carte ne lit
 * aucune de ses lignes et ne partage aucun de ses états. Elle pose sa propre
 * question au serveur et rend sa propre réponse — c'est-à-dire tout ce qu'il
 * faut pour vivre à côté plutôt que dedans.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { moneyShort, percent } from '../lib/format'
import { CompositionBar } from './charts'
import { AsyncBoundary, Button, Card, Skeleton } from './ui'

/**
 * How much of the variance is a move between bins rather than a loss.
 *
 * The reason the screen opens on the per-reference reading. A pallet moved from
 * one location to another shows up twice in the per-location view — short here,
 * over there — and drags the IRA down without a single part having been lost.
 * That is a location-accuracy problem, worth fixing, but it is not the same
 * alarm as a shortfall, and conflating the two sends people chasing the wrong
 * thing.
 */
export function TransferCard({
  campaignId,
  onDrillDown,
}: {
  campaignId: string
  onDrillDown: () => void
}) {
  const query = useQuery({
    queryKey: ['transfers', campaignId],
    queryFn: () => api.transfers(campaignId, 20),
  })

  return (
    <AsyncBoundary
      query={query}
      skeleton={<Skeleton height={160} />}
      isEmpty={(data) => data.grossValue === 0}
      empty={null}
    >
      {(data) => (
        <Card
          title="Perte sèche ou simple transfert ?"
          message="L’écart vu par emplacement compte deux fois une palette déplacée. La différence avec l’écart par référence mesure exactement cette part-là."
          actions={
            data.itemCount > 0 ? (
              <Button size="sm" variant="ghost" onClick={onDrillDown}>
                Voir le détail par emplacement
              </Button>
            ) : null
          }
        >
          <div className="stack">
            <CompositionBar
              format={moneyShort}
              segments={[
                {
                  label: 'Écart net par référence',
                  value: data.netValue,
                  color: 'var(--cat-4)',
                },
                {
                  label: 'Transfert entre emplacements',
                  value: data.transferValue,
                  color: 'var(--cat-2)',
                },
              ]}
            />
            <p className="subtle">
              {percent(data.transferShare)} de l’écart brut par emplacement (
              {moneyShort(data.grossValue)}) se compense entre deux emplacements de la
              même référence, sur {data.itemCount.toLocaleString('fr-FR')} référence(s).
              Ce n’est pas une perte : c’est le stock qui n’est pas là où l’ERP le
              croit.
            </p>
            {data.rows.length > 0 && (
              <div className="table-wrap" style={{ maxHeight: 260 }}>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Référence</th>
                      <th className="num">Écart par référence</th>
                      <th className="num">Écart par emplacement</th>
                      <th className="num">Dont transfert</th>
                      <th className="num">Emplacements</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((row) => (
                      <tr key={row.itemNumber}>
                        <td>
                          <div className="mono">{row.itemNumber}</div>
                          <div className="subtle truncate" style={{ maxWidth: 240 }}>
                            {row.name}
                          </div>
                        </td>
                        <td className="num">{moneyShort(row.netValue)}</td>
                        <td className="num">{moneyShort(row.grossValue)}</td>
                        <td className="num">
                          <strong>{moneyShort(row.transferValue)}</strong>{' '}
                          <span className="subtle">
                            ({percent(row.transferShare)})
                          </span>
                        </td>
                        <td className="num">{row.locations}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>
      )}
    </AsyncBoundary>
  )
}
