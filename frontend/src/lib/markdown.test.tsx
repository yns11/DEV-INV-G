/**
 * Ce que l'écran fait d'une réponse de modèle.
 *
 * Le défaut : l'assistant rendait le gras et les puces, rien d'autre. Un
 * tableau — ce qu'un modèle produit dès qu'on lui demande de comparer trois
 * articles sur deux mesures — arrivait à l'écran sous la forme où il l'avait
 * écrit : des barres verticales et des tirets, alignés sur rien, dans une bulle
 * de discussion. Et la Synthèse IA, à qui le serveur demande pourtant une note
 * structurée en sections, l'affichait entièrement brute : « ## Message clé » s'y
 * lisait tel quel.
 *
 * Ces contrôles portent sur le rendu, et sur une propriété qui compte autant :
 * **rien n'est injecté en HTML**. Le dossier envoyé au modèle porte des
 * désignations d'articles et des pièces jointes, c'est-à-dire du texte que
 * l'application n'écrit pas.
 */

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Markdown } from './markdown'

const TABLE = [
  '| Article | Écart qté | Écart € |',
  '|---|---:|---:|',
  '| P-00324093 | -12 | -150,00 |',
  '| MASS-00040707 | 3 | 37,50 |',
].join('\n')

function show(text: string) {
  return render(<Markdown text={text} />)
}

describe('un tableau', () => {
  it('devient un vrai tableau', () => {
    show(TABLE)
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('columnheader').map((c) => c.textContent)).toEqual(
      ['Article', 'Écart qté', 'Écart €'],
    )
    expect(within(table).getAllByRole('row')).toHaveLength(3)
  })

  it('garde les cellules à leur place', () => {
    show(TABLE)
    const [, first] = screen.getAllByRole('row')
    expect(within(first!).getAllByRole('cell').map((c) => c.textContent)).toEqual(
      ['P-00324093', '-12', '-150,00'],
    )
  })

  it('aligne à droite les colonnes que le modèle déclare telles', () => {
    show(TABLE)
    const [, first] = screen.getAllByRole('row')
    const cells = within(first!).getAllByRole('cell')
    expect(cells[1]).toHaveStyle({ textAlign: 'right' })
    expect(cells[0]).toHaveStyle({ textAlign: 'left' })
  })

  it('aligne aussi une colonne de chiffres non déclarée', () => {
    // Un modèle oublie souvent `---:`. Une colonne d'écarts alignée à gauche ne
    // se compare pas d'une ligne à l'autre, et c'est le seul usage du tableau.
    show(['| Zone | Lignes |', '|---|---|', '| B06 | 412 |', '| B07 | 38 |'].join('\n'))
    const [, first] = screen.getAllByRole('row')
    const cells = within(first!).getAllByRole('cell')
    expect(cells[1]).toHaveStyle({ textAlign: 'right' })
    expect(cells[0]).not.toHaveStyle({ textAlign: 'right' })
  })

  it('laisse l’alignement déclaré l’emporter sur la déduction', () => {
    // Le repli sur « ça ressemble à des chiffres » ne doit pas recouvrir un
    // choix explicite : ici le modèle demande l'inverse de ce qu'on déduirait.
    show(
      ['| Code | Quantité |', '|---:|:---|', '| B06 | 412 |'].join('\n'),
    )
    const [, first] = screen.getAllByRole('row')
    const cells = within(first!).getAllByRole('cell')
    expect(cells[0]).toHaveStyle({ textAlign: 'right' })
    expect(cells[1]).toHaveStyle({ textAlign: 'left' })
  })

  it('laisse le texte à gauche', () => {
    show(['| Zone | Responsable |', '|---|---|', '| B06 | Dupont |'].join('\n'))
    const [, first] = screen.getAllByRole('row')
    expect(within(first!).getAllByRole('cell')[1]).not.toHaveStyle({ textAlign: 'right' })
  })

  it('se détache du paragraphe qui l’introduit', () => {
    // Les modèles collent volontiers le tableau à sa phrase d'introduction ;
    // sans séparation, l'ensemble se rendait en un seul paragraphe.
    show(`Voici les trois plus gros écarts :\n${TABLE}`)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText(/Voici les trois plus gros/)).toBeInTheDocument()
  })

  it('accepte une ligne sans barres de bord', () => {
    show(['Article | Qté', '---|---:', 'P-1 | 4'].join('\n'))
    expect(screen.getAllByRole('columnheader').map((c) => c.textContent)).toEqual(
      ['Article', 'Qté'],
    )
  })

  it('laisse une barre échappée dans sa cellule', () => {
    // « Écart \\| brut » est un libellé, pas deux colonnes.
    show(['| Mesure | Valeur |', '|---|---|', '| Écart \\| brut | 12 |'].join('\n'))
    const [, first] = screen.getAllByRole('row')
    const cells = within(first!).getAllByRole('cell')
    expect(cells).toHaveLength(2)
    expect(cells[0]?.textContent).toBe('Écart | brut')
  })

  it('n’en fabrique pas un à partir d’une phrase à barres', () => {
    // Sans ligne de séparation, ce n'est pas un tableau — c'est une phrase.
    show('Le choix est simple | rapide, ou complet.')
    expect(screen.queryByRole('table')).toBeNull()
  })

  it('ni à partir de deux lignes à barres sans séparation', () => {
    // C'est le cas qui compte : deux lignes suffisent en nombre, et seule la
    // ligne de tirets distingue un tableau d'une énumération à barres.
    show('Zone B06 | responsable Dupont\nZone B07 | responsable Martin')
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText(/responsable Dupont/)).toBeInTheDocument()
  })
})

describe('le reste du format', () => {
  it('rend les titres comme des titres', () => {
    show('## Message clé')
    expect(screen.getByRole('heading', { name: 'Message clé' })).toBeInTheDocument()
    expect(screen.queryByText(/##/)).toBeNull()
  })

  it('rend une liste numérotée', () => {
    show('1. Recompter B06\n2. Vérifier les nomenclatures')
    const items = screen.getAllByRole('listitem')
    expect(items.map((i) => i.textContent)).toEqual([
      'Recompter B06',
      'Vérifier les nomenclatures',
    ])
  })

  it('rend les puces', () => {
    show('- Premier point\n- Second point')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('rend le gras, l’italique et le code', () => {
    const { container } = show('Un **écart** *net* de `12` unités.')
    expect(container.querySelector('strong')?.textContent).toBe('écart')
    expect(container.querySelector('em')?.textContent).toBe('net')
    expect(container.querySelector('code')?.textContent).toBe('12')
  })

  it('n’ajoute pas de ligne vide en fin de paragraphe', () => {
    // Le rendu précédent posait un `<br/>` après *chaque* ligne, dont la
    // dernière : chaque réponse se terminait par un blanc.
    const { container } = show('Une seule ligne.')
    expect(container.querySelectorAll('br')).toHaveLength(0)
  })

  it('garde la coupure entre deux lignes d’un même paragraphe', () => {
    const { container } = show('Première ligne.\nSeconde ligne.')
    expect(container.querySelectorAll('br')).toHaveLength(1)
  })
})

describe('rien n’est injecté', () => {
  it('affiche le HTML au lieu de l’exécuter', () => {
    // Une désignation d'article venue d'un fichier peut porter ceci, et le
    // modèle peut la recopier. Le pire qu'elle doit obtenir est de s'afficher.
    const { container } = show('<img src=x onerror="alert(1)">')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x/)).toBeInTheDocument()
  })

  it('y compris dans une cellule de tableau', () => {
    const { container } = show(
      ['| Article |', '|---|', '| <script>alert(1)</script> |'].join('\n'),
    )
    expect(container.querySelector('script')).toBeNull()
    expect(screen.getByRole('cell').textContent).toContain('<script>')
  })
})
