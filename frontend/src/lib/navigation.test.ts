/**
 * Ce qui est ouvert, et quand.
 *
 * La barre latérale décide de deux choses distinctes : si une section est
 * atteignable (`enabled`), et ce qu'on lit à sa place quand elle ne l'est pas
 * (`locked`). Les deux se règlent ici, une ligne par section, et une ligne
 * change sans que rien ne s'affiche différemment ailleurs — c'est ce qui rend
 * un verrou facile à poser par inadvertance, et invisible ensuite.
 *
 * **Contrôles n'est jamais verrouillée.** Elle l'a été, derrière le gel du
 * stock ERP, avec le reste de l'analyse. Or elle ne calcule aucun écart : elle
 * relit le dossier tel qu'il est, et la plupart de ses contrôles portent sur ce
 * qui se prépare *avant* le gel — le référentiel, les nomenclatures, les zones,
 * et les références que le stock ERP n'a pas pu charger. Les retenir jusqu'au
 * gel revenait à cacher les défauts pendant toute la phase où ils se corrigent
 * encore, puis à les montrer d'un coup au moment le plus cher.
 *
 * Le reste de l'analyse garde son verrou, et c'est le second contrôle : ouvrir
 * les écarts sans stock de référence afficherait des tirets, pas des écarts.
 */

import { describe, expect, it } from 'vitest'

import { SECTIONS } from './navigation'
import type { Overview } from './types'

/** Le strict nécessaire : ce que les gardes de `SECTIONS` lisent réellement. */
function overview(over: {
  frozen?: string | null
  status?: string
  blockedBy?: Record<string, string>
} = {}): Overview {
  return {
    campaign: {
      book_stock_frozen_at: over.frozen ?? null,
      status: over.status ?? 'PREPARATION',
    },
    sequence: { blockedBy: over.blockedBy ?? {} },
  } as unknown as Overview
}

const section = (to: string) => {
  const found = SECTIONS.find((s) => s.to === to)
  if (!found) throw new Error(`section « ${to} » introuvable`)
  return found
}

/** Les vues d'analyse qui, elles, ont besoin du stock gelé. */
const NEEDS_FROZEN_STOCK = ['ecarts', 'causes', 'ajustements']

describe('la vue Contrôles', () => {
  it('est atteignable dès la préparation, sans stock ERP gelé', () => {
    expect(section('controles').enabled(overview())).toBe(true)
  })

  it('l’est encore une fois le stock gelé', () => {
    expect(section('controles').enabled(overview({ frozen: '2026-09-01T06:00:00Z' })))
      .toBe(true)
  })

  it('n’annonce aucune raison d’être fermée', () => {
    // `locked` n'est lu que lorsque `enabled` rend faux. En laisser un ici
    // serait du texte mort — et le signe qu'un verrou a été remis.
    expect(section('controles').locked).toBeUndefined()
  })
})

describe('le reste de l’analyse attend le stock gelé', () => {
  it.each(NEEDS_FROZEN_STOCK)('%s est fermée sans stock gelé', (to) => {
    expect(section(to).enabled(overview())).toBe(false)
  })

  it.each(NEEDS_FROZEN_STOCK)('%s dit pourquoi elle est fermée', (to) => {
    expect(section(to).locked?.(overview())).toMatch(/stock ERP gelé/)
  })

  it.each(NEEDS_FROZEN_STOCK)('%s s’ouvre une fois le stock gelé', (to) => {
    expect(section(to).enabled(overview({ frozen: '2026-09-01T06:00:00Z' }))).toBe(true)
  })
})

describe('une section fermée dit toujours pourquoi', () => {
  it('aucune ne laisse un lien mort derrière elle', () => {
    // Une section qui refuse sans un mot est un lien qui ne répond pas :
    // l'utilisateur clique, rien ne se passe, et rien ne dit ce qui manque.
    const mute = SECTIONS.filter(
      (s) => !s.enabled(overview()) && !s.locked?.(overview()),
    )
    expect(mute.map((s) => s.to)).toEqual([])
  })
})

/**
 * L'ordre de la phase Comptage suit l'ordre des gestes.
 *
 * Un emplacement précompté l'est des jours avant le jour J — avant même que le
 * stock ERP ne soit gelé. « Comptages avancés » listé après « Stock ERP »
 * donnait donc un ordre de lecture qui contredisait l'ordre du travail, et la
 * barre latérale est justement ce qui apprend le déroulement à quelqu'un qui
 * fait sa première campagne.
 *
 * Vérifié sur l'ordre entier plutôt que sur une paire : une section insérée au
 * milieu passerait à travers une comparaison deux à deux.
 */
describe('la phase Comptage', () => {
  it('se lit dans l’ordre où le travail se fait', () => {
    const counting = SECTIONS.filter((s) => s.phase === 'COUNTING').map((s) => s.to)
    expect(counting).toEqual([
      'comptages-avances',
      'stock-erp',
      'backflush',
      'compil',
      'comptage',
    ])
  })
})
