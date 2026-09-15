/**
 * Les filtres de l'affectation des causes.
 *
 * L'écran sert à une seule chose : faire descendre à zéro la part d'écart sans
 * cause affectée. Sur deux cents lignes, celles qui restent à traiter sont
 * noyées parmi celles qui sont déjà faites, et rien ne permettait de ne voir
 * que les premières.
 *
 * Trois filtres, et aucun ne s'exprime comme un filtre de colonne : l'absence
 * d'une valeur, la comparaison de deux colonnes entre elles, le signe d'un
 * nombre. La recherche libre, elle, a rejoint `DataGrid` avec le reste du
 * tableau — deux champs de recherche côte à côte auraient obligé à deviner
 * lequel cherche quoi.
 *
 * Ce qui est vérifié ici est la décision, pas la mise en page : quelles lignes
 * restent. C'est aussi la seule partie qui se compose — « sans cause **et**
 * avec une proposition IA » est la file de travail réelle — et une composition
 * ne se vérifie pas à l'œil.
 */

import { describe, expect, it } from 'vitest'

import { NO_CAUSE_FILTER, matchesCause, type CauseFilters } from './analysis.causes'

function row(over: Partial<Parameters<typeof matchesCause>[0]> = {}) {
  return {
    itemNumber: 'ART-1',
    name: 'Stator M3',
    causeCode: null,
    aiSuggestedCause: null,
    varianceValue: -1200,
    ...over,
  }
}

const filters = (over: Partial<CauseFilters> = {}): CauseFilters => ({
  ...NO_CAUSE_FILTER,
  ...over,
})

describe('sans filtre', () => {
  it('tout passe', () => {
    expect(matchesCause(row(), NO_CAUSE_FILTER)).toBe(true)
    expect(matchesCause(row({ causeCode: 'SAISIE' }), NO_CAUSE_FILTER)).toBe(true)
  })
})

describe('la cause retenue', () => {
  it('« non affectées » ne garde que celles qui n’en ont pas', () => {
    const f = filters({ cause: 'none' })
    expect(matchesCause(row({ causeCode: null }), f)).toBe(true)
    expect(matchesCause(row({ causeCode: 'SAISIE' }), f)).toBe(false)
  })

  it('« affectées » est exactement le complément', () => {
    const f = filters({ cause: 'any' })
    expect(matchesCause(row({ causeCode: null }), f)).toBe(false)
    expect(matchesCause(row({ causeCode: 'SAISIE' }), f)).toBe(true)
  })

  it('un code précis ne garde que lui', () => {
    const f = filters({ cause: 'SAISIE' })
    expect(matchesCause(row({ causeCode: 'SAISIE' }), f)).toBe(true)
    expect(matchesCause(row({ causeCode: 'VOL' }), f)).toBe(false)
    expect(matchesCause(row({ causeCode: null }), f)).toBe(false)
  })

  it('un code nommé « none » resterait un code', () => {
    /* Les deux valeurs réservées ne doivent pas se confondre avec un code de
       cause : la liste des causes vient de la base, pas de ce fichier. */
    const f = filters({ cause: 'any' })
    expect(matchesCause(row({ causeCode: 'none' }), f)).toBe(true)
  })
})

describe('la proposition IA', () => {
  it('« avec proposition » écarte les lignes que l’IA n’a pas vues', () => {
    const f = filters({ ai: 'with' })
    expect(matchesCause(row({ aiSuggestedCause: 'SAISIE' }), f)).toBe(true)
    expect(matchesCause(row({ aiSuggestedCause: null }), f)).toBe(false)
  })

  it('« à valider » retire ce qui est déjà entériné', () => {
    /* C'est la liste sur laquelle on clique « Accepter ». Y laisser les lignes
       dont la décision reprend déjà la proposition la viderait de son sens. */
    const f = filters({ ai: 'pending' })
    expect(
      matchesCause(row({ aiSuggestedCause: 'SAISIE', causeCode: null }), f),
    ).toBe(true)
    expect(
      matchesCause(row({ aiSuggestedCause: 'SAISIE', causeCode: 'VOL' }), f),
    ).toBe(true)
    expect(
      matchesCause(row({ aiSuggestedCause: 'SAISIE', causeCode: 'SAISIE' }), f),
    ).toBe(false)
    expect(matchesCause(row({ aiSuggestedCause: null }), f)).toBe(false)
  })
})

describe('le sens de l’écart', () => {
  it('les excédents sont les valeurs positives', () => {
    const f = filters({ sign: 'pos' })
    expect(matchesCause(row({ varianceValue: 500 }), f)).toBe(true)
    expect(matchesCause(row({ varianceValue: -500 }), f)).toBe(false)
  })

  it('les manquants sont les valeurs négatives', () => {
    const f = filters({ sign: 'neg' })
    expect(matchesCause(row({ varianceValue: -500 }), f)).toBe(true)
    expect(matchesCause(row({ varianceValue: 500 }), f)).toBe(false)
  })

  it('un écart nul n’est ni l’un ni l’autre', () => {
    /* Le classer d'un côté ferait apparaître, sous « excédents », des lignes
       qui n'en sont pas — et le total affiché ne correspondrait plus. */
    expect(matchesCause(row({ varianceValue: 0 }), filters({ sign: 'pos' }))).toBe(false)
    expect(matchesCause(row({ varianceValue: 0 }), filters({ sign: 'neg' }))).toBe(false)
  })
})

describe('les filtres se composent', () => {
  it('en et, jamais en ou', () => {
    /* La file de travail réelle : ce que l'IA propose et que personne n'a
       encore tranché. Composés en « ou », les mêmes filtres rendraient la
       liste entière. */
    const f = filters({ cause: 'none', ai: 'pending' })

    expect(matchesCause(row({ causeCode: null, aiSuggestedCause: 'SAISIE' }), f)).toBe(true)
    expect(matchesCause(row({ causeCode: 'VOL', aiSuggestedCause: 'SAISIE' }), f)).toBe(false)
    expect(matchesCause(row({ causeCode: null, aiSuggestedCause: null }), f)).toBe(false)
  })

  it('les trois à la fois', () => {
    const f = filters({ cause: 'none', ai: 'with', sign: 'neg' })

    expect(matchesCause(row({ aiSuggestedCause: 'SAISIE' }), f)).toBe(true)
    // Un seul critère qui bascule suffit à retirer la ligne.
    expect(
      matchesCause(row({ aiSuggestedCause: 'SAISIE', varianceValue: 900 }), f),
    ).toBe(false)
    expect(
      matchesCause(row({ aiSuggestedCause: 'SAISIE', causeCode: 'VOL' }), f),
    ).toBe(false)
  })
})
