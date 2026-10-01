/**
 * Ce que le rapport d'import dit d'un périmètre restreint.
 *
 * Le fichier ERP couvre toute l'usine ; la campagne choisit son périmètre. Les
 * lignes des articles exclus ne sont pas chargées — et ne sont pas non plus
 * refusées, sans quoi l'écriture entière serait annulée puisque le stock ERP
 * remplace l'ensemble.
 *
 * Reste à le **dire**. Écarter mille cinq cents lignes en silence serait la
 * troncature muette que ce projet refuse partout ailleurs : l'écran annoncerait
 * « 40 lignes enregistrées » sur un fichier qui en portait mille six cents,
 * sans que rien n'explique l'écart.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ImportReport } from './ImportPanel'
import type { GridContract, ImportResult } from '../lib/types'

const CONTRACT = { key: 'book_stock', label: 'Stock ERP', fields: [] } as unknown as GridContract

function result(over: Partial<ImportResult> = {}): ImportResult {
  return {
    target: 'book_stock',
    rowsReceived: 1598,
    rowsAccepted: 40,
    rowsRejected: 0,
    ok: true,
    errors: [],
    warnings: [],
    truncatedErrors: 0,
    missingColumns: [],
    unknownColumns: [],
    duplicateKeys: [],
    batchId: 'lot-1',
    details: {},
    ...over,
  } as ImportResult
}

function show(over: Partial<ImportResult> = {}) {
  render(
    <ImportReport result={result(over)} contract={CONTRACT} onCancel={() => {}} done />,
  )
}

describe('Les lignes hors périmètre', () => {
  it('sont annoncées avec leur décompte', () => {
    show({ details: { outOfScopeLines: 1558, outOfScopeItems: 412 } })

    expect(screen.getByText(/1 558 ligne\(s\) hors périmètre/)).toBeTruthy()
  })

  it('disent combien d’articles elles concernent', () => {
    show({ details: { outOfScopeLines: 1558, outOfScopeItems: 412 } })

    expect(screen.getByText(/412 article\(s\) exclu\(s\)/)).toBeTruthy()
  })

  it('disent le geste qui en inventorierait un', () => {
    show({ details: { outOfScopeLines: 3, outOfScopeItems: 1 } })

    expect(screen.getByText(/grille\s+Articles/)).toBeTruthy()
  })

  it('ne disent rien quand il n’y en a pas', () => {
    show({ details: { outOfScopeLines: 0, outOfScopeItems: 0 } })

    expect(screen.queryByText(/hors périmètre/)).toBeNull()
  })

  it('ne disent rien quand le serveur ne compte pas encore', () => {
    // Un client qui blanchit l'écran parce qu'une clé manque est un client
    // trop fragile pour être déployé — la règle vaut aussi pour celle-ci.
    show({ details: {} })

    expect(screen.queryByText(/hors périmètre/)).toBeNull()
  })

  it('ne sont pas comptées comme rejetées', () => {
    /* C'est le décompte des refus qui annulait l'écriture entière. */
    show({ rowsRejected: 0, details: { outOfScopeLines: 1558 } })

    const rejetees = screen.getByText('Rejetées').parentElement
    expect(rejetees?.textContent).toContain('0')
  })
})

describe('Les lignes signalées', () => {
  it('portent un titre qui vaut pour ce qu’elles sont', () => {
    /* « Corrections automatiques » décrivait les journaux ; le stock ERP y met
       désormais ses lignes écartées, qui ne corrigent rien. */
    show({
      warnings: [
        { line: 7, column: 'item_number', value: 'X-1', message: 'hors du périmètre' },
      ],
    })

    expect(screen.getByText(/1 ligne\(s\) signalée\(s\)/)).toBeTruthy()
    expect(screen.queryByText(/correction\(s\) automatique\(s\)/)).toBeNull()
  })

  it('nomment la ligne et la raison', () => {
    show({
      warnings: [
        { line: 7, column: 'item_number', value: 'X-1', message: 'hors du périmètre' },
      ],
    })

    expect(screen.getByText(/Ligne 7 — hors du périmètre/)).toBeTruthy()
  })
})

describe('Les références inconnues du référentiel', () => {
  /* L'autre moitié du même choix. Elles étaient refusées, ce qui annulait tout
     le chargement : plus le référentiel était en retard, moins le stock était
     chargeable. Elles sont désormais écartées — et donc, elles aussi, à dire. */

  it('sont annoncées avec leur décompte', () => {
    show({ details: { unknownLines: 42, unknownItems: 17 } })

    expect(screen.getByText(/42 ligne\(s\) sur des références inconnues/)).toBeTruthy()
  })

  it('nomment quelques références, pour reconnaître ce qui manque', () => {
    show({
      details: {
        unknownLines: 3, unknownItems: 3,
        unknownItemNumbers: ['N-1', 'N-2', 'N-3'],
      },
    })

    expect(screen.getByText(/N-1 · N-2 · N-3/)).toBeTruthy()
  })

  it('n’en nomment qu’un échantillon, et le disent', () => {
    /* Douze mille références dans un bandeau ne sont plus un message. */
    show({
      details: {
        unknownLines: 12_000, unknownItems: 12_000,
        unknownItemNumbers: Array.from({ length: 200 }, (_, n) => `N-${n}`),
      },
    })

    expect(screen.getByText(/N-0 · N-1 · N-2 · N-3 · N-4 · N-5 …/)).toBeTruthy()
  })

  it('renvoient vers Contrôles, où la liste survit à ce panneau', () => {
    show({ details: { unknownLines: 1, unknownItems: 1 } })

    expect(screen.getByText('Contrôles')).toBeTruthy()
  })

  it('disent le geste qui les chargerait', () => {
    show({ details: { unknownLines: 1, unknownItems: 1 } })

    expect(screen.getByText(/Complétez le référentiel articles/)).toBeTruthy()
  })

  it('ne se confondent pas avec les lignes hors périmètre', () => {
    /* Deux causes, deux gestes : compléter le référentiel, ou lever une
       exclusion. Les afficher sous un même bandeau enverrait la moitié des cas
       au mauvais écran. */
    show({
      details: {
        outOfScopeLines: 5, outOfScopeItems: 2,
        unknownLines: 3, unknownItems: 1,
      },
    })

    expect(screen.getByText(/5 ligne\(s\) hors périmètre/)).toBeTruthy()
    expect(screen.getByText(/3 ligne\(s\) sur des références inconnues/)).toBeTruthy()
  })

  it('ne disent rien quand il n’y en a pas', () => {
    show({ details: { unknownLines: 0, unknownItems: 0 } })

    expect(screen.queryByText(/références inconnues/)).toBeNull()
  })

  it('ne sont pas comptées comme rejetées', () => {
    show({ rowsRejected: 0, details: { unknownLines: 42 } })

    const rejetees = screen.getByText('Rejetées').parentElement
    expect(rejetees?.textContent).toContain('0')
  })
})


describe('Les quantités qui ne reviennent pas telles qu’elles sont parties', () => {
  /* Le contrôle qui manquait. La consolidation GENERIQUE avait calculé
     1 336,92 kg ; un typage automatique dans un tableur, entre l'extraction et
     l'import, a renvoyé 1 337. L'import a accepté sans un mot, et l'écart ne
     s'est vu que des jours plus tard. */

  it('sont annoncées avec leur décompte', () => {
    show({ details: { roundTripMismatches: 64 } })

    expect(
      screen.getByText(/64 quantité\(s\) ne reviennent pas/),
    ).toBeTruthy()
  })

  it('disent que la valeur de l’application est conservée', () => {
    /* Sans quoi le premier réflexe serait de tout recharger pour « réparer »,
       et le rechargement est précisément ce qui a introduit l'écart. */
    show({ details: { roundTripMismatches: 64 } })

    expect(screen.getByText(/valeur de l’application est conservée/)).toBeTruthy()
  })

  it('ne disent rien quand tout coïncide', () => {
    show({ details: { roundTripMismatches: 0 } })

    expect(screen.queryByText(/ne reviennent pas/)).toBeNull()
  })
})

describe('Un constat qui ne porte sur aucune ligne du fichier', () => {
  it('ne renvoie pas le lecteur chercher une « ligne 0 »', () => {
    /* Ces constats portent sur la campagne — un aller-retour qui ne coïncide
       pas, un emplacement conservé — et non sur une ligne du fichier. */
    show({
      warnings: [
        { line: 0, column: '', value: '', message: 'MASS-1 : l’application tient 1336.92' },
      ],
    } as never)

    expect(screen.getByText(/MASS-1/)).toBeTruthy()
    expect(screen.queryByText(/Ligne 0/)).toBeNull()
  })

  it('mais garde le numéro quand il en a un', () => {
    show({
      warnings: [
        { line: 42, column: 'qty', value: '', message: 'quantité douteuse' },
      ],
    } as never)

    expect(screen.getByText(/Ligne 42/)).toBeTruthy()
  })
})
