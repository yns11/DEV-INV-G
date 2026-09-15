/**
 * L'ordre du document, et ce que l'écran d'aperçu en fait.
 *
 * L'ordre des lignes n'est pas une préférence d'affichage : c'est lui qui dit
 * sous quel intertitre se trouve chaque article, donc *où le compteur doit
 * aller le chercher*. Le serveur relit la sous-section dans cet ordre. Un
 * décalage d'un rang au déplacement ferait donc passer un article d'un
 * emplacement à un autre — silencieusement, et jusqu'à l'impression.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { PRINTED_SECTIONS, layoutLinePayload, moveLine } from './generic.layout'
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

/**
 * Ce que l'aperçu envoie, et ce qu'il n'envoie pas.
 *
 * L'omission de la quantité et du commentaire est **load-bearing** depuis que
 * le serveur distingue « champ absent » de « champ vide » : c'est elle qui
 * protège les comptages relevés en atelier quand quelqu'un réordonne la
 * feuille. Un contrôle qui ne dirait que les champs présents laisserait
 * réintroduire les deux autres sans un mot — et la perte reviendrait.
 */
describe('la charge utile de l’aperçu', () => {
  const ligne = {
    id: 'l-1',
    item_number: 'P-1',
    name: 'CARTER ARRIÈRE M3 GEN2',
    section: 'WIP',
    line_kind: 'ARTICLE',
    label: '',
    unit: 'KG',
    qty: 151,
    comment: 'bac du fond',
  }

  it('porte le document : ordre, section, genre, référence, unité', () => {
    expect(layoutLinePayload(ligne, 3)).toEqual({
      id: 'l-1',
      itemNumber: 'P-1',
      section: 'WIP',
      lineKind: 'ARTICLE',
      label: '',
      unit: 'KG',
      displayOrder: 3,
    })
  })

  it.each(['qty', 'comment', 'name'])(
    'ne parle pas de « %s », même quand la ligne en porte un',
    (champ) => {
      expect(Object.keys(layoutLinePayload(ligne, 0))).not.toContain(champ)
    },
  )

  it('le rang envoyé est celui du document, pas celui d’origine', () => {
    expect(layoutLinePayload(ligne, 7).displayOrder).toBe(7)
  })
})

/**
 * Les deux écrans qui, eux, parlent bien de comptage.
 *
 * L'autre moitié de la règle : un champ absent n'est plus écrit, donc un écran
 * qui affiche la colonne comptage **doit** l'envoyer. S'il cessait de le faire,
 * vider une case n'aurait plus aucun effet et l'ancienne valeur reviendrait au
 * rechargement — un défaut silencieux, exactement symétrique de celui qu'on
 * vient de fermer.
 */
describe('les écrans de saisie disent ce qu’ils affichent', () => {
  const source = (name: string) =>
    readFileSync(join(process.cwd(), 'src', 'features', name), 'utf8')

  it.each([
    ['generic.sheet.tsx', 'qty:'],
    ['generic.sheet.tsx', 'comment:'],
    ['preparation.sheets.tsx', 'qty:'],
    ['preparation.sheets.tsx', 'comment:'],
  ])('%s envoie %s', (fichier, champ) => {
    expect(source(fichier)).toContain(champ)
  })
})
