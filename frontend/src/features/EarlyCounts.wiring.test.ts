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
/**
 * L'écran, et les fenêtres qu'il ouvre.
 *
 * Une modale ouverte *depuis* l'écran en fait partie : elle n'est pas un
 * second écran, elle n'a ni route ni entrée de navigation, et l'exigence est la
 * même — ce que le client expose doit être appelé de quelque part. Les nommer
 * une par une plutôt que balayer le dossier : le jour où un module cesse d'être
 * ouvert par l'écran, la liste le dit.
 */
const SCREEN = [
  read('./EarlyCounts.tsx'),
  read('./earlyCounts.journalLines.tsx'),
].join('\n')
/**
 * Les deux listes que le précomptage laisse derrière lui.
 *
 * Elles sont parties dans les Contrôles, et c'est le sens de la décision : ce
 * qui se regarde sans se décider appartient aux constats. Elles restent
 * couvertes ici parce qu'elles appellent les mêmes routes que l'écran.
 */
const OBSERVATIONS = read('./earlyCounts.observations.tsx')
const ANALYSIS = read('./Analysis.tsx')
const ROUTER = read('../../../app/inventory/api/routers/early_counts.py')

describe("l'écran est atteignable", () => {
  it('a sa route', () => {
    expect(APP).toContain('path="comptages-avances"')
    expect(APP).toContain('<EarlyCounts />')
  })

  it('est importé', () => {
    expect(APP).toContain("import EarlyCounts from './features/EarlyCounts'")
  })

  it('a son entrée de navigation, dans la phase de préparation', () => {
    // En préparation, et non en comptage : un emplacement précompté l'est des
    // jours avant le jour J, avant même que le stock ERP n'existe.
    const entry = NAVIGATION.slice(
      NAVIGATION.indexOf("to: 'comptages-avances'"),
      NAVIGATION.indexOf("to: 'stock-erp'"),
    )
    expect(entry).toContain("phase: 'PREPARATION'")
    expect(entry).toContain("label: 'Comptages avancés'")
  })

  it('vient avant les journaux de comptage', () => {
    // Un précomptage se scelle des jours avant le comptage général : l'ordre de
    // la barre latérale suit celui du travail.
    expect(NAVIGATION.indexOf("to: 'comptages-avances'")).toBeLessThan(
      NAVIGATION.indexOf("to: 'comptage',"),
    )
  })

  it('ne déclare plus aucune sous-section', () => {
    // Journaux ERP est la seule vue. « À rescanner » n'existait que par
    // l'issue « signaler », qui n'est plus proposée ; les dérives et les
    // étiquettes sont passées dans les Contrôles.
    const entry = NAVIGATION.slice(
      NAVIGATION.indexOf("to: 'comptages-avances'"),
      NAVIGATION.indexOf("to: 'stock-erp'"),
    )
    expect(entry).not.toContain('subs:')
    expect(entry).not.toContain('rescanner')
  })

  it('les dérives et les étiquettes sont des volets des Contrôles', () => {
    const entry = NAVIGATION.slice(
      NAVIGATION.indexOf("to: 'controles'"),
      NAVIGATION.indexOf("to: 'ecarts'"),
    )
    for (const sub of ['constats', 'derives', 'etiquettes']) {
      expect(entry).toContain(`id: '${sub}'`)
    }
    expect(ANALYSIS).toContain("section=\"controles\"")
    expect(ANALYSIS).toContain('<DriftsTab')
    expect(ANALYSIS).toContain('<LabelsTab')
  })
})

describe('le client vise des adresses que le serveur sert', () => {
  /** Les chemins déclarés par le routeur FastAPI, sans son préfixe. */
  const served: string[] = [
    ...ROUTER.matchAll(/@router\.(get|post|put)\(\s*\n?\s*"([^"]+)"/g),
  ].map((match) => match[2] ?? '')

  it('le routeur en déclare huit', () => {
    // Onze, puis huit. Trois routes portaient des décisions qui n'existent
    // plus : trancher une dérive, dire où est une pièce, lister ce qu'il reste
    // à rescanner. Rien ne se calcule à partir d'un comptage avancé, donc rien
    // ne s'y décide.
    expect(served).toHaveLength(8)
  })

  it('ne sert plus aucune route de décision', () => {
    expect(served).not.toContain('/drifts/resolve')
    expect(served).not.toContain('/label-alerts/decide')
    expect(served).not.toContain('/to-rescan')
  })

  it.each([
    ['erpJournals', '/journals'],
    ['erpJournalLines', '/lines'],
    ['scopeProposal', '/scope-proposal'],
    ['declareScope', '/scope'],
    ['unsealJournal', '/unseal'],
    ['drifts', '/drifts'],
    ['labelAlerts', '/label-alerts'],
    ['recountedInPlace', '/recounted-in-place'],
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

  it('le client en expose huit, une par route', () => {
    expect(methods).toHaveLength(8)
  })

  it.each(methods.map((name) => [name]))('api.%s', (name) => {
    // L'écran, ou l'un des deux volets qu'il a laissés aux Contrôles.
    expect(`${SCREEN}\n${OBSERVATIONS}`).toContain(`api.${name}`)
  })
})

describe('les décisions que porte l’écran', () => {
  it('exige un motif pour desceller', () => {
    expect(SCREEN).toContain('Desceller annule une preuve datée')
  })

  it('ne propose plus de trancher une dérive', () => {
    // La dérive est un indice, pas un écart : le précomptage a été posté dans
    // l'ERP avant la photo du jour J, qui l'a donc déjà intégré. Trancher
    // reviendrait à compter deux fois la même correction.
    for (const gone of ['KEEP_EARLY', 'RESOLUTIONS', 'resolveDrifts']) {
      expect(`${SCREEN}\n${OBSERVATIONS}`).not.toContain(gone)
    }
  })

  it('ne propose plus de dire où est une pièce', () => {
    for (const gone of ['KEEP_NEW', 'KEEP_SEALED', 'LABEL_ACTIONS', 'decideLabel']) {
      expect(`${SCREEN}\n${OBSERVATIONS}`).not.toContain(gone)
    }
  })

  it('dit des deux listes qu’elles se regardent', () => {
    // Un écran qui montre deux colonnes qui ne concordent pas sans dire quoi
    // en faire se lit comme une tâche en attente. Le bandeau est ce qui évite
    // qu'on aille chercher le bouton qui n'existe plus.
    expect(OBSERVATIONS).toContain('À regarder, pas à trancher')
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
