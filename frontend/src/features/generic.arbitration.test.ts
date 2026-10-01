/**
 * Remplir n'est pas valider, et « Valider tout » valide ce qui est affiché.
 *
 * Le défaut se lisait sur l'écran : quatre boutons, dont deux qui *validaient*
 * quarante lignes d'un clic alors que leur libellé — « Tout le n°1 », « Tout le
 * n°2 » — annonce un remplissage, et un « Valider tout » qui annonçait des
 * lignes non tranchées en montrant à l'utilisateur les quantités qu'il croyait
 * valider.
 *
 * La cause tenait en une phrase : la quantité de chaque ligne vivait dans la
 * ligne. Le bouton de lot ne pouvait donc que redemander au serveur ce que
 * *lui* pensait être la bonne quantité — et le serveur ne voit pas les champs.
 * Elle vit maintenant au-dessus du tableau, et c'est elle qui remonte.
 */

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { __test__ } from './generic.arbitration'

const { numeric, suggested } = __test__

const source = readFileSync(
  join(__dirname, 'generic.arbitration.tsx'),
  'utf8',
)

const line = (over: Record<string, unknown> = {}) =>
  ({
    id: 'a-1',
    zone_id: 'z-1',
    item_number: 'ROTOR',
    section: 'LINE_SIDE',
    qty_pass_1: 50,
    qty_pass_2: 200,
    qty_arbitrated: null,
    decided_by: null,
    decided_at: null,
    comment: '',
    name: 'Rotor stack',
    zoneCode: 'PREPA STACK',
    zoneLabel: '',
    gap: 150,
    gapValue: 743,
    unitCost: 5,
    divergent: true,
    needsDecision: true,
    isProposed: false,
    ...over,
  }) as never

describe('la quantité proposée d’office', () => {
  it('est le second comptage, le plus tardif', () => {
    expect(suggested(line())).toBe('200')
  })

  it('est zéro quand le second passage n’a rien relevé', () => {
    /* Le tiret laissait la ligne sans quantité à reprendre, donc sans rien à
       valider : le bouton restait grisé et l'arbitrage de la zone ne pouvait
       pas se terminer. Une case vide compte zéro partout ailleurs. */
    expect(suggested(line({ qty_pass_2: 0 }))).toBe('0')
    expect(suggested(line({ qty_pass_2: null }))).toBe('0')
  })

  it('cède la place à une quantité déjà posée', () => {
    expect(suggested(line({ qty_arbitrated: 105 }))).toBe('105')
  })

  it('ne traîne pas les six décimales du stockage dans un champ à relire', () => {
    expect(suggested(line({ qty_pass_2: 200.0 }))).toBe('200')
  })
})

describe('lire ce qui est tapé', () => {
  it('accepte la virgule, qui est ce qu’on tape sur un clavier français', () => {
    expect(numeric('2,5')).toBe(2.5)
  })

  it('accepte le zéro, qui est une réponse', () => {
    expect(numeric('0')).toBe(0)
  })

  it('rend null sur un champ vide plutôt que zéro', () => {
    /* « Rien » et « zéro » sont deux réponses différentes ici : la première
       laisse la ligne ouverte, la seconde la tranche. */
    expect(numeric('')).toBeNull()
    expect(numeric('   ')).toBeNull()
  })

  it('rend null sur ce qui n’est pas un nombre', () => {
    expect(numeric('douze')).toBeNull()
  })
})

describe('ce que les boutons font, et ne font pas', () => {
  it('« Tout le n°1 » et « Tout le n°2 » remplissent, sans appeler le serveur', () => {
    expect(source).toContain('onClick={() => fillAll((row) => row.qty_pass_1 ?? 0)}')
    expect(source).toContain('onClick={() => fillAll((row) => row.qty_pass_2 ?? 0)}')
    /* La faute serait de rebrancher un de ces deux boutons sur la mutation :
       le libellé promet un remplissage, et quarante décisions signées
       partiraient sans que personne n'ait relu une ligne. */
    expect(source).not.toContain("decideAll.mutate({ zoneId")
  })

  it('« Valider tout » poste les quantités affichées', () => {
    expect(source).toContain('onClick={submitAll}')
    const submit = source.slice(
      source.indexOf('const submitAll'),
      source.indexOf('const columns'),
    )
    expect(submit).toContain('valueOf(row)')
    expect(submit).toContain('decideAll.mutate(decisions)')
  })

  it('n’envoie que des lignes qui appellent une décision', () => {
    const submit = source.slice(
      source.indexOf('const submitAll'),
      source.indexOf('const columns'),
    )
    expect(submit).toContain('of pending')
  })

  it('ne demande que les lignes divergentes au serveur', () => {
    expect(source).toContain('api.arbitrations(campaignId, zoneFilter || undefined, true)')
  })

  it('porte la zone et de quoi filtrer, la vue couvrant toute la campagne', () => {
    expect(source).toContain("key: 'zoneCode'")
    expect(source).toContain("filter: 'choice'")
    expect(source).toContain("filter: 'range'")
  })

  it('affiche zéro plutôt qu’un tiret sur un passage qui n’a rien relevé', () => {
    expect(source).toContain('qty(row.qty_pass_1 ?? 0)')
    expect(source).toContain('qty(row.qty_pass_2 ?? 0)')
  })
})
