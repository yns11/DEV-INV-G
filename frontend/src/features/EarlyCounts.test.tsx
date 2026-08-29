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
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import EarlyCounts from './EarlyCounts'
import { ToastProvider } from '../components/ui'
import type { Overview } from '../lib/types'

/**
 * Un journal comme l'usine en produit : cinquante-sept emplacements.
 *
 * Le chiffre n'est pas décoratif. Écrits bout à bout dans la cellule, ces
 * cinquante-sept codes poussaient la ligne sur six hauteurs et chassaient les
 * autres journaux hors de l'écran — c'est ce qu'une campagne réelle a montré.
 */
const fixtures = vi.hoisted(() => {
  const scope = Array.from({ length: 57 }, (_, i) => ({
    warehouseId: 'QUAL',
    locationId: `APQP ${'CDEFGHIJKLMNO'[Math.floor(i / 5)]}${i % 5}`,
  }))
  return {
    scope,
    createEarlyBatch: vi.fn(
      (_campaignId: string, body: { code: string; erpJournalIds: string[] }) =>
        Promise.resolve({ id: 'lot-1', code: body.code, locations: scope }),
    ),
  }
})

// L'écran interroge l'API dès qu'il s'affiche. Ce qui se vérifie ici est le
// rendu, pas ce qu'il y a derrière : les réponses sont immédiates.
vi.mock('../lib/api', () => ({
  api: {
    erpJournals: () =>
      Promise.resolve([
        {
          id: 'j-1',
          journalNumber: 'NPEM-521213',
          kind: 'INVE',
          description: 'Inventaire par étiquette',
          lineCount: 217,
          erpPosted: true,
          scopeDeclared: true,
          scope: fixtures.scope,
        },
        {
          id: 'j-3',
          journalNumber: 'NPEM-521288',
          kind: 'INVE',
          description: 'Inventaire par étiquette',
          lineCount: 7,
          erpPosted: true,
          scopeDeclared: true,
          scope: [{ warehouseId: 'MAG', locationId: 'RACK A' }],
        },
        {
          id: 'j-2',
          journalNumber: 'NPEM-521301',
          kind: 'INVE',
          description: 'Inventaire par étiquette',
          lineCount: 5,
          erpPosted: false,
          scopeDeclared: false,
          scope: [],
        },
      ]),
    earlyBatches: () =>
      Promise.resolve([
        {
          id: 'lot-0',
          code: 'LOT-DEJA',
          isClosed: true,
          isSealed: true,
          locations: [{ warehouseId: 'MAG', locationId: 'RACK A' }],
        },
      ]),
    createEarlyBatch: fixtures.createEarlyBatch,
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
  downloads: { importTemplate: () => '', gridTemplate: () => '' },
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

describe('la colonne Périmètre', () => {
  it('résume au lieu de déballer cinquante-sept emplacements', async () => {
    show(null)
    // Le nombre d'abord : c'est la grandeur du lot qu'on ouvrira. Puis les
    // deux premiers, puis le reste compté.
    expect(await screen.findByText(/57 emplacements/)).toBeTruthy()
    expect(screen.getByText(/\+55/)).toBeTruthy()
  })

  it('garde la liste entière pour le survol, le filtre et l’export', async () => {
    show(null)
    const cell = await screen.findByText(/57 emplacements/)
    // Le dernier des cinquante-sept : s'il est là, rien n'a été tronqué.
    expect(cell.getAttribute('title')).toContain('QUAL / APQP N1')
  })
})

describe('ouvrir un lot', () => {
  it('se propose toujours, y compris quand aucun lot n’existe', async () => {
    // C'est exactement là que le geste manquait : l'état vide disait « ouvrez
    // un lot dessus » et l'écran n'offrait aucun moyen de le faire. Le bouton
    // vit donc hors de la liste, pas dedans.
    show(null, '?vue=lots')
    expect(
      await screen.findByRole('button', { name: 'Ouvrir un lot' }),
    ).toBeTruthy()
  })

  it('grise un journal dont les emplacements sont déjà pris', async () => {
    // Le service refuse — un emplacement ne se précompte qu'une fois. Le dire
    // avant plutôt qu'après : proposer puis refuser fait découvrir la règle au
    // plus mauvais moment.
    show(null, '?vue=lots')
    fireEvent.click(await screen.findByRole('button', { name: 'Ouvrir un lot' }))
    await screen.findByText('NPEM-521288')
    expect(screen.getByText(/Déjà dans LOT-DEJA/)).toBeTruthy()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes.some((b) => b.hasAttribute('disabled'))).toBe(true)
  })

  it('ne propose que les journaux dont le périmètre est déclaré', async () => {
    show(null, '?vue=lots')
    fireEvent.click(await screen.findByRole('button', { name: 'Ouvrir un lot' }))
    expect(await screen.findByText('NPEM-521213')).toBeTruthy()
    expect(screen.queryByText('NPEM-521301')).toBeNull()
  })

  it('appelle le client avec le code et les journaux choisis', async () => {
    fixtures.createEarlyBatch.mockClear()
    show(null, '?vue=lots')
    fireEvent.click(await screen.findByRole('button', { name: 'Ouvrir un lot' }))
    await screen.findByText('NPEM-521213')

    fireEvent.change(screen.getByPlaceholderText('LOT-J2-ATELIER'), {
      target: { value: 'LOT-J2-ATELIER' },
    })
    fireEvent.click(screen.getAllByRole('checkbox')[0]!)
    fireEvent.click(screen.getByRole('button', { name: 'Ouvrir le lot' }))

    await waitFor(() => expect(fixtures.createEarlyBatch).toHaveBeenCalled())
    const body = fixtures.createEarlyBatch.mock.calls[0]?.[1]
    expect(body?.code).toBe('LOT-J2-ATELIER')
    expect(body?.erpJournalIds).toEqual(['j-1'])
  })

  it('refuse de partir sans code ni journal', async () => {
    show(null, '?vue=lots')
    fireEvent.click(await screen.findByRole('button', { name: 'Ouvrir un lot' }))
    const submit = await screen.findByRole('button', { name: 'Ouvrir le lot' })
    expect(submit.hasAttribute('disabled')).toBe(true)
  })
})
