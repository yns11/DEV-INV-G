/**
 * L'écran des comptages avancés, monté pour de vrai.
 *
 * Il ne s'affichait pas. `useOutletContext<T>()` est une **assertion**, pas une
 * vérification : l'écran y lisait `{ campaign, overview }` alors que la coquille
 * de campagne passe l'aperçu tel quel, et TypeScript n'avait rien à redire.
 * `overview` valait donc `undefined`, et le premier accès levait
 * « Cannot read properties of undefined (reading 'campaign') » — en production,
 * sur une campagne où tout allait bien.
 *
 * Le contrôle de câblage à côté ne pouvait pas le voir : il lit les fichiers
 * comme du texte, et le texte était cohérent. Il fallait monter le composant.
 * C'est tout ce que fait ce fichier, et c'est pour ça qu'il existe.
 *
 * Le second défaut qu'il tient est du même genre, en plus discret : la bannière
 * du dernier import lisait `journalsImportedAt` au travers d'un cast, alors que
 * la campagne voyage en `snake_case`. Elle affichait donc « aucun import »
 * pour toujours, y compris l'heure d'après un import réussi — un écran qui ne
 * plante pas, qui ne se plaint pas, et qui ment.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import EarlyCounts from './EarlyCounts'
import { ToastProvider } from '../components/ui'
import type { Overview } from '../lib/types'

// L'écran interroge l'API dès qu'il s'affiche. Ce qui se vérifie ici est le
// rendu, pas ce qu'il y a derrière : les réponses sont vides et immédiates.
vi.mock('../lib/api', () => ({
  api: {
    erpJournals: () => Promise.resolve([]),
    earlyBatches: () => Promise.resolve([]),
    drifts: () => Promise.resolve([]),
    labelAlerts: () => Promise.resolve([]),
    contracts: () =>
      Promise.resolve([
        {
          key: 'count_journal_lines',
          title: 'Lignes de journaux de comptage',
          description: '',
          hint: '',
          fields: [],
        },
      ]),
    alerts: () => Promise.resolve({ controls: 0, consolidation: 0 }),
    erpSource: () => Promise.resolve({ available: false }),
    erpStockDates: () => Promise.resolve({ dates: [] }),
  },
  downloads: { importTemplate: () => '' },
}))

function overview(importedAt: string | null): Overview {
  return {
    campaign: {
      id: 'camp-1',
      code: 'INV-2026-T3',
      status: 'COUNTING',
      book_stock_frozen_at: null,
      journals_imported_at: importedAt,
    },
    permissions: { earlyCounts: true },
    access: { role: 'OWNER', canWrite: true, isOwner: true, owner: 'test' },
    journalProgress: { total: 0, complete: 0, running: 0, pending: 0, ratio: 0 },
    perimeter: { resolved: false, journalCount: 0, zoneCount: 0 },
  } as unknown as Overview
}

function show(importedAt: string | null, search = '') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/${search}`]}>
          <Routes>
            <Route path="/" element={<Outlet context={overview(importedAt)} />}>
              <Route index element={<EarlyCounts />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('l’écran s’affiche', () => {
  it('sans lever, ce qui était tout le problème', () => {
    expect(() => show(null)).not.toThrow()
  })

  it.each(['', '?vue=lots', '?vue=derives', '?vue=etiquettes'])(
    'y compris sur la sous-section « %s »',
    (search) => {
      expect(() => show(null, search)).not.toThrow()
    },
  )
})

describe('la bannière du dernier import', () => {
  it('dit qu’il n’y en a pas eu quand c’est le cas', () => {
    show(null)
    expect(screen.getByText('Aucun import de journaux')).toBeTruthy()
  })

  it('dit quand, dès qu’il y en a eu un', () => {
    show('2026-06-13T07:30:00Z')
    expect(screen.getByText('Dernier import de journaux')).toBeTruthy()
    expect(screen.queryByText('Aucun import de journaux')).toBeNull()
  })
})

describe('le panneau d’import', () => {
  it('est sur cet écran, parce que celui des journaux n’ouvre qu’au jour J', async () => {
    // Sans lui, l'état vide disait « chargez l'export » depuis le seul écran
    // d'où c'était impossible : l'écran des journaux de comptage attend le
    // stock ERP, et le lot avancé se compte avant.
    show(null)
    expect(
      await screen.findByText(/Chaque import remplace les journaux/),
    ).toBeTruthy()
  })
})
