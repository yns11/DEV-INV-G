# Guide de déploiement — pas à pas, toutes variantes

Ce guide couvre **quatre variantes** de déploiement. Chacune est autonome :
suivez celle qui correspond à votre contexte, sans lire les autres.

| Variante | Pour qui | Section |
|---|---|---|
| **A. Local** | Développement, démonstration, recette hors Databricks | [§3](#3-variante-a--exécution-locale) |
| **B. Databricks CLI + Asset Bundle** | Déploiement standard, CI/CD | [§4](#4-variante-b--databricks-cli--asset-bundle-recommandé) |
| **C. Interface graphique Databricks** | Première installation sans CLI | [§5](#5-variante-c--interface-graphique-databricks) |
| **D. CI/CD (GitHub Actions / Azure DevOps)** | Industrialisation | [§6](#6-variante-d--cicd) |

Les sections [§1](#1-prérequis) et [§2](#2-provisionnement-des-ressources-databricks)
sont communes à toutes les variantes sauf la variante A.

---

## 1. Prérequis

### 1.1 Outils

| Outil | Version minimale | Vérification |
|---|---|---|
| Python | 3.11 | `python3 --version` |
| Node.js | 20 | `node --version` |
| Databricks CLI | 0.240 | `databricks --version` |
| Git | 2.30 | `git --version` |

Installation du CLI Databricks :

```bash
# macOS / Linux (Homebrew)
brew tap databricks/tap && brew install databricks

# Linux / WSL (script officiel)
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh

# Windows (winget)
winget install Databricks.DatabricksCLI
```

> ⚠️ L'ancien CLI (`pip install databricks-cli`, version 0.1x) **n'est pas
> compatible** : il ne connaît ni `databricks apps`, ni `databricks bundle`.
> Vérifiez que `databricks --version` renvoie `v0.2xx.x`.

### 1.2 Droits requis dans le workspace

| Droit | Utilisé pour | Comment vérifier |
|---|---|---|
| `CAN_MANAGE` sur les Databricks Apps | Créer et déployer l'app | Onglet **Compute → Apps** visible |
| `CREATE SCHEMA` sur le catalogue cible | Créer le schéma `inventory` | `SHOW GRANTS ON CATALOG emotors_data_champions` |
| `CREATE VOLUME` sur ce schéma | Créer le volume des preuves | idem |
| Création de projets Lakebase | Base opérationnelle | Onglet **Compute → Lakebase** visible |
| `CAN_USE` sur un SQL warehouse | Lectures Delta | `databricks warehouses list` |
| `CAN_QUERY` sur un endpoint LLM vision | Lecture des feuilles scannées | `databricks serving-endpoints list` |

> Ce projet **ne crée pas de catalogue**. Il ajoute un schéma, des tables et un
> volume dans un catalogue existant (`emotors_data_champions` par défaut).

### 1.3 Authentification du CLI

```bash
# OAuth (recommandé — nécessaire pour `databricks apps logs`)
databricks auth login --host https://<votre-workspace>.cloud.databricks.com \
    --profile PROD

# Vérification
databricks auth profiles
databricks current-user me --profile PROD
```

Un profil PAT fonctionne aussi, mais `databricks apps logs` exige OAuth ;
utilisez `databricks apps get` pour l'état si vous êtes en PAT.

### 1.4 Récupération du dépôt

```bash
git clone <url-du-dépôt> campagnes-inventaire
cd campagnes-inventaire
```

---

## 2. Provisionnement des ressources Databricks

À faire **une seule fois par environnement** (dev, prod).

### 2.1 Identifier le SQL warehouse

```bash
databricks warehouses list --profile PROD -o json \
  | jq -r '.[] | "\(.id)\t\(.name)\t\(.state)"'
```

Notez l'`id` (ex. `4b9b953939869799`). Un warehouse **serverless de taille
2X-Small** suffit : l'application ne l'utilise que pour les lectures Delta.

```bash
export WAREHOUSE_ID=4b9b953939869799
```

### 2.2 Créer le schéma, le volume, les tables et les vues Unity Catalog

Le fichier `sql/00_unity_catalog.sql` est idempotent (`CREATE ... IF NOT EXISTS`).

```bash
databricks sql query --warehouse-id "$WAREHOUSE_ID" \
    --file sql/00_unity_catalog.sql --profile PROD
```

**Sans CLI** : ouvrez **SQL Editor** dans le workspace, collez le contenu du
fichier, sélectionnez le warehouse, exécutez.

Vérification :

```bash
databricks sql query --warehouse-id "$WAREHOUSE_ID" --profile PROD \
    --query "SHOW TABLES IN emotors_data_champions.inventory"
```

Vous devez voir 9 tables et 4 vues (`v_variance`, `v_campaign_kpi`,
`v_variance_recurrence`, `v_wip_contribution`).

> Si votre catalogue ne s'appelle pas `emotors_data_champions`, remplacez-le en
> tête du fichier SQL **et** dans la variable `uc_catalog` de `databricks.yml`.

### 2.3 Créer le projet Lakebase

```bash
# Lister les projets existants — réutilisez-en un si possible
databricks postgres list-projects --profile PROD

# Ou en créer un : la branche `main` et la base `databricks_postgres`
# sont provisionnées automatiquement
databricks postgres create-project --json '{
  "name": "inventaire",
  "display_name": "Campagnes Inventaire"
}' --profile PROD

# Relever le nom de la branche et de la base
databricks postgres list-branches --project-name inventaire --profile PROD
databricks postgres list-databases --project-name inventaire --branch main --profile PROD
```

**Sans CLI** : **Compute → Lakebase → Create project**.

Notez la branche (typiquement `main`) et la base (typiquement
`databricks_postgres`) : ce sont les valeurs de `lakebase_branch` et
`lakebase_database` dans `databricks.yml`.

> 💡 Créez un projet Lakebase **dédié** à l'application. Le schéma `inventory`
> y sera créé au premier démarrage, et le service principal de l'app en sera
> propriétaire — ce qui évite les conflits de propriété avec un schéma existant.

### 2.4 Identifier ou créer l'endpoint LLM

L'application utilise un modèle **capable de lire une image** pour extraire les
feuilles de comptage scannées.

```bash
databricks serving-endpoints list --profile PROD -o json \
  | jq -r '.[] | .name'
```

Les endpoints *pay-per-token* proposés par Databricks (par exemple
`databricks-claude-sonnet-4-5`) conviennent et ne demandent aucun
provisionnement. Notez le nom exact.

```bash
export LLM_ENDPOINT=databricks-claude-sonnet-4-5
```

> Si aucun endpoint vision n'est disponible, l'application fonctionne
> intégralement **sans** : seule la lecture automatique des scans est
> indisponible, la saisie manuelle et l'import de fichiers restent ouverts.

---

## 3. Variante A — exécution locale

Utile pour développer, faire une démonstration hors ligne ou recetter la logique
métier sans workspace.

### 3.1 Base PostgreSQL locale

**Avec Docker :**

```bash
docker run -d --name inv-pg \
  -e POSTGRES_PASSWORD=inventaire \
  -e POSTGRES_DB=inventaire \
  -p 5432:5432 postgres:16
```

**Sans Docker (PostgreSQL installé) :**

```bash
createdb inventaire
```

### 3.2 Variables d'environnement

```bash
cp .env.example .env
```

Puis éditez `.env` :

```dotenv
INV_ENV=local
PGHOST=127.0.0.1
PGPORT=5432
PGDATABASE=inventaire
PGUSER=postgres
PGPASSWORD=inventaire
PGSSLMODE=disable
```

Laissez `DATABRICKS_WAREHOUSE_ID` et `INV_LLM_ENDPOINT` vides : l'application
démarre en mode dégradé pour ces deux fonctions (l'extraction IA et les lectures
Delta) et signale clairement lesquelles sont indisponibles.

### 3.3 Dépendances

```bash
make install
```

équivalent à :

```bash
python3 -m pip install -r app/requirements.txt
python3 -m pip install pytest ruff mypy
cd frontend && npm install && cd ..
```

### 3.4 Lancer

**Mode production locale** (un seul processus, comme sur la plateforme) :

```bash
make run          # construit la SPA puis sert tout sur http://127.0.0.1:8000
```

**Mode développement** (rechargement à chaud des deux côtés, deux terminaux) :

```bash
# terminal 1
make dev-api      # API sur :8000, rechargement automatique

# terminal 2
make dev-ui       # Vite sur :5173, proxy /api → :8000
```

Ouvrez http://localhost:5173.

Les migrations SQL sont appliquées automatiquement au démarrage. Vérifiez :

```bash
curl -s localhost:8000/api/health | jq
# {"status":"ok","ready":true,...}
```

### 3.5 Tests et qualité

```bash
make test      # 132 tests, ~0,3 s, aucune base requise
make lint      # ruff + tsc
make check     # les deux
```

---

## 4. Variante B — Databricks CLI + Asset Bundle (recommandé)

### 4.1 Renseigner les variables du bundle

Créez `databricks.yml.local` **ou** exportez les variables (elles ne doivent
jamais être committées) :

```bash
export BUNDLE_VAR_warehouse_id="$WAREHOUSE_ID"
export BUNDLE_VAR_llm_endpoint="$LLM_ENDPOINT"
export BUNDLE_VAR_lakebase_branch="main"
export BUNDLE_VAR_lakebase_database="databricks_postgres"
export BUNDLE_VAR_uc_catalog="emotors_data_champions"
```

Alternative — passer les variables en ligne de commande :

```bash
databricks bundle validate -t prod --profile PROD \
  --var="warehouse_id=$WAREHOUSE_ID" \
  --var="llm_endpoint=$LLM_ENDPOINT"
```

### 4.2 Construire la SPA

**Étape indispensable.** `app/static/` est ignoré par Git : il doit être
régénéré avant chaque déploiement.

```bash
make build-frontend
ls app/static/index.html   # doit exister
```

### 4.3 Valider

```bash
databricks bundle validate -t prod --profile PROD
```

Corrigez toute erreur avant de continuer. La validation vérifie la syntaxe du
bundle, l'existence des ressources et la résolution des variables.

### 4.4 Déployer et démarrer

```bash
databricks apps deploy -t prod --profile PROD
```

> ⚠️ **N'utilisez pas `databricks bundle deploy` seul.** Il téléverse le code
> mais crée l'app avec `no_compute` : elle reste **arrêtée**, sans URL.
> `databricks apps deploy` valide, téléverse, applique la configuration **et**
> démarre l'application.

Ou, en deux temps :

```bash
databricks bundle deploy -t prod --profile PROD
databricks bundle run campagnes_inventaire -t prod --profile PROD
```

### 4.5 Vérifier

```bash
# État et URL
databricks apps get campagnes-inventaire --profile PROD -o json \
  | jq '{state: .app_status.state, url: .url}'

# Journaux en direct (OAuth requis)
databricks apps logs campagnes-inventaire --follow --profile PROD
```

Attendez `app_status.state == "RUNNING"`, puis ouvrez l'URL et vérifiez :

```bash
curl -s "$(databricks apps get campagnes-inventaire --profile PROD -o json | jq -r .url)/api/health"
```

Réponse attendue :

```json
{
  "status": "ok",
  "ready": true,
  "lakebaseConfigured": true,
  "warehouseConfigured": true,
  "startupError": null
}
```

### 4.6 Déployer le job de publication

```bash
databricks bundle deploy -t prod --profile PROD
databricks bundle run inventory_publish_campaign -t prod --profile PROD \
    --params campaign_code=INV-2026-06
```

---

## 5. Variante C — interface graphique Databricks

Pour une première installation sans CLI.

### 5.1 Préparer l'archive

En local :

```bash
make build-frontend
```

### 5.2 Téléverser le code

1. Dans le workspace, ouvrez **Workspace → Users → votre-email**.
2. **Create → Folder**, nommez-le `campagnes-inventaire`.
3. Téléversez le contenu du dossier `app/` (y compris `static/`) via
   **Import → Drag & drop**, ou synchronisez avec :
   ```bash
   databricks sync app /Workspace/Users/<vous>/campagnes-inventaire --profile PROD
   ```

> Le glisser-déposer de l'interface est limité en nombre de fichiers ; pour un
> premier essai, `databricks sync` reste beaucoup plus simple même si le reste
> du déploiement se fait à la souris.

### 5.3 Créer l'application

1. **Compute → Apps → Create app**.
2. **Name** : `campagnes-inventaire` (≤ 26 caractères, minuscules et tirets).
3. **Source code path** : le dossier téléversé.
4. **Compute size** : `Medium` (6 Go) suffit ; passez à `Large` si vous importez
   régulièrement plus de 100 000 lignes d'un coup.

### 5.4 Attacher les ressources

Dans l'onglet **Resources** de l'app, ajoutez :

| Type | Valeur | Permission | Clé (`valueFrom`) |
|---|---|---|---|
| SQL warehouse | votre warehouse | `CAN_USE` | `sql-warehouse` |
| Serving endpoint | votre endpoint LLM | `CAN_QUERY` | `serving-endpoint` |
| Database (Lakebase) | branche + base | `CAN_CONNECT_AND_CREATE` | `database` |

La plateforme accorde automatiquement ces permissions au service principal de
l'application.

### 5.5 Variables d'environnement

Onglet **Environment**, ajoutez :

| Nom | Valeur |
|---|---|
| `INV_UC_CATALOG` | `emotors_data_champions` |
| `INV_UC_SCHEMA` | `inventory` |
| `INV_UC_VOLUME` | `inventory_evidence` |
| `INV_GENERIC_WAREHOUSE` | `B06VRAC` |
| `INV_GENERIC_LOCATION` | `GENERIQUE` |
| `INV_LOG_LEVEL` | `INFO` |
| `INV_ENV` | `prod` |

`DATABRICKS_WAREHOUSE_ID` et `INV_LLM_ENDPOINT` sont fournis par les ressources
attachées (`valueFrom`), ne les saisissez pas à la main.

### 5.6 Démarrer

**Deploy**, puis **Start**. Suivez les journaux dans l'onglet **Logs**.

### 5.7 Modifier les ressources plus tard

Utilisez toujours `create-update` (jamais `update`, qui est obsolète) :

```bash
# 1) Lire la configuration actuelle
databricks apps get campagnes-inventaire --profile PROD -o json > current.json

# 2) Construire le corps en FUSIONNANT vos ajouts avec les ressources existantes
#    (update_mask=resources remplace le tableau ENTIER)
cat > update.json <<'JSON'
{
  "update_mask": "resources",
  "app": {
    "resources": [
      {"name": "sql-warehouse",    "sql_warehouse":    {"id": "<ID>", "permission": "CAN_USE"}},
      {"name": "serving-endpoint", "serving_endpoint": {"name": "<NOM>", "permission": "CAN_QUERY"}},
      {"name": "database",         "database":         {"branch": "main", "database": "databricks_postgres", "permission": "CAN_CONNECT_AND_CREATE"}}
    ]
  }
}
JSON

databricks apps create-update campagnes-inventaire --json @update.json --profile PROD
```

---

## 6. Variante D — CI/CD

### 6.1 GitHub Actions

`.github/workflows/deploy.yml` :

```yaml
name: Déploiement
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write        # pour l'OIDC Databricks

jobs:
  qualite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: frontend/package-lock.json }
      - run: python -m pip install -r app/requirements.txt pytest ruff
      - run: make test
      - run: python -m ruff check app tests jobs
      - run: cd frontend && npm ci && npx tsc --noEmit

  deploiement:
    needs: qualite
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20', cache: 'npm', cache-dependency-path: frontend/package-lock.json }
      - uses: databricks/setup-cli@main

      - name: Construire la SPA
        run: cd frontend && npm ci && npm run build

      - name: Valider le bundle
        env:
          DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}
          DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}
          DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}
          BUNDLE_VAR_warehouse_id: ${{ vars.WAREHOUSE_ID }}
          BUNDLE_VAR_llm_endpoint: ${{ vars.LLM_ENDPOINT }}
        run: databricks bundle validate -t prod

      - name: Déployer
        env:
          DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}
          DATABRICKS_CLIENT_ID: ${{ secrets.DATABRICKS_CLIENT_ID }}
          DATABRICKS_CLIENT_SECRET: ${{ secrets.DATABRICKS_CLIENT_SECRET }}
          BUNDLE_VAR_warehouse_id: ${{ vars.WAREHOUSE_ID }}
          BUNDLE_VAR_llm_endpoint: ${{ vars.LLM_ENDPOINT }}
        run: databricks apps deploy -t prod
```

Créez au préalable un **service principal** dans le workspace, donnez-lui
`CAN_MANAGE` sur l'app et stockez son `client_id` / `client_secret` en secrets.

### 6.2 Azure DevOps

```yaml
trigger: [main]

pool: { vmImage: ubuntu-latest }

steps:
  - task: UsePythonVersion@0
    inputs: { versionSpec: '3.11' }
  - task: NodeTool@0
    inputs: { versionSpec: '20.x' }

  - script: |
      python -m pip install -r app/requirements.txt pytest ruff
      make test
      python -m ruff check app tests jobs
    displayName: Qualité

  - script: cd frontend && npm ci && npm run build
    displayName: Construire la SPA

  - script: |
      curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
      databricks apps deploy -t prod
    displayName: Déployer
    env:
      DATABRICKS_HOST: $(DATABRICKS_HOST)
      DATABRICKS_CLIENT_ID: $(DATABRICKS_CLIENT_ID)
      DATABRICKS_CLIENT_SECRET: $(DATABRICKS_CLIENT_SECRET)
      BUNDLE_VAR_warehouse_id: $(WAREHOUSE_ID)
      BUNDLE_VAR_llm_endpoint: $(LLM_ENDPOINT)
```

---

## 7. Après le déploiement

### 7.1 Donner accès aux utilisateurs

```bash
databricks apps set-permissions campagnes-inventaire --json '{
  "access_control_list": [
    {"group_name": "inventaire-gestionnaires", "permission_level": "CAN_USE"},
    {"group_name": "inventaire-admins",        "permission_level": "CAN_MANAGE"}
  ]
}' --profile PROD
```

Ou, à la souris : onglet **Permissions** de l'app.

### 7.2 Vérifier l'identité vue par l'application

Ouvrez `<url-de-lapp>/api/me`. Vous devez voir votre adresse et
`"source": "databricks-apps"`. Si vous voyez `unknown@unauthenticated`,
l'application est accessible sans passer par le proxy : vérifiez la configuration
réseau du workspace.

### 7.3 Première campagne

1. **Campagnes → Nouvelle campagne** : code, libellé, date de comptage.
2. **Référentiels & seuils → Articles** : charger l'export articles.
3. **Nomenclatures** : charger la BOM effective, vérifier l'onglet *Santé des
   nomenclatures* (cycles, assemblages sans structure).
4. **Seuils** : ajuster les seuils de matérialité par type d'article.
5. **GENERIQUE → Créer une zone** pour chaque aire physique, puis saisir la
   liste d'articles pré-imprimée de chaque zone.
6. **Imprimer toutes les feuilles n°1** la veille de l'inventaire.
7. Passer en **Comptage** — les référentiels sont alors gelés.

Le détail fonctionnel est dans [`04-guide-utilisateur.md`](04-guide-utilisateur.md).

---

## 8. Dépannage

| Symptôme | Cause probable | Correction |
|---|---|---|
| **502 Bad Gateway** | L'app n'écoute pas sur `DATABRICKS_APP_PORT`, ou sur `localhost` | Vérifiez `app.yaml` : `--host 0.0.0.0 --port ${DATABRICKS_APP_PORT}` |
| `ModuleNotFoundError` au démarrage | Dépendance absente de `app/requirements.txt` | Ajoutez-la et redéployez ; aucun paquet système n'est installable |
| `/api/health` → `ready: false` | Lakebase non attaché ou permissions manquantes | Vérifiez la ressource `database` et `CAN_CONNECT_AND_CREATE` |
| `Lakebase n'est pas configuré` | `PGHOST` / `PGDATABASE` / `PGUSER` absents | La ressource `database` n'est pas attachée à l'app |
| `password authentication failed` après ~1 h | Jeton Lakebase expiré | Normalement géré automatiquement ; si cela persiste, redémarrez l'app et ouvrez un ticket |
| **404 sur toutes les pages sauf `/api/...`** | SPA non construite | `make build-frontend` puis redéployez ; `app/static/index.html` doit exister |
| **504 après 2 minutes, rien dans les journaux** | Requête dépassant les 120 s du proxy | Réduisez le volume importé par lot, ou augmentez la taille de compute |
| `Client de modèle indisponible` | Endpoint LLM non attaché ou sans `CAN_QUERY` | Attachez la ressource `serving-endpoint` |
| `relation "campaign" does not exist` | Migrations non appliquées | Consultez les journaux de démarrage ; le rôle doit avoir `CREATE` sur le schéma |
| `La migration 001 a été modifiée après application` | Un fichier de migration déjà appliqué a été édité | Restaurez le fichier ; créez une **nouvelle** migration |
| L'app démarre puis s'arrête | Dépassement des 10 min de démarrage | Épinglez les versions, réduisez les dépendances |
| **Pas d'espace disque** pendant le build | Quota du conteneur atteint | Supprimez `frontend/node_modules` et les caches, relancez |

### 8.1 Commandes de diagnostic

```bash
# État de l'application
databricks apps get campagnes-inventaire --profile PROD -o json | jq '.app_status'

# Journaux (JSON structuré, une ligne par évènement)
databricks apps logs campagnes-inventaire --follow --profile PROD

# Filtrer les erreurs
databricks apps logs campagnes-inventaire --profile PROD | jq 'select(.level=="ERROR")'

# Retrouver une requête précise à partir de l'identifiant affiché à l'utilisateur
databricks apps logs campagnes-inventaire --profile PROD \
  | jq 'select(.request_id=="<identifiant>")'
```

### 8.2 Se connecter à Lakebase pour inspecter

```bash
# Générer un credential temporaire
databricks postgres generate-credential --instance-names <PGHOST> --profile PROD

# Se connecter
PGPASSWORD=<jeton> psql "host=<PGHOST> dbname=databricks_postgres user=<PGUSER> sslmode=require"

# Requêtes utiles
\dt inventory.*
SELECT code, status, count_date FROM inventory.campaign ORDER BY created_at DESC;
SELECT version, applied_at FROM inventory.schema_migration ORDER BY version;
SELECT action, COUNT(*) FROM inventory.audit_event GROUP BY action;
```

---

## 9. Mise à jour d'une version existante

```bash
git pull
make build-frontend
databricks bundle validate -t prod --profile PROD
databricks apps deploy -t prod --profile PROD
```

Les nouvelles migrations SQL s'appliquent automatiquement au redémarrage. Elles
sont **en avant uniquement** : il n'y a pas de migration descendante, parce
qu'annuler un schéma dans un système dont la promesse est un journal d'audit
immuable n'est pas une garantie qu'on peut offrir. Pour revenir en arrière,
redéployez la version précédente du code — le schéma reste compatible tant
qu'aucune colonne n'a été supprimée.

### 9.1 Sauvegarde avant une mise à jour majeure

```bash
# Publier toutes les campagnes ouvertes vers Delta (archive)
for code in $(databricks sql query --warehouse-id "$WAREHOUSE_ID" --profile PROD \
    --query "SELECT code FROM emotors_data_champions.inventory.campaign" -o json \
    | jq -r '.[].code'); do
  databricks bundle run inventory_publish_campaign -t prod --params campaign_code=$code
done
```

Lakebase gère par ailleurs ses propres sauvegardes ponctuelles (*point-in-time
restore*) ; consultez la console Lakebase pour la fenêtre de rétention.

---

## 10. Désinstallation

```bash
# Arrêter et supprimer l'application
databricks apps delete campagnes-inventaire --profile PROD

# Supprimer les ressources du bundle (jobs)
databricks bundle destroy -t prod --profile PROD
```

Les données restent : le schéma Lakebase et les tables Delta ne sont **pas**
supprimés. Pour les retirer aussi :

```sql
DROP SCHEMA emotors_data_champions.inventory CASCADE;
```

```bash
databricks postgres delete-project --name inventaire --profile PROD
```

> ⚠️ Ces deux commandes détruisent l'historique complet des campagnes, y compris
> le journal d'audit. Publiez et exportez avant.
