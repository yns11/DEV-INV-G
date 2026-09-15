/**
 * La colonne de scellement, d'un bout à l'autre.
 *
 * Le matin du jour J, la question qu'on pose à la grille des journaux de
 * comptage est « lesquels reste-t-il à compter ? ». Un emplacement précompté et
 * scellé y ressemblait à tous les autres : son comptage est pourtant déjà fait,
 * daté et figé, et rien ne le disait.
 *
 * Trois valeurs, parce qu'une dérive change ce qu'on fait de l'emplacement sans
 * changer ce qu'on lui demande — rien n'est requis, rien n'est bloqué, mais
 * quelqu'un voudra peut-être aller voir avant de clore.
 *
 * Le contrôle porte sur le **câblage** : une colonne peut être écrite, avoir
 * son libellé, et lire un champ que le serveur n'envoie jamais. La colonne
 * s'affiche alors vide sur toutes les lignes, et rien ne le signale.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

const COUNTING = read('./Counting.tsx')
const FORMAT = read('../lib/format.ts')
const TYPES = read('../lib/types.ts')
const ENUMS = read('../../../app/inventory/domain/enums.py')
const MODELS = read('../../../app/inventory/domain/models.py')
const SERVICE = read('../../../app/inventory/services/counting_service.py')

/** Les trois valeurs, telles que la décision les nomme. */
const VALUES = ['UNSEALED', 'SEALED_CLEAN', 'SEALED_DRIFTING']

describe('le domaine nomme les trois états', () => {
  it('et pas un quatrième', () => {
    const block = ENUMS.slice(
      ENUMS.indexOf('class SealStatus(StrEnum):'),
      ENUMS.indexOf('class ', ENUMS.indexOf('class SealStatus(StrEnum):') + 10),
    )
    const declared = [...block.matchAll(/^ {4}(\w+) = "/gm)].map((m) => m[1])
    expect(declared).toEqual(VALUES)
  })

  it('les décide en un seul endroit', () => {
    // La règle est lue par la grille et par l'export. Chacun avec sa copie,
    // ils auraient fini par ne plus dire la même chose du même emplacement.
    expect(MODELS).toContain('def seal_status(')
    expect(MODELS).toContain('if journal.sealed_at is None:')
    expect(MODELS).toContain('if journal.key in drifting:')
  })
})

describe('le serveur envoie le champ', () => {
  it('sur chaque journal de la grille', () => {
    expect(SERVICE).toContain('"sealStatus": str(seal_status(journal, drifting=drifting))')
  })

  it('en lisant les dérives une seule fois pour toute la grille', () => {
    // Le jour J, la grille en affiche des centaines. Une lecture par ligne
    // rendrait la question plus coûteuse que sa réponse.
    expect(SERVICE).toContain(
      'drift.key for drift in ctx.drifts.list(campaign_id) if drift.drift_qty != 0',
    )
  })

  it('et « avec dérives » veut dire une dérive non nulle', () => {
    // Une dérive nulle est le cas normal — l'ERP s'est réaligné en postant le
    // journal — et elle est conservée en base comme trace du calcul. La
    // compter ici marquerait tout emplacement scellé « avec dérives ».
    expect(SERVICE).toContain('drift.drift_qty != 0')
  })
})

describe('la grille montre la colonne', () => {
  it('avec son libellé et son champ', () => {
    expect(COUNTING).toContain("key: 'sealStatus'")
    expect(COUNTING).toContain("label: 'Scellement'")
    expect(COUNTING).toContain('SEAL_STATUS_LABELS')
  })

  it('filtrable, puisque c’est ainsi qu’on répartit les équipes', () => {
    const column = COUNTING.slice(COUNTING.indexOf("key: 'sealStatus'"))
    expect(column.slice(0, 200)).toContain("filter: 'choice'")
  })

  it('avec une valeur exportable et non un badge nu', () => {
    // Sans `value`, l'export et le filtre reçoivent l'élément React : la
    // colonne s'exporte vide et le filtre ne propose rien.
    const column = COUNTING.slice(COUNTING.indexOf("key: 'sealStatus'"))
    expect(column.slice(0, 1400)).toContain(
      'value: (row) => toLabel(SEAL_STATUS_LABELS, row.sealStatus)',
    )
  })
})

describe('les trois libellés sont ceux de la décision', () => {
  it.each([
    ['UNSEALED', 'Non scellé'],
    ['SEALED_CLEAN', 'Scellé sans dérive'],
    ['SEALED_DRIFTING', 'Scellé avec dérives'],
  ])('%s se lit « %s »', (value, label) => {
    const table = FORMAT.slice(
      FORMAT.indexOf('export const SEAL_STATUS_LABELS'),
      FORMAT.indexOf('}', FORMAT.indexOf('export const SEAL_STATUS_LABELS')),
    )
    expect(table).toContain(`${value}: '${label}'`)
  })

  it('et le type du client porte les trois, pas une chaîne libre', () => {
    expect(TYPES).toContain(
      "export type SealStatus = 'UNSEALED' | 'SEALED_CLEAN' | 'SEALED_DRIFTING'",
    )
    expect(TYPES).toContain('sealStatus: SealStatus')
  })
})
