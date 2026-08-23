import { defineConfig } from '@playwright/test'

/**
 * Le parcours de bout en bout, dans un vrai navigateur.
 *
 * **L'application doit tourner**, contre une base à elle. Ce banc ne la démarre
 * pas : elle a besoin d'un PostgreSQL, et la faire démarrer ici masquerait
 * l'échec le plus courant — une base absente — derrière un délai d'attente.
 *
 *     createdb e2e
 *     cd app && INV_ENV=local DATABRICKS_APP_PORT=8100 PGDATABASE=e2e \
 *       PGHOST=... PGUSER=... python main.py
 *     npm --prefix frontend run e2e
 *
 * `E2E_BASE_URL` déplace la cible ; par défaut le port 8100, pour ne pas
 * entrer en conflit avec l'application de développement sur 8000.
 */
export default defineConfig({
  testDir: './e2e',
  // Le parcours est une chaîne : chaque étape suppose la précédente. Le
  // paralléliser ferait courir plusieurs campagnes dans la même base et
  // transformerait un échec en énigme.
  fullyParallel: false,
  workers: 1,
  // Un pas du parcours attend un import ou un recalcul côté serveur ; la
  // valeur par défaut de cinq secondes échoue sur une base froide.
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8100',
    // La trace du dernier échec suffit à comprendre ce que l'écran montrait ;
    // la garder pour les passages réussis remplirait le disque pour rien.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1600, height: 1000 },
    locale: 'fr-FR',
  },
})
