/**
 * Une clé de grille se recolle et se redécoupe au même endroit.
 *
 * Le défaut, tel qu'il s'est produit
 * ----------------------------------
 * Les deux moitiés de la vue Préparation avaient divergé : la clé était
 * assemblée sur une espace et redécoupée sur le caractère nul. Le composant
 * ressortait `undefined`, `JSON.stringify` retirait purement la clé de l'objet
 * envoyé, et l'activation groupée des nomenclatures était refusée par la
 * validation d'entrée sans que l'écran puisse dire pourquoi.
 *
 * Rien n'échouait à la compilation : `childItem!` affirmait au vérificateur de
 * types ce que le code ne tenait pas.
 *
 * Un contrôle Python existe déjà, mais il porte sur la **forme du source** —
 * que le séparateur soit écrit en échappement plutôt qu'en caractère brut, ce
 * qui est une question de fichier, pas de comportement. Ce qu'il ne pouvait
 * pas faire, faute de banc côté navigateur, c'est exercer les deux fonctions
 * ensemble. C'est ce que fait celui-ci.
 */

import { describe, expect, it } from 'vitest'

import { compositeKey, splitCompositeKey } from './rowKey'

describe('recoller puis redécouper rend ce qu’on avait', () => {
  it.each([
    ['P-00012', 'P-00099'],
    ['B06', 'PAL 01'],
    ['ASSEMBLAGE 1', 'COMPOSANT 2'],
  ])('sur (%s, %s)', (first, second) => {
    expect(splitCompositeKey(compositeKey(first, second))).toEqual([first, second])
  })

  it('sur une référence qui porte une espace', () => {
    // La normalisation ne l'interdit pas, et c'est le cas qui avait cassé.
    expect(splitCompositeKey(compositeKey('PAL 01', 'VIS M6 INOX')))
      .toEqual(['PAL 01', 'VIS M6 INOX'])
  })

  it.each(['-', '/', '_', '.', ':', '|', ';', ',', '\t'])(
    'sur une référence qui porte « %s »',
    (character) => {
      const first = `A${character}1`
      const second = `B${character}2`
      expect(splitCompositeKey(compositeKey(first, second))).toEqual([first, second])
    },
  )
})

describe('le séparateur ne peut pas venir des données', () => {
  it('ce n’est pas une espace', () => {
    // C'était l'écriture fautive : une clé sur espace redécoupe « PAL 01 » en
    // « PAL » et « 01 ».
    expect(compositeKey('PAL 01', 'X')).not.toBe('PAL 01 X')
  })

  it('c’est un caractère de contrôle, qu’aucune valeur métier ne porte', () => {
    // La propriété, pas la valeur : deux contrôles du dépôt exigent que ce
    // caractère ne soit défini qu'une fois et jamais écrit brut. Le nommer
    // ici en redéfinirait une seconde copie — celle-là même dont la
    // divergence avait produit le défaut.
    const key = compositeKey('A', 'B')
    expect(key).toHaveLength(3)
    expect(key.charCodeAt(1)).toBeLessThan(32)
  })
})

describe('une moitié absente reste visible', () => {
  it('elle ressort en chaîne vide, pas en undefined', () => {
    // `undefined` disparaît de l'objet à la sérialisation : la requête part
    // sans la clé, et le serveur la refuse sans pouvoir dire laquelle manque.
    const [first, second] = splitCompositeKey('SEUL')
    expect(first).toBe('SEUL')
    expect(second).toBe('')
  })

  it('une clé vide donne deux chaînes vides', () => {
    expect(splitCompositeKey('')).toEqual(['', ''])
  })

  it('les deux moitiés survivent à la sérialisation', () => {
    const [parent, child] = splitCompositeKey('P-00012')
    const sent = JSON.parse(JSON.stringify({ parent, child }))
    // C'est le contrôle du défaut lui-même : avec `undefined`, `child`
    // n'existerait pas dans l'objet reçu.
    expect(Object.keys(sent)).toEqual(['parent', 'child'])
  })
})

describe('les valeurs sont converties comme le ferait une interpolation', () => {
  it('un nombre devient sa forme décimale', () => {
    expect(splitCompositeKey(compositeKey(12, 34))).toEqual(['12', '34'])
  })

  it('null et undefined ne font pas disparaître la moitié', () => {
    expect(splitCompositeKey(compositeKey(null, undefined)))
      .toEqual(['null', 'undefined'])
  })
})
