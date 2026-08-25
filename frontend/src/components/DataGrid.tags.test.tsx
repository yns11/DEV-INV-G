/**
 * Le filtre d'une colonne qui porte plusieurs étiquettes par ligne.
 *
 * Le défaut, capture à l'appui : la colonne « Signalements » de la vue Écarts
 * affiche jusqu'à quatre badges par ligne — au-delà des seuils, hors ERP, non
 * compté, la cause retenue — et son filtre ne proposait qu'une seule entrée,
 * « (vide) 408 », sur les quatre cent huit lignes de l'écran.
 *
 * La cause : la colonne n'avait qu'un `render`. Le filtre lit `value`, à défaut
 * la clé de la ligne — ici `row.flags`, qui n'existe pas. Toutes les lignes
 * valaient donc « vide », et la seule question que la colonne permet de poser —
 * « montre-moi les hors ERP » — n'était pas posable.
 *
 * Le remède n'est pas un `value` : une ligne en porte **plusieurs**, et une
 * chaîne jointe aurait fait de chaque *combinaison* une entrée distincte —
 * « au-delà des seuils · hors ERP » à côté de « au-delà des seuils », et cocher
 * la seconde n'aurait pas montré les lignes de la première. D'où `tags`, une
 * facette multivaluée : une entrée par étiquette, et une ligne retenue dès
 * qu'elle en porte **une** de celles cochées.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DataGrid, type Column } from './DataGrid'
import { ToastProvider } from './ui'

interface Variance {
  itemNumber: string
  isMaterial: boolean
  countedOnly: boolean
  bookOnly: boolean
}

const columns: Column<Variance>[] = [
  { key: 'itemNumber', label: 'Article' },
  {
    key: 'flags',
    label: 'Signalements',
    sortable: false,
    tags: (row) => [
      ...(row.isMaterial ? ['au-delà des seuils'] : []),
      ...(row.bookOnly ? ['non compté'] : []),
      ...(row.countedOnly ? ['hors ERP'] : []),
    ],
    render: (row) => <span>{row.isMaterial ? 'badge' : ''}</span>,
  },
]

function variance(over: Partial<Variance> & { itemNumber: string }): Variance {
  return { isMaterial: false, countedOnly: false, bookOnly: false, ...over }
}

/** Le jeu de la capture, en miniature. */
const ROWS: Variance[] = [
  // Deux étiquettes à la fois : le cas que la chaîne jointe cassait.
  variance({ itemNumber: 'P-00171843', isMaterial: true, countedOnly: true }),
  variance({ itemNumber: 'P-00171838', isMaterial: true, countedOnly: true }),
  // Une seule.
  variance({ itemNumber: 'P-00001423', isMaterial: true }),
  variance({ itemNumber: 'P-00068308', isMaterial: true }),
  // Une autre seule.
  variance({ itemNumber: 'P-00099999', bookOnly: true }),
  // Aucune : elle doit se ranger sous « (vide) ».
  variance({ itemNumber: 'P-00000001' }),
]

function grid(rows: Variance[] = ROWS) {
  return render(
    <ToastProvider>
      <DataGrid columns={columns} rows={rows} getRowId={(row) => row.itemNumber} />
    </ToastProvider>,
  )
}

const shownItems = () =>
  Array.from(document.querySelectorAll<HTMLElement>('tr[data-row]')).map(
    (row) => row.textContent?.match(/P-\d+/)?.[0] ?? '',
  )

/**
 * Ouvre le panneau de filtre de la colonne « Signalements ».
 *
 * Deux clics, comme dans l'application : la barre de filtres est repliée, puis
 * chaque colonne a sa puce.
 */
async function openFilter(rows: Variance[] = ROWS) {
  grid(rows)
  await userEvent.click(screen.getByRole('button', { name: /Filtres/ }))
  const trigger = screen
    .getAllByRole('button')
    .find((b) => (b.textContent ?? '').includes('Signalements'))
  if (!trigger) throw new Error('aucun déclencheur de filtre pour « Signalements »')
  await userEvent.click(trigger)
  return within(screen.getByRole('dialog', { name: 'Signalements' }))
}

/** Les cases du panneau ouvert, par leur libellé. */
function option(panel: ReturnType<typeof within>, label: RegExp): HTMLElement {
  const found = panel
    .getAllByRole('checkbox')
    .find((box: HTMLElement) => label.test(box.closest('label')?.textContent ?? ''))
  if (!found) throw new Error(`aucune case « ${label} »`)
  return found
}

function labels(panel: ReturnType<typeof within>): string[] {
  return panel
    .getAllByRole('checkbox')
    .map((box: HTMLElement) => box.closest('label')?.textContent ?? '')
}

describe('les valeurs proposées', () => {
  it('sont les étiquettes elles-mêmes, pas « (vide) »', async () => {
    const panel = await openFilter()

    const found = labels(panel).join(' | ')
    expect(found).toContain('au-delà des seuils')
    expect(found).toContain('hors ERP')
    expect(found).toContain('non compté')
  })

  it('comptent les lignes qui les portent, pas les combinaisons', async () => {
    /* Quatre lignes « au-delà des seuils » dont deux aussi « hors ERP » : le
       compte de la première doit être quatre, pas deux. */
    const panel = await openFilter()

    const row = option(panel, /au-delà des seuils/).closest('label')
    expect(row?.textContent).toContain('4')
  })

  it('gardent « (vide) » pour les lignes sans étiquette', async () => {
    const panel = await openFilter()

    expect(labels(panel).some((l) => /vide/i.test(l))).toBe(true)
  })

  it('ne proposent pas de combinaison', async () => {
    /* « au-delà des seuils · hors ERP » comme entrée à part entière est
       exactement ce que la chaîne jointe aurait produit. */
    const panel = await openFilter()

    for (const label of labels(panel)) {
      expect(label).not.toMatch(/seuils.*hors ERP/)
    }
  })
})

describe('cocher une étiquette', () => {
  it('garde toutes les lignes qui la portent, seule ou accompagnée', async () => {
    const panel = await openFilter()

    await userEvent.click(option(panel, /hors ERP/))

    expect(shownItems().sort()).toEqual(['P-00171838', 'P-00171843'])
  })

  it('garde aussi celles qui en portent d’autres', async () => {
    /* Le cœur du sujet : « au-delà des seuils » doit ramener les quatre
       lignes, y compris les deux qui sont en plus « hors ERP ». */
    const panel = await openFilter()

    await userEvent.click(option(panel, /au-delà des seuils/))

    expect(shownItems()).toHaveLength(4)
  })

  it('deux étiquettes cochées font une union, pas une intersection', async () => {
    /* « montre-moi les hors ERP et les non comptés », pas « ceux qui sont les
       deux à la fois » — lesquels n'existent pas. */
    const panel = await openFilter()

    await userEvent.click(option(panel, /hors ERP/))
    await userEvent.click(option(panel, /non compté/))

    expect(shownItems().sort()).toEqual([
      'P-00099999', 'P-00171838', 'P-00171843',
    ])
  })

  it('« (vide) » ne garde que les lignes sans aucune étiquette', async () => {
    const panel = await openFilter()

    await userEvent.click(option(panel, /vide/i))

    expect(shownItems()).toEqual(['P-00000001'])
  })
})

describe('la recherche libre', () => {
  it('trouve une ligne par son étiquette', async () => {
    /* Les badges sont du texte à l'écran : ne pas les chercher ferait mentir
       la recherche sur ce qu'elle couvre. */
    grid()

    await userEvent.type(screen.getByRole('searchbox'), 'hors ERP')

    expect(shownItems().sort()).toEqual(['P-00171838', 'P-00171843'])
  })
})
