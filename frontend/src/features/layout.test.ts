/**
 * Les trois écrans découpés ne se recollent pas.
 *
 * Ce qu'étaient ces fichiers
 * --------------------------
 * `Preparation.tsx` faisait 2114 lignes, `Generic.tsx` 2268 et `Analysis.tsx`
 * 1761 — six mille lignes pour trois écrans. Aucun n'était faux ; chacun était
 * devenu l'endroit où l'on ajoute, parce que c'était déjà l'endroit où tout se
 * trouvait. Ouvrir « la préparation » pour corriger un libellé de seuil
 * obligeait à traverser les articles, les nomenclatures et le stock ERP.
 *
 * Le découpage suit les onglets, qui étaient déjà les sections du fichier. Ce
 * qui reste dans le fichier d'origine est un aiguillage : quel onglet est
 * ouvert, et à qui le rendre.
 *
 * Ce que ces contrôles tiennent
 * -----------------------------
 * La pente d'un écran découpé est qu'un onglet regrossisse, ou que l'aiguillage
 * se remette à afficher quelque chose. Les deux se corrigent une fois et
 * reviennent, sauf si quelque chose les refuse.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const FEATURES = join(process.cwd(), 'src', 'features')

/**
 * Les trois écrans découpés, et le préfixe de leurs onglets.
 *
 * Le nombre d'onglets attendu est écrit ici : un module qui disparaît sans
 * qu'on le décide se voit, et un onglet ajouté demande de passer par ce
 * contrôle — ce qui est le moment de se demander s'il a sa place.
 */
const SCREENS = [
  { shell: 'Preparation.tsx', prefix: 'preparation.', tabs: 6 },
  { shell: 'Generic.tsx', prefix: 'generic.', tabs: 6 },
  { shell: 'Analysis.tsx', prefix: 'analysis.', tabs: 5 },
]

/**
 * Au-delà, l'onglet est redevenu ce qu'on vient de défaire.
 *
 * Sept cent cinquante laisse de la marge au plus gros — les écarts, avec leur
 * grille, leur explication et leur carte de transfert — sans laisser repasser
 * deux mille lignes.
 */
const TAB_CEILING = 750

/**
 * Les modules d'un écran que l'aiguillage n'importe pas, et pourquoi.
 *
 * Ce ne sont pas des onglets : ce sont des fenêtres et des vues ouvertes
 * *depuis* un onglet. `generic.sheet` est la feuille qu'on ouvre pour saisir,
 * `generic.scan` la lecture d'une pile, `generic.layout` l'aperçu de la feuille
 * imprimée — les trois s'ouvrent depuis l'onglet Zones.
 */
const SHARED_MODULES: Record<string, string[]> = {
  'generic.': ['generic.layout.tsx', 'generic.scan.tsx', 'generic.sheet.tsx'],
  // Les blocs communs aux six onglets de la préparation — encarts d'import,
  // bandeaux de gel — vivent à côté d'eux plutôt que recopiés six fois.
  'preparation.': ['preparation.shared.tsx'],
}

/**
 * Un aiguillage tient en une centaine de lignes.
 *
 * Au-delà, il a recommencé à afficher : le seuil est bas exprès, parce que
 * c'est par là que le recollage commence — « juste ce petit encart, il est
 * commun aux trois onglets ».
 */
const SHELL_CEILING = 110

function read(name: string): string {
  return readFileSync(join(FEATURES, name), 'utf8')
}

function tabsOf(prefix: string): string[] {
  return readdirSync(FEATURES)
    .filter((f) => f.startsWith(prefix) && f.endsWith('.tsx') && !f.endsWith('.test.tsx'))
    .sort()
}

function lines(name: string): number {
  return read(name).split('\n').length
}

describe.each(SCREENS)('$shell', ({ shell, prefix, tabs }) => {
  it('a bien été découpé', () => {
    expect(tabsOf(prefix)).toHaveLength(tabs)
  })

  it('ne garde qu’un aiguillage', () => {
    expect(lines(shell)).toBeLessThanOrEqual(SHELL_CEILING)
  })

  it('rend ses onglets plutôt que leur contenu', () => {
    // Un aiguillage importe ce qu'il rend ; s'il déclare des composants, il a
    // recommencé à être un écran.
    const source = read(shell)
    const declared = source.match(/^(export )?function [A-Z]\w*/gm) ?? []
    expect(declared.length).toBeLessThanOrEqual(1)
  })

  it('importe chacun de ses onglets', () => {
    const source = read(shell)
    const missing = tabsOf(prefix).filter(
      (f) => !source.includes(`'./${f.replace(/\.tsx$/, '')}'`),
    )
    // Un module qui n'est pas dans l'aiguillage est ouvert **depuis** un onglet
    // — une fenêtre, une grille partagée. Les nommer un par un plutôt que
    // tolérer « deux ou trois » : le jour où un vrai onglet est oublié, la
    // liste le dit, alors qu'un compte l'absorbe en silence.
    expect(missing).toEqual(SHARED_MODULES[prefix] ?? [])
  })
})

describe('aucun onglet ne redevient un écran', () => {
  const everyTab = SCREENS.flatMap(({ prefix }) => tabsOf(prefix))

  it('il y a bien des onglets à contrôler', () => {
    // Un contrôle qui passerait sur zéro fichier ne contrôlerait rien.
    expect(everyTab.length).toBeGreaterThanOrEqual(14)
  })

  it.each(everyTab)('%s tient sous le plafond', (name) => {
    expect(lines(name)).toBeLessThanOrEqual(TAB_CEILING)
  })

  it.each(everyTab)('%s dit ce qu’il porte', (name) => {
    // Sans phrase d'ouverture, seize fichiers valent une liste de noms.
    const first = read(name).split('\n')[0] ?? ''
    expect(first.startsWith('/**')).toBe(true)
    expect(first.length).toBeGreaterThan(30)
  })
})

describe('les onglets ne rappellent pas l’écran dont ils sortent', () => {
  const shells = SCREENS.map((s) => s.shell.replace(/\.tsx$/, ''))
  const everyTab = SCREENS.flatMap(({ prefix }) => tabsOf(prefix))

  it.each(everyTab)('%s n’importe aucun aiguillage', (name) => {
    // « C'est là qu'on avait la campagne sous la main » est la phrase par
    // laquelle un onglet redevient une méthode de son écran.
    const source = read(name)
    for (const shell of shells) {
      expect(source).not.toContain(`from './${shell}'`)
    }
  })
})
