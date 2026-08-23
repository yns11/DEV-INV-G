/**
 * Un bloc collé depuis Excel devient des lignes de feuille — ou est signalé.
 *
 * Rien n'est positionnel : les blocs que les gens collent viennent d'un
 * extrait ERP, d'une campagne précédente ou d'une liste faite à la main, et
 * chacun a son ordre de colonnes et son vocabulaire. Chaque cellule est donc
 * classée par ce qu'elle **est** — un nom de section, une unité, une quantité,
 * une référence — et ce qui reste est l'article.
 *
 * Deux erreurs comptent, et une seule se voit :
 *
 * * **Une ligne perdue.** Une ligne qui n'a pas donné d'article doit être
 *   signalée, jamais écartée en silence — une ligne perdue entre l'atelier et
 *   la feuille est exactement la défaillance que cette application existe pour
 *   supprimer.
 * * **Une ligne inventée.** Une colonne de désignation prise pour une
 *   référence crée une ligne que le référentiel ne peut pas rapprocher, et
 *   personne ne la remarque avant l'écart.
 */

import { describe, expect, it } from 'vitest'

import { parseSheetLines, type ParsedSheetLine } from './pasteSheetLines'

/**
 * La première ligne lue, ou un échec explicite.
 *
 * `lines[0]` sous `noUncheckedIndexedAccess` oblige à écarter le cas vide à
 * chaque appel ; l'écarter ici une fois donne en prime un message utile quand
 * un bloc cesse d'être lu du tout.
 */
function firstLine(text: string): ParsedSheetLine {
  const { lines } = parseSheetLines(text)
  if (lines.length === 0) throw new Error(`aucune ligne lue de : ${text}`)
  return lines[0]!
}

const tabbed = (...rows: string[]) => rows.join('\n')

describe('les colonnes se reconnaissent sans ordre imposé', () => {
  it('lit une ligne dans l’ordre habituel', () => {
    const { lines } = parseSheetLines('P-00012\tBDL\tPCE\t40')
    expect(lines).toEqual([
      { item_number: 'P-00012', section: 'LINE_SIDE', unit: 'PCE', qty: '40' },
    ])
  })

  it('lit la même ligne dans l’ordre inverse', () => {
    // C'est tout l'objet : deux extraits n'ont pas le même ordre.
    const { lines } = parseSheetLines('40\tPCE\tBDL\tP-00012')
    expect(lines[0]).toEqual({
      item_number: 'P-00012', section: 'LINE_SIDE', unit: 'PCE', qty: '40',
    })
  })

  it('accepte le point-virgule d’un export CSV', () => {
    expect(firstLine('P-00012;BDL;PCE;40').qty).toBe('40')
  })

  it('accepte la barre verticale', () => {
    expect(firstLine('P-00012|BDL|PCE|40').qty).toBe('40')
  })

  it('accepte des colonnes séparées par plusieurs espaces', () => {
    expect(firstLine('P-00012   BDL   PCE   40').qty).toBe('40')
  })
})

describe('le vocabulaire des sections, y compris celui du classeur', () => {
  it.each([
    ['BDL', 'LINE_SIDE'],
    ['BL', 'LINE_SIDE'],
    ['Bord de ligne', 'LINE_SIDE'],
    ['LINE_SIDE', 'LINE_SIDE'],
    ['WIP', 'WIP'],
    ['MOM waiting', 'WIP'],
    ['En cours', 'WIP'],
    ['WIP OK', 'WIP_OK'],
    ['MOM OK', 'WIP_OK'],
    ['Eclatee', 'WIP_OK'],
  ])('« %s » vaut %s', (written, expected) => {
    expect(firstLine(`P-00012\t${written}`).section).toBe(expected)
  })

  it('les accents ne changent rien', () => {
    expect(firstLine('P-00012\téclatée').section).toBe('WIP_OK')
  })

  it('une section absente prend la lecture la plus ordinaire', () => {
    expect(firstLine('P-00012').section).toBe('LINE_SIDE')
  })
})

describe('les unités', () => {
  it('sont normalisées vers la pièce', () => {
    expect(firstLine('P-00012\tPC').unit).toBe('PCE')
    expect(firstLine('P-00012\tP').unit).toBe('PCE')
  })

  it('gardent leur forme quand elles en ont une', () => {
    expect(firstLine('P-00012\tKG').unit).toBe('KG')
  })

  it('valent la pièce par défaut', () => {
    expect(firstLine('P-00012').unit).toBe('PCE')
  })
})

describe('les quantités se lisent dans les deux notations', () => {
  it('à la française', () => {
    expect(firstLine('P-00012\t1 234,5').qty).toBe('1234.5')
  })

  it('à l’anglaise', () => {
    expect(firstLine('P-00012\t1,234.5').qty).toBe('1234.5')
  })

  it('un entier reste un entier', () => {
    expect(firstLine('P-00012\t40').qty).toBe('40')
  })

  it('une quantité négative est conservée', () => {
    // Certaines feuilles autorisent le négatif ; l'écarter fausserait le total.
    expect(firstLine('P-00012\t-3').qty).toBe('-3')
  })

  it('une quantité absente reste absente, pas zéro', () => {
    // Une feuille imprimée pour être remplie à la main n'a pas de quantité :
    // la mettre à zéro affirmerait qu'on a compté et trouvé rien.
    expect(firstLine('P-00012\tBDL').qty).toBeNull()
  })
})

describe('une désignation ne devient jamais une référence', () => {
  it('la prose est ignorée au profit du code', () => {
    expect(
      firstLine('P-00012\tVIS TETE HEXAGONALE M6\tBDL\tPCE\t40').item_number,
    ).toBe('P-00012')
  })

  it('une ligne qui n’a que de la prose est rejetée', () => {
    // Inventer une référence créerait une ligne que le référentiel ne peut
    // pas rapprocher, et personne ne la verrait avant l'écart.
    const { lines, rejected } = parseSheetLines('VIS TETE HEXAGONALE M6')
    expect(lines).toHaveLength(0)
    expect(rejected).toEqual([1])
  })

  it('une référence sans chiffre reste acceptée', () => {
    // Rare mais légitime : un code alphabétique n'est pas de la prose.
    expect(firstLine('ABCDEF\tBDL').item_number).toBe('ABCDEF')
  })

  it('la référence est normalisée en majuscules', () => {
    expect(firstLine('p-00012\tBDL').item_number).toBe('P-00012')
  })
})

describe('rien n’est perdu en silence', () => {
  it('les lignes rejetées sont numérotées', () => {
    const { lines, rejected } = parseSheetLines(tabbed(
      'P-00012\tBDL\tPCE\t40',
      'ceci est une phrase',
      'P-00014\tBDL\tPCE\t12',
    ))
    expect(lines).toHaveLength(2)
    expect(rejected).toEqual([2])
  })

  it('la numérotation tient compte de l’en-tête retiré', () => {
    // Sinon l'utilisateur cherche la ligne 2 de son bloc alors que la fautive
    // est la 3 — et conclut que le message est faux.
    const { rejected } = parseSheetLines(tabbed(
      'Article\tSection\tUnité\tQuantité',
      'P-00012\tBDL\tPCE\t40',
      'ceci est une phrase',
    ))
    expect(rejected).toEqual([3])
  })

  it('les lignes vides ne sont pas comptées comme des rejets', () => {
    const { rejected } = parseSheetLines(tabbed('P-00012\tBDL', '', '  '))
    expect(rejected).toEqual([])
  })
})

describe('un en-tête est reconnu et retiré', () => {
  it('il ne devient pas une ligne', () => {
    const { lines, headerSkipped } = parseSheetLines(tabbed(
      'Article\tSection\tUnité\tQuantité',
      'P-00012\tBDL\tPCE\t40',
    ))
    expect(headerSkipped).toBe(true)
    expect(lines).toHaveLength(1)
  })

  it('une première ligne qui porte une référence n’est pas un en-tête', () => {
    // Coller un bloc sans en-tête est le cas courant : en perdre la première
    // ligne serait une ligne perdue de plus.
    const { lines, headerSkipped } = parseSheetLines(tabbed(
      'P-00012\tBDL\tPCE\t40',
      'P-00014\tBDL\tPCE\t12',
    ))
    expect(headerSkipped).toBe(false)
    expect(lines).toHaveLength(2)
  })

  it('une seule ligne n’est jamais prise pour un en-tête', () => {
    const { headerSkipped } = parseSheetLines('Article\tSection')
    expect(headerSkipped).toBe(false)
  })
})

describe('un bloc vide', () => {
  it('ne donne ni ligne ni rejet', () => {
    expect(parseSheetLines('')).toEqual({
      lines: [], rejected: [], headerSkipped: false,
    })
  })

  it('des sauts de ligne seuls ne donnent rien non plus', () => {
    expect(parseSheetLines('\n\n  \n').lines).toHaveLength(0)
  })
})
