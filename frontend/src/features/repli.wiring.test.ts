/**
 * Le classeur de repli se télécharge, depuis les deux endroits où on le cherche.
 *
 * Un fichier que le serveur sait produire et qu'aucun bouton ne demande n'existe
 * pas : c'est la classe de défaut de ce dépôt, et elle ne se voit pas en
 * relisant le module qui le construit — celui-ci est parfait, il n'est
 * simplement appelé par personne.
 *
 * Deux points d'entrée, et ce n'est pas une redondance. Le premier est
 * l'export de la campagne, parce que la demande est là : « rajoute un 2nd
 * fichier ». Le second est l'écran de consolidation, parce que c'est en
 * regardant ce tableau qu'on se demande ce qu'on ferait sans l'application.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

const API = read('../lib/api.ts')
const SHELL = read('./CampaignShell.tsx')
const CONSOLIDATION = read('./generic.consolidation.tsx')

describe('le classeur de repli est atteignable', () => {
  it('le client sait son adresse', () => {
    expect(API).toContain('consolidationFallback')
    expect(API).toContain('/reports/consolidation-fallback.xlsx')
  })

  it('depuis l’export de la campagne', () => {
    expect(SHELL).toContain('downloads.consolidationFallback(campaign.id)')
    expect(SHELL).toContain('startDownload')
  })

  it('sans remplacer le dossier complet, qui ne fait pas le même travail', () => {
    /* L'un est une photo qu'on classe, l'autre porte les données et les
       recalcule. Le second qui chasserait le premier ferait perdre l'archive. */
    expect(SHELL).toContain('downloads.campaignWorkbook(campaign.id)')
  })

  it('et depuis l’écran de consolidation', () => {
    expect(CONSOLIDATION).toContain('downloads.consolidationFallback(campaignId)')
    expect(CONSOLIDATION).toContain('Classeur de repli')
  })

  it('sans condition de droit d’écriture : lire et exporter est ouvert à tous', () => {
    /* Le repli est un export. Le réserver aux gestionnaires laisserait sans
       recours celui qui vient justement de constater que rien ne répond. */
    const button = CONSOLIDATION.slice(
      CONSOLIDATION.indexOf('Le repli se télécharge'),
      CONSOLIDATION.indexOf('Classeur de repli'),
    )
    expect(button).not.toContain('permissions')
    expect(button).not.toContain('disabled')
  })
})
