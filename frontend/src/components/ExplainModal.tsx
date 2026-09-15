/**
 * Ce que le modèle dit d'un écart, et sur quoi il s'appuie.
 *
 * La réponse arrive en markdown — le serveur la demande structurée en sections
 * — et s'affichait brute : « ## Ce qui est probable » se lisait tel quel, les
 * listes à puces en tirets collés, les tableaux en barres verticales alignées
 * sur rien. Elle passe maintenant par le même rendu que la synthèse et
 * l'assistant, qui peint des nœuds React et jamais du HTML : le dossier envoyé
 * au modèle porte des désignations d'articles et des commentaires de comptage
 * venus de fichiers que l'application n'écrit pas, et une réponse qui en
 * reprendrait un morceau ne doit pas pouvoir l'exécuter.
 *
 * Les deux tableaux sous la réponse ne sont pas décoratifs : ce sont les
 * chiffres que le modèle a eus sous les yeux. Une explication sans eux se
 * vérifie sur parole.
 */

import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { moneyShort, qty } from '../lib/format'
import { Markdown } from '../lib/markdown'
import { Alert, AsyncBoundary, Card, Modal, Skeleton } from './ui'

export function ExplainModal({
  campaignId,
  itemNumber,
  onClose,
}: {
  campaignId: string
  itemNumber: string
  onClose: () => void
}) {
  const query = useQuery({
    queryKey: ['explain', campaignId, itemNumber],
    queryFn: () => api.explain(campaignId, itemNumber),
  })

  return (
    <Modal title={`Analyse de l’écart — ${itemNumber}`} onClose={onClose} width={820}>
      <AsyncBoundary query={query} skeleton={<Skeleton count={5} height={18} />}>
        {(data) => (
          <div className="stack">
            <Alert tone="info" title="Généré par IA — à vérifier">
              Proposition fondée sur les chiffres de la campagne. Elle n’écrit rien.
            </Alert>
            <Markdown text={data.explanation} />
            {data.wipBreakdown.length > 0 && (
              <Card title="Composition du WIP">
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Zone</th>
                        <th>Assemblage</th>
                        <th className="num">Quantité apportée</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.wipBreakdown.map((row, index) => (
                        <tr key={index}>
                          <td>{row.zone_code}</td>
                          <td className="mono">{row.parent_item}</td>
                          <td className="num">{qty(row.child_qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
            {data.movements.length > 0 && (
              <Card title="Mouvements enregistrés">
                <div className="table-wrap" style={{ maxHeight: 220 }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Nature</th>
                        <th className="num">Quantité</th>
                        <th className="num">Valeur</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.movements.map((row, index) => (
                        <tr key={index}>
                          <td>{String(row.date ?? '—')}</td>
                          <td>{String(row.kind)}</td>
                          <td className="num">{qty(Number(row.qty))}</td>
                          <td className="num">{moneyShort(Number(row.value))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        )}
      </AsyncBoundary>
    </Modal>
  )
}
