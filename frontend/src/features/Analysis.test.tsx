/**
 * L'aiguillage de l'écran d'analyse, et la porte qu'il tient.
 *
 * Les écarts se calculent à partir du stock ERP gelé : les ouvrir avant
 * afficherait des tirets là où on vient chercher des montants, d'où le refus
 * franc plutôt qu'un écran vide.
 *
 * **Les contrôles passent cette porte.** Ils ne calculent aucun écart : ils
 * relisent le dossier tel qu'il est. La plupart portent sur ce qui se prépare
 * avant le gel — le référentiel, les nomenclatures, les zones — et c'est là que
 * le stock ERP signale les références qu'il n'a pas pu charger, ce qui se lit le
 * jour de l'import et non trois semaines plus tard.
 *
 * Le contrôle est ici et non sur la barre latérale parce que ce sont deux
 * verrous distincts, posés à deux endroits : la barre décide du lien, l'écran
 * décide de ce qu'il affiche. Retirer l'un sans l'autre laissait un lien qui
 * mène à « Analyse indisponible ».
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { Analysis, type AnalysisView } from './Analysis'
import { ToastProvider } from '../components/ui'
import type { Overview } from '../lib/types'

// L'écran interroge l'API dès qu'il s'affiche. Ce qui est vérifié ici est la
// porte, pas ce qu'il y a derrière : la réponse est donc vide et immédiate,
// plutôt qu'un `fetch` qui échouerait au bout d'un délai.
vi.mock('../lib/api', () => ({
  api: {
    controls: () => Promise.resolve({ summary: { bySeverity: {} }, groups: [], findings: [] }),
    closureChecklist: () => Promise.resolve(undefined),
  },
}))

function overview(frozen: string | null, sealed = 0): Overview {
  return {
    campaign: {
      id: 'camp-1',
      code: 'INV-2026-T3',
      status: 'PREPARATION',
      book_stock_frozen_at: frozen,
    },
    counts: { items: 0, bookStockLines: 0, sealedLocations: sealed },
    permissions: {},
  } as unknown as Overview
}

function show(view: AnalysisView, frozen: string | null, sealed = 0) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <Routes>
            <Route path="/" element={<Outlet context={overview(frozen, sealed)} />}>
              <Route index element={<Analysis view={view} />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const refused = () => screen.queryByText('Analyse indisponible')

describe('sans stock ERP gelé', () => {
  it('les contrôles s’affichent quand même', () => {
    show('controls', null)
    expect(refused()).toBeNull()
  })

  it('les écarts sont refusés, en disant quoi faire', () => {
    show('variances', null)
    expect(refused()).not.toBeNull()
    expect(screen.getByText(/Scellez un/)).toBeTruthy()
  })

  it.each(['variances', 'causes', 'adjustments'] as const)('%s est refusée', (view) => {
    show(view, null)
    expect(refused()).not.toBeNull()
  })
})

describe('une fois le stock gelé', () => {
  it.each(['controls', 'variances', 'causes', 'adjustments'] as const)(
    '%s s’affiche',
    (view) => {
      show(view, '2026-09-01T06:00:00Z')
      expect(refused()).toBeNull()
    },
  )
})


describe('un précomptage scellé ouvre l’analyse avant le gel', () => {
  /**
   * Le gel du stock ERP est **global** et arrive au jour J. Le scellement d'un
   * précomptage est un gel **par emplacement** : pour ceux-là, référence et
   * comptage sont déjà posés et ne bougeront plus, puisque le chargement
   * général les préserve. Leur écart est donc définitif dès la déclaration.
   *
   * Attendre le gel général le cachait pendant les jours où l'on peut encore
   * aller voir sur le terrain — c'est-à-dire au seul moment où il sert.
   */
  it('les écarts s’affichent', () => {
    show('variances', null, 3)
    expect(refused()).toBeNull()
  })

  it('et disent sur combien d’emplacements ils portent', () => {
    show('variances', null, 3)
    expect(
      screen.getByText(/Écarts partiels — stock ERP pas encore gelé/),
    ).toBeTruthy()
    expect(screen.getByText(/3 emplacement/)).toBeTruthy()
  })

  it('le bandeau disparaît une fois le stock gelé', () => {
    show('variances', '2026-06-13T08:00:00Z', 3)
    expect(
      screen.queryByText(/Écarts partiels — stock ERP pas encore gelé/),
    ).toBeNull()
  })
})
