/**
 * L'état des lieux avant le seul geste irréversible.
 *
 * Ce que ces contrôles tiennent, et pourquoi c'est un écran à contrôler plutôt
 * qu'un simple affichage :
 *
 * * **Un point vert ne propose pas d'aller le défaire.** Le lien n'apparaît
 *   que là où il reste quelque chose à faire ; l'inverse est une invitation à
 *   revenir sur ce qui est réglé, la veille de la clôture.
 * * **Ce qui est fait figure aussi.** Une liste qui ne montre que les
 *   reproches se lit comme une machine à empêcher, alors qu'on vient y
 *   chercher un état des lieux.
 * * **Le résumé compte ce que la liste montre.** Deux chiffres qui divergent
 *   de leurs lignes font douter des deux.
 */

import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { ClosureChecklistView } from './ClosureChecklist'
import type { ChecklistItem, ClosureChecklist } from '../lib/types'

const CAMPAIGN = 'camp-1'

function item(over: Partial<ChecklistItem> = {}): ChecklistItem {
  return {
    code: 'MATERIAL_VARIANCES_UNEXPLAINED',
    label: 'Écarts matériels expliqués',
    state: 'BLOCKING',
    detail: '3 écarts sans cause assignée.',
    where: 'ecarts',
    ...over,
  } as ChecklistItem
}

function checklist(items: ChecklistItem[]): ClosureChecklist {
  const count = (state: string) => items.filter((i) => i.state === state).length
  return {
    ready: count('BLOCKING') === 0,
    allowed: true,
    items,
    counts: {
      blocking: count('BLOCKING'),
      attention: count('ATTENTION'),
      done: count('DONE'),
    },
  } as ClosureChecklist
}

function show(data: ClosureChecklist | undefined, pending = false) {
  return render(
    <MemoryRouter>
      <ClosureChecklistView campaignId={CAMPAIGN} data={data} pending={pending} />
    </MemoryRouter>,
  )
}

const rows = () => screen.getAllByRole('listitem')

/** La n-ième ligne de la liste, ou un échec qui nomme la cause. */
function row(index = 0): HTMLElement {
  const found = rows()[index]
  if (!found) throw new Error(`aucun point de contrôle au rang ${index}`)
  return found
}

describe('les trois tons se lisent', () => {
  it('un point bloquant est annoncé comme tel', () => {
    show(checklist([item()]))
    expect(within(row()).getByText('Bloquant')).toBeInTheDocument()
  })

  it('un point à regarder n’est pas présenté comme bloquant', () => {
    show(checklist([item({ state: 'ATTENTION' })]))
    expect(within(row()).getByText('À regarder')).toBeInTheDocument()
    expect(within(row()).queryByText('Bloquant')).not.toBeInTheDocument()
  })

  it('un point réglé figure dans la liste', () => {
    // Le taire ferait de l'écran une machine à empêcher.
    show(checklist([item({ state: 'DONE' })]))
    expect(within(row()).getByText('Fait')).toBeInTheDocument()
  })

  it('le détail accompagne le libellé', () => {
    // « Écarts matériels expliqués : bloquant » n'apprend pas combien.
    show(checklist([item()]))
    expect(screen.getByText('3 écarts sans cause assignée.')).toBeInTheDocument()
  })
})

describe('le lien mène là où il y a quelque chose à faire', () => {
  it('un point bloquant en propose un', () => {
    show(checklist([item()]))
    expect(screen.getByRole('link')).toHaveAttribute(
      'href', `/campagnes/${CAMPAIGN}/ecarts`,
    )
  })

  it('un point à regarder en propose un aussi', () => {
    show(checklist([item({ state: 'ATTENTION' })]))
    expect(screen.getByRole('link')).toBeInTheDocument()
  })

  it('un point réglé n’en propose pas', () => {
    // Proposer d'aller « corriger » un point déjà vert est une invitation à
    // défaire ce qui est fait.
    show(checklist([item({ state: 'DONE' })]))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('un point sans écran à ouvrir n’en propose pas', () => {
    // `where` est nul pour les constats qui n'ont pas d'écran dédié.
    show(checklist([item({ where: null })]))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('le lien reste dans la campagne courante', () => {
    show(checklist([item({ where: 'compil?vue=arbitration' })]))
    expect(screen.getByRole('link').getAttribute('href'))
      .toContain(`/campagnes/${CAMPAIGN}/`)
  })
})

describe('le résumé compte ce que la liste montre', () => {
  const mixed = checklist([
    item(),
    item({ code: 'B', state: 'BLOCKING' }),
    item({ code: 'C', state: 'ATTENTION' }),
    item({ code: 'D', state: 'DONE' }),
    item({ code: 'E', state: 'DONE' }),
  ])

  it('les bloquants', () => {
    show(mixed)
    expect(screen.getByText('2 bloquant(s)')).toBeInTheDocument()
  })

  it('les points à regarder', () => {
    show(mixed)
    expect(screen.getByText('1 à regarder')).toBeInTheDocument()
  })

  it('les points faits', () => {
    show(mixed)
    expect(screen.getByText('2 fait(s)')).toBeInTheDocument()
  })

  it('autant de lignes que de points', () => {
    show(mixed)
    expect(rows()).toHaveLength(5)
  })

  it('chaque point garde sa place', () => {
    show(mixed)
    expect(rows()).toHaveLength(mixed.items.length)
  })
})

describe('un dossier prêt', () => {
  it('n’annonce aucun bloquant', () => {
    show(checklist([item({ state: 'DONE' }), item({ code: 'B', state: 'DONE' })]))
    expect(screen.getByText('0 bloquant(s)')).toBeInTheDocument()
  })

  it('ne propose aucun détour', () => {
    show(checklist([item({ state: 'DONE' })]))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})

describe('tant que la liste n’est pas là', () => {
  it('elle ne prétend pas que tout va bien', () => {
    // Rendre « 0 bloquant » pendant le chargement est le pire des affichages :
    // il dit exactement le contraire de ce qu'on ne sait pas encore.
    show(undefined, true)
    expect(screen.queryByText('0 bloquant(s)')).not.toBeInTheDocument()
  })

  it('une réponse absente est traitée comme un chargement', () => {
    show(undefined, false)
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })
})
