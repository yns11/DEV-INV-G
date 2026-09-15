/**
 * Choisir des références du référentiel, pour les poser sur une feuille.
 *
 * Le référentiel d'une campagne fait quelques milliers de lignes : la liste ne
 * s'ouvre donc pas sur tout, elle s'ouvre sur une recherche. Le filtrage est
 * fait par le serveur — c'est lui qui connaît le référentiel gelé de la
 * campagne — et l'écran n'en garde qu'une page.
 *
 * **Plusieurs à la fois.** On ajoute rarement une seule référence à une
 * feuille : on en ajoute trois qu'on avait oubliées, ou la demi-douzaine d'un
 * nouveau bac. Les prendre une par une redemanderait à chaque fois où les
 * poser, et ferait perdre la recherche entre deux.
 *
 * Ce que la fenêtre montre de chaque article est ce qui permet de le
 * reconnaître — sa référence, sa désignation, son unité — et rien d'autre : un
 * prix ou un type n'aide pas à décider qu'une pièce est dans le bac.
 */

import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { AsyncBoundary, Button, EmptyState, Icons, Modal, SearchInput, Skeleton } from './ui'

/** Ce qu'une ligne de feuille a besoin de savoir d'un article. */
export interface PickedItem {
  item_number: string
  name: string
  unit: string
}

/**
 * Combien de références la fenêtre ramène par recherche.
 *
 * Assez pour qu'une recherche large reste utilisable, assez peu pour qu'on
 * n'attende pas : au-delà, ce qu'il faut n'est pas plus de lignes mais une
 * recherche plus précise, et la fenêtre le dit.
 */
const PAGE = 200

export function ItemPicker({
  campaignId,
  title,
  onPick,
  onClose,
}: {
  campaignId: string
  title: string
  onPick: (items: PickedItem[]) => void
  onClose: () => void
}) {
  const [search, setSearch] = useState('')
  const [chosen, setChosen] = useState<Map<string, PickedItem>>(new Map())

  const query = useQuery({
    queryKey: ['item-picker', campaignId, search],
    queryFn: () => api.items(campaignId, { search: search.trim(), limit: PAGE }),
  })

  const toggle = (item: PickedItem) => {
    setChosen((current) => {
      const next = new Map(current)
      if (!next.delete(item.item_number)) next.set(item.item_number, item)
      return next
    })
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      width={760}
      footer={
        <>
          {chosen.size > 0 && (
            <Button variant="ghost" onClick={() => setChosen(new Map())}>
              Tout décocher
            </Button>
          )}
          <span className="spacer" />
          <Button variant="ghost" onClick={onClose}>
            Annuler
          </Button>
          <Button
            variant="primary"
            disabled={chosen.size === 0}
            onClick={() => onPick([...chosen.values()])}
          >
            Ajouter {chosen.size > 0 ? `(${chosen.size})` : ''}
          </Button>
        </>
      }
    >
      <div className="stack">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="Référence ou désignation…"
        />

        {/* Ce qui est coché reste visible quand la recherche change : sinon,
            chercher la troisième référence fait perdre les deux premières —
            et personne ne s'en aperçoit avant d'avoir cliqué « Ajouter ». */}
        {chosen.size > 0 && (
          <div className="row-wrap" style={{ gap: 'var(--space-2)' }}>
            {[...chosen.values()].map((item) => (
              <button
                key={item.item_number}
                className="chip chip--active"
                title={`Retirer ${item.item_number}`}
                onClick={() => toggle(item)}
              >
                <span className="mono">{item.item_number}</span>
                <span className="chip__remove">
                  <Icons.x size={11} />
                </span>
              </button>
            ))}
          </div>
        )}

        <AsyncBoundary query={query} skeleton={<Skeleton height={280} />}>
          {(page) => (
            <Rows
              rows={page.rows as unknown as PickedItem[]}
              total={page.total}
              chosen={chosen}
              onToggle={toggle}
            />
          )}
        </AsyncBoundary>
      </div>
    </Modal>
  )
}

function Rows({
  rows,
  total,
  chosen,
  onToggle,
}: {
  rows: PickedItem[]
  total: number
  chosen: Map<string, PickedItem>
  onToggle: (item: PickedItem) => void
}) {
  const hidden = useMemo(() => Math.max(0, total - rows.length), [total, rows])

  if (rows.length === 0) {
    return (
      <EmptyState title="Aucun article ne correspond">
        Le référentiel de la campagne ne porte aucune référence pour cette
        recherche. Un article absent du référentiel ne peut pas être posé sur
        une feuille : complétez-le d’abord.
      </EmptyState>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      <div className="table-wrap" style={{ maxHeight: 380 }}>
        <table className="data">
          <thead>
            <tr>
              <th style={{ width: 36 }} />
              <th>Référence</th>
              <th>Désignation</th>
              <th style={{ width: 80 }}>Unité</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.item_number}>
                <td>
                  <input
                    type="checkbox"
                    checked={chosen.has(item.item_number)}
                    onChange={() => onToggle(item)}
                    aria-label={`Choisir ${item.item_number}`}
                  />
                </td>
                <td className="mono">{item.item_number}</td>
                <td className="truncate">{item.name}</td>
                <td className="subtle">{item.unit}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {hidden > 0 && (
        <span className="subtle">
          {hidden} référence(s) de plus correspondent — affinez la recherche
          plutôt que de faire défiler.
        </span>
      )}
    </div>
  )
}
