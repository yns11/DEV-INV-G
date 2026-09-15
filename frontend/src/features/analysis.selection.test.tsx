/**
 * Cocher une ligne, puis la retrouver — la grille triée, l'écran ne l'est pas.
 *
 * Le défaut, tel qu'il s'est produit
 * ----------------------------------
 * La vue Écarts identifiait ses lignes par `article-entrepôt-emplacement-rang`.
 * Or les deux bouts de l'affectation en lot ne comptaient pas dans la même
 * liste : la grille numérote **ce qu'elle affiche**, c'est-à-dire après
 * recherche, filtre de colonne et tri ; l'écran, lui, renumérotait la liste
 * brute reçue du serveur. Trier une colonne — le premier geste que fait
 * n'importe qui devant une grille d'écarts — suffisait à décaler les deux
 * numérotations.
 *
 * Ce que l'utilisateur voyait alors : « Requête mal formée ». Le lot partait
 * vide, parce qu'aucun identifiant coché ne se retrouvait dans la liste brute,
 * et la validation d'entrée refusait une liste de références vide. Le message
 * était exact et n'expliquait rien.
 *
 * Le cas plus discret, et plus grave, était l'autre : quand une partie
 * seulement des identifiants se retrouvait, l'écran postait moins de lignes
 * qu'on en avait cochées et annonçait « n écart(s) mis à jour » sans que rien
 * ne signale les manquantes.
 *
 * Ce que ce contrôle exerce
 * -------------------------
 * La vraie fonction de l'écran — `varianceRowKey` — dans une vraie grille, avec
 * un vrai tri et une vraie recherche. Ce qui est vérifié n'est pas sa forme mais
 * la seule propriété qui compte : **l'identifiant rendu par la grille retrouve,
 * dans la liste d'origine, la ligne que l'on a cochée**. Y remettre un rang la
 * fait tomber.
 */

import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DataGrid, type Column } from '../components/DataGrid'
import { ToastProvider } from '../components/ui'
import { varianceRowKey } from './analysis.variances'

interface Ligne {
  itemNumber: string
  warehouseId: string
  locationId: string
  varianceValue: number
}

/** Trois entrepôts, trois emplacements : le triplet est seul à distinguer. */
const LIGNES: Ligne[] = [
  { itemNumber: 'P-001', warehouseId: 'M1', locationId: 'A01', varianceValue: -40 },
  { itemNumber: 'P-002', warehouseId: 'M1', locationId: 'B02', varianceValue: 900 },
  { itemNumber: 'P-003', warehouseId: 'M2', locationId: 'C03', varianceValue: -12 },
  { itemNumber: 'P-004', warehouseId: 'M2', locationId: 'D04', varianceValue: 7 },
  { itemNumber: 'P-005', warehouseId: 'M3', locationId: 'E05', varianceValue: 300 },
]

const COLONNES: Column<Ligne>[] = [
  { key: 'itemNumber', label: 'Article', sortable: true },
  { key: 'warehouseId', label: 'Entrepôt', sortable: true },
  { key: 'locationId', label: 'Emplacement', sortable: true },
  { key: 'varianceValue', label: 'Écart', numeric: true, sortable: true },
]

/**
 * Monte la grille comme la vue Écarts la monte, et rend de quoi rejouer le
 * geste du lot : `resoudre()` fait exactement ce que fait la barre d'outils —
 * filtrer la liste **brute** sur les identifiants cochés.
 */
function grille() {
  let selected = new Set<string>()

  function Ecran() {
    const [coches, setCoches] = useState<Set<string>>(new Set())
    selected = coches
    return (
      <DataGrid
        columns={COLONNES}
        rows={LIGNES}
        getRowId={varianceRowKey}
        selectable
        selected={coches}
        onSelectedChange={setCoches}
        searchable
      />
    )
  }

  render(
    <ToastProvider>
      <Ecran />
    </ToastProvider>,
  )
  return {
    resoudre: () => LIGNES.filter((ligne) => selected.has(varianceRowKey(ligne))),
    taille: () => selected.size,
  }
}

/** Les lignes réellement rendues, dans l'ordre où elles sont affichées. */
function affichees(): HTMLElement[] {
  const corps = document.querySelector('tbody')
  return Array.from(corps?.querySelectorAll<HTMLElement>('tr[data-row]') ?? [])
}

async function cocher(rang: number) {
  const user = userEvent.setup()
  const cases = within(affichees()[rang]!).getAllByRole('checkbox')
  await user.click(cases[0]!)
}

describe('un lot coché se retrouve dans la liste d’origine', () => {
  it('sans rien avoir trié ni cherché', async () => {
    const { resoudre } = grille()
    await cocher(0)

    expect(resoudre().map((l) => l.itemNumber)).toEqual(['P-001'])
  })

  it('après un tri de colonne — le geste qui cassait tout', async () => {
    const user = userEvent.setup()
    const { resoudre, taille } = grille()

    // Deux clics : décroissant. Le plus gros excédent passe en tête — c'est
    // P-002, deuxième dans la liste reçue. Les deux numérotations divergent
    // donc, ce qui est précisément la condition du défaut.
    const entete = screen.getByRole('columnheader', { name: /Écart/ })
    await user.click(entete)
    await user.click(entete)
    expect(within(affichees()[0]!).getByText('P-002')).toBeInTheDocument()

    await cocher(0)

    // Un identifiant coché, une ligne retrouvée : c'est là que le lot partait
    // vide et que le serveur répondait « requête mal formée ».
    expect(taille()).toBe(1)
    expect(resoudre().map((l) => l.itemNumber)).toEqual(['P-002'])
  })

  it('après une recherche qui ne laisse qu’une ligne', async () => {
    const user = userEvent.setup()
    const { resoudre } = grille()

    await user.type(screen.getByRole('searchbox'), 'P-005')
    expect(affichees()).toHaveLength(1)
    await cocher(0)

    // La ligne était cinquième dans la liste reçue et première à l'écran.
    expect(resoudre().map((l) => l.itemNumber)).toEqual(['P-005'])
  })

  it('et « tout cocher » sur une liste cherchée ne vise que ce qui est montré', async () => {
    const user = userEvent.setup()
    const { resoudre } = grille()

    await user.type(screen.getByRole('searchbox'), 'M2')
    await user.click(screen.getByLabelText('Tout sélectionner'))

    expect(resoudre().map((l) => l.itemNumber)).toEqual(['P-003', 'P-004'])
  })
})

describe('la clé ne désigne que la ligne', () => {
  it('deux emplacements du même article ne se confondent pas', () => {
    // En vue par emplacement, un article apparaît autant de fois qu'il est
    // rangé à d'endroits : c'est le triplet entier qui distingue.
    const a = { itemNumber: 'P-001', warehouseId: 'M1', locationId: 'A01' }
    const b = { itemNumber: 'P-001', warehouseId: 'M1', locationId: 'B02' }
    expect(varianceRowKey(a)).not.toBe(varianceRowKey(b))
  })

  it('et une même ligne garde sa clé quel que soit son rang', () => {
    // La propriété que le rang détruisait, énoncée seule : la clé ne lit que
    // la ligne, donc rien de ce qui l'entoure ne peut la changer.
    const ligne = { itemNumber: 'P-003', warehouseId: 'M2', locationId: 'C03' }
    expect(varianceRowKey(ligne)).toBe(varianceRowKey({ ...ligne }))
    expect(varianceRowKey(ligne)).toBe(
      varianceRowKey(LIGNES.find((l) => l.itemNumber === 'P-003')!),
    )
  })

  it('un emplacement qui porte un séparateur naïf ne collisionne pas', () => {
    // Pourquoi `compositeKey` plutôt qu'un tiret ou une barre écrits ici : une
    // référence et un emplacement peuvent en contenir, et deux lignes
    // différentes recolleraient alors sur la même chaîne.
    const a = { itemNumber: 'P-1', warehouseId: 'M1-A', locationId: '01' }
    const b = { itemNumber: 'P-1', warehouseId: 'M1', locationId: 'A-01' }
    expect(varianceRowKey(a)).not.toBe(varianceRowKey(b))
  })
})
