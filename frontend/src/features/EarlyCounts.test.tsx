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
    decideLabel: vi.fn(
      (
        _campaignId: string,
        body: { labelId: string; decision: string },
      ) => Promise.resolve({ ...body }),
    ),
    unsealJournal: vi.fn(
      (_campaignId: string, _journalId: string, _reason: string) =>
        Promise.resolve({ locations: 57 }),
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
          isSealed: true,
          countedOn: '2026-06-10',
          scope: fixtures.scope,
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
    labelAlerts: () =>
      Promise.resolve([
        {
          labelId: '001609233',
          itemNumber: 'MEL-STA-4412',
          sealedWarehouseId: 'ATP',
          sealedLocationId: 'SOL',
          otherWarehouseId: 'ATP',
          otherLocationId: 'QUAI EXP',
          otherJournalNumber: 'NPEM-523004',
          otherQtyCounted: 8,
          decision: null,
        },
      ]),
    toRescan: () =>
      Promise.resolve([
        {
          warehouseId: 'ATP',
          locationId: 'SOL',
          journalNumber: 'NPEM-521215',
          erpJournalId: 'j-1',
          isSealed: true,
          labels: [
            {
              labelId: '001609233',
              itemNumber: 'MEL-STA-4412',
              otherWarehouseId: 'ATP',
              otherLocationId: 'QUAI EXP',
              comment: '',
            },
          ],
        },
      ]),
    decideLabel: fixtures.decideLabel,
    unsealJournal: fixtures.unsealJournal,
    recountedInPlace: () =>
      Promise.resolve([
        {
          sealedWarehouseId: 'ATP',
          sealedLocationId: 'SF1',
          ownerJournalNumber: 'NPEM-521215',
          otherJournalNumber: 'NPEM-522821',
          labelCount: 6,
        },
      ]),
    drifts: () => Promise.resolve([]),
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

  it.each(['', '?vue=derives', '?vue=etiquettes', '?vue=rescanner'])(
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

describe('les étiquettes', () => {
  it('offrent les trois issues une fois une ligne cochée', async () => {
    show(null, '?vue=etiquettes')
    const row = await screen.findByText('001609233')
    expect(row).toBeTruthy()
    fireEvent.click(screen.getAllByRole('checkbox')[1]!)
    expect(
      await screen.findByRole('button', { name: 'La mettre au nouvel emplacement' }),
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'L’enlever du nouvel emplacement' }),
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Signaler : à rescanner' }),
    ).toBeTruthy()
  })

  it('appellent le client avec l’issue et les deux emplacements', async () => {
    fixtures.decideLabel.mockClear()
    show(null, '?vue=etiquettes')
    await screen.findByText('001609233')
    fireEvent.click(screen.getAllByRole('checkbox')[1]!)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Signaler : à rescanner' }),
    )

    await waitFor(() => expect(fixtures.decideLabel).toHaveBeenCalled())
    const body = fixtures.decideLabel.mock.calls[0]?.[1] as {
      labelId: string
      decision: string
      sealedLocationId: string
      otherLocationId: string
    }
    expect(body.labelId).toBe('001609233')
    expect(body.decision).toBe('RECOUNT')
    expect(body.sealedLocationId).toBe('SOL')
    expect(body.otherLocationId).toBe('QUAI EXP')
  })
})

describe('les emplacements recomptés sur place', () => {
  /**
   * Ces lignes-là remplissaient la liste des étiquettes comptées ailleurs,
   * avec le même emplacement dans les deux colonnes — « ATP / SF1 comptée
   * aussi en ATP / SF1 ». La pièce n'a pas bougé, et aucune des trois issues
   * n'a de sens sans nouvel emplacement. Elles en sortent ; le bandeau dit
   * qu'elles existent, sans quoi les retirer les cacherait.
   */
  it('sont dits au-dessus de la liste, pas dedans', async () => {
    show(null, '?vue=etiquettes')
    expect(
      await screen.findByText(/Emplacements recomptés sur place/),
    ).toBeTruthy()
    // Le journal retenu et celui qui ne l'est pas : c'est la seule chose à
    // savoir, puisqu'il n'y a rien à trancher.
    expect(screen.getByText(/retenu NPEM-521215, ignoré NPEM-522821/)).toBeTruthy()
  })
})

describe('la liste à rescanner', () => {
  it('expose l’ancien emplacement, celui qu’il faut desceller', async () => {
    show(null, '?vue=rescanner')
    expect(await screen.findByText(/ATP \/ SOL/)).toBeTruthy()
    expect(screen.getByText(/NPEM-521215/)).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Desceller le journal' }),
    ).toBeTruthy()
  })
})

describe('la grille des journaux', () => {
  it('dit qu’un journal est scellé et depuis quelle date de comptage', async () => {
    show(null)
    // L'en-tête de colonne, plus le badge du seul journal scellé — le second
    // ne l'est pas, et un troisième « Scellé » voudrait dire qu'il l'est.
    expect((await screen.findAllByText('Scellé')).length).toBe(2)
    expect(screen.getByText('10/06/2026')).toBeTruthy()
  })

  it('offre le geste inverse là où le scellement s’est fait', async () => {
    // Desceller n'existait que dans l'onglet « À rescanner », c'est-à-dire
    // dans le seul cas où une étiquette avait signalé l'emplacement. Un
    // périmètre coché de travers n'avait aucun retour en arrière, alors que la
    // route et le service l'assuraient déjà.
    show(null)
    expect(
      await screen.findByRole('button', { name: 'Desceller' }),
    ).toBeTruthy()
  })

  it('ne l’offre que sur les journaux scellés', async () => {
    // Le second journal n'est pas déclaré : rien à desceller, et un bouton qui
    // ne peut que refuser vaut moins que pas de bouton.
    show(null)
    await screen.findByText('NPEM-521301')
    expect(screen.getAllByRole('button', { name: 'Desceller' }).length).toBe(1)
  })

  it('demande un motif, parce que desceller annule une preuve datée', async () => {
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockReturnValue('périmètre coché de travers')
    fixtures.unsealJournal.mockClear()
    show(null)
    fireEvent.click(await screen.findByRole('button', { name: 'Desceller' }))

    await waitFor(() => expect(fixtures.unsealJournal).toHaveBeenCalled())
    expect(prompt).toHaveBeenCalled()
    expect(fixtures.unsealJournal.mock.calls[0]?.slice(1)).toEqual([
      'j-1',
      'périmètre coché de travers',
    ])
    prompt.mockRestore()
  })

  it('n’appelle rien si le motif est laissé vide', async () => {
    // Deux clics : le premier avec un motif blanc, le second avec un vrai. Si
    // le blanc passait, le mock porterait deux appels — et l'ordre le dirait.
    // Vérifier l'absence d'appel juste après le premier clic ne prouverait
    // rien : `mutate` diffère l'appel d'une micro-tâche, et l'assertion
    // passerait aussi bien avec la garde qu'en son absence.
    const prompt = vi
      .spyOn(window, 'prompt')
      .mockReturnValueOnce('   ')
      .mockReturnValueOnce('bon motif')
    fixtures.unsealJournal.mockClear()
    show(null)
    const button = await screen.findByRole('button', { name: 'Desceller' })
    fireEvent.click(button)
    fireEvent.click(button)

    await waitFor(() => expect(fixtures.unsealJournal).toHaveBeenCalledTimes(1))
    expect(fixtures.unsealJournal.mock.calls[0]?.[2]).toBe('bon motif')
    prompt.mockRestore()
  })
})
