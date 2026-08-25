/**
 * Les signalements d'une ligne d'écart.
 *
 * Ils étaient écrits deux fois : une fois en badges dans le `render` de la
 * colonne, et… nulle part ailleurs. Le filtre, lui, ne lisait aucune valeur et
 * n'offrait donc que « (vide) », sur les quatre cent huit lignes d'une colonne
 * pleine de badges — la question que cette colonne existe pour poser,
 * « montre-moi les hors ERP », n'était pas posable.
 *
 * Les deux dérivent désormais de cette liste. Ce qui se vérifie ici est donc à
 * la fois ce qui s'affiche et ce qui se filtre : les faire diverger demanderait
 * de modifier une seule fonction dans deux directions à la fois.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { FLAGS_COLUMN, varianceFlags, type VarianceFlag } from './varianceFlags'
import type { VarianceRow } from '../lib/types'

const labels = (row: Parameters<typeof varianceFlags>[0]) =>
  varianceFlags(row).map((flag: VarianceFlag) => flag.label)

describe('ce qu’une ligne porte', () => {
  it('rien, quand il n’y a rien à signaler', () => {
    expect(varianceFlags({})).toEqual([])
  })

  it('« au-delà des seuils » quand l’écart est matériel', () => {
    expect(labels({ isMaterial: true })).toEqual(['au-delà des seuils'])
  })

  it('« hors ERP » pour un comptage sans stock en face', () => {
    expect(labels({ countedOnly: true })).toEqual(['hors ERP'])
  })

  it('« non compté » pour un stock que personne n’a vu', () => {
    expect(labels({ bookOnly: true })).toEqual(['non compté'])
  })

  it('plusieurs à la fois, et c’est le cas courant', () => {
    /* Les lignes de la capture : matérielles *et* hors ERP. C'est ce cumul qui
       interdisait une valeur unique — « au-delà des seuils · hors ERP » serait
       devenu une entrée de filtre à part entière. */
    expect(labels({ isMaterial: true, countedOnly: true })).toEqual([
      'au-delà des seuils', 'hors ERP',
    ])
  })

  it('dans l’ordre de l’urgence', () => {
    /* Ce qui dépasse les seuils d'abord, ce qui explique ensuite : l'ordre est
       celui dans lequel on lit la ligne. */
    expect(
      labels({ isMaterial: true, bookOnly: true, causeCode: 'SAISIE' }),
    ).toEqual(['au-delà des seuils', 'non compté', 'cause SAISIE'])
  })
})

describe('la cause et sa proposition', () => {
  it('la cause retenue s’affiche', () => {
    expect(labels({ causeCode: 'VOL' })).toEqual(['cause VOL'])
  })

  it('la proposition IA s’affiche tant qu’aucune cause n’est retenue', () => {
    expect(labels({ aiSuggestedCause: 'SAISIE' })).toEqual(['IA : SAISIE'])
  })

  it('elle s’efface dès qu’une cause est retenue', () => {
    /* La rappeler à côté de la décision laisse croire qu'il reste quelque
       chose à trancher. */
    expect(labels({ causeCode: 'VOL', aiSuggestedCause: 'SAISIE' })).toEqual([
      'cause VOL',
    ])
  })

  it('la justification de l’IA voyage en infobulle, pas en étiquette', () => {
    /* Une phrase entière comme valeur de filtre ferait une entrée par ligne. */
    const [flag] = varianceFlags({
      aiSuggestedCause: 'SAISIE',
      aiRationale: 'quantité voisine d’un multiple de 50',
    })

    expect(flag?.label).toBe('IA : SAISIE')
    expect(flag?.title).toBe('quantité voisine d’un multiple de 50')
  })
})

describe('les étiquettes sont utilisables comme valeurs de filtre', () => {
  it('elles sont stables d’une ligne à l’autre', () => {
    /* Une étiquette qui porterait la quantité ou la référence ferait autant
       d'entrées de filtre que de lignes — c'est-à-dire aucun filtre. */
    const first = labels({ isMaterial: true, countedOnly: true })
    const second = labels({ isMaterial: true, countedOnly: true })

    expect(first).toEqual(second)
  })

  it('aucune n’est vide', () => {
    /* Une étiquette vide se rangerait sous « (vide) » avec les lignes qui n'en
       ont aucune, et le compte de cette entrée deviendrait faux. */
    const every = varianceFlags({
      isMaterial: true, bookOnly: true, countedOnly: true, causeCode: 'X',
    })

    expect(every).toHaveLength(4)
    for (const flag of every) expect(flag.label.trim()).not.toBe('')
  })
})

describe('la colonne de l’écran, et non une copie du test', () => {
  /* T1 : retirer `tags` de la colonne réelle — le défaut de production
     exactement — ne faisait tomber aucun contrôle, parce que le contrôle de la
     grille définit sa propre colonne. Ceux-ci portent sur `FLAGS_COLUMN`, celle
     que l'écran monte. */

  it('déclare une facette d’étiquettes', () => {
    expect(FLAGS_COLUMN.tags).toBeTypeOf('function')
  })

  it('cette facette rend les étiquettes de la ligne', () => {
    const row = { isMaterial: true, countedOnly: true } as VarianceRow

    expect(FLAGS_COLUMN.tags?.(row)).toEqual(['au-delà des seuils', 'hors ERP'])
  })

  it('et les badges affichés portent exactement les mêmes mots', () => {
    /* T6 : c'est la propriété qui n'arrêtait pas de se perdre. Deux écritures
       de la même chose finissent par diverger — ici elles ne le peuvent plus,
       et ce contrôle est ce qui le vérifie. */
    const row = {
      isMaterial: true, bookOnly: true, causeCode: 'VOL',
    } as unknown as VarianceRow

    render(<>{FLAGS_COLUMN.render?.(row, 0)}</>)

    for (const label of FLAGS_COLUMN.tags?.(row) ?? []) {
      expect(screen.getByText(label)).toBeTruthy()
    }
  })

  it('une ligne sans signalement n’affiche aucun badge', () => {
    const row = {} as VarianceRow

    const { container } = render(<>{FLAGS_COLUMN.render?.(row, 0)}</>)

    expect(FLAGS_COLUMN.tags?.(row)).toEqual([])
    expect(container.textContent?.trim()).toBe('')
  })
})
