/**
 * Ce que la cellule de comptage envoie au serveur.
 *
 * La cellule accepte du texte — c'est un `<input>`, pas un `type="number"` —
 * et le compteur y écrit ce qu'il voit : `3*48+7` pour trois palettes de
 * quarante-huit et un fond de bac de sept.
 *
 * La conversion faisait `Number(...)`, qui rend `NaN` sur une opération, et
 * `JSON.stringify` écrit `null` pour un `NaN`. La formule ne devenait donc pas
 * une erreur : **elle devenait une case vide**, sur une ligne que quelqu'un
 * venait de compter, sans que rien ne le signale. C'est la panne silencieuse
 * que ces contrôles ferment.
 *
 * Le navigateur ne juge pas de la validité : le réglage vit sur la campagne, et
 * le refus doit pouvoir le nommer. Le texte traverse, le serveur tranche.
 */

import { describe, expect, it } from 'vitest'

import { quantityToSend } from './generic.sheet'

describe('une valeur numérique', () => {
  it('part comme un nombre', () => {
    expect(quantityToSend('151')).toBe(151)
    expect(quantityToSend(151)).toBe(151)
  })

  it('accepte la virgule décimale', () => {
    expect(quantityToSend('2,5')).toBe(2.5)
  })

  it('accepte un nombre négatif', () => {
    /* Un bac rendu, une correction : c'est la zone qui décide, pas la saisie. */
    expect(quantityToSend('-4')).toBe(-4)
  })
})

describe('une opération', () => {
  it('part telle quelle, et non en NaN', () => {
    /* Le défaut : `Number('3*48+7')` vaut NaN, sérialisé `null`. */
    expect(quantityToSend('3*48+7')).toBe('3*48+7')
  })

  it('garde son signe égal de tête', () => {
    expect(quantityToSend('=(10+2)/4')).toBe('=(10+2)/4')
  })

  it('est débarrassée des espaces de bord, pas de son contenu', () => {
    expect(quantityToSend('  3 * 48 + 7  ')).toBe('3 * 48 + 7')
  })
})

describe('une case vide', () => {
  it('reste vide, jamais zéro', () => {
    /* Vide ≠ zéro : la ligne n'a pas été comptée, ce qui n'est pas la même
       chose que d'avoir compté zéro. */
    for (const empty of ['', '   ', null, undefined]) {
      expect(quantityToSend(empty)).toBeNull()
    }
  })
})

describe('ce qui ne peut pas être une quantité', () => {
  it('part quand même, pour que le serveur dise pourquoi', () => {
    /* Refuser ici donnerait un message de navigateur là où le serveur sait
       nommer la référence et le réglage. */
    expect(quantityToSend('douze')).toBe('douze')
  })

  it('un NaN déjà numérique ne devient pas une quantité', () => {
    expect(quantityToSend(Number.NaN)).toBeNull()
    expect(quantityToSend(Number.POSITIVE_INFINITY)).toBeNull()
  })
})
