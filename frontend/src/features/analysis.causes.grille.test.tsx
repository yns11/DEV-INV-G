/**
 * La grille d'affectation des causes, montée pour de vrai.
 *
 * Deux manques et un défaut, tous les trois invisibles à la lecture du source.
 *
 * **Le stock ERP manquait.** On affectait une cause en voyant le compté, le
 * physique et la valeur de l'écart, mais pas ce que l'ERP annonçait. « −40 » ne
 * se raconte pas de la même façon selon qu'il manque 40 pièces sur 45 ou sur
 * 4 000.
 *
 * **Le commentaire était en lecture seule.** Il s'écrivait depuis la vue Écarts
 * et se relisait ici, ce qui est exactement l'inverse de l'usage : cet écran est
 * celui où l'on solde ce qui reste.
 *
 * **Et il s'effaçait.** La liste déroulante de cause postait `comment: ''`. Le
 * serveur fait foi du formulaire, commentaire vidé compris — c'est ce qui permet
 * d'effacer un commentaire en effaçant le champ — donc changer une cause depuis
 * cette grille supprimait sans rien dire le commentaire écrit ailleurs. Rien ne
 * pouvait le signaler : la colonne d'à côté était muette, et le texte
 * disparaissait de la ligne.
 *
 * Ces contrôles regardent ce qui **part au serveur**, parce que c'est là que le
 * défaut vivait : l'écran, lui, se montait très bien.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ToastProvider } from '../components/ui'
import type { Overview, VarianceRow } from '../lib/types'

const saveVarianceAnalysis = vi.fn(() => Promise.resolve({}))
const saveVarianceCauses = vi.fn(() => Promise.resolve({ updated: 0 }))

vi.mock('../lib/api', () => ({
  api: {
    causeSplit: () => Promise.resolve({ unassignedShare: 0, rows: [] }),
    causes: () =>
      Promise.resolve([
        { code: '1', label: 'Erreur de saisie' },
        { code: '99', label: 'Autre' },
      ]),
    variances: () => Promise.resolve(LIGNES),
    saveVarianceAnalysis: (...args: unknown[]) => saveVarianceAnalysis(...(args as [])),
    saveVarianceCauses: (...args: unknown[]) => saveVarianceCauses(...(args as [])),
    suggestCauses: () => Promise.resolve({ suggestions: 0 }),
  },
}))

const { CausesTab } = await import('./analysis.causes')

function ligne(patch: Partial<VarianceRow>): VarianceRow {
  return {
    itemNumber: 'P-001',
    name: 'Vis M6',
    warehouseId: 'M1',
    locationId: 'A01',
    manufacturedProduct: '',
    unit: 'PCE',
    unitCost: 1,
    bookQty: 4000,
    bookValue: 4000,
    countedQty: 3960,
    physicalQty: 3960,
    physicalValue: 3960,
    varianceQty: -40,
    varianceValue: -40,
    causeCode: null,
    comment: '',
    aiSuggestedCause: null,
    aiConfidence: null,
    aiRationale: '',
    ...patch,
  } as unknown as VarianceRow
}

/** Ce que le serveur rend, remis à neuf avant chaque contrôle. */
let LIGNES: VarianceRow[] = []

const PAR_DEFAUT = (): VarianceRow[] => [
  ligne({ itemNumber: 'P-001', bookQty: 4000, comment: 'palette en zone B' }),
  ligne({ itemNumber: 'P-002', bookQty: 45, countedQty: 5, varianceQty: -40, comment: '' }),
]

function ecran() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const overview = { permissions: { analysis: true } } as unknown as Overview
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <CausesTab campaignId="camp-1" overview={overview} />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

/** La ligne de la grille qui porte cette référence. */
async function rangee(itemNumber: string): Promise<HTMLElement> {
  const cellule = await screen.findByText(itemNumber)
  return cellule.closest('tr')!
}

beforeEach(() => {
  saveVarianceAnalysis.mockClear()
  saveVarianceCauses.mockClear()
  LIGNES = PAR_DEFAUT()
})

describe('le stock ERP est sous les yeux de qui affecte la cause', () => {
  it('la colonne existe et porte la quantité de la ligne', async () => {
    ecran()
    expect(await screen.findByText('Stock ERP')).toBeInTheDocument()
    // 4 000, séparateur insécable à la française.
    expect(within(await rangee('P-001')).getByText(/4\s000/)).toBeInTheDocument()
  })

  it('et elle se décompose comme ses voisines', async () => {
    // La quantité est cliquable : « d'où sort ce chiffre » est la question
    // qu'on se pose juste avant de choisir une cause, et les trois colonnes
    // voisines y répondent déjà.
    ecran()
    const cellule = within(await rangee('P-001')).getByText(/4\s000/)
    expect(cellule.closest('button.drill')).toBeTruthy()
    expect(cellule.closest('button')).toHaveAttribute('title', 'Voir la décomposition')
  })
})

describe('le commentaire se saisit ici, et rien ne l’efface', () => {
  it('la cellule est un champ, pas un texte mort', async () => {
    ecran()
    const champ = within(await rangee('P-001')).getByDisplayValue('palette en zone B')
    expect(champ).toBeEnabled()
    // Un champ en lecture seule est « enabled » lui aussi : sans cette
    // seconde vérification, la colonne pourrait redevenir muette sans que rien
    // ne le dise.
    expect(champ).not.toHaveAttribute('readonly')
  })

  it('le sortir après l’avoir changé enregistre la ligne entière', async () => {
    const user = userEvent.setup()
    ecran()
    const champ = within(await rangee('P-002')).getByPlaceholderText(/constaté/)
    await user.click(champ)
    await user.keyboard('recomptée le 12')
    await user.tab()

    await waitFor(() => expect(saveVarianceAnalysis).toHaveBeenCalledTimes(1))
    expect(saveVarianceAnalysis).toHaveBeenCalledWith(
      'camp-1',
      'P-002',
      expect.objectContaining({ comment: 'recomptée le 12' }),
    )
  })

  it('le traverser sans y toucher n’écrit rien', async () => {
    // Sans ce garde, descendre la colonne au clavier réécrirait chaque ligne
    // et signerait chacune au journal d'audit.
    const user = userEvent.setup()
    ecran()
    const champ = within(await rangee('P-001')).getByDisplayValue('palette en zone B')
    await user.click(champ)
    await user.tab()

    expect(saveVarianceAnalysis).not.toHaveBeenCalled()
  })

  it('changer la cause emporte le commentaire au lieu de l’effacer', async () => {
    /* Le défaut, tel qu'il s'est produit : la liste déroulante postait
       `comment: ''`, le serveur faisait foi du formulaire, et le commentaire
       écrit depuis la vue Écarts disparaissait sans un mot. */
    const user = userEvent.setup()
    ecran()
    const liste = within(await rangee('P-001')).getByRole('combobox')
    await user.selectOptions(liste, '1')

    await waitFor(() => expect(saveVarianceAnalysis).toHaveBeenCalledTimes(1))
    expect(saveVarianceAnalysis).toHaveBeenCalledWith(
      'camp-1',
      'P-001',
      expect.objectContaining({ causeCode: '1', comment: 'palette en zone B' }),
    )
  })

  it('et changer le commentaire garde la cause', async () => {
    // La symétrie : chaque champ envoie la valeur courante de l'autre, sinon
    // le défaut réapparaît dans l'autre sens.
    const user = userEvent.setup()
    LIGNES = [ligne({ itemNumber: 'P-003', causeCode: '99', comment: 'à revoir' })]
    ecran()
    const champ = within(await rangee('P-003')).getByDisplayValue('à revoir')
    await user.clear(champ)
    await user.keyboard('soldé')
    await user.tab()

    await waitFor(() => expect(saveVarianceAnalysis).toHaveBeenCalledTimes(1))
    expect(saveVarianceAnalysis).toHaveBeenCalledWith(
      'camp-1',
      'P-003',
      expect.objectContaining({ causeCode: '99', comment: 'soldé' }),
    )
  })
})
