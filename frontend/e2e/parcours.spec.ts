/**
 * Le parcours d'un inventaire, du dossier vide à la clôture.
 *
 * Pourquoi ce banc, alors que tout est déjà contrôlé
 * --------------------------------------------------
 * Les contrôles Python tiennent chaque règle ; ceux du navigateur tiennent
 * chaque écran. Aucun des deux ne tient la **chaîne** : que la phase suivante
 * s'ouvre là où la précédente s'est fermée, qu'un référentiel chargé ici se
 * retrouve là-bas, qu'un arbitrage tranché débloque la fermeture d'une zone.
 * C'est là que vivent les défauts qu'on découvre le jour J — quand deux mille
 * lignes ont déjà été comptées à la main.
 *
 * L'ordre est celui de l'usine, et il n'est pas négociable
 * -------------------------------------------------------
 *   1. créer la campagne ;
 *   2. charger le référentiel articles, par collage ;
 *   3. créer les feuilles de comptage — en préparation, jamais après ;
 *   4. passer en comptage, ce qui gèle le référentiel ;
 *   5. charger le stock ERP et le geler — la phase de comptage l'ouvre, et
 *      c'est bien après le passage, pas avant ;
 *   6. saisir deux comptages qui se contredisent sur une référence ;
 *   7. trancher l'arbitrage ;
 *   8. passer en analyse ;
 *   9. lire l'état des lieux avant clôture.
 *
 * Chaque étape suppose la précédente : le banc est déclaré `serial`, et une
 * étape qui tombe arrête la suite au lieu de produire dix échecs dont un seul
 * a une cause.
 *
 * Une campagne par exécution
 * --------------------------
 * Le code porte un horodatage : deux exécutions ne se marchent pas dessus, et
 * un échec laisse son dossier en place pour qu'on puisse l'ouvrir. Une base
 * qui accumule est le prix d'un banc qu'on relance sans rien nettoyer d'abord
 * — et c'est une base de contrôle, pas la production.
 */

import { expect, test, type Page } from '@playwright/test'

/** Le code de la campagne du jour : unique par exécution, lisible à l'écran. */
const CODE = `E2E-${Date.now().toString(36).toUpperCase()}`

/** La zone comptée deux fois, où naîtra la contradiction à arbitrer. */
const ZONE = 'ZONE-PARCOURS'

/** Quinze articles, assez pour que les écrans aient quelque chose à montrer. */
const ARTICLES = [
  'Numéro d’article\tNom du produit\tType produit\tUnité\tPrix standard (€)',
  ...Array.from({ length: 15 }, (_, i) => {
    const n = String(i + 1).padStart(5, '0')
    return `P-${n}\tVIS TETE HEXAGONALE M6x${20 + i}\tCOMPONENT\tPCE\t${2 + i}`
  }),
].join('\n')

/** Les feuilles de la zone : cinq références comptées au bord de ligne. */
const SHEETS = [
  'Feuille\tArticle\tSection\tUnité',
  ...Array.from({ length: 5 }, (_, i) => {
    const n = String(i + 1).padStart(5, '0')
    return `${ZONE}\tP-${n}\tBDL\tPCE`
  }),
].join('\n')

/** Le stock ERP : quinze références réparties sur quatre emplacements. */
const STOCK = [
  'Numéro d’article\tEntrepôt\tEmplacement\tStock physique\tUnité\tCoût unitaire',
  ...Array.from({ length: 15 }, (_, i) => {
    const n = String(i + 1).padStart(5, '0')
    const pal = String((i % 4) + 1).padStart(2, '0')
    return `P-${n}\tB06\tPAL ${pal}\t${101 + i}\tPCE\t${2 + i}`
  }),
].join('\n')

/** L'identifiant de la campagne, retenu d'une étape à l'autre. */
let campaignId = ''

/** L'écran, une fois le réseau tu. */
async function goto(page: Page, path: string) {
  await page.goto(path, { waitUntil: 'networkidle' })
}

/**
 * Colle un bloc dans le panneau d'import de l'écran courant, et confirme.
 *
 * Trois gestes, parce que l'écran en demande trois : ouvrir le panneau,
 * analyser le collage — c'est là que les colonnes sont reconnues, sans ordre
 * imposé — puis confirmer. Les enchaîner ici évite de les réécrire à chaque
 * import, et nomme l'étape qui a échoué quand l'une des trois change.
 */
async function pasteAndImport(page: Page, block: string) {
  await page.getByRole('button', { name: 'Copier / Coller' }).click()
  const area = page.getByPlaceholder(/Collez ici/)
  await expect(area).toBeVisible()
  await area.fill(block)
  await page.getByRole('button', { name: 'Analyser le collage' }).click()
  const confirm = page.getByRole('button', { name: /Confirmer l’import/ })
  await expect(confirm).toBeEnabled()
  await confirm.click()
  await expect(confirm).toBeHidden()
}

/**
 * Confirme le dialogue ouvert, s'il y en a un.
 *
 * Les gestes irréversibles en demandent un ; les autres non, et l'attendre
 * ferait échouer l'étape sur une absence qui est le comportement voulu.
 */
async function confirmDialog(page: Page) {
  const dialog = page.getByRole('dialog')
  if ((await dialog.count()) === 0) return
  await dialog.getByRole('button').last().click()
  await expect(dialog).toBeHidden()
}


/**
 * Saisit les quantités d'un passage de comptage sur la feuille ouverte.
 *
 * La grille passe en édition, chaque ligne reçoit sa quantité, puis on
 * enregistre. La colonne « Comptage » est la quatrième : les trois premières
 * sont la référence, la désignation et la section, et la désignation n'est pas
 * saisissable.
 */
async function fillSheet(page: Page, quantities: number[]) {
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('button', { name: 'Modifier les lignes' }).click()
  const rows = dialog.locator('tr[data-row]')
  await expect(rows).toHaveCount(quantities.length)
  for (const [index, quantity] of quantities.entries()) {
    const cell = rows.nth(index).locator('td').nth(3).locator('input')
    await cell.fill(String(quantity))
  }
  const save = dialog.getByRole('button', { name: 'Enregistrer' })
  await expect(save).toBeEnabled()
  await save.click()
  // Enregistré : le bouton redevient inactif faute de modification en attente.
  // Il ne disparaît pas — la feuille reste ouverte pour la ligne suivante.
  await expect(save).toBeDisabled()
}

/** Ouvre la feuille du passage demandé (1 ou 2) depuis la vue Compil. */
async function openSheet(page: Page, pass: number) {
  await goto(page, `/campagnes/${campaignId}/compil`)
  await page.getByRole('button', { name: 'Ouvrir la feuille' }).nth(pass - 1).click()
  await expect(page.getByRole('dialog')).toBeVisible()
}

test.describe.configure({ mode: 'serial' })

test.describe('un inventaire, du dossier vide à la clôture', () => {
  test('1 — la campagne se crée et s’ouvre sur son référentiel', async ({ page }) => {
    await goto(page, '/campagnes')
    await page.getByRole('button', { name: 'Nouvelle campagne' }).click()

    // Portée au dialogue : la liste porte sa propre zone de recherche, et
    // compter les champs de la page entière dépendrait de ce que la base
    // contient déjà.
    const fields = page.getByRole('dialog').locator('input')
    await fields.nth(0).fill(CODE)
    await fields.nth(1).fill('Parcours de bout en bout')
    await fields.nth(2).fill('2026-06-30')
    await page.getByRole('button', { name: 'Créer la campagne' }).click()

    // L'écran ouvert est celui de la phase : une campagne neuve s'ouvre sur son
    // référentiel, pas sur un tableau de bord vide.
    await expect(page).toHaveURL(/\/campagnes\/[0-9a-f-]+\/articles$/)
    campaignId = page.url().split('/campagnes/')[1]!.split('/')[0]!
    expect(campaignId).toMatch(/^[0-9a-f-]{36}$/)
  })

  test('2 — le référentiel articles s’importe par collage', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/articles`)
    await pasteAndImport(page, ARTICLES)

    // Les quinze lignes sont au dossier, et l'écran le dit sans rechargement.
    await expect(page.getByText('P-00001').first()).toBeVisible()
    await expect(page.getByText('P-00015').first()).toBeVisible()
  })

  test('3 — les feuilles de comptage se créent en préparation', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/feuilles`)
    await pasteAndImport(page, SHEETS)

    // La zone et ses feuilles existent : c'est ce qui sera compté le jour J.
    await expect(page.getByText(ZONE).first()).toBeVisible()
  })

  test('4 — la campagne passe en comptage', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/articles`)
    const pass = page.getByRole('button', { name: 'Passer en comptage' })
    await expect(pass).toBeEnabled()
    await pass.click()
    await confirmDialog(page)

    // Le statut a changé, et l'écran le porte.
    await expect(page.getByText('Comptage', { exact: true }).first()).toBeVisible()
  })

  test('5 — le stock ERP se charge et se gèle, une fois en comptage', async ({ page }) => {
    // Il se charge **au début du comptage**, pas en préparation : l'écran de
    // création le dit, et le séquencement l'impose.
    await goto(page, `/campagnes/${campaignId}/stock-erp`)
    await pasteAndImport(page, STOCK)
    await expect(page.getByText('P-00001').first()).toBeVisible()

    // Le gel rend le stock opposable : après lui, une ligne ne se corrige plus
    // en silence.
    const freeze = page.getByRole('button', { name: /Geler/ })
    await expect(freeze).toBeEnabled()
    await freeze.click()
    await confirmDialog(page)
    await expect(page.getByText(/gelé/i).first()).toBeVisible()
  })

  test('6 — la première équipe compte', async ({ page }) => {
    await openSheet(page, 1)
    // P-00001 est compté 95 : c'est là que la seconde équipe divergera.
    await fillSheet(page, [95, 102, 103, 104, 105])
    await expect(page.getByText('5 / 5 lignes').first()).toBeVisible()
  })

  test('7 — la seconde équipe compte, et contredit la première', async ({ page }) => {
    await openSheet(page, 2)
    // Même feuille, une seule quantité différente : 90 au lieu de 95.
    await fillSheet(page, [90, 102, 103, 104, 105])

    // La contradiction remonte **d'elle-même**, sans qu'on ait rien demandé.
    //
    // Elle ne le faisait pas : la comparaison ne se calculait qu'à la
    // fermeture d'une zone. Entre-temps l'onglet Arbitrages affirmait « les
    // deux équipes ont trouvé les mêmes quantités » et l'indicateur restait à
    // zéro — l'écart n'apparaissait qu'au refus de la clôture, c'est-à-dire au
    // moment où l'on croyait avoir fini. C'est ce parcours qui l'a constaté.
    await goto(page, `/campagnes/${campaignId}/compil?vue=arbitration`)
    await expect(page.getByText('P-00001').first()).toBeVisible()
    await expect(
      page.getByText(/Aucun écart entre les deux comptages/),
    ).toHaveCount(0)
  })

  test('8 — l’arbitrage se tranche', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/compil?vue=arbitration`)

    // La quantité est pré-remplie avec celle du second comptage, sans être
    // validée : c'est une proposition, pas une décision. Retenir 90 reste un
    // geste explicite.
    const quantity = page.getByLabel('Quantité arbitrée')
    await expect(quantity).toHaveValue('90')
    await page.getByRole('button', { name: 'Valider' }).first().click()

    // Tranché : plus rien n'attend de décision sur cet écran. Le seul
    // « Valider » était celui de la ligne en litige — les lignes d'accord n'en
    // portent pas.
    await expect(page.getByRole('button', { name: 'Valider' })).toHaveCount(0)
  })

  test('9 — la zone se ferme une fois l’écart tranché', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/compil`)
    const close = page.getByRole('button', { name: 'Terminer la zone' }).first()
    await expect(close).toBeEnabled()
    await close.click()
    await confirmDialog(page)

    // Le refus d'avant portait sur l'arbitrage en attente ; il n'a plus lieu.
    await expect(
      page.getByText(/ne sont pas tranchés/),
    ).toHaveCount(0)
    await expect(page.getByText('Terminée').first()).toBeVisible()
  })

  test('10 — les emplacements non comptés sont forcés au stock ERP', async ({ page }) => {
    // Quatre journaux ERP n'ont été comptés par personne : le passage en
    // analyse les refuse tant qu'ils ne sont pas tranchés. Les forcer au stock
    // ERP est le geste qui dit « on n'a pas compté là, on retient le chiffre
    // du système » — c'est un choix, et il laisse une trace.
    await goto(page, `/campagnes/${campaignId}/comptage`)
    await page.getByLabel('Tout sélectionner').check()
    await page.getByRole('button', { name: 'Forcer au stock ERP' }).click()
    await confirmDialog(page)
    await expect(page.getByRole('button', { name: /Forcé au stock ERP\s*4/ })).toBeVisible()
  })

  test('11 — la campagne passe en analyse', async ({ page }) => {
    await goto(page, `/campagnes/${campaignId}/compil`)
    const pass = page.getByRole('button', { name: /Passer en analyse/ })
    await expect(pass).toBeEnabled()
    await pass.click()
    await confirmDialog(page)
    await expect(page.getByText('Analyse', { exact: true }).first()).toBeVisible()
  })

  test('12 — l’état des lieux avant clôture se lit', async ({ page }) => {
    // Lisible pendant toute la phase d'analyse, et pas seulement dans la
    // fenêtre qui clôture : découvrir trois points bloquants au moment de
    // cliquer, un vendredi soir, est exactement ce qu'on évite.
    await goto(page, `/campagnes/${campaignId}/controles`)
    await expect(page.getByText(/bloquant\(s\)/).first()).toBeVisible()
    await expect(page.getByText(/fait\(s\)/).first()).toBeVisible()
  })
})
