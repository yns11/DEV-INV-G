/**
 * La colonne « Section » telle qu'elle est réellement déclarée.
 *
 * Ce test porte sur la colonne que les deux écrans utilisent, pas sur une
 * colonne écrite pour l'occasion : c'est la leçon d'un contrôle précédent, qui
 * validait un `tags` de laboratoire pendant que la vraie colonne l'avait perdu.
 *
 * Le défaut couvert : sur les feuilles préparées, la colonne n'avait pas de
 * rendu. Une grille non éditable retombe alors sur la valeur de la clé — le
 * code — et la capture montrait « LINE_SIDE » là où la même colonne, ouverte à
 * l'édition, disait « Bord de ligne ».
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SECTION_CHOICES, sectionColumn, sectionLabel } from './sectionColumn'
import { SECTION_LABELS } from '../lib/format'
import { exportValue } from '../components/DataGrid'

describe('sectionLabel', () => {
  it.each(SECTION_CHOICES)('%s se lit en français', (code) => {
    expect(sectionLabel(code)).toBe(SECTION_LABELS[code])
    expect(sectionLabel(code)).not.toBe(code)
  })

  it('rend une cellule vide vide, pas un tiret', () => {
    // La grille peint déjà l'absence à sa façon ; un tiret ici partirait aussi
    // dans le fichier exporté.
    expect(sectionLabel(null)).toBe('')
    expect(sectionLabel('')).toBe('')
  })

  it('laisse passer un code qu’on ne connaît pas', () => {
    expect(sectionLabel('MOM_OK')).toBe('MOM_OK')
  })
})

describe('la colonne', () => {
  it('offre les trois sections', () => {
    expect(sectionColumn().choices).toEqual(['LINE_SIDE', 'WIP', 'WIP_OK'])
  })

  it('affiche le libellé sans qu’on lui demande un rendu', () => {
    const column = sectionColumn()
    render(<>{column.render?.({ section: 'LINE_SIDE' }, 0)}</>)
    expect(screen.getByText('Bord de ligne')).toBeInTheDocument()
  })

  it('exporte le libellé', () => {
    expect(exportValue({ section: 'WIP' }, sectionColumn())).toBe('WIP (à éclater)')
  })

  it('trie et filtre toujours sur le code', () => {
    // Le libellé se lit ; c'est le code qui ordonne et qui se compare.
    expect(sectionColumn().value?.({ section: 'WIP_OK' })).toBe('WIP_OK')
  })

  it('se laisse repeindre sans perdre son vocabulaire', () => {
    // La saisie d'une feuille remplace le rendu par un badge ; la liste
    // déroulante et l'export doivent continuer de parler français.
    const column = sectionColumn({ render: () => <span>badge</span> })
    expect(column.choiceLabel?.('LINE_SIDE')).toBe('Bord de ligne')
  })
})
