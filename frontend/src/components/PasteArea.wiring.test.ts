/**
 * Les zones de collage passent toutes par `PasteArea`.
 *
 * Ce contrôle-ci ne juge pas un comportement mais un branchement, et c'est
 * délibéré : le défaut le plus fréquent de ce dépôt n'est pas une règle fausse,
 * c'est une règle écrite quelque part et pas appelée. Une `<textarea>` de
 * collage déclarée à la main serait exactement cela — la tabulation y
 * redeviendrait une touche de navigation, sans que rien ne le dise.
 *
 * Le repère est la classe `textarea mono`, celle des zones qui attendent un
 * bloc tabulé venu d'un tableur. La zone de dialogue de l'assistant n'en est
 * pas une : on y écrit une phrase, et Tab doit y mener au bouton d'envoi.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = join(process.cwd(), 'src')
const OWNER = 'PasteArea.tsx'

function sourcesUnder(directory: string): string[] {
  return readdirSync(join(SRC, directory))
    .filter((f) => /\.tsx$/.test(f) && !f.includes('.test.'))
    .map((f) => join(directory, f))
}

const FILES = [...sourcesUnder('components'), ...sourcesUnder('features')]

describe('la classe des zones de collage', () => {
  it('il y a bien des fichiers à contrôler', () => {
    expect(FILES.length).toBeGreaterThan(20)
  })

  it('n’est déclarée que par PasteArea', () => {
    const guilty = FILES.filter(
      (path) =>
        !path.endsWith(OWNER) &&
        readFileSync(join(SRC, path), 'utf8').includes('className="textarea mono"'),
    )
    expect(guilty).toEqual([])
  })

  it('est bien déclarée par PasteArea', () => {
    // Sinon le contrôle ci-dessus passerait sur un repère qui n'existe plus.
    const source = readFileSync(join(SRC, 'components', OWNER), 'utf8')
    expect(source).toContain('className="textarea mono"')
  })
})

describe('les deux écrans qui collent un bloc', () => {
  it.each(['components/ImportPanel.tsx', 'features/generic.sheet.tsx'])(
    '%s utilise PasteArea',
    (path) => {
      const source = readFileSync(join(SRC, path), 'utf8')
      expect(source).toContain('<PasteArea')
    },
  )
})
