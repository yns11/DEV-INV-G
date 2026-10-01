/**
 * Ce que la grille à plat renvoie au serveur — et surtout ce qu'elle ne renvoie
 * plus.
 *
 * **Le défaut.** La colonne *Quantité* de cet écran est en lecture seule : les
 * quantités se saisissent au comptage, jamais ici. Elle affiche pourtant zéro
 * sur les lignes que personne n'a comptées, parce qu'« une case vide vaut
 * zéro », et l'enregistrement renvoyait ce zéro comme une valeur.
 *
 * Le serveur y lisait une demande d'écriture de comptage — ce qu'elle était,
 * littéralement — et opposait la garde de phase. En préparation, où **aucune**
 * ligne ne porte de quantité, chaque ligne de la feuille déclenchait donc la
 * garde : renommer une désignation, corriger une section, changer une unité,
 * tout était refusé par « la saisie des comptages est gelée ».
 *
 * Rien dans l'écran n'était visiblement faux, et c'est pour cela que le contrôle
 * porte sur la charge utile plutôt que sur le rendu : la faute était dans ce que
 * l'écran *dit*, pas dans ce qu'il montre.
 */

import { describe, expect, it } from 'vitest'

import { sheetLinePayload } from './preparation.sheets'

/** Une ligne telle que la grille la reçoit du serveur. */
function row(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'ligne-1',
    item_number: 'P-00001',
    section: 'LINE_SIDE',
    name: 'Vis M6 tête bombée',
    unit: 'PCE',
    comment: '',
    // Ce que le serveur envoie sur une ligne jamais comptée : la quantité
    // effective vaut zéro, et `hasEntry` dit qu'elle ne vient de personne.
    qty: 0,
    hasEntry: false,
    ...over,
  }
}

describe('une ligne que personne n’a comptée', () => {
  it('ne renvoie aucune quantité', () => {
    expect(sheetLinePayload(row()).qty).toBeNull()
  })

  it('même si la colonne affiche zéro', () => {
    // Le zéro affiché est la convention d'affichage de l'écran, pas une valeur
    // relevée. Le renvoyer demandait au serveur d'écrire un comptage.
    expect(row().qty).toBe(0)
    expect(sheetLinePayload(row()).qty).toBeNull()
  })

  it('mais renvoie bien la désignation, qui est ce qu’on venait modifier', () => {
    const payload = sheetLinePayload(row({ name: 'VIS 6 PANS ATELIER' }))
    expect(payload.name).toBe('VIS 6 PANS ATELIER')
  })
})

describe('une ligne qui porte une quantité', () => {
  it('la renvoie telle quelle', () => {
    // Ne pas la renvoyer l'effacerait : l'enregistrement réécrit la ligne.
    const payload = sheetLinePayload(row({ qty: 151, hasEntry: true }))
    expect(payload.qty).toBe(151)
  })

  it('y compris quand cette quantité est zéro', () => {
    // « Bac vide » est un constat de comptage. Une ligne qui le porte a bien
    // une entrée, et la taire reviendrait à effacer le constat.
    const payload = sheetLinePayload(row({ qty: 0, hasEntry: true }))
    expect(payload.qty).toBe(0)
  })
})

describe('le reste de la ligne', () => {
  it('part avec ses valeurs par défaut plutôt que vide', () => {
    const payload = sheetLinePayload({ id: 'l-2', item_number: 'P-2' })
    expect(payload).toMatchObject({
      id: 'l-2',
      itemNumber: 'P-2',
      section: 'LINE_SIDE',
      unit: 'PCE',
      name: '',
      comment: '',
      qty: null,
    })
  })
})
