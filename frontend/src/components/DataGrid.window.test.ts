/**
 * La fenêtre de la grille, contrôlée sur la vraie fonction.
 *
 * Ce calcul était déjà couvert — mais en Python, son arithmétique **rejouée**
 * à partir du source TypeScript, faute de banc côté navigateur. Une
 * transcription ne contrôle que sa propre copie : la première version portait
 * ses propres garde-fous, si bien que les retirer du TypeScript ne faisait
 * rien échouer. Le défaut avait été rattrapé en épinglant les lignes exactes,
 * ce qui rendait le contrôle fragile à un renommage.
 *
 * Il lit désormais la fonction livrée. C'est le premier acquis du banc Vitest,
 * et la raison pour laquelle la transcription Python disparaît plutôt que de
 * coexister : deux copies d'un même contrôle finissent par ne plus dire la
 * même chose, et c'est la moins vraie qu'on croit.
 *
 * Ce qui peut être faux ici sans qu'on le voie : une erreur d'un cran affiche
 * les bonnes lignes au mauvais endroit, ou laisse un blanc en fin de liste —
 * deux défauts qu'on attribue au navigateur avant de les attribuer au calcul.
 */

import { describe, expect, it } from 'vitest'

import { windowOf } from './DataGrid'

/** Un cadre courant : lignes de 37 px, fenêtre de 600 px. */
const frame = { rowHeight: 37, viewport: 600 }

describe('la fenêtre couvre ce qu’on voit', () => {
  it('part du haut quand rien n’a défilé', () => {
    expect(windowOf({ total: 5000, scrollTop: 0, ...frame }).start).toBe(0)
  })

  it('ne remonte jamais avant la première ligne', () => {
    // Le sur-rendu retrancherait douze lignes à zéro.
    expect(windowOf({ total: 5000, scrollTop: 0, ...frame }).start).toBe(0)
  })

  it('couvre au moins la hauteur visible', () => {
    const { start, end } = windowOf({ total: 5000, scrollTop: 3700, ...frame })
    const visibleFirst = Math.floor(3700 / 37)
    const visibleLast = Math.ceil((3700 + 600) / 37)
    expect(start).toBeLessThanOrEqual(visibleFirst)
    expect(end).toBeGreaterThanOrEqual(visibleLast)
  })

  it('rend un peu au-delà, des deux côtés', () => {
    // Sans marge, un coup de molette montre du blanc le temps d'un rendu.
    const { start, end } = windowOf({ total: 5000, scrollTop: 3700, ...frame })
    expect(Math.floor(3700 / 37) - start).toBe(12)
    expect(end - Math.ceil((3700 + 600) / 37)).toBe(12)
  })

  it('ne dépasse jamais le nombre de lignes', () => {
    // Vingt lignes tiennent dans la fenêtre visible plus sa marge : la borne
    // qui s'applique est le nombre de lignes, pas le calcul de hauteur.
    const { end } = windowOf({ total: 20, scrollTop: 0, ...frame })
    expect(end).toBe(20)
  })

  it('s’arrête à la marge quand la liste est plus longue qu’elle', () => {
    // Quarante lignes : la fenêtre en couvre dix-sept visibles plus douze de
    // marge, et les onze dernières restent hors du DOM. Rendre les quarante
    // ferait disparaître le gain sur les listes moyennes.
    const { end } = windowOf({ total: 40, scrollTop: 0, ...frame })
    expect(end).toBe(Math.ceil(600 / 37) + 12)
    expect(end).toBeLessThan(40)
  })

  it('reste cohérente une fois la liste dépassée', () => {
    // Peut arriver quand un filtre raccourcit la liste sans remettre à zéro
    // la position de défilement.
    const { start, end } = windowOf({ total: 10, scrollTop: 90_000, ...frame })
    expect(end).toBeGreaterThanOrEqual(start)
  })

  it('ne rend rien sur une liste vide', () => {
    const { start, end } = windowOf({ total: 0, scrollTop: 0, ...frame })
    expect(end - start).toBe(0)
  })
})

describe('les cales portent les lignes absentes', () => {
  it('la cale du haut vaut les lignes sautées', () => {
    const { start, before } = windowOf({ total: 5000, scrollTop: 3700, ...frame })
    expect(before).toBe(start * 37)
  })

  it('la cale du bas vaut les lignes restantes', () => {
    const { end, after } = windowOf({ total: 5000, scrollTop: 3700, ...frame })
    expect(after).toBe((5000 - end) * 37)
  })

  it('les deux cales et les lignes rendues font la hauteur totale', () => {
    // C'est ce qui empêche la barre de défilement de mentir sur la longueur
    // du tableau — sans quoi vingt mille lignes défileraient sur la hauteur
    // de trente, et il n'y aurait plus rien à faire glisser.
    const { start, end, before, after } = windowOf({
      total: 5000, scrollTop: 3700, ...frame,
    })
    expect(before + (end - start) * 37 + after).toBe(5000 * 37)
  })

  it('la cale du bas ne devient jamais négative', () => {
    const { after } = windowOf({ total: 10, scrollTop: 90_000, ...frame })
    expect(after).toBe(0)
  })

  it('rien au-dessus quand on est en haut', () => {
    expect(windowOf({ total: 5000, scrollTop: 0, ...frame }).before).toBe(0)
  })

  it('rien en dessous quand tout est rendu', () => {
    expect(windowOf({ total: 20, scrollTop: 0, ...frame }).after).toBe(0)
  })
})

describe('une hauteur de ligne non mesurée', () => {
  it('ne rend pas la fenêtre vide', () => {
    // La mesure est faite après le premier rendu : avant, elle vaut zéro.
    // Diviser par elle donnerait l'infini, et il n'y aurait rien à afficher —
    // donc rien à mesurer.
    const { start, end } = windowOf({
      total: 5000, scrollTop: 0, rowHeight: 0, viewport: 600,
    })
    expect(end).toBeGreaterThan(start)
  })

  it('retombe sur une hauteur plausible', () => {
    const { after } = windowOf({
      total: 100, scrollTop: 0, rowHeight: 0, viewport: 600,
    })
    // 37 px : la hauteur d'une ligne de ce tableau, au pixel près.
    expect(after % 37).toBe(0)
  })

  it('une hauteur négative est traitée comme absente', () => {
    const guessed = windowOf({
      total: 100, scrollTop: 0, rowHeight: -5, viewport: 600,
    })
    const missing = windowOf({
      total: 100, scrollTop: 0, rowHeight: 0, viewport: 600,
    })
    expect(guessed).toEqual(missing)
  })
})

describe('le sur-rendu se règle', () => {
  it('zéro ne rend que le visible', () => {
    const { start } = windowOf({
      total: 5000, scrollTop: 3700, ...frame, overscan: 0,
    })
    expect(start).toBe(Math.floor(3700 / 37))
  })

  it('une marge plus large part de plus haut', () => {
    const petite = windowOf({ total: 5000, scrollTop: 3700, ...frame, overscan: 2 })
    const large = windowOf({ total: 5000, scrollTop: 3700, ...frame, overscan: 40 })
    expect(large.start).toBeLessThan(petite.start)
    expect(large.end).toBeGreaterThan(petite.end)
  })
})

describe('la fenêtre reste juste sur tout le parcours', () => {
  it('ne saute ni ne recouvre en descendant ligne à ligne', () => {
    // Le défaut d'un cran ne se voit pas sur une position isolée : il se voit
    // en descendant, quand une ligne cesse d'être couverte par la fenêtre qui
    // devrait la contenir.
    const total = 400
    for (let scrollTop = 0; scrollTop <= total * 37; scrollTop += 37) {
      const { start, end } = windowOf({ total, scrollTop, ...frame })
      const first = Math.floor(scrollTop / 37)
      const last = Math.min(total, Math.ceil((scrollTop + 600) / 37))
      expect(start).toBeLessThanOrEqual(first)
      expect(end).toBeGreaterThanOrEqual(last)
    }
  })

  it('les cales restent exactes sur tout le parcours', () => {
    const total = 400
    for (let scrollTop = 0; scrollTop <= total * 37; scrollTop += 53) {
      const { start, end, before, after } = windowOf({ total, scrollTop, ...frame })
      expect(before).toBe(start * 37)
      expect(after).toBe((total - end) * 37)
    }
  })
})
