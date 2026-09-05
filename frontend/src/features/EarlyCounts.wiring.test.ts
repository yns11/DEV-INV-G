/**
 * L'écran des comptages avancés est **branché**.
 *
 * C'est la classe de défaut que ce dépôt rencontre le plus souvent : un écran
 * écrit, testé, et que rien n'atteint — pas de route, pas d'entrée de
 * navigation, ou un appel qui vise une adresse que le serveur ne sert pas.
 * Aucun test de rendu ne l'attrape, puisque le composant se rend très bien
 * quand on le monte à la main.
 */

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')

const APP = read('../App.tsx')
const NAVIGATION = read('../lib/navigation.ts')
const API = read('../lib/api.ts')
const SCREEN = read('./EarlyCounts.tsx')
const ROUTER = read('../../../app/inventory/api/routers/early_counts.py')

describe("l'écran est atteignable", () => {
  it('a sa route', () => {
    expect(APP).toContain('path="comptages-avances"')
    expect(APP).toContain('<EarlyCounts />')
  })

  it('est importé', () => {
    expect(APP).toContain("import EarlyCounts from './features/EarlyCounts'")
  })

  it('a son entrée de navigation, dans la phase de comptage', () => {
    const entry = NAVIGATION.slice(
      NAVIGATION.indexOf("to: 'comptages-avances'"),
      NAVIGATION.indexOf("to: 'comptage',"),
    )
    expect(entry).toContain("phase: 'COUNTING'")
    expect(entry).toContain("label: 'Comptages avancés'")
  })

  it('vient avant les journaux de comptage', () => {
    // Un lot avancé s'ouvre et se scelle des jours avant le comptage général :
    // l'ordre de la barre latérale suit celui du travail.
    expect(NAVIGATION.indexOf("to: 'comptages-avances'")).toBeLessThan(
      NAVIGATION.indexOf("to: 'comptage',"),
    )
  })

  it('déclare ses quatre sous-sections', () => {
    for (const sub of ['journaux', 'derives', 'etiquettes', 'rescanner']) {
      expect(NAVIGATION).toContain(`id: '${sub}'`)
      expect(SCREEN).toContain(`'${sub}'`)
    }
  })
})

describe('le client vise des adresses que le serveur sert', () => {
  /** Les chemins déclarés par le routeur FastAPI, sans son préfixe. */
  const served: string[] = [
    ...ROUTER.matchAll(/@router\.(get|post|put)\(\s*\n?\s*"([^"]+)"/g),
  ].map((match) => match[2] ?? '')

  it('le routeur en déclare dix', () => {
    // Onze avant : le lot avancé en portait cinq — ouvrir, lister, clore,
    // sceller, desceller. Le journal ERP *est* le précomptage, et déclarer son
    // périmètre scelle ; il reste le descellement, plus les deux routes que le
    // traitement des étiquettes a demandées.
    expect(served).toHaveLength(10)
  })

  it.each([
    ['erpJournals', '/journals'],
    ['scopeProposal', '/scope-proposal'],
    ['declareScope', '/scope'],
    ['unsealJournal', '/unseal'],
    ['drifts', '/drifts'],
    ['resolveDrifts', '/drifts/resolve'],
    ['labelAlerts', '/label-alerts'],
    ['decideLabel', '/label-alerts/decide'],
    ['toRescan', '/to-rescan'],
  ])('%s appelle une route servie', (method, fragment) => {
    expect(API).toContain(`${method}:`)
    const call = API.slice(API.indexOf(`${method}:`), API.indexOf(`${method}:`) + 500)
    expect(call).toContain('/early-counts')
    expect(served.some((path) => path.includes(fragment))).toBe(true)
  })
})

describe("l'écran appelle réellement le client", () => {
  /**
   * Les méthodes lues dans le client, jamais recopiées ici.
   *
   * Cette liste a d'abord été écrite à la main, et il y en avait dix pour onze
   * routes : `createEarlyBatch` manquait — la seule méthode qu'aucun composant
   * n'appelait était aussi la seule que personne n'avait pensé à inscrire.
   * L'écran listait les lots, savait les clore, les sceller, les desceller, et
   * n'avait aucun moyen d'en ouvrir un ; son état vide demandait pourtant
   * « ouvrez un lot dessus ». Une liste recopiée ne tient que ce qu'on a pensé
   * à y mettre, c'est-à-dire jamais le cas qu'on a oublié.
   */
  const methods = [...API.matchAll(/^ {2}(\w+): \(/gm)]
    .map((match) => match[1] ?? '')
    .filter((name) => {
      const start = API.indexOf(`\n  ${name}: (`)
      return API.slice(start, start + 600).includes('/early-counts')
    })

  it('le client en expose dix, une par route', () => {
    expect(methods).toHaveLength(10)
  })

  it.each(methods.map((name) => [name]))('api.%s', (name) => {
    expect(SCREEN).toContain(`api.${name}`)
  })
})

describe('les décisions que porte l’écran', () => {
  /** Les deux tables d'issues partagent `RECOUNT` : on lit chacune chez elle. */
  const block = (name: string) => {
    const start = SCREEN.indexOf(`const ${name}`)
    return SCREEN.slice(start, SCREEN.indexOf('\n]', start))
  }

  it('nomme les deux issues d’une dérive, et pas une troisième', () => {
    const resolutions = [...block('RESOLUTIONS').matchAll(/id: '(\w+)'/g)]
    expect(resolutions.map((m) => m[1])).toEqual(['KEEP_EARLY', 'RECOUNT'])
  })

  it('dit que conserver le comptage avancé demande une cause', () => {
    expect(SCREEN).toContain('cause est obligatoire')
  })

  it('exige un motif pour desceller', () => {
    expect(SCREEN).toContain('Desceller annule une preuve datée')
  })

  it('nomme les trois issues d’une étiquette, et pas une quatrième', () => {
    // Où est la pièce : au nouvel emplacement, à l'ancien, ou on ne tranche
    // pas. Aucun calcul ne répond ; seul quelqu'un qui va voir le peut.
    const actions = [...block('LABEL_ACTIONS').matchAll(/id: '(\w+)'/g)]
    expect(actions.map((m) => m[1])).toEqual([
      'KEEP_NEW', 'KEEP_SEALED', 'RECOUNT',
    ])
  })

  it('n’a plus de lot : le journal ERP est le précomptage', () => {
    expect(SCREEN).not.toContain('earlyBatches')
    expect(SCREEN).not.toContain('createEarlyBatch')
  })

  it('affiche l’heure du dernier import', () => {
    // Le notebook est rejoué toutes les quelques minutes le jour J : de quand
    // datent les chiffres qu'on regarde n'est pas un détail d'affichage.
    //
    // Le nom du champ est celui du modèle. Cette ligne a d'abord épinglé
    // `journalsImportedAt`, qui n'existe nulle part : la campagne est le seul
    // objet de l'aperçu qui voyage tel quel, en `snake_case`. Le contrôle
    // passait, la bannière affichait « aucun import » pour toujours — un
    // contrôle par chaînes ne vaut que ce que vaut la chaîne qu'il épingle,
    // d'où le contrôle de rendu à côté, qui monte l'écran pour de bon.
    expect(SCREEN).toContain('journals_imported_at')
    expect(SCREEN).toContain('relativeTime')
  })

  it("n'encode pas une clé d'emplacement avec un séparateur choisi", () => {
    // Un identifiant d'emplacement peut contenir n'importe quel caractère.
    // Concaténer revient à parier qu'un séparateur n'y figurera jamais, et le
    // pari se perd en silence — sur une ligne qui se coche à la place d'une
    // autre. Un octet invisible est pire encore : il ne se voit même pas en
    // relisant le fichier.
    expect(SCREEN).not.toContain(String.fromCharCode(0))
    expect(SCREEN).toContain('JSON.stringify([warehouseId, locationId])')
  })
})
