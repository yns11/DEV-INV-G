/**
 * Quatre écrans qui montraient moins qu'ils ne pouvaient.
 *
 * Aucun n'était faux : les chiffres affichés étaient justes. Ce qui manquait
 * était de quoi s'en servir — un filtre, un pied de grille, un choix de
 * colonnes, ou simplement la donnée qu'on vient chercher.
 *
 * Ces contrôles portent sur le **câblage**, pas sur le rendu : c'est la classe
 * de défaut de ce dépôt, un composant écrit et testé que rien n'atteint. Un
 * test de rendu ne l'attrape pas, puisque le composant se monte très bien à la
 * main.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

const CAMPAIGNS = read('./Campaigns.tsx')
const COUNTING = read('./Counting.tsx')
const EARLY_LINES = read('./earlyCounts.journalLines.tsx')
const LAYOUT = read('./generic.layout.tsx')
const SHEET = read('./generic.sheet.tsx')
const CONSOLIDATION = read('./generic.consolidation.tsx')
const IMPORT_PANEL = read('../components/ImportPanel.tsx')
const PICKER = read('../components/CampaignSourcePicker.tsx')
const BASE_CSS = read('../design/base.css')

// --------------------------------------------------------------------------- //
// 1. La date de création d'une campagne
// --------------------------------------------------------------------------- //

describe('la date de création se voit, se trie et se filtre', () => {
  it('porte sa colonne dans la liste', () => {
    expect(CAMPAIGNS).toContain("key: 'created_at'")
    expect(CAMPAIGNS).toContain("label: 'Créée le'")
  })

  it('se trie, la question étant « depuis quand ce dossier traîne »', () => {
    const column = CAMPAIGNS.slice(CAMPAIGNS.indexOf("key: 'created_at'"))
    expect(column.slice(0, 400)).toContain('sortable: true')
  })

  it('a ses deux bornes dans la barre de filtres', () => {
    expect(CAMPAIGNS).toContain('createdFrom')
    expect(CAMPAIGNS).toContain('createdTo')
    expect(CAMPAIGNS).toContain('Créée à partir du')
    expect(CAMPAIGNS).toContain('Créée jusqu’au')
  })

  it('se compare sur la journée, pas sur l’horodatage', () => {
    /* « Créées le 12 » exclurait sinon tout ce qui l'a été après minuit. */
    expect(CAMPAIGNS).toContain('const created = day(campaign.created_at)')
  })

  it('ne dérange pas la date de comptage, qui répond à une autre question', () => {
    expect(CAMPAIGNS).toContain('campaign.count_date < filters.from')
    expect(CAMPAIGNS).toContain("key: 'count_date'")
  })

  it('la barre de filtres reste sur une ligne', () => {
    /* Sept champs au lieu de cinq : sans piste plus serrée, la rangée passe à
       la ligne et sépare visuellement des critères qui se lisent ensemble. */
    expect(CAMPAIGNS).toContain('filters-row filters-row--dense')
    expect(BASE_CSS).toContain('.filters-row--dense')
    const rule = BASE_CSS.slice(
      BASE_CSS.indexOf('.filters-row--dense'),
      BASE_CSS.indexOf('.filters-row--dense') + 260,
    )
    expect(rule).toMatch(/minmax\(1[0-9]{2}px/)
  })
})

// --------------------------------------------------------------------------- //
// 2. Vider une section
// --------------------------------------------------------------------------- //

describe('une section se vide d’un geste', () => {
  it('depuis la conception de la feuille', () => {
    expect(LAYOUT).toContain('const clearSection')
    expect(LAYOUT).toContain('Vider la section')
  })

  it('depuis la saisie', () => {
    expect(SHEET).toContain('Vider la section')
    expect(SHEET).toContain('replaceSection(section, [])')
  })

  it('sans rien écrire avant « Enregistrer »', () => {
    /* Le geste se défait comme les autres : c'est ce qui permet de l'offrir
       sans confirmation, sur quatre-vingts lignes. */
    const fn = LAYOUT.slice(LAYOUT.indexOf('const clearSection'))
    expect(fn.slice(0, 220)).toContain('edit(')
    expect(fn.slice(0, 220)).not.toContain('mutate')
  })

  it('n’est offert que sur une section qui porte des lignes', () => {
    expect(LAYOUT).toContain('group.length > 0')
    expect(SHEET).toContain('(bySection[section] ?? []).length > 0')
  })
})

// --------------------------------------------------------------------------- //
// 3. Les deux grilles alignées sur les autres
// --------------------------------------------------------------------------- //

describe('un journal ERP s’ouvre sur ses lignes', () => {
  it('la fenêtre est atteinte depuis la grille des journaux', () => {
    const screen = read('./EarlyCounts.tsx')
    expect(screen).toContain('ErpJournalLinesModal')
    expect(screen).toContain('setLines(row)')
  })

  it('elle se manœuvre comme les autres grilles', () => {
    expect(EARLY_LINES).toContain('<DataGrid<ErpJournalLine>')
    expect(EARLY_LINES).toContain('exportTitle=')
    expect(EARLY_LINES).toContain("filter: 'text'")
    expect(EARLY_LINES).toContain("filter: 'choice'")
    expect(EARLY_LINES).toContain("filter: 'range'")
  })

  it('porte toutes les colonnes de la ligne ERP', () => {
    for (const key of [
      'site_id', 'warehouse_id', 'location_id',
      'label_id', 'serial_number', 'item_number', 'qtyOnHand',
      'qtyCounted', 'varianceQty', 'unit', 'inventory_status_id',
    ]) {
      expect(EARLY_LINES).toContain(`key: '${key}'`)
    }
  })

  it('dit ce qui compte et ce qui ne compte pas', () => {
    /* Un journal porte des lignes sur des emplacements qu'il ne couvre pas :
       elles sont la trace d'un déplacement, et elles ne comptent pas. Sans
       cette colonne, la grille laisserait additionner des quantités que le
       journal ne retient pas. */
    expect(EARLY_LINES).toContain("key: 'inScope'")
    expect(EARLY_LINES).toContain('Hors périmètre')
  })

  it('n’affiche aucun numéro de ligne ERP', () => {
    /* L'ERP n'en donne pas sur les journaux comptés par étiquette, et la chaîne
       d'extraction en inventait pour départager des lignes qu'il numérote
       pareil — « 1, -1, -2, … -79 ». Les afficher invitait à s'y fier ; ils ne
       désignent rien dans l'ERP. */
    // La déclaration de colonne, pas la prose : le commentaire qui explique
    // pourquoi elle n'est plus là cite forcément son nom.
    expect(EARLY_LINES).not.toContain("key: 'erp_line_number'")
    expect(EARLY_LINES).not.toContain("label: 'N° ligne'")
  })
})

describe('un journal de comptage s’ouvre comme une grille', () => {
  it('sur un DataGrid, et non sur un tableau nu', () => {
    expect(COUNTING).toContain('<DataGrid<JournalLine>')
    expect(COUNTING).toContain('lineColumns')
  })

  it('avec ses filtres et son export', () => {
    const columns = COUNTING.slice(
      COUNTING.indexOf('const lineColumns'),
      COUNTING.indexOf('return (\n    <Modal'),
    )
    expect(columns).toContain("filter: 'text'")
    expect(columns).toContain("filter: 'range'")
    expect(columns).toContain("filter: 'choice'")
    expect(COUNTING).toContain('exportTitle={`Journal ')
  })

  it('et la correction se saisit toujours dans la ligne', () => {
    /* La valeur importée reste à côté : recharger l'export dix fois dans la
       journée ne doit jamais effacer ce qu'une main a écrit. */
    expect(COUNTING).toContain("key: 'qty_manual'")
    expect(COUNTING).toContain('saveLine.mutate')
    expect(COUNTING).toContain("key: 'qty_imported'")
  })
})

// --------------------------------------------------------------------------- //
// 4. Reprendre une grille d'une autre campagne
// --------------------------------------------------------------------------- //

describe('l’import depuis une autre campagne', () => {
  it('est offert par le panneau d’import, donc partout', () => {
    /* Le panneau est partagé : un mode ajouté ici arrive sur chaque grille qui
       l'utilise, plutôt que d'être recâblé écran par écran. */
    expect(IMPORT_PANEL).toContain('Reprendre d’une campagne')
    expect(IMPORT_PANEL).toContain('CampaignSourcePicker')
  })

  it('passe par l’essai à blanc, comme un fichier', () => {
    expect(IMPORT_PANEL).toContain('void validate({ campaign: source })')
    expect(IMPORT_PANEL).toContain('api.importFromCampaign')
  })

  it('n’est proposé que sur les grilles qu’une campagne sait redonner', () => {
    expect(IMPORT_PANEL).toContain('const CAMPAIGN_TARGETS')
    expect(IMPORT_PANEL).toContain('hasCampaignSource &&')
  })

  it('montre ce que chaque campagne porte, ce qui est l’information qui décide', () => {
    expect(PICKER).toContain('source.rows.toLocaleString')
    expect(PICKER).toContain('ligne(s)')
  })

  it('grise celles qui ne portent rien plutôt que de les cacher', () => {
    /* « Elle n'a rien » est une réponse ; la masquer ferait chercher une
       campagne qu'on croit avoir oubliée. */
    expect(PICKER).toContain('const empty = source.rows === 0')
    expect(PICKER).toContain('disabled={empty}')
  })

  it('met en tête celles qui portent quelque chose', () => {
    expect(PICKER).toContain('(a.rows > 0) !== (b.rows > 0)')
  })

  it('annonce que rien n’est écrit tout de suite', () => {
    expect(PICKER).toContain('Rien n’est écrit tout de suite')
  })
})

// --------------------------------------------------------------------------- //
// 5. Le journal consolidé écarte ce qu'il ne sait pas valoriser
// --------------------------------------------------------------------------- //

describe('les articles hors référentiel ont leur pastille', () => {
  it('elle existe', () => {
    expect(CONSOLIDATION).toContain("code: 'UNKNOWN_ITEM'")
    expect(CONSOLIDATION).toContain('Hors référentiel')
  })

  it('et elle dit pourquoi la quantité est écartée', () => {
    const pill = CONSOLIDATION.slice(CONSOLIDATION.indexOf("code: 'UNKNOWN_ITEM'"))
    expect(pill.slice(0, 500)).toContain('écartés du journal')
  })
})
