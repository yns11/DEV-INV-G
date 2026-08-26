/**
 * Les deux écrans qui affichent une réponse de modèle passent par le rendu.
 *
 * C'est le défaut d'origine, et il ne se voyait nulle part : la Synthèse IA
 * *avait* du markdown à afficher — le serveur le lui demande explicitement,
 * sections comprises — et le posait à l'écran en texte brut. Rien n'échouait ;
 * le comité de direction lisait « ## Message clé ».
 *
 * Un rendu écrit et non appelé est la forme la plus fréquente de défaut dans ce
 * dépôt. Ces contrôles portent donc sur le branchement, pas sur le rendu.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src')

/** Les écrans qui reçoivent du texte écrit par un modèle. */
const SCREENS = [
  'features/Assistant.tsx',
  'features/analysis.controls.tsx',
]

function source(path: string): string {
  return readFileSync(join(SRC, path), 'utf8')
}

describe.each(SCREENS)('%s', (path) => {
  it('rend la réponse par le composant partagé', () => {
    expect(source(path)).toMatch(/<Markdown\s/)
  })

  it('l’importe depuis le module partagé', () => {
    // Une copie locale rendrait le contrôle vert en laissant les deux rendus
    // diverger — c'est exactement d'où l'on vient.
    expect(source(path)).toContain("from '../lib/markdown'")
  })
})

describe('le rendu ne vit qu’à un endroit', () => {
  it('aucun écran ne redéclare le sien', () => {
    const guilty = SCREENS.filter((path) => /function Markdown\b/.test(source(path)))
    expect(guilty).toEqual([])
  })

  it('personne n’injecte la réponse en HTML', () => {
    // La réponse reprend du texte venu de fichiers importés. Le rendu écrit des
    // nœuds texte ; `dangerouslySetInnerHTML` défferait cette garantie d'une
    // ligne, et rien à l'écran ne le montrerait.
    for (const path of [...SCREENS, 'lib/markdown.tsx']) {
      expect(source(path)).not.toContain('dangerouslySetInnerHTML')
    }
  })
})

describe('le modèle sait qu’il peut produire ces tableaux', () => {
  const PROMPTS = join(process.cwd(), '..', 'app', 'inventory', 'ai')

  it('l’assistant le lui dit', () => {
    // Rendre un format que le modèle n'émet jamais ne change rien à l'écran ;
    // le demander sans le rendre l'aurait affiché en barres verticales.
    const prompt = readFileSync(join(PROMPTS, 'assistant.py'), 'utf8')
    expect(prompt).toContain('tableaux')
    expect(prompt).toContain('|---|---:|---:|')
  })

  it('la synthèse de clôture aussi', () => {
    const prompt = readFileSync(join(PROMPTS, 'insights.py'), 'utf8')
    expect(prompt).toContain('tableau')
  })
})
