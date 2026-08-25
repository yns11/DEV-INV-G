/**
 * La colonne « Section » d'une grille de lignes de feuille, en un seul endroit.
 *
 * Elle apparaît sur deux écrans — les feuilles préparées et la saisie d'une
 * feuille — et les deux déclaraient leur propre liste `['LINE_SIDE', 'WIP',
 * 'WIP_OK']` avec leur propre façon de la nommer. L'une peignait un badge et
 * disait « Bord de ligne » ; l'autre n'avait pas de rendu et affichait le code
 * tel quel. Deux déclarations du même vocabulaire, dont une seule traduite.
 */

import type { Column } from '../components/DataGrid'
import { SECTION_LABELS, label as toLabel } from '../lib/format'

/** Les trois sections, dans l'ordre où elles se lisent sur une feuille. */
export const SECTION_CHOICES = ['LINE_SIDE', 'WIP', 'WIP_OK']

/** Comment une section se lit. Un code inconnu se rend tel quel, pas en tiret. */
export function sectionLabel(value: unknown): string {
  const code = value === null || value === undefined ? '' : String(value)
  return code === '' ? '' : toLabel(SECTION_LABELS, code)
}

/**
 * La colonne, avec son rendu par défaut : le libellé.
 *
 * `overrides` s'applique après, ce qui permet de peindre un badge à la place —
 * ou de repasser `render: undefined` quand la grille est en cours d'édition et
 * que c'est la liste déroulante qui s'affiche.
 */
export function sectionColumn(overrides: Partial<Column> = {}): Column {
  return {
    key: 'section',
    label: 'Section',
    width: 160,
    choices: SECTION_CHOICES,
    choiceLabel: (value) => toLabel(SECTION_LABELS, value),
    render: (row) => sectionLabel((row as Record<string, unknown>).section),
    value: (row) => String((row as Record<string, unknown>).section ?? ''),
    ...overrides,
  }
}
