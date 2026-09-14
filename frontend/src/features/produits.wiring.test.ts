/**
 * Le produit fabriqué, la cause posée depuis les écarts, et les emplacements
 * désactivés — le câblage des trois.
 *
 * Contrôles de **câblage**, la classe de défaut de ce dépôt : un composant
 * écrit, testé, et que rien n'atteint. Un onglet absent de la table
 * d'aiguillage, un filtre déclaré sur une colonne que la grille ne lit pas, une
 * bascule qui n'atteint jamais le serveur — aucun de ces défauts ne se voit à
 * l'exécution d'un test de rendu, puisque chaque pièce se monte parfaitement à
 * la main.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Fins de ligne normalisées : un dépôt cloné sous Windows rend des `\r\n`, et
// un repère qui contient un `\n` n'y trouve plus rien.
const read = (path: string) =>
  readFileSync(new URL(path, import.meta.url), 'utf8').replace(/\r\n/g, '\n')

const SHELL = read('./Preparation.tsx')
const TAB = read('./preparation.products.tsx')
const VARIANCES = read('./analysis.variances.tsx')
const CAUSES = read('./analysis.causes.tsx')
const GRID = read('../components/DataGrid.tsx')
const BREAKDOWN = read('../components/BreakdownModal.tsx')
const DIALOG = read('../components/CauseDialog.tsx')
const EXPLAIN = read('../components/ExplainModal.tsx')
const NAVIGATION = read('../lib/navigation.ts')
const API = read('../lib/api.ts')

// --------------------------------------------------------------------------- //
// 1. L'onglet Produits fabriqués
// --------------------------------------------------------------------------- //

describe('l’onglet Produits fabriqués est réellement câblé', () => {
  it('l’aiguillage l’importe', () => {
    expect(SHELL).toContain("import { ProductsTab } from './preparation.products'")
  })

  it('et le range parmi les onglets qui ont un contrat d’import', () => {
    /* Se tromper de table le rendrait sans son contrat, c'est-à-dire sans
       panneau de chargement — la moitié de ce que l'onglet est. */
    const table = SHELL.slice(
      SHELL.indexOf('const WITH_CONTRACT'),
      SHELL.indexOf('const WITHOUT_CONTRACT'),
    )
    expect(table).toContain('products: ProductsTab')
  })

  it('l’onglet est déclaré dans les deux listes de l’aiguillage', () => {
    const union = SHELL.slice(SHELL.indexOf('type GestionTab'), SHELL.indexOf('const GESTION_TABS'))
    expect(union).toContain("| 'products'")
    const liste = SHELL.slice(SHELL.indexOf('const GESTION_TABS'))
    expect(liste.slice(0, 220)).toContain("'products'")
  })

  it('et la navigation le nomme', () => {
    expect(NAVIGATION).toContain("{ id: 'products', label: 'Produits fabriqués' }")
  })

  it('il se charge par le panneau partagé — fichier, collage ou saisie', () => {
    expect(TAB).toContain('<ImportPanel')
    expect(TAB).toContain('target="products"')
  })

  it('il suit le drapeau des gestionnaires, pas celui des seuils', () => {
    /* C'est ce qui le rend chargeable sur une campagne dont le référentiel
       articles est déjà gelé — sa raison d'être. */
    expect(TAB).toContain('const editable = overview.permissions.managers')
    expect(TAB).not.toContain('permissions.items')
  })

  it('un chargement rafraîchit les écarts, pas seulement sa propre grille', () => {
    /* Les lignes d'écart portent le produit : sans cela, le filtre de la vue
       Écarts continuerait de proposer la liste d'avant le chargement. */
    const apres = TAB.slice(TAB.indexOf('onImported='))
    expect(apres.slice(0, 400)).toContain('invalidateQueries()')
  })

  it('et l’application ne dit nulle part que l’onglet est provisoire', () => {
    /* Il le sera — la colonne rejoindra le référentiel articles — et le dire à
       l'écran ferait hésiter à s'en servir aujourd'hui. */
    for (const mot of ['provisoire', 'temporaire', 'en attendant', 'pour le moment']) {
      expect(TAB.toLowerCase()).not.toContain(mot)
    }
  })
})

// --------------------------------------------------------------------------- //
// 2. Les deux filtres de la vue Écarts
// --------------------------------------------------------------------------- //

describe('la vue Écarts garde son affichage et gagne deux filtres', () => {
  it('la référence et la désignation restent dans une seule colonne', () => {
    /* Deux colonnes prendraient la moitié de la largeur pour la même
       information. C'est la contrainte posée, et le filtre devait s'y plier. */
    const colonne = VARIANCES.slice(
      VARIANCES.indexOf("key: 'itemNumber'"),
      VARIANCES.indexOf("key: 'name'"),
    )
    expect(colonne).toContain('{row.itemNumber}')
    expect(colonne).toContain('{row.name}')
  })

  it('la désignation se filtre sans occuper de colonne', () => {
    const colonne = VARIANCES.slice(
      VARIANCES.indexOf("key: 'name'"),
      VARIANCES.indexOf("key: 'manufacturedProduct'"),
    )
    expect(colonne).toContain('filterOnly: true')
    expect(colonne).toContain("filter: 'text'")
  })

  it('le produit fabriqué aussi, et en liste à choix unique', () => {
    /* Pas un « contient » : une trentaine de valeurs se cochent. Et un seul à
       la fois : la question est « montre-moi cet ensemble », pour y chercher
       deux écarts qui se compensent ; trois produits rendraient un mélange. */
    const colonne = VARIANCES.slice(VARIANCES.indexOf("key: 'manufacturedProduct'"))
    expect(colonne.slice(0, 700)).toContain('filterOnly: true')
    expect(colonne.slice(0, 700)).toContain("filter: 'choice'")
    expect(colonne.slice(0, 700)).toContain('choiceSingle: true')
  })

  it('la grille sait ne pas dessiner une colonne de filtre seul', () => {
    /* Déclarée sans être honorée, elle mettrait deux colonnes vides dans la
       vue Écarts — l'inverse de ce qui était demandé. */
    expect(GRID).toContain('!column.filterOnly && !hidden.has(column.key)')
  })

  it('et la barre de filtres les lit quand même', () => {
    const barre = GRID.slice(GRID.indexOf('const filterable'))
    expect(barre.slice(0, 600)).toContain('column.filterOnly || !hidden.has(column.key)')
  })

  it('le choix unique remplace au lieu d’ajouter', () => {
    const bascule = GRID.slice(GRID.indexOf('const toggle = (value: string)'))
    expect(bascule.slice(0, 400)).toContain('if (single)')
    expect(bascule.slice(0, 400)).toContain('[value]')
  })
})

// --------------------------------------------------------------------------- //
// 3. La cause, depuis les écarts, à la ligne et en lot
// --------------------------------------------------------------------------- //

describe('la cause se pose là où on regarde les chiffres', () => {
  it('un bouton de ligne, voisin de celui qui appelle le modèle', () => {
    /* L'un propose une lecture, l'autre enregistre la vôtre ; les deux se font
       au même moment, avec les mêmes chiffres sous les yeux. */
    const colonne = VARIANCES.slice(VARIANCES.indexOf("key: 'explain',"))
    expect(colonne.slice(0, 1400)).toContain('Expliquer cet écart')
    expect(colonne.slice(0, 1400)).toContain('setAssigning([row])')
  })

  it('et un lot, depuis la sélection de la grille', () => {
    expect(VARIANCES).toContain('selectable={editable}')
    expect(VARIANCES).toContain('onSelectedChange={setSelected}')
    const barre = VARIANCES.slice(VARIANCES.indexOf('toolbar={'))
    expect(barre.slice(0, 900)).toContain('setAssigning(')
  })

  it('les trois états de la barre d’outils sont couverts', () => {
    /* Le troisième est celui qui manquait, et il a coûté une question : sur une
       campagne hors phase d'analyse, la colonne de cases à cocher disparaît —
       c'est la garde qui le veut — et **rien** ne le disait. « Je ne vois pas
       comment sélectionner un lot » était la seule conclusion possible.

       Les trois : la raison quand c'est fermé, l'invitation quand c'est ouvert
       et que rien n'est coché, le bouton quand quelque chose l'est. */
    const barre = VARIANCES.slice(VARIANCES.indexOf('toolbar={'))
    expect(barre.slice(0, 1400)).toContain('!editable ? (')
    expect(barre.slice(0, 1400)).toContain('CAUSE_CLOSED')
    expect(barre.slice(0, 1400)).toContain('Cochez des lignes')
  })

  it('et le bouton de ligne suit la même garde que le lot', () => {
    /* Sans elle, il invitait à remplir une fenêtre dont l'enregistrement était
       refusé par le serveur : une porte peinte sur un mur. */
    const colonne = VARIANCES.slice(VARIANCES.indexOf("key: 'explain',"))
    const bouton = colonne.slice(colonne.indexOf('Icons.clipboard'))
    expect(bouton.slice(0, 500)).toContain('disabled={!editable}')
  })

  it('la raison est écrite une fois, et sert aux deux endroits', () => {
    /* Deux formulations divergeraient, et l'une des deux finirait par décrire
       une règle que le serveur n'applique plus. */
    expect(VARIANCES).toContain('const CAUSE_CLOSED')
    const occurrences = VARIANCES.match(/CAUSE_CLOSED/g) ?? []
    expect(occurrences.length).toBeGreaterThanOrEqual(3)
  })

  it('les deux passent par la même fenêtre', () => {
    for (const source of [VARIANCES, CAUSES]) {
      expect(source).toContain("from '../components/CauseDialog'")
      expect(source).toContain('<CauseDialog')
    }
  })

  it('elle est pré-remplie sur une ligne, à blanc sur un lot', () => {
    /* Ce qui décide du sort d'un commentaire vide : là, le champ montre ce
       qu'il va remplacer ; ici, il ne montre rien. */
    for (const source of [VARIANCES, CAUSES]) {
      expect(source).toContain('assigning.length === 1 ? assigning[0]!.causeCode : null')
      expect(source).toContain("assigning.length === 1 ? assigning[0]!.comment : ''")
    }
  })

  it('et elle dit ce qu’un commentaire vide fera sur un lot', () => {
    expect(DIALOG).toContain('ne touche pas aux commentaires déjà écrits')
  })

  it('le lot part en un seul appel', () => {
    /* Un appel par ligne donnerait autant de transactions et d'entrées d'audit,
       et un échec au milieu laisserait la moitié du lot posée. */
    for (const source of [VARIANCES, CAUSES]) {
      expect(source).toContain('api.saveVarianceCauses(')
    }
    const client = API.slice(API.indexOf('  saveVarianceCauses:'))
    expect(client.slice(0, 400)).toContain('itemNumbers: string[]')
  })
})

describe('la vue Causes gagne les filtres et le lot', () => {
  it('elle rend une grille, et non plus un tableau nu', () => {
    /* C'est la grille qui apporte filtres de colonne, recherche, export et
       sélection — quatre choses que le tableau à la main n'avait pas. */
    expect(CAUSES).toContain('<DataGrid')
    expect(CAUSES).not.toContain('<table className="data">')
  })

  it('avec la sélection branchée sur l’affectation en lot', () => {
    expect(CAUSES).toContain('selectable={editable}')
    const barre = CAUSES.slice(CAUSES.indexOf('toolbar={'))
    expect(barre.slice(0, 700)).toContain('setAssigning(')
  })

  it('et garde ses trois files de travail, qu’aucun filtre de colonne ne dit', () => {
    /* L'absence d'une valeur, la comparaison de deux colonnes entre elles, le
       signe d'un nombre. */
    expect(CAUSES).toContain('<CauseFilterBar')
    expect(CAUSES).toContain('matchesCause(row, filters)')
  })
})

// --------------------------------------------------------------------------- //
// 4. Les emplacements désactivés
// --------------------------------------------------------------------------- //

describe('la décomposition masque les emplacements désactivés', () => {
  it('la fenêtre ne les demande pas par défaut', () => {
    expect(BREAKDOWN).toContain('const [withDisabled, setWithDisabled] = useState(false)')
  })

  it('le filtre est côté serveur, pour que le total suive', () => {
    /* Masquer à l'affichage aurait laissé un total qui contredit ce qu'on lit
       en dessous — exactement le défaut que la fenêtre existe pour éviter. */
    expect(BREAKDOWN).toContain('includeDisabled: withDisabled || undefined')
    const cle = BREAKDOWN.slice(BREAKDOWN.indexOf('queryKey: ['))
    expect(cle.slice(0, cle.indexOf(']'))).toContain('withDisabled')
  })

  it('et la bascule n’apparaît que s’il y a quelque chose derrière', () => {
    expect(BREAKDOWN).toContain('data.hiddenDisabled > 0 || withDisabled')
  })
})

// --------------------------------------------------------------------------- //
// 5. Les réponses du modèle
// --------------------------------------------------------------------------- //

describe('les réponses IA sont mises en forme', () => {
  it('l’explication d’un écart passe par le rendu partagé', () => {
    expect(EXPLAIN).toContain('<Markdown text={data.explanation} />')
    expect(EXPLAIN).not.toContain("whiteSpace: 'pre-wrap'")
  })

  it('la justification d’une proposition aussi', () => {
    expect(CAUSES).toContain('<Markdown text={row.aiRationale} />')
  })

  it('et ce rendu peint des nœuds, jamais du HTML', () => {
    /* Le dossier envoyé au modèle porte des désignations et des commentaires
       venus de fichiers que l'application n'écrit pas : une réponse qui en
       reprendrait un morceau ne doit pas pouvoir l'exécuter. */
    const rendu = read('../lib/markdown.tsx')
    expect(rendu).not.toContain('dangerouslySetInnerHTML')
  })
})
