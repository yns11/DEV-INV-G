/**
 * La grille, rendue pour de vrai.
 *
 * Le calcul de la fenêtre est contrôlé à côté ; ce qui manquait, et qu'aucune
 * transcription ne pouvait atteindre, c'est **ce qui arrive réellement dans le
 * DOM**. Un calcul juste branché sur le mauvais tableau ne fait rien échouer :
 * la fonction rend les bons indices, et le composant en affiche d'autres.
 *
 * Les contrôles portent donc sur trois promesses de l'écran :
 *
 * * une grande grille ne met pas tout dans le DOM — c'était le défaut mesuré,
 *   cinquante mille cellules pour en montrer trente ;
 * * une petite grille n'y perd rien, seuil délibéré : la fenêtre coûte une
 *   hypothèse (des lignes de hauteur égale) qu'il est inutile de payer sur
 *   quarante lignes ;
 * * trier, filtrer et chercher continuent de porter sur **toutes** les lignes,
 *   et pas seulement sur celles qui sont rendues — c'est l'erreur naturelle
 *   quand on ajoute une fenêtre, et elle est silencieuse.
 */

import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { DataGrid, type Column } from './DataGrid'
import { ToastProvider } from './ui'

interface Article {
  item_number: string
  name: string
  qty: number
}

const columns: Column<Article>[] = [
  { key: 'item_number', label: 'Référence', sortable: true },
  { key: 'name', label: 'Désignation', sortable: true },
  { key: 'qty', label: 'Quantité', numeric: true, sortable: true },
]

function articles(count: number): Article[] {
  return Array.from({ length: count }, (_, i) => ({
    item_number: `P-${String(i + 1).padStart(5, '0')}`,
    name: `VIS TETE HEXAGONALE M6x${20 + i}`,
    qty: i + 1,
  }))
}

/**
 * La grille dans le contexte que l'application lui donne.
 *
 * `ToastProvider` n'est pas un décor : la grille signale par lui les échecs
 * d'export. Le monter ici est ce qui distingue « le composant se rend » de
 * « le composant se rend là où il est utilisé ».
 */
function grid(rows: Article[], extra: Record<string, unknown> = {}) {
  return render(
    <ToastProvider>
      <DataGrid
        columns={columns}
        rows={rows}
        getRowId={(row) => row.item_number}
        {...extra}
      />
    </ToastProvider>,
  )
}

/** Les lignes de données réellement présentes dans le document. */
function renderedRows(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>('tr[data-row]'))
}

/**
 * La n-ième ligne rendue, ou un échec explicite.
 *
 * Sous `noUncheckedIndexedAccess`, `renderedRows()[0]` est peut-être absent.
 * L'écarter ici donne un message qui nomme la cause — « la grille n'a rendu
 * aucune ligne » — au lieu d'un « undefined » à déchiffrer.
 */
function renderedRow(index = 0): HTMLElement {
  const row = renderedRows()[index]
  if (!row) throw new Error(`la grille n'a pas rendu de ligne au rang ${index}`)
  return row
}

describe('une petite grille est rendue entièrement', () => {
  it('les quarante lignes sont dans le DOM', () => {
    grid(articles(40))
    expect(renderedRows()).toHaveLength(40)
  })

  it('la dernière ligne est atteignable sans défiler', () => {
    grid(articles(40))
    expect(screen.getByText('P-00040')).toBeInTheDocument()
  })

  it('aucune cale n’est posée', () => {
    // Le seuil est délibéré : en dessous, la grille se comporte exactement
    // comme avant la fenêtre.
    grid(articles(40))
    expect(document.querySelectorAll('tr[aria-hidden="true"]')).toHaveLength(0)
  })
})

describe('une grande grille ne met que le visible dans le DOM', () => {
  it('mille lignes n’en produisent qu’une poignée', () => {
    grid(articles(1000))
    const rendered = renderedRows().length
    expect(rendered).toBeGreaterThan(0)
    expect(rendered).toBeLessThan(200)
  })

  it('la première ligne est bien la première de la liste', () => {
    grid(articles(1000))
    expect(within(renderedRow()).getByText('P-00001')).toBeInTheDocument()
  })

  it('les cales portent les lignes absentes', () => {
    grid(articles(1000))
    expect(
      document.querySelectorAll('tr[aria-hidden="true"]').length,
    ).toBeGreaterThan(0)
  })

  it('la dernière ligne n’est pas rendue tant qu’on n’y est pas', () => {
    // C'est le principe même : elle existe, elle n'est pas dans le document.
    grid(articles(1000))
    expect(screen.queryByText('P-01000')).not.toBeInTheDocument()
  })
})

describe('le tri porte sur toutes les lignes, pas sur celles qu’on voit', () => {
  it('trier à l’envers ramène la dernière ligne en tête', async () => {
    // L'erreur naturelle quand on ajoute une fenêtre est de trier le tableau
    // déjà découpé : l'écran affiche alors les cent premières lignes triées
    // entre elles, ce qui a l'air juste.
    const user = userEvent.setup()
    grid(articles(1000))
    await user.click(screen.getByRole('columnheader', { name: /Référence/ }))
    await user.click(screen.getByRole('columnheader', { name: /Référence/ }))
    expect(within(renderedRow()).getByText('P-01000')).toBeInTheDocument()
  })

  it('le tri numérique ne compare pas des chaînes', async () => {
    // « 1000 » précède « 2 » en tri texte : la colonne est numérique.
    const user = userEvent.setup()
    grid(articles(1000))
    await user.click(screen.getByRole('columnheader', { name: /Quantité/ }))
    await user.click(screen.getByRole('columnheader', { name: /Quantité/ }))
    expect(within(renderedRow()).getByText('P-01000')).toBeInTheDocument()
  })
})

describe('la recherche porte sur toutes les lignes', () => {
  it('trouve une ligne qui n’était pas rendue', async () => {
    const user = userEvent.setup()
    grid(articles(1000), { searchable: true })
    await user.type(screen.getByRole('searchbox'), 'P-00997')
    expect(screen.getByText('P-00997')).toBeInTheDocument()
  })

  it('ne garde que ce qui correspond', async () => {
    const user = userEvent.setup()
    grid(articles(1000), { searchable: true })
    await user.type(screen.getByRole('searchbox'), 'P-00997')
    expect(renderedRows()).toHaveLength(1)
  })

  it('une recherche sans résultat ne laisse aucune ligne', async () => {
    const user = userEvent.setup()
    grid(articles(1000), { searchable: true })
    await user.type(screen.getByRole('searchbox'), 'INTROUVABLE')
    expect(renderedRows()).toHaveLength(0)
  })

  it('la grille reste utilisable après une recherche vide', async () => {
    // Une liste raccourcie sans remise à zéro du défilement est le cas qui
    // fait sortir la fenêtre de la liste.
    const user = userEvent.setup()
    grid(articles(1000), { searchable: true })
    const box = screen.getByRole('searchbox')
    await user.type(box, 'INTROUVABLE')
    await user.clear(box)
    expect(renderedRows().length).toBeGreaterThan(0)
  })
})

describe('la fenêtre ne décide de rien d’autre que du DOM', () => {
  it('tout sélectionner sélectionne la liste entière, pas la fenêtre', async () => {
    // Une fenêtre qui déciderait de ce qu'on sélectionne serait un piège, pas
    // une optimisation : l'utilisateur croit tout tenir et n'en tient que
    // trente lignes.
    const user = userEvent.setup()
    let selected = new Set<string>()
    render(
      <ToastProvider>
        <DataGrid
          columns={columns}
          rows={articles(1000)}
          getRowId={(row) => row.item_number}
          selectable
          selected={selected}
          onSelectedChange={(next) => { selected = next }}
        />
      </ToastProvider>,
    )
    await user.click(screen.getByLabelText('Tout sélectionner'))
    expect(selected.size).toBe(1000)
  })

  it('le pied annonce la liste entière', () => {
    grid(articles(1000))
    // Le compte est écrit à la française : « 1 000 », séparateur insécable.
    expect(document.body.textContent).toMatch(/1\s000\s*ligne/)
  })

  it('une ligne rendue garde son rang dans la liste entière', async () => {
    // Sans le décalage, éditer la première ligne visible modifierait la
    // première ligne de la liste — quelqu'un d'autre, plus haut.
    const user = userEvent.setup()
    grid(articles(1000), { searchable: true })
    await user.type(screen.getByRole('searchbox'), 'P-00997')
    expect(within(renderedRow()).getByText('P-00997')).toBeInTheDocument()
  })
})

describe('les cales sont invisibles aux lecteurs d’écran', () => {
  /**
   * Les cales, désignées par ce qu'elles **ne sont pas** — une ligne de
   * données. Les chercher par `aria-hidden` reviendrait à ne rien contrôler :
   * une cale qui perd l'attribut sort du résultat, et la boucle passe.
   */
  function spacers(): HTMLElement[] {
    const body = document.querySelector('tbody')
    return Array.from(body?.querySelectorAll<HTMLElement>('tr') ?? []).filter(
      (row) => !row.hasAttribute('data-row'),
    )
  }

  /** Fait défiler le cadre, pour que la cale du haut existe aussi. */
  function scrollTo(offset: number) {
    const frame = document.querySelector<HTMLElement>('.table-wrap')
    if (!frame) throw new Error('cadre défilant absent')
    fireEvent.scroll(frame, { target: { scrollTop: offset } })
  }

  it('il y en a une en bas dès qu’il manque des lignes', () => {
    grid(articles(1000))
    expect(spacers()).toHaveLength(1)
  })

  it('il y en a deux une fois qu’on a défilé', () => {
    grid(articles(1000))
    scrollTo(5000)
    expect(spacers()).toHaveLength(2)
  })

  it('aucune n’est annoncée comme une ligne', () => {
    // Deux cellules vides annoncées feraient dire au lecteur d'écran qu'il y a
    // deux lignes de plus qu'il n'y en a.
    grid(articles(1000))
    scrollTo(5000)
    for (const spacer of spacers()) {
      expect(spacer).toHaveAttribute('aria-hidden', 'true')
    }
  })

  it('aucune ne porte de bordure', () => {
    // Une bordure sur une cale se lit comme une ligne vide.
    grid(articles(1000))
    scrollTo(5000)
    for (const spacer of spacers()) {
      const cell = spacer.querySelector<HTMLElement>('td')
      // La propriété longue plutôt que le raccourci : jsdom développe
      // `border: none` et rend « medium » pour le raccourci.
      expect(cell?.style.borderStyle).toBe('none')
      expect(cell?.style.padding).toBe('0px')
    }
  })

  it('aucune ne porte de texte', () => {
    grid(articles(1000))
    scrollTo(5000)
    for (const spacer of spacers()) {
      expect(spacer.textContent).toBe('')
    }
  })

  it('après avoir défilé, les lignes rendues ne sont plus les premières', () => {
    // C'est ce qui prouve que le défilement est bien branché sur la fenêtre :
    // sans cela les contrôles ci-dessus porteraient tous sur le haut de liste.
    grid(articles(1000))
    scrollTo(5000)
    expect(screen.queryByText('P-00001')).not.toBeInTheDocument()
  })
})

describe('une grille vide dit ce qui manque', () => {
  it('elle affiche son message plutôt qu’un tableau nu', () => {
    grid([], { emptyTitle: 'Aucun article' })
    expect(screen.getByText('Aucun article')).toBeInTheDocument()
  })

  it('elle ne rend pas de tableau du tout', () => {
    // Le commentaire du composant affirmait le contraire — « la grille est
    // visible avant toute donnée, avec les en-têtes attendus » — et ce n'était
    // plus vrai depuis que l'état vide a remplacé le tableau. Le premier
    // contrôle rendu sur le composant l'a constaté ; le commentaire est
    // corrigé, et c'est ce comportement-ci qui est désormais tenu.
    grid([])
    expect(screen.queryByRole('columnheader')).not.toBeInTheDocument()
  })

  it('elle propose le geste qui la remplit', () => {
    grid([], { emptyTitle: 'Aucun article', emptyAction: <button>Importer</button> })
    expect(screen.getByRole('button', { name: 'Importer' })).toBeInTheDocument()
  })

  it('une recherche sans résultat ne dit pas la même chose qu’une grille vide', () => {
    // « Aucun résultat » invite à effacer la recherche ; « Aucun article »
    // invite à en importer. Les confondre envoie l'utilisateur au mauvais
    // endroit.
    grid([], { emptyTitle: 'Aucun article' })
    expect(screen.getByText('Aucun article')).toBeInTheDocument()
    expect(screen.queryByText('Aucun résultat')).not.toBeInTheDocument()
  })
})
