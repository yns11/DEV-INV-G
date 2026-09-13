/**
 * Les portefeuilles, et la bascule qui s'en sert.
 *
 * Deux fonctionnalités, un seul fichier, parce que l'une ne vaut que par
 * l'autre : attribuer des références à des personnes ne sert à rien si aucun
 * écran ne s'en sert, et une bascule « mes références » sans table d'attribution
 * ne rend jamais rien.
 *
 * Ces contrôles portent sur le **câblage**, la classe de défaut de ce dépôt : un
 * composant écrit, testé, et que rien n'atteint. `PortfoliosTab` s'est trouvé
 * exactement dans cet état pendant l'écriture de cette fonctionnalité — rendu
 * par l'aiguillage, mais jamais importé.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

const SHELL = read('./Preparation.tsx')
const TAB = read('./preparation.portfolios.tsx')
const BOOK_STOCK = read('./preparation.bookStock.tsx')
const BACKFLUSH = read('./Backflush.tsx')
const VARIANCES = read('./analysis.variances.tsx')
const TOGGLE = read('../components/MineToggle.tsx')
const NAVIGATION = read('../lib/navigation.ts')
const API = read('../lib/api.ts')

// --------------------------------------------------------------------------- //
// 1. L'onglet existe, et l'aiguillage l'atteint
// --------------------------------------------------------------------------- //

describe('l’onglet Portefeuilles est réellement câblé', () => {
  it('l’aiguillage l’importe', () => {
    /* Sans cette ligne le fichier compile chez son auteur et casse à la
       compilation — ou pire, un jour où `PortfoliosTab` existe ailleurs. */
    expect(SHELL).toContain("import { PortfoliosTab } from './preparation.portfolios'")
  })

  it('et le rend sur son onglet', () => {
    expect(SHELL).toContain("gestion === 'portfolios'")
    expect(SHELL).toContain('<PortfoliosTab')
  })

  it('l’onglet est déclaré dans les deux listes de l’aiguillage', () => {
    /* L'union de types et le tableau : déclarer l'un sans l'autre donne soit un
       onglet qu'aucun bouton n'ouvre, soit un bouton qui n'ouvre rien. */
    const union = SHELL.slice(SHELL.indexOf('type GestionTab'), SHELL.indexOf('const GESTION_TABS'))
    expect(union).toContain("| 'portfolios'")
    const list = SHELL.slice(SHELL.indexOf('const GESTION_TABS'))
    expect(list.slice(0, 220)).toContain("'portfolios'")
  })

  it('et il se place entre Affectation journaux et Paramètres', () => {
    const nav = NAVIGATION.indexOf("id: 'portfolios'")
    expect(nav).toBeGreaterThan(NAVIGATION.indexOf("id: 'journal_scope'"))
    expect(nav).toBeLessThan(NAVIGATION.indexOf("id: 'settings'"))
    expect(NAVIGATION).toContain("label: 'Portefeuilles'")
  })
})

// --------------------------------------------------------------------------- //
// 2. Le tableau se charge comme les autres grilles
// --------------------------------------------------------------------------- //

describe('le tableau d’attribution se charge par le panneau partagé', () => {
  it('fichier, collage ou saisie — le panneau les porte tous les trois', () => {
    /* Le panneau d'import est partagé : le brancher, c'est obtenir les trois
       modes d'un coup plutôt que d'en recâbler un par écran. */
    expect(TAB).toContain('<ImportPanel')
    expect(TAB).toContain('target="portfolios"')
  })

  it('et la grille se rafraîchit après le chargement', () => {
    const panel = TAB.slice(TAB.indexOf('<ImportPanel'))
    expect(panel.slice(0, 600)).toContain('invalidateQueries')
  })

  it('il suit le drapeau des gestionnaires, pas celui des seuils', () => {
    /* Même nature de décision que les affectations — qui s'occupe de quoi — et
       elle bouge aux mêmes moments, comptage et analyse compris. */
    expect(TAB).toContain('const editable = overview.permissions.managers')
    expect(TAB).not.toContain('permissions.thresholds')
  })

  it('il montre les références attribuées que le référentiel ne connaît pas', () => {
    /* Le tableau se prépare souvent avant que les articles ne soient chargés :
       une coquille y resterait invisible jusqu'au jour où le filtre ne rend
       rien. */
    expect(TAB).toContain('hors référentiel')
    expect(TAB).toContain("key: 'known'")
  })

  it('et il dit qu’un portefeuille n’interdit rien', () => {
    expect(TAB).toContain('n’est pas une habilitation')
  })
})

// --------------------------------------------------------------------------- //
// 3. La bascule, sur les trois grilles annoncées
// --------------------------------------------------------------------------- //

describe('la bascule « Mes références » est sur les trois écrans', () => {
  /* Chacun la place *sur sa grille*, et le contrôle vise cet endroit-là. « Le
     fichier contient MineToggle quelque part » laisserait passer une bascule
     restée sur l'état vide, c'est-à-dire visible exactement quand elle ne sert
     à rien. */

  it('le stock ERP, dans la barre d’outils de la grille', () => {
    const toolbar = BOOK_STOCK.slice(
      BOOK_STOCK.indexOf('toolbar={'),
      BOOK_STOCK.indexOf('exportTitle="Stock ERP"'),
    )
    expect(toolbar).toContain('<MineToggle')
    expect(BOOK_STOCK).toContain("from '../components/MineToggle'")
  })

  it('l’écart backflush, sur la carte qui porte le tableau', () => {
    const card = BACKFLUSH.slice(
      BACKFLUSH.indexOf('title="Écart par article"'),
      BACKFLUSH.indexOf('<DataGrid'),
    )
    expect(card).toContain('<MineToggle')
    expect(BACKFLUSH).toContain("from '../components/MineToggle'")
  })

  it('les écarts, à côté du filtre des seuils', () => {
    const actions = VARIANCES.slice(
      VARIANCES.indexOf('Au-delà des seuils uniquement'),
      VARIANCES.indexOf('<DataGrid'),
    )
    expect(actions).toContain('<MineToggle')
    expect(VARIANCES).toContain("from '../components/MineToggle'")
  })

  it('elle est visible : un bouton étiqueté, pas une case perdue', () => {
    expect(TOGGLE).toContain('Mes références')
    expect(TOGGLE).toContain('chip--active')
    expect(TOGGLE).toContain('aria-pressed')
  })

  it('un seul composant, pour que le libellé ne dérive pas d’un écran à l’autre', () => {
    for (const source of [BOOK_STOCK, BACKFLUSH, VARIANCES]) {
      expect(source).not.toContain('Mes références')
    }
  })
})

describe('le filtre voyage jusqu’au serveur', () => {
  it.each([
    ['le stock ERP', BOOK_STOCK, 'book-stock'],
    ['l’écart backflush', BACKFLUSH, 'backflush'],
    ['les écarts', VARIANCES, 'variances'],
  ])('%s le met dans la clé de cache et dans la requête', (_label, source, key) => {
    /* Sans le drapeau dans la clé, la vue filtrée et la vue entière partagent
       la même entrée de cache et l'une sert les lignes de l'autre — un écran
       qui montre tout sous une bascule allumée. */
    // La clé seule — jusqu'à son crochet fermant. Une fenêtre plus large
    // attraperait le `mine` du `queryFn` juste en dessous et déclarerait câblé
    // un cache qui ne l'est pas.
    const from = source.indexOf(`queryKey: ['${key}'`)
    const key_ = source.slice(from, source.indexOf(']', from))
    expect(key_).toContain('mine')
    expect(source).toContain('mine: mine || undefined')
  })

  it('le navigateur ne nomme jamais personne', () => {
    /* Le portefeuille est résolu côté serveur, à partir de l'identité que la
       plateforme transmet. Une adresse dans l'URL laisserait demander celui
       d'un autre. */
    for (const source of [BOOK_STOCK, BACKFLUSH, VARIANCES]) {
      expect(source).not.toMatch(/actor|email/i)
    }
  })

  it('les clients déclarent le paramètre sur les trois lectures', () => {
    for (const call of ['bookStock', 'variances', 'backflush']) {
      const fn = API.slice(API.indexOf(`  ${call}: (`))
      expect(fn.slice(0, 400)).toContain('mine?: boolean')
    }
  })
})

// --------------------------------------------------------------------------- //
// 4. Ce que l'écran dit quand le filtre ne rend rien
// --------------------------------------------------------------------------- //

describe('« aucun écart » et « rien à moi » ne se disent pas pareil', () => {
  it.each([
    ['le stock ERP', BOOK_STOCK],
    ['l’écart backflush', BACKFLUSH],
    ['les écarts', VARIANCES],
  ])('%s distingue les deux', (_label, source) => {
    /* Sans cela, quelqu'un conclut que son périmètre est en ordre alors qu'il
       n'a jamais eu de portefeuille. */
    expect(source).toContain('MINE_EMPTY')
    expect(source).toMatch(/mine \?/)
  })
})

// --------------------------------------------------------------------------- //
// 5. L'export emporte la bascule
// --------------------------------------------------------------------------- //

describe('le fichier ressemble à l’écran depuis lequel on l’a demandé', () => {
  it('les deux exports des écarts emportent le filtre', () => {
    const exportFn = VARIANCES.slice(VARIANCES.indexOf('const exportAs'))
    expect(exportFn.slice(0, 500)).toContain('mine: mine || undefined')
  })

  it('et l’adresse d’export le déclare', () => {
    const link = API.slice(API.indexOf('  variances: (\n    id: string,\n    format:'))
    expect(link.slice(0, 300)).toContain('mine?: boolean')
  })
})
