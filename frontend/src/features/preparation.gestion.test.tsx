/**
 * L'onglet Paramètres : ce que la campagne accepte, puis à partir de quand un
 * écart compte.
 *
 * Il s'appelait « Seuils » et ne portait qu'eux. Le premier réglage venu qui
 * n'était pas un seuil n'avait donc aucun endroit où aller — un onglet nommé
 * d'après son unique contenu ne peut pas en accueillir un second sans mentir
 * sur ce qu'il contient.
 *
 * Ce qui se vérifie ici :
 *
 * * **Les deux blocs cohabitent, dans cet ordre.** Les formules décident de ce
 *   qu'un champ de saisie accepte, et la question se pose le jour de
 *   l'inventaire ; les seuils décident de ce qui sera signalé trois semaines
 *   plus tard. Le plus urgent est en tête.
 * * **L'état affiché est celui de la campagne**, jamais un état local qui
 *   dériverait de la base.
 * * **Le gel se voit.** Un interrupteur actif sur une campagne où il ne change
 *   plus rien est une promesse que le serveur refusera.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SettingsTab } from './preparation.gestion'
import { ToastProvider } from '../components/ui'
import type { Overview } from '../lib/types'

const saveSettings = vi.fn(() => Promise.resolve({}))

vi.mock('../lib/api', () => ({
  api: {
    thresholds: () => Promise.resolve([]),
    saveThresholds: () => Promise.resolve([]),
    saveSettings: (...args: unknown[]) => saveSettings(...(args as [])),
  },
}))

function overview({
  allowFormulas = false,
  settings = true,
}: { allowFormulas?: boolean; settings?: boolean } = {}): Overview {
  return {
    campaign: {
      id: 'camp-1',
      code: 'INV-2026-T3',
      status: 'COUNTING',
      config: { allow_formulas: allowFormulas },
    },
    permissions: { settings, thresholds: false },
  } as unknown as Overview
}

function show(over: Parameters<typeof overview>[0] = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SettingsTab campaignId="camp-1" overview={overview(over)} />
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const toggle = () => screen.getByRole('checkbox')

describe('l’onglet porte plus que les seuils', () => {
  it('le réglage des formules est présent', () => {
    show()
    expect(screen.getByText('Accepter des formules dans les comptages')).toBeTruthy()
  })

  it('les seuils y sont toujours', () => {
    show()
    expect(screen.getByText('Seuils de matérialité')).toBeTruthy()
  })

  it('les formules viennent avant les seuils', () => {
    /* L'ordre est la décision : ce qui se règle le jour J passe devant ce qui
       se règle trois semaines plus tard. */
    const { container } = show()
    const text = container.textContent ?? ''
    expect(text.indexOf('Accepter des formules')).toBeLessThan(
      text.indexOf('Seuils de matérialité'),
    )
  })
})

describe('l’interrupteur', () => {
  it('reflète l’état de la campagne, pas un état local', () => {
    show({ allowFormulas: true })
    expect((toggle() as HTMLInputElement).checked).toBe(true)
  })

  it('est décoché quand la campagne refuse les formules', () => {
    show({ allowFormulas: false })
    expect((toggle() as HTMLInputElement).checked).toBe(false)
  })

  it('envoie le nouveau réglage au serveur', async () => {
    saveSettings.mockClear()
    show({ allowFormulas: false })

    await userEvent.click(toggle())

    expect(saveSettings).toHaveBeenCalledWith('camp-1', { allowFormulas: true })
  })

  it('sait aussi éteindre', async () => {
    saveSettings.mockClear()
    show({ allowFormulas: true })

    await userEvent.click(toggle())

    expect(saveSettings).toHaveBeenCalledWith('camp-1', { allowFormulas: false })
  })

  it('dit en toutes lettres ce que l’état veut dire', () => {
    /* « Activé » seul n'apprend rien à qui ouvre l'écran sans savoir de quoi
       il s'agit. */
    show({ allowFormulas: true })
    expect(screen.getByText(/sont calculées/)).toBeTruthy()
  })

  it('montre ce qui est accepté, plutôt que de le décrire', () => {
    show()
    expect(screen.getByText('3*48+7')).toBeTruthy()
  })
})

describe('quand le réglage est gelé', () => {
  it('l’interrupteur est désactivé', () => {
    show({ settings: false })
    expect((toggle() as HTMLInputElement).disabled).toBe(true)
  })

  it('et l’écran dit pourquoi', () => {
    /* Un contrôle grisé sans explication est indistinguable d'un bug. */
    show({ settings: false })
    expect(screen.getByText('Réglage gelé')).toBeTruthy()
  })

  it('rien n’est dit quand il ne l’est pas', () => {
    show({ settings: true })
    expect(screen.queryByText('Réglage gelé')).toBeNull()
  })
})
