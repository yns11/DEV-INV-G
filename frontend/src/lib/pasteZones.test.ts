/**
 * Un bloc collé depuis Excel devient des zones — ou est signalé.
 *
 * Trois choses se jouent ici, et une seule est visible à l'œil nu :
 *
 * * **Une zone perdue.** Une ligne dont aucun code n'est tiré doit être
 *   signalée, jamais écartée en silence. Une zone qui disparaît entre le
 *   tableur et l'application est exactement le genre de silence que cette
 *   application existe pour supprimer.
 * * **Un en-tête pris pour une zone, ou une zone prise pour un en-tête.** Les
 *   deux créent un dégât : la première invente une zone « Code », la seconde
 *   en fait disparaître une.
 * * **Un nombre de lignes qui n'en est pas un.** « 40 lignes » ou « beaucoup »
 *   ne sont pas des quantités ; les laisser passer imprimerait n'importe quoi
 *   sans que rien ne l'ait dit.
 */

import { describe, expect, it } from 'vitest'

import { MAX_BLANK_ROWS, parseZonePaste } from './pasteZones'

const tabbed = (...rows: string[]) => rows.join('\n')

describe('la colonne obligatoire est le code', () => {
  it('lit une zone d’une seule colonne', () => {
    const { zones } = parseZonePaste('FI ASSY M3.1')
    expect(zones).toEqual([
      { code: 'FI ASSY M3.1', label: '', blankRows: {} },
    ])
  })

  it('lit autant de zones que de lignes', () => {
    const { zones } = parseZonePaste(tabbed('ZONE A', 'ZONE B', 'ZONE C'))
    expect(zones.map((z) => z.code)).toEqual(['ZONE A', 'ZONE B', 'ZONE C'])
  })

  it('ignore les lignes vides sans les compter comme perdues', () => {
    const { zones, rejected } = parseZonePaste(tabbed('ZONE A', '', '  ', 'ZONE B'))
    expect(zones.map((z) => z.code)).toEqual(['ZONE A', 'ZONE B'])
    expect(rejected).toEqual([])
  })

  it('signale une ligne dont aucun code n’est tiré, et ne la crée pas', () => {
    const { zones, rejected } = parseZonePaste(
      tabbed('ZONE A', '\tLibellé orphelin\t40', 'ZONE B'),
    )
    expect(zones.map((z) => z.code)).toEqual(['ZONE A', 'ZONE B'])
    expect(rejected).toEqual([2])
  })
})

describe('l’en-tête décide, pas la position', () => {
  it('lit les colonnes dans l’ordre annoncé', () => {
    const { zones, headerSkipped } = parseZonePaste(
      tabbed(
        'Lignes WIP\tCode\tLibellé\tLignes BDL',
        '10\tFI ASSY\tAssemblage\t40',
      ),
    )
    expect(headerSkipped).toBe(true)
    expect(zones).toEqual([
      {
        code: 'FI ASSY',
        label: 'Assemblage',
        blankRows: { WIP: 10, LINE_SIDE: 40 },
      },
    ])
  })

  it.each([
    ['Lignes BDL', 'LINE_SIDE'],
    ['BDL', 'LINE_SIDE'],
    ['Bord de ligne', 'LINE_SIDE'],
    ['Lignes WIP', 'WIP'],
    ['WIP', 'WIP'],
    ['Lignes WOP OK', 'WIP_OK'],
    ['WIP OK', 'WIP_OK'],
  ])('reconnaît « %s » comme la section %s', (header, section) => {
    const { zones } = parseZonePaste(tabbed(`Code\t${header}`, 'ZONE A\t7'))
    expect(zones[0]!.blankRows).toEqual({ [section]: 7 })
  })

  it('sans en-tête, lit code puis BDL, WIP, WIP OK — l’ordre de la feuille', () => {
    const { zones, headerSkipped } = parseZonePaste('ZONE A\t40\t10\t5')
    expect(headerSkipped).toBe(false)
    expect(zones).toEqual([
      {
        code: 'ZONE A',
        label: '',
        blankRows: { LINE_SIDE: 40, WIP: 10, WIP_OK: 5 },
      },
    ])
  })

  it('ne prend pas une zone nommée « BDL » pour un en-tête', () => {
    // Un seul mot connu ne fait pas un en-tête : le consommer ferait
    // disparaître une zone en silence, ce qui est la faute la plus chère ici.
    const { zones, headerSkipped } = parseZonePaste(tabbed('BDL', 'ZONE B'))
    expect(headerSkipped).toBe(false)
    expect(zones.map((z) => z.code)).toEqual(['BDL', 'ZONE B'])
  })

  it('accepte le point-virgule comme séparateur', () => {
    const { zones } = parseZonePaste('Code;Libellé;Lignes BDL\nZONE A;Assemblage;40')
    expect(zones).toEqual([
      { code: 'ZONE A', label: 'Assemblage', blankRows: { LINE_SIDE: 40 } },
    ])
  })
})

describe('les nombres de lignes vierges', () => {
  it('ne transmet pas un zéro : zéro et absent sont le même état', () => {
    const { zones } = parseZonePaste(tabbed('Code\tLignes BDL\tLignes WIP', 'ZONE A\t0\t10'))
    expect(zones[0]!.blankRows).toEqual({ WIP: 10 })
  })

  it('accepte la borne haute', () => {
    const { zones, refusals } = parseZonePaste(
      tabbed('Code\tLignes BDL', `ZONE A\t${MAX_BLANK_ROWS}`),
    )
    expect(zones[0]!.blankRows).toEqual({ LINE_SIDE: MAX_BLANK_ROWS })
    expect(refusals).toEqual([])
  })

  it('refuse ce qui dépasse la borne, et le dit', () => {
    const { zones, refusals } = parseZonePaste(
      tabbed('Code\tLignes BDL', `ZONE A\t${MAX_BLANK_ROWS + 1}`),
    )
    expect(zones[0]!.blankRows).toEqual({})
    expect(refusals).toHaveLength(1)
    expect(refusals[0]).toContain(String(MAX_BLANK_ROWS + 1))
    expect(refusals[0]).toContain('LINE_SIDE')
  })

  it('refuse ce qui n’est pas un nombre, et le dit', () => {
    const { zones, refusals } = parseZonePaste(
      tabbed('Code\tLignes BDL', 'ZONE A\tbeaucoup'),
    )
    expect(zones[0]!.blankRows).toEqual({})
    expect(refusals).toHaveLength(1)
    expect(refusals[0]).toContain('beaucoup')
  })

  it('refuse le nombre sans perdre la zone', () => {
    // Le refus porte sur une colonne, pas sur la ligne : perdre la zone
    // parce qu’une cellule est mal remplie coûterait plus que de la créer
    // sans son nombre de lignes.
    const { zones } = parseZonePaste(tabbed('Code\tLignes BDL', 'ZONE A\t999'))
    expect(zones.map((z) => z.code)).toEqual(['ZONE A'])
  })
})

describe('un bloc vide ne donne rien', () => {
  it.each(['', '   ', '\n\n'])('« %s »', (text) => {
    expect(parseZonePaste(text)).toEqual({
      zones: [],
      rejected: [],
      headerSkipped: false,
      refusals: [],
    })
  })
})
