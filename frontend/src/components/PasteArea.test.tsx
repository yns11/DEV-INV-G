/**
 * La tabulation, dans la zone où l'on colle un bloc venu d'Excel.
 *
 * Le défaut : le séparateur de colonnes du presse-papier d'Excel est la
 * tabulation, et c'était le seul caractère qu'on ne pouvait pas taper dans la
 * zone faite pour recevoir ce presse-papier — la touche envoyait le focus sur
 * le bouton suivant. Ajouter à la main une colonne oubliée demandait d'ouvrir
 * un éditeur à côté, d'y composer la ligne, et de la recoller.
 *
 * Capturer une touche de navigation n'est pas gratuit : ce qui est tenu ici,
 * c'est autant l'insertion que la sortie qu'elle laisse — Échap, puis Tab.
 */

import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { PasteArea } from './PasteArea'

/** La zone, pilotée comme elle l'est en vrai : la valeur vit chez le parent. */
function Host({ initial = '' }: { initial?: string }) {
  const [text, setText] = useState(initial)
  return (
    <>
      <PasteArea value={text} onChange={setText} aria-label="Coller" />
      <button type="button">Analyser</button>
    </>
  )
}

const field = () => screen.getByLabelText('Coller') as HTMLTextAreaElement

describe('Tab', () => {
  it('insère une tabulation au lieu de changer de champ', async () => {
    render(<Host />)
    await userEvent.click(field())
    await userEvent.keyboard('P-00324093{Tab}PCE')
    expect(field().value).toBe('P-00324093\tPCE')
    expect(field()).toHaveFocus()
  })

  it('insère là où est le curseur, pas à la fin', async () => {
    render(<Host initial="PCE" />)
    const area = field()
    await userEvent.click(area)
    area.setSelectionRange(0, 0)
    await userEvent.keyboard('{Tab}')
    expect(field().value).toBe('\tPCE')
  })

  it('laisse le curseur après la tabulation insérée', async () => {
    render(<Host initial="AB" />)
    const area = field()
    await userEvent.click(area)
    area.setSelectionRange(1, 1)
    await userEvent.keyboard('{Tab}')
    expect(field().selectionStart).toBe(2)
    // Et la frappe suivante atterrit bien là.
    await userEvent.keyboard('X')
    expect(field().value).toBe('A\tXB')
  })

  it('remplace la sélection', async () => {
    render(<Host initial="ABCD" />)
    const area = field()
    await userEvent.click(area)
    area.setSelectionRange(1, 3)
    await userEvent.keyboard('{Tab}')
    expect(field().value).toBe('A\tD')
  })
})

describe('la sortie au clavier', () => {
  it('Maj+Tab quitte le champ', async () => {
    // Sans elle, entrer dans la zone sans connaître Échap serait sans issue.
    render(<Host />)
    await userEvent.click(field())
    await userEvent.tab({ shift: true })
    expect(field()).not.toHaveFocus()
    expect(field().value).toBe('')
  })

  it('Échap rend la touche à la navigation', async () => {
    render(<Host />)
    await userEvent.click(field())
    await userEvent.keyboard('{Escape}')
    await userEvent.keyboard('{Tab}')
    expect(field().value).toBe('')
    expect(screen.getByRole('button', { name: 'Analyser' })).toHaveFocus()
  })

  it('reprendre la frappe rend la touche au texte', async () => {
    // Échap libère le champ le temps d'en sortir ; taper, c'est y revenir.
    render(<Host />)
    await userEvent.click(field())
    await userEvent.keyboard('{Escape}')
    await userEvent.keyboard('A{Tab}B')
    expect(field().value).toBe('A\tB')
    expect(field()).toHaveFocus()
  })

  it('oublie la libération quand on revient dans le champ', async () => {
    render(<Host />)
    await userEvent.click(field())
    await userEvent.keyboard('{Escape}')
    await userEvent.keyboard('{Tab}')
    await userEvent.click(field())
    await userEvent.keyboard('{Tab}')
    expect(field().value).toBe('\t')
  })
})
