/**
 * L'ordre du document, et ce que l'écran d'aperçu en fait.
 *
 * L'ordre des lignes n'est pas une préférence d'affichage : c'est lui qui dit
 * sous quel intertitre se trouve chaque article, donc *où le compteur doit
 * aller le chercher*. Le serveur relit la sous-section dans cet ordre. Un
 * décalage d'un rang au déplacement ferait donc passer un article d'un
 * emplacement à un autre — silencieusement, et jusqu'à l'impression.
 */

import { describe, expect, it } from 'vitest'

import { PRINTED_SECTIONS, moveLine } from './generic.layout'
import { DEFAULT_SECTION_TITLES } from '../lib/format'

const doc = ['titre', 'a', 'b', 'vide', 'c']

describe('déplacer une ligne', () => {
  it('la met à la place demandée', () => {
    expect(moveLine(doc, 1, 2)).toEqual(['titre', 'b', 'a', 'vide', 'c'])
  })

  it('remonte aussi bien qu’elle descend', () => {
    expect(moveLine(doc, 3, 1)).toEqual(['titre', 'vide', 'a', 'b', 'c'])
  })

  it('ne perd ni ne duplique de ligne', () => {
    const moved = moveLine(doc, 0, 4)
    expect(moved).toHaveLength(doc.length)
    expect([...moved].sort()).toEqual([...doc].sort())
  })

  it('ne bouge rien quand la cible sort de la feuille', () => {
    /* Le premier « monter » et le dernier « descendre » : le bouton est grisé,
       mais la fonction ne doit pas dépendre du bouton pour être sûre. */
    expect(moveLine(doc, 0, -1)).toEqual(doc)
    expect(moveLine(doc, 4, 5)).toEqual(doc)
  })

  it('ne bouge rien quand la cible est la place actuelle', () => {
    expect(moveLine(doc, 2, 2)).toBe(doc)
  })
})

describe('les sections imprimées', () => {
  it('sont les trois, dans l’ordre du papier', () => {
    /* Le même ordre que la feuille imprimée : l'aperçu ne serait pas un aperçu
       s'il les montrait dans un autre. */
    expect([...PRINTED_SECTIONS]).toEqual(['LINE_SIDE', 'WIP', 'WIP_OK'])
  })

  it('ont chacune un texte par défaut à proposer', () => {
    /* Le champ vide affiche ce texte en filigrane : c'est ce qui s'imprimera si
       personne n'écrit rien, et le montrer évite de le recopier « pour voir ». */
    for (const section of PRINTED_SECTIONS) {
      expect(DEFAULT_SECTION_TITLES[section]).toBeTruthy()
    }
  })
})
