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
| Databricks CLI | 0.294 (ou 1.x) | `databricks --version` |
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
> En deçà de la version 0.294, le groupe `databricks postgres` et la clé de
> ressource `postgres` (Lakebase Autoscaling) sont également absents ; le
> `bundle validate` signale alors `unknown field: postgres`. Vérifiez que
> `databricks --version` renvoie `v0.29x.x` ou `v1.x.x`.

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
make uc WAREHOUSE_ID="$WAREHOUSE_ID" PROFILE=PROD
```

`databricks sql query --file` **n'existe pas** — la CLI répond « unknown
command "sql" » et propose « psql ». `make uc` passe par
`scripts/apply_unity_catalog.py`, qui découpe le fichier et l'exécute
instruction par instruction sur le warehouse, en portant le catalogue et le
schéma courants (une session par instruction : un `USE CATALOG` n'y survivrait
pas).

**Sans CLI** : ouvrez **SQL Editor** dans le workspace, collez le contenu du
fichier, sélectionnez le warehouse, exécutez.

Vérification :

```bash
databricks api post /api/2.0/sql/statements --profile PROD --json '{
  "warehouse_id": "'"$WAREHOUSE_ID"'",
  "statement": "SHOW TABLES IN emotors_data_champions.inventory",
  "wait_timeout": "30s"
}'
```

Ou, sans CLI : `SHOW TABLES IN emotors_data_champions.inventory` dans le SQL
Editor.

Vous devez voir 10 tables et 4 vues (`v_variance`, `v_campaign_kpi`,
`v_variance_recurrence`, `v_wip_contribution`).

> Si votre catalogue ne s'appelle pas `emotors_data_champions`, remplacez-le en
> tête du fichier SQL **et** dans la variable `uc_catalog` de `databricks.yml`.

### 2.3 Créer le projet Lakebase

```bash
# Lister les projets existants — réutilisez-en un si possible
databricks postgres list-projects --profile PROD

# Ou en créer un : la branche `production`, l'endpoint `primary` et la base
# `databricks_postgres` sont provisionnés automatiquement
databricks postgres create-project inventaire \
    --json '{"spec": {"display_name": "Campagnes Inventaire"}}' --profile PROD

# Relever les identifiants de branche et de base
databricks postgres list-branches  projects/inventaire --profile PROD
databricks postgres list-databases projects/inventaire/branches/production --profile PROD
```

**Sans CLI** : **Compute → Lakebase → Create project**.

#### Les trois identifiants attendus par `databricks.yml`

L'application est rattachée à Lakebase par des **chemins de ressource**, pas par
des libellés d'affichage :

```
branch   = projects/<projet>/branches/<branche>
database = projects/<projet>/branches/<branche>/databases/<base>
```

La console donne les deux premiers directement (**Compute → Lakebase → votre
projet → Tables**) :

```
Projects  ›  inventaire  ›  production          ← projet, puis branche
                              ▲
   ┌─────────────────────────────────────┐
   │ 🗄  databricks_postgres          ▾  │      ← nom PostgreSQL de la base
   ├─────────────────────────────────────┤
   │ 🔲 Schema                            │
   │ ⚙  public                        ▾  │      ← schémas existants
   └─────────────────────────────────────┘
```

- le **deuxième élément du fil d'Ariane** est le projet → `lakebase_project` ;
- le **troisième** est la branche → `lakebase_branch` ;
- pour la base, **ne recopiez pas le libellé du sélecteur**. Les identifiants de
  ressource suivent la norme RFC 1123 (minuscules, chiffres et tirets, jamais de
  souligné) : le sélecteur affiche le nom PostgreSQL `databricks_postgres`, alors
  que l'identifiant de ressource attendu est `databricks-postgres`. Prenez-le
  dans le dernier segment du champ `name` renvoyé par `list-databases`.

Pour un projet créé avec les valeurs par défaut, cela donne :

```yaml
lakebase_project:  inventaire
lakebase_branch:   production
lakebase_database: databricks-postgres
```

Le nom PostgreSQL de la base (`databricks_postgres`) n'a pas à être déclaré :
la plateforme l'injecte dans le conteneur sous `PGDATABASE`, avec `PGHOST`,
`PGUSER`, `PGPORT`, `PGSSLMODE` et `PGAPPNAME`.

> Ce que la plateforme n'injecte **pas**, c'est le chemin de ressource de
> l'endpoint — et c'est pourtant la seule clé acceptée pour obtenir un
> credential Lakebase. `databricks.yml` publie donc `INV_LAKEBASE_BRANCH`, à
> partir des mêmes variables que la ressource, et l'application retrouve son
> endpoint en listant ceux de la branche et en comparant `PGHOST`. Si le
> service principal n'a pas le droit de les lister, court-circuitez la
> recherche :
>
> ```bash
> databricks postgres list-endpoints projects/inventaire/branches/production --profile PROD
> # puis, en variable d'environnement de l'app :
> #   INV_LAKEBASE_ENDPOINT=projects/inventaire/branches/production/endpoints/primary
> ```

> Ne renseignez **pas** le schéma dans `databricks.yml`. Au premier démarrage,
> l'application crée son propre schéma `inventory` à côté de `public` et
> `__db_system`, et y applique ses migrations. C'est la ressource `postgres`
> déclarée avec `CAN_CONNECT_AND_CREATE` qui lui en donne le droit.
> Si votre organisation impose un autre nom de schéma, passez-le par la variable
> d'environnement `INV_PG_SCHEMA`.

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
`databricks-claude-opus-4-8`) conviennent et ne demandent aucun
provisionnement. Notez le nom exact.

```bash
export LLM_ENDPOINT=databricks-claude-opus-4-8
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

Le fichier se pose à la racine du dépôt, et c'est là qu'il est lu quel que soit
le répertoire d'où l'application démarre — `make run` fait `cd app`. Un `.env`
placé dans `app/` reste lu et l'emporte, pour ceux qui en ont déjà un.

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
make test      # 2375 contrôles, ~45 s ; 64 ignorés sans PostgreSQL
make lint      # ruff + tsc
make check     # les deux
```

---

## 4. Variante B — Databricks CLI + Asset Bundle (recommandé)

### 4.1 Renseigner les variables du bundle

Seul `warehouse_id` n'a pas de valeur par défaut ; les autres variables ne se
renseignent que pour s'écarter des défauts de `databricks.yml`.

**Méthode recommandée — un fichier d'override par cible.** Les valeurs sont
lues automatiquement par *toutes* les commandes du bundle (`validate`,
`deploy`, `run`), sans drapeau à répéter. `.databricks/` est ignoré par Git,
rien n'est donc committé.

```bash
mkdir -p .databricks/bundle/prod
cat > .databricks/bundle/prod/variable-overrides.json <<'JSON'
{
  "warehouse_id":      "4b9b953939869799",
  "llm_endpoint":      "databricks-claude-opus-4-8",
  "lakebase_project":  "inventaire",
  "lakebase_branch":   "production",
  "lakebase_database": "databricks-postgres",
  "uc_catalog":        "emotors_data_champions"
}
JSON
```

**Alternatives** — variables d'environnement, ou drapeaux en ligne de commande :

```bash
export BUNDLE_VAR_warehouse_id="$WAREHOUSE_ID"
export BUNDLE_VAR_llm_endpoint="$LLM_ENDPOINT"

databricks bundle validate -t prod --profile PROD \
  --var="warehouse_id=$WAREHOUSE_ID"
```

> ⚠️ Les deux alternatives ci-dessus valent **pour une seule commande** :
> `--var` doit être répété à l'identique sur `validate` **et** sur
> `apps deploy`. C'est la source d'erreur la plus fréquente — un `validate`
> vert suivi d'un `deploy` qui échoue sur
> `no value assigned to required variable warehouse_id`.
>
> ⚠️ Sous PowerShell comme sous bash, une variable non définie s'étend en
> chaîne **vide** sans avertissement. `--var="warehouse_id=$WAREHOUSE_ID"` avec
> `$WAREHOUSE_ID` non défini transmet une valeur vide : le `validate` reste vert
> — une valeur *a* été assignée — et l'échec ne survient qu'au `deploy`, côté
> API (`Invalid SQL warehouse resource sql-warehouse: ID  is invalid`). Même
> piège sur `lakebase_branch`, où le vide écrase le défaut `production`.
> Vérifiez d'abord vos variables (`echo $WAREHOUSE_ID`, ou `echo $env:...` si
> elles viennent de l'environnement Windows), ou passez par le fichier
> d'override — la valeur y est écrite en clair, donc visible.

**Variante sans identifiant à recopier.** Un bundle sait résoudre un warehouse
par son **nom**. Le nom est stable et lisible, une faute de frappe échoue
proprement au `validate` (`warehouse named ... not found`), et il n'y a plus
d'identifiant vide possible. Remplacez dans `databricks.yml` :

```yaml
variables:
  warehouse_id:
    description: SQL warehouse utilisé pour toutes les lectures Delta.
    lookup:
      warehouse: "Serverless Starter Warehouse"   # ← le nom exact du vôtre
```

Le nom exact se lit avec `databricks warehouses list --profile PROD`. Plus
aucun `--var` n'est alors nécessaire pour cette variable.

> 🚫 Ne redéclarez **jamais** une variable dans un `targets:` en la faisant
> pointer sur elle-même (`warehouse_id: ${var.warehouse_id}`) : le CLI y voit
> une dépendance circulaire et refuse de déployer
> (`cycle detected in field resolution`). Dans un `targets:`, n'inscrivez
> qu'une valeur littérale propre à cet environnement.

### 4.2 Construire la SPA

**Étape indispensable.** `app/static/` est ignoré par Git : il doit être
régénéré avant chaque déploiement.

```bash
make build-frontend
ls app/static/index.html   # doit exister
```

Sans `make` (Windows) :

```powershell
cd frontend
npm ci
npm run build              # écrit ../app/static/
cd ..
dir app\static\index.html   # doit exister
```

> Un déploiement sans ce dossier réussit et l'app démarre : seule l'interface
> manque. Elle répond alors **503** avec une page qui explique quoi faire, et
> `/api/health` renvoie `"frontendBuilt": false`.
>
> ⚠️ Construire la SPA ne suffit pas si le bundle ne la téléverse pas. La
> synchronisation d'un Asset Bundle **applique `.gitignore`**, où `app/static/`
> figure puisqu'il s'agit d'un produit de compilation. `databricks.yml` le
> réadmet explicitement via `sync.include` ; ne retirez pas ce bloc. Pour
> vérifier ce qui part réellement :
>
> ```bash
> databricks bundle sync --dry-run --full -t prod --profile PROD -o json \
>   | grep '"type":"complete"'
> ```

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

#### Les trois points d'entrée de santé

Ils répondent à trois questions différentes, et ne s'échangent pas.

| Chemin | Question | Code | À câbler sur |
|---|---|---|---|
| `/api/health/live` | Le processus est-il figé ? | Toujours 200 | La sonde de **vivacité** |
| `/api/health/ready` | Ce conteneur peut-il servir ? | 200 ou **503** | La sonde de **disponibilité** |
| `/api/health` | Que sait-on de ce conteneur ? | Toujours 200 | Un humain, un `curl` |

`/api/health/live` ne consulte **aucune** dépendance, et c'est délibéré : y
faire entrer l'état de Lakebase ferait redémarrer en boucle des conteneurs
parfaitement sains le jour où la base est indisponible, et la rafale de
reconnexions qu'ils produiraient l'empêcherait de revenir.

`/api/health/ready` répond 503 quand la base ne répond pas, quand une migration
reste en attente, quand l'état du schéma est illisible, ou quand
l'initialisation a échoué. Une migration en attente compte : le schéma n'est
pas celui que le code attend, et servir dans cet état produit des colonnes
manquantes au moment où quelqu'un enregistre un comptage, plutôt qu'un refus
franc à la porte.

```bash
URL="$(databricks apps get campagnes-inventaire --profile PROD -o json | jq -r .url)"
curl -s -o /dev/null -w "live:%{http_code}\n"  "$URL/api/health/live"
curl -s -w "\nready:%{http_code}\n"           "$URL/api/health/ready"
```

### 4.6 Déployer le job de publication

> **Repartitionnement des tables d'archive.** Les tables Delta sont désormais
> partitionnées par `campaign_id` et non plus par `campaign_code`. Un code
> métier se réutilise — l'application ne supprime que logiquement — si bien
> qu'une campagne « INV-2026-06 » créée après le retrait d'une homonyme
> écrasait l'archive de la première.
>
> `sql/00_unity_catalog.sql` utilise `CREATE TABLE IF NOT EXISTS` : un
> déploiement neuf obtient le bon partitionnement, **une installation existante
> garde l'ancien**. Pour la reprendre, sauvegardez les tables, supprimez-les et
> rejouez le script :
>
> ```sql
> CREATE TABLE inventory.book_stock_snapshot_sauvegarde
>   DEEP CLONE inventory.book_stock_snapshot;   -- et ainsi de suite
> DROP TABLE inventory.book_stock_snapshot;
> -- puis `make uc`, et republier chaque campagne
> ```
>
> Republier est sans risque : chaque écriture est un `replaceWhere` sur
> l'identifiant de la campagne.

> **La table `publication`.** Elle est écrite en dernier par le job, et par rien
> d'autre. Une campagne y figure si et seulement si son archive est complète :
> Delta n'offrant pas de transaction couvrant plusieurs tables, c'est ce qui
> empêche une publication interrompue de se faire passer pour aboutie. Le job
> repose ensuite l'horodatage sur `campaign.published_at` dans Lakebase, que la
> clôture consulte.

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
| Postgres (Lakebase) | chemins branche + base | `CAN_CONNECT_AND_CREATE` | `postgres` |

> La clé de ressource est `postgres`. L'ancienne clé `database`
> (`instance_name` / `database_name`) désigne une instance *provisionnée*, un
> palier qui n'existe plus : le déploiement échoue alors sur
> « Database instance \<nom\> does not exist ».

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
| `INV_ASSISTANT_PROFILE` | `etendu` (seul profil livré) |
| `INV_SCAN_LLM_ENDPOINT` | *(vide)* — voir §8.2 bis |
| `INV_SCAN_MAX_WORKERS` | `4` |
| `INV_SCAN_MAX_PAGES` | `250` |
| `INV_SCAN_MAX_PIXELS` | `40000000` |
| `INV_SCAN_ROUTING_BATCH` | `12` |
| `INV_SCAN_DPI` | `150` |
| `INV_ERP_SCHEMA` | `emotors_data_champions.silver_erp_ye` |
| `INV_ERP_ITEMS_TABLE` | `silver_base_article` |
| `INV_ERP_BOM_TABLE` | `silver_bom` |
| `INV_ERP_STOCK_TABLE` | `stock_snapshot` |
| `INV_ERP_MOVEMENTS_TABLE` | `mouvements` |

`INV_ASSISTANT_PROFILE` décide de ce que l'assistant de campagne reçoit et de
ce qu'on lui demande. Un seul profil est livré — `etendu` : le dossier complet
de la campagne, un raisonnement libre, des chiffres qui restent ceux du dossier.
La variable existe pour qu'en ajouter un autre, plus restreint pour un public
plus large par exemple, soit un redémarrage et non une livraison de code.

`INV_ERP_SCHEMA` et ses tables désignent les tables **silver** lues par « Lire
depuis l'ERP » sur les grilles Articles, Nomenclatures et Stock ERP.

`INV_ERP_STOCK_TABLE` désigne le **snapshot de stock physique** : une ligne par
article × entrepôt × emplacement, partitionnée par `snapshot_date`. Les colonnes
attendues sont `item_id`, `entrepot`, `emplacement`, `stock_physique`, `unite` et
`snapshot_date` ; l'entité juridique et les lignes supprimées sont filtrées en
amont.

L'écran propose les journées publiées et **n'en charge qu'une**, celle que
l'utilisateur désigne — la plus récente par défaut. Deux conséquences pour la
plateforme : la table doit garder quelques jours d'historique pour que le choix
ait un sens, et `GET /api/erp/stock-dates` fait un `SELECT DISTINCT
snapshot_date … LIMIT 30` sur cette table, borné pour la même raison que la liste
affichée.

En lecture par le miroir, cette liste ne vaut que ce que le job de
synchronisation y a déposé : c'est `stock_days` qui décide combien de photos
elle propose — voir *Le snapshot de stock physique* plus bas.

`INV_ERP_MOVEMENTS_TABLE` désigne la table des **mouvements de stock**, lue par
« Tout charger de l'ERP » dans la vue Comparaison. Une ligne par référence et par
jour, une colonne par flux : `reception`, `expedition`, `production`,
`conso_theorique`, `consommation`, `rebut`. Les cinq quantités dont la
comparaison a besoin en sortent, y compris la production et la consommation
théorique, qui venaient auparavant de la table de faits du backflush.

Elle vit dans `INV_ERP_SCHEMA`, le schéma du référentiel : **même catalogue,
même grant**. Et tout ce que l'application filtrait elle-même contre les tables
bronze est appliqué en amont — l'entité juridique, l'exclusion des lignes
supprimées, la reconnaissance du rebut à son emplacement `QUAL VRAC` /
`QUA REBUT`, et la déduplication de la production d'un parent sur ses
composants.

Ces lectures empruntent l'entrepôt SQL attaché (`DATABRICKS_WAREHOUSE_ID`) et
les droits Unity Catalog de l'application : sans entrepôt ou sans `SELECT` sur
ces tables, l'option apparaît désactivée avec sa raison, et le chargement par
fichier reste disponible.

### 8.2 bis — Régler la lecture des scans

Une pile de cent feuilles de comptage fait deux cents pages. Cinq variables
gouvernent ce que ce volume coûte, et **aucune n'a de valeur optimale
universelle** : elles dépendent du débit réel de l'endpoint. Les défauts sont un
point de départ à mesurer, pas un réglage.

| Variable | Défaut | Ce qu'elle décide |
|---|---|---|
| `INV_SCAN_LLM_ENDPOINT` | *(vide)* | L'endpoint qui lit les scans. Vide, c'est `INV_LLM_ENDPOINT` — le comportement d'avant |
| `INV_SCAN_MAX_WORKERS` | `4` | Combien de feuilles sont lues en même temps |
| `INV_SCAN_MAX_PAGES` | `250` | Le plafond d'une pile. Au-delà : refus explicite, jamais troncature |
| `INV_SCAN_MAX_PIXELS` | `40000000` | Le plafond d'**une page rendue**. `render()` alloue son bitmap hors de portée de la garde anti-bombe de PIL : un PDF de quelques kilo-octets peut déclarer une page de deux cents pouces de côté, soit 900 Mpx à 150 dpi. Au-delà du plafond la résolution est **réduite**, pas la page refusée — un MediaBox démesuré est presque toujours un artefact de scanner, et une feuille reste lisible à cent dpi. Un A4 à 600 dpi tient dans la valeur par défaut |
| `INV_SCAN_ROUTING_BATCH` | `12` | Combien de pieds de page partent dans un même appel de routage |
| `INV_SCAN_DPI` | `150` | Résolution de rastérisation |

**Si le routage échoue en masse.** Un lot en erreur est recoupé en deux et
redemandé jusqu'à la page seule : la pile passe, au prix d'appels
supplémentaires. Le rapport et les logs distinguent maintenant les deux causes —
*« Réponse du modèle coupée au plafond de N jetons »* (le modèle a écrit plus que
le budget accordé) de *« pas renvoyé de JSON exploitable »* (la réponse est
entière mais mal formée). Si c'est la première, et systématiquement, baissez
`INV_SCAN_ROUTING_BATCH` : le plafond de jetons suit la taille du lot, mais un
endpoint peut aussi buter sur le **nombre d'images** d'un appel, et cette
limite-là ne s'achète pas en jetons.

**Un endpoint vision dédié.** Transcrire des chiffres manuscrits en JSON
n'appelle aucun raisonnement : payer un modèle de raisonnement pour cela coûte
du temps sur *chacune* des cent feuilles d'une pile, et c'est le temps qui fait
renoncer à scanner. Pointer `INV_SCAN_LLM_ENDPOINT` sur un modèle vision rapide
laisse l'assistant de campagne sur le modèle puissant. Les deux clients
négocient leurs paramètres séparément : ils n'ont aucune raison d'accepter les
mêmes.

**Le parallélisme se mesure, il ne se devine pas.** Quatre est un début. Montez
en surveillant deux choses dans les journaux du serving endpoint : les **429**
et la **profondeur de file**. Au-delà de ce que l'endpoint absorbe, les appels
attendent côté serving et le temps gagné se paie en relances. La marche à
suivre est celle que Databricks recommande pour tout endpoint : un test de
charge, puis un réglage sur le débit observé.

**Ce que le rapport mesure.** Chaque lecture renvoie ses chronomètres —
`evidence_upload_ms`, `pdf_render_ms`, `routing_ms`, `model_inference_ms`,
`db_write_ms`, `totalMs` — plus le nombre de pages, d'octets d'image et de
tokens. « C'est lent » ne dit pas où ; ces cinq nombres, si, et trois des cinq
causes possibles ne sont pas dans ce code.

**Le travail est asynchrone.** Le dépôt (`POST …/generic/scan`) rend un
identifiant tout de suite ; l'écran interroge `GET …/generic/scan/jobs/{id}`. La
lecture tourne dans **un** fil de l'application — pas dans un job Databricks :
le démarrage d'un job coûterait à lui seul plus que le rendu de la pile. Un seul
scan à la fois par conteneur, délibérément : deux piles se disputeraient le même
endpoint sans aller plus vite.

Conséquence à connaître : le PDF vit en mémoire du conteneur qui l'a reçu.
**Un redémarrage pendant une lecture la perd**, et le travail est marqué en
échec au démarrage suivant avec un message qui invite à recharger. Les feuilles
déjà écrites avant l'interruption sont conservées.

### 8.3 bis — Quand le catalogue de l'ERP n'est pas ouvrable à l'application

La lecture directe suppose que le **service principal de l'App** — pas vous —
ait `USE CATALOG` sur le catalogue de l'ERP, puis `USE SCHEMA` et `SELECT`.
Sans quoi le chargement échoue en nommant la commande à faire exécuter :

```sql
GRANT USE CATALOG ON CATALOG emotors_data_champions              TO `<sp-de-l-app>`;
GRANT USE SCHEMA  ON SCHEMA  emotors_data_champions.silver_erp_ye TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.silver_base_article TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.silver_bom          TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.stock_snapshot      TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.mouvements          TO `<sp-de-l-app>`;
```

Seul un propriétaire du catalogue peut les passer. Quand aucun n'est joignable
— et un inventaire garde sa date —, `INV_ERP_SOURCE=mirror` renverse la
contrainte : l'application lit une copie locale, dans sa propre base, alimentée
par le job `inventory_sync_erp_mirror` qui tourne, lui, avec une identité ayant
déjà accès à l'ERP.

| `INV_ERP_SOURCE` | Lit | Exige |
|---|---|---|
| `uc` (défaut) | les tables silver, en direct | `USE CATALOG` + `SELECT` pour le SP de l'App |
| `mirror` | `erp_base_article`, `erp_bom`, `erp_ecart_backflush`, `erp_mouvements`, `erp_stock_snapshot` (Lakebase) | que le job de synchronisation ait tourné |

En `uc`, deux catalogues se demandent : celui du référentiel — qui porte aussi
les mouvements — et celui du backflush. Deux grants, potentiellement deux
propriétaires.

**La voie recommandée est le notebook**, `jobs/sync_erp_mirror_notebook.py` :
importez-le dans le workspace (*Workspace → Import → File*), renseignez les
widgets, « Exécuter tout », puis planifiez-le depuis l'interface. Il obtient son
jeton du contexte de session et ne dépend donc pas de la version du SDK — que le
runtime serverless fige en deçà de l'API Lakebase (voir plus bas). Un seul
widget demande une valeur qui ne se devine pas, `pg_host` : console Lakebase → le
projet → l'endpoint en écriture, ou le `PGHOST` de l'App dans son onglet
*Environment*.

L'écart backflush est copié par le même notebook, sous deux widgets :
`sync_backflush` (`non` pour ne copier que le référentiel) et `backflush_since`,
le lundi ISO à partir duquel copier. La table de faits est à la maille semaine
et grossit indéfiniment, d'où la borne. La dernière cellule affiche les semaines
effectivement couvertes — c'est la réponse à la seule question que pose l'écran
*Backflush* quand il n'affiche rien : une période d'inventaire hors de cet
intervalle ne renverra jamais de ligne.

Les **mouvements de stock** se copient sous deux widgets seulement :
`sync_movements` et `movements_since`. La table est déjà agrégée et filtrée à la
source — plus d'entité juridique, plus d'emplacement de rebut, plus de
déduplication à paramétrer. Elle remonte à janvier 2022 et grossit d'un jour par
jour, d'où la borne ; la maille référence × jour se retaille ensuite sur
n'importe quelle période d'inventaire. La dernière cellule affiche l'intervalle
couvert et le total de chacun des six flux.

Le **snapshot de stock physique** se copie sous deux widgets : `sync_stock` et
`stock_days`. La source est partitionnée par jour et en garde tout l'historique ;
le job en copie les **sept dernières photos publiées** — des jours publiés, pas
des jours de calendrier, puisque la source ne publie pas le week-end. C'est ce
qui alimente la liste « Photo du » de l'écran *Stock ERP* : n'en copier qu'une,
comme le faisait la première version, réduisait cette liste à la seule journée
la plus récente, c'est-à-dire à celle que le choix existe pour ne pas subir. Un
inventaire étalé sur plus d'une semaine demande d'élever `stock_days`. La
dernière cellule affiche le nombre de photos, la plus récente et le total de
lignes.

```bash
# 1. l'App d'abord : la migration 006 s'applique à son démarrage et ouvre
#    l'écriture du miroir à l'identité qui synchronise
databricks apps deploy -t prod --profile PROD

# 2. exécuter le notebook depuis l'interface, puis basculer l'App sur le miroir
databricks apps deploy -t prod --profile PROD --var=erp_source=mirror
```

Le job `inventory_sync_erp_mirror` du bundle fait la même chose en ligne de
commande ; sa planification est en pause, faute de quoi il échouerait chaque
nuit tant que le SDK du runtime n'expose pas l'API Lakebase.

Le job est planifié à 4 h 30 (Europe/Paris) : un référentiel n'est pas un flux
temps réel, et la copie doit être en place **avant** la journée de comptage.
Le bouton « Lire depuis l'ERP » ne change ni de place ni de comportement ; il
affiche à côté de lui la date de la copie, et la signale en orange au-delà de
sept jours. L'historique d'import enregistre « … (miroir du JJ/MM/AAAA) » plutôt
que la table seule : une campagne chargée sur une copie de trois semaines doit
rester lisible six mois plus tard.

Si le job échoue en cours de route, le miroir précédent reste intact — le
remplacement se fait dans une seule transaction. Et un ERP qui ne renvoie aucun
article n'écrase rien : le job s'arrête en erreur.

**Deux identités, et c'est le principe même.** L'application ne peut pas lire
l'ERP ; le job le peut. Il tourne donc sous une autre identité qu'elle, ce qui a
deux conséquences côté Lakebase, à régler une fois :

1. **Le job doit pouvoir se connecter.** Contrairement à une App, un job ne
   reçoit aucune variable `PG*` : il déduit l'endpoint de `--branch`, passé par
   le bundle, puis mint un credential pour l'identité qui l'exécute. Il faut donc
   que cette identité ait un rôle dans la base (console Lakebase → le projet →
   *Roles*). Sinon : `FATAL: role "…" does not exist`, et le job dit quoi faire.
2. **Le job doit pouvoir écrire.** Les tables du miroir appartiennent au service
   principal de l'App, qui les a créées ; dans PostgreSQL, seul le propriétaire
   accorde des droits dessus. La migration `006` le fait, à l'endroit unique où
   l'application parle en tant que propriétaire. Elle s'applique au démarrage de
   l'App — donc **redéployez l'App avant de relancer le job**.

Le grant de la migration 006 porte sur les deux tables du miroir et sur elles
seules : l'identité de synchronisation n'accède ni aux campagnes, ni aux
comptages, ni à l'audit — vérifié, `permission denied for table campaign`.

À défaut, le job accepte `PGHOST` / `PGUSER` / `PGPASSWORD` depuis un secret
scope, ou `--pg-user` pour un rôle Postgres dédié.

**Si la découverte de l'endpoint est refusée** (« Impossible de lister les
endpoints de … »), la même commande depuis votre poste tranche en dix secondes,
puisqu'elle emprunte la même identité que le job :

```bash
databricks postgres list-projects --profile PROD
databricks postgres list-endpoints projects/inventaire/branches/production --profile PROD
```

- Elle répond → l'identité a bien l'accès ; c'est un refus côté job, et la cause
  exacte figure dans son journal.
- Elle échoue → c'est l'accès au projet Lakebase, ou le chemin de branche.
  Corrigez `lakebase_project` / `lakebase_branch`, ou contournez avec
  `--pg-host`, lu dans la console Lakebase.

**Le SDK d'un job ne se choisit pas.** `w.postgres` (API Lakebase Autoscaling)
n'existe qu'à partir de `databricks-sdk` 0.81, mais `databricks-sdk` figure dans
`immutable-package-constraints.txt` du runtime serverless : en déclarer une autre
version fait échouer l'installation de **tout** l'environnement, et le job ne
démarre pas — l'environnement n'accepte d'ailleurs que des versions exactes,
jamais de bornes. Le runtime de ce workspace apporte la 0.49, et la publication
s'est arrêtée là au premier lancement.

Ce que la 0.81 apporte n'est pourtant pas un accès privilégié : ce sont deux
appels HTTP, `GET /api/2.0/postgres/{branche}/endpoints` et
`POST /api/2.0/postgres/credentials`. Le client HTTP du SDK les émet depuis
n'importe quelle version, avec la même authentification — c'est ce que le job
fait quand la façade typée manque. La découverte ne dépend donc plus de la
version :

| Ce que le SDK offre | Hôte | Mot de passe |
|---|---|---|
| `w.postgres` (≥ 0.81) | déduit de `--branch` | credential dédié à l'endpoint |
| version plus ancienne | déduit de `--branch`, en appelant l'API en direct | credential dédié à l'endpoint |
| ni l'un ni l'autre | **`--pg-host` requis** | jeton OAuth, ou `PGPASSWORD` d'un secret scope |

L'hôte reste relevable à la main dans la console Lakebase (le projet →
l'endpoint en écriture) en dernier recours ; il ne change pas, et c'est la même
valeur que le `PGHOST` de l'App.

Le job journalise la version du SDK qu'il utilise et reporte la cause exacte de
chaque refus : ces trois pannes se ressemblaient à l'écran.

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
      {"name": "postgres",         "postgres":         {"branch": "projects/inventaire/branches/production",
                                                        "database": "projects/inventaire/branches/production/databases/databricks-postgres",
                                                        "permission": "CAN_CONNECT_AND_CREATE"}}
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
`"source": "databricks-apps"`.

Si la réponse est un **401** (`« Identité absente. Cette application doit être
atteinte via le proxy d'authentification Databricks »`), l'application est
joignable sans passer par le proxy. Vérifiez la configuration réseau du
workspace avant toute autre chose : dans cet état, chaque écriture serait
attribuée à quelqu'un que personne n'a authentifié.

L'application inventait auparavant une identité générique dans ce cas et
laissait passer. Les campagnes créées ainsi portent un propriétaire que
personne ne peut identifier, et la barrière d'identité — propriétaire ou
gestionnaire déclaré — ne protégeait alors rien. Le refus a remplacé
l'invention : mieux vaut une application injoignable qu'une piste d'audit qui
ment.

### 7.3 Première campagne

1. **Campagnes → Nouvelle campagne** : code, libellé, date de comptage.
2. **Référentiels & seuils → Articles** : charger l'export articles.
3. **Nomenclatures** : charger la BOM effective, vérifier l'onglet *Santé des
   nomenclatures* (cycles, assemblages sans structure).
4. **Paramètres** : accepter ou non les formules dans les comptages, puis
   ajuster les seuils de matérialité par type d'article.
5. **GENERIQUE → Créer une zone** pour chaque aire physique, puis saisir la
   liste d'articles pré-imprimée de chaque zone.
6. **Imprimer toutes les feuilles n°1** la veille de l'inventaire.
7. Passer en **Comptage** — les référentiels sont alors gelés.

Le détail fonctionnel est dans [`04-guide-utilisateur.md`](04-guide-utilisateur.md).

### 7.4 Le volume des pièces justificatives

Chaque fichier chargé et chaque feuille scannée est déposé dans un volume Unity
Catalog, et son chemin enregistré à côté de ce qu'il a produit. C'est ce qui
permet, six mois plus tard, de rouvrir la feuille manuscrite derrière un écart
signé — le conteneur de l'application étant éphémère, le fichier n'y survit pas
à la requête qui l'a reçu.

Le volume se crée une fois, dans le schéma de l'application :

`make uc` crée le volume, mais **pas le droit** : un volume créé depuis un
poste appartient à l'identité qui l'a créé, jamais à l'application. Le grant se
pose une fois, avec l'identifiant du service principal de l'app :

```bash
# L'identifiant à mettre dans le GRANT
databricks apps get campagnes-inventaire --profile PROD  # → service_principal_client_id
```

```sql
CREATE VOLUME IF NOT EXISTS emotors_data_champions.inventory.inventory_evidence;

-- Les trois, et dans cet ordre. Unity Catalog traverse la hiérarchie :
-- accorder le dernier seul ne donne rien, et le refus qui suit nomme le
-- maillon manquant, pas celui qu'on vient d'accorder.
GRANT USE CATALOG ON CATALOG emotors_data_champions
  TO `<service_principal_client_id>`;
GRANT USE SCHEMA ON SCHEMA emotors_data_champions.inventory
  TO `<service_principal_client_id>`;
GRANT READ VOLUME, WRITE VOLUME
  ON VOLUME emotors_data_champions.inventory.inventory_evidence
  TO `<service_principal_client_id>`;
```

> **« C'est pourtant le même catalogue que les tables. »** Oui — mais pas la
> même identité. Les tables sont créées par `make uc` et écrites par le job de
> publication, tous deux sous l'identité qui lance la commande. Le volume est
> écrit par l'**application**, sous son service principal. Un droit sur l'un ne
> dit rien de l'autre.

> **Si le premier GRANT est refusé** — `PERMISSION_DENIED: User does not have
> MANAGE on Catalog` — c'est que vous n'êtes pas propriétaire du catalogue.
> Seuls `USE SCHEMA` et les droits sur le volume sont alors à votre portée : le
> `USE CATALOG` doit être posé par le propriétaire du catalogue, que
> `DESCRIBE CATALOG EXTENDED emotors_data_champions` nomme. C'est le cas
> ordinaire d'un catalogue partagé, où le projet n'a le droit que d'ajouter des
> schémas. **Quand ce propriétaire est hors d'atteinte, voir §7.4 bis.**

**Vérifier sans attendre le jour de l'inventaire :**

```bash
curl -s https://<app>/api/health/evidence | jq
# {"ok": true, "configured": true, "path": "/Volumes/.../_diagnostic/ecriture.probe"}
```

La sonde dépose un octet dans le volume et le retire. `evidenceConfigured` du
diagnostic complet ne lit que la configuration : elle répond « oui » à un
conteneur dont le service principal n'a aucun droit, et la panne n'apparaît
alors qu'au premier scan — sur une feuille manuscrite déjà repartie à l'atelier.

Sans lui, le scan d'une feuille échoue — et il **doit** échouer : le papier
repart dans l'atelier, et écrire des quantités dont l'image n'a pas été
archivée fabriquerait un comptage invérifiable. Le refus nomme désormais le
droit, le principal et le chemin visé.

Il s'organise par campagne, puis par nature de pièce :

```
/Volumes/<catalogue>/<schéma>/<volume>/
  INV-2026-T3/
    items/       20260901T063015-3016ef88-articles.xlsx
    book_stock/  20260901T071140-a71c0e42-stock-erp.csv
    scans/       20260902T081205-9d4b1f07-releve-atelier.pdf
```

L'horodatage précède le nom pour que l'ordre alphabétique du dossier soit
l'ordre chronologique, qui est celui dans lequel on cherche.

**Le fragment hexadécimal est l'empreinte du contenu.** Le chemin ne portait
auparavant que l'horodatage à la seconde et le nom du fichier, et le dépôt se
faisait en écrasement. Deux scans nommés `scan.pdf` déposés dans la même
seconde — deux feuilles envoyées ensemble, un re-scan après correction —
écrivaient au même endroit : le second effaçait le premier, et la feuille dont
la base gardait le chemin pointait alors sur l'image d'une autre. Rien ne le
signalait, et un contrôle six mois plus tard aurait relu la mauvaise pièce.

Deux contenus différents ne peuvent plus se retrouver au même chemin ; deux
dépôts du **même** fichier convergent vers le même, ce qui est le comportement
voulu. Plus rien n'est déposé en écrasement.

Les feuilles scannées conservent en outre l'empreinte, la taille et le type du
fichier lu (`count_sheet.evidence_sha256`, `evidence_bytes`, `evidence_mime`,
migration 019). Le chemin dit *où* ; l'empreinte dit *lequel* — un volume se
modifie depuis l'espace de travail, et c'est la seule façon de répondre
autrement que par la confiance. Les feuilles scannées avant la migration 019
gardent leur chemin sans empreinte : les remplir après coup reviendrait à
affirmer que le fichier présent aujourd'hui est bien l'original, ce que ces
colonnes existent précisément pour ne plus avoir à supposer.

**L'archivage fait échouer ce qu'il accompagne — ou non, selon la pièce.**

*Un chargement de fichier* aboutit même si l'archivage échoue : volume absent,
droit manquant, API indisponible, la pièce n'est pas déposée et
l'avertissement part dans les journaux. L'écran affiche alors le nom du fichier
en texte simple au lieu d'un lien — « pas de pièce » se voit, ce qui vaut mieux
qu'un import de deux cent mille lignes refusé, et l'export se relit dans l'ERP.

*Un scan de feuille* est refusé. Le papier manuscrit repart dans l'atelier et
finit à la benne ; écrire les quantités que le modèle y a lues en sachant que
l'image n'a pas été archivée fabriquerait un comptage que personne ne pourra
jamais vérifier. Si le volume n'est pas configuré, la lecture de scans est donc
indisponible et le dit, au lieu de produire des chiffres sans pièce.

`<url-de-lapp>/api/health` répond `"evidenceConfigured": true` quand les trois
noms qui composent le chemin sont renseignés — ou, en mode `lakebase`, quand la
base est configurée — et `"evidenceStore"` dit laquelle des deux archives reçoit
les pièces. Les droits, eux, ne se vérifient qu'au premier dépôt : c'est ce que
`/api/health/evidence` fait à la demande.

Ni les collages ni les lectures ERP ne produisent de pièce : le texte collé est
déjà dans les lignes chargées, et une lecture ERP se rejoue par sa requête, que
l'historique des imports nomme.

### 7.4 bis — Quand le `USE CATALOG` du volume ne peut pas être obtenu

`GRANT USE CATALOG` exige `MANAGE` sur le catalogue, c'est-à-dire d'en être
propriétaire. Sur un catalogue partagé, ce propriétaire peut être injoignable —
et l'inventaire garde sa date. `INV_EVIDENCE_STORE=lakebase` renverse alors la
contrainte : les pièces sont archivées dans la base de l'application, qu'elle
possède et où elle écrit déjà tout le reste. **Aucun administrateur n'est
impliqué.** C'est le même renversement que `INV_ERP_SOURCE=mirror` (§8.3 bis),
appliqué aux pièces plutôt qu'aux lignes.

| `INV_EVIDENCE_STORE` | Où la pièce est écrite | Ce qu'il faut |
|---|---|---|
| `volume` (défaut) | le volume Unity Catalog | les trois GRANT du §7.4, dont un du propriétaire du catalogue |
| `lakebase` | `evidence_blob`, dans le schéma de l'application (migration 022) | rien : l'application possède ce schéma |

```bash
databricks apps deploy -t prod --profile PROD --var=evidence_store=lakebase
curl -s https://<app>/api/health/evidence | jq
# {"ok": true, "configured": true, "path": "lakebase:/_diagnostic/ecriture.probe"}
```

La migration 022 s'applique au démarrage de l'app ; `/api/health` en donne la
liste sous `migrations.applied`.

**La garantie ne change pas.** Un scan est archivé avant que ses quantités
soient écrites, ou l'opération est refusée — c'est le point, et une archive de
secours qui le perdrait ne serait pas une solution mais un renoncement. Ce qui
change tient en deux lignes :

- **Ce qu'on y perd.** Un volume se parcourt depuis l'espace de travail ; la
  table ne se lit qu'à travers l'application ou en SQL. Qui peut obtenir les
  trois GRANT a intérêt à les obtenir.
- **Ce qu'on n'y perd pas.** Le chemin garde la même forme —
  `lakebase:/<campagne>/<nature>/<horodatage>-<empreinte>-<nom>` — donc un
  `SELECT path FROM inventory.evidence_blob` reste lisible, et les pièces
  pourront être ressorties vers le volume le jour où le grant arrive.

**Basculer est sûr dans les deux sens.** La relecture s'aiguille sur le chemin
enregistré, jamais sur le réglage du jour : ce qui est déjà dans le volume y
reste lisible après la bascule, et ce qui est en base le reste après le retour.
Rien n'est déplacé, rien n'est perdu.

Une pièce archivée en base compte dans la taille de la base Lakebase, à hauteur
de ce qu'elle pèse — quelques centaines de kilo-octets par feuille scannée, la
taille du fichier pour un export ERP, plafonnée par `INV_MAX_UPLOAD_BYTES`
(64 Mo par défaut).

---

## 8. Dépannage

| Symptôme | Cause probable | Correction |
|---|---|---|
| **502 Bad Gateway** | L'app n'écoute pas sur `DATABRICKS_APP_PORT`, ou sur `localhost` | La commande est `python main.py` ; `main.py` lit `DATABRICKS_APP_PORT` et se lie à `0.0.0.0`. Vérifiez la ligne `Uvicorn running on http://0.0.0.0:<port>` dans les logs |
| `ModuleNotFoundError` au démarrage | Dépendance absente de `app/requirements.txt` | Ajoutez-la et redéployez ; aucun paquet système n'est installable |
| `/api/health` → `ready: false` | Lakebase non attaché ou permissions manquantes | Vérifiez la ressource `postgres` et `CAN_CONNECT_AND_CREATE` |
| `Database instance '<hôte>.database....cloud.databricks.com' not found` | Appel de l'API du palier *provisionné* avec un nom d'hôte | Corrigé : le credential est désormais émis contre le chemin de ressource de l'endpoint (§2.3). Vérifiez que `INV_LAKEBASE_BRANCH` figure bien dans les variables de l'app |
| `Impossible de lister les endpoints de « projects/... »` | `CAN_CONNECT_AND_CREATE` sur la base n'autorise pas à énumérer les endpoints du projet | Donnez au service principal l'accès au projet Lakebase, ou court-circuitez la recherche en fixant `INV_LAKEBASE_ENDPOINT` (relevé par `databricks postgres list-endpoints projects/<projet>/branches/<branche>`) |
| `Lakebase n'est pas configuré` | `PGHOST` / `PGDATABASE` / `PGUSER` absents | La ressource `postgres` n'est pas attachée à l'app |
| `Database instance <nom> does not exist` | Ressource déclarée avec l'ancienne clé `database` | Utilisez la clé `postgres` avec des chemins de ressource complets (§2.3) |
| `unknown field: branch` au `bundle validate` | Idem — clé `database` au lieu de `postgres` | Voir §2.3 |
| `Use 'value_from' instead of 'valueFrom'` | `databricks.yml` attend le snake_case | `value_from` dans `databricks.yml`, `valueFrom` dans `app.yaml` |
| `path ... is not contained in sync root path` | Chemin de fichier remontant au-dessus de la racine du bundle | Les chemins de `databricks.yml` sont relatifs à son propre répertoire : `./jobs/...`, jamais `../jobs/...` |
| `cycle detected in field resolution: variables.X.default -> var.X -> var.X` | Une cible redéclare `X: ${var.X}` | Supprimez l'override : une variable ne peut pas se référencer elle-même (§4.1) |
| `no value assigned to required variable warehouse_id` au `deploy` alors que le `validate` passait | `--var` n'a été passé qu'au `validate` | Répétez `--var` sur chaque commande, ou utilisez `variable-overrides.json` (§4.1) |
| `Invalid SQL warehouse resource sql-warehouse: ID  is invalid` (400) | Valeur **vide** transmise à `warehouse_id` : la variable du shell référencée par `--var` n'était pas définie | Le `validate` ne détecte pas ce cas. Vérifiez la variable, ou passez au fichier d'override / au `lookup` par nom (§4.1) |
| `invalid dependency "${DATABRICKS_APP_PORT}", no such node ""` | `${...}` est aussi la syntaxe d'interpolation du bundle : le résolveur cherche ce nom dans l'arbre du bundle | Ne mettez aucune variable d'exécution dans `config.command`. La commande est `python main.py`, et `main.py` lit le port depuis l'environnement. Non détecté par `validate` |
| `Error installing packages. Please check /logz for more details` | Conflit de dépendances dans `app/requirements.txt` — le message de l'app ne dit pas lequel | Reproduisez-le localement, où pip nomme le coupable : `pip install --dry-run -r app/requirements.txt` |
| Page « L'API fonctionne, l'interface n'a pas été construite » (503), ou `{"detail":"Not Found"}` sur les versions antérieures | `app/static/` absent du déploiement | Deux causes distinctes : la SPA n'a pas été construite, **ou** elle l'a été mais le bundle ne la téléverse pas (`.gitignore` s'applique à la synchronisation — d'où le bloc `sync.include` de `databricks.yml`). Contrôlez l'un avec `dir app\static\index.html`, l'autre avec `databricks bundle sync --dry-run --full -o json` (§4.2) |
| **Le déploiement réussit, l'app démarre, et c'est la version précédente qui s'affiche** | Trois causes possibles, à départager par `curl` (voir §8.3) | Comparez `frontend.bundle` de `/api/health` avec le nom du fichier présent dans `app/static/assets/` après un build local. Identiques → c'est le cache du navigateur (`Ctrl`+`Maj`+`R`). Différents → le téléversement n'a pas emporté la nouvelle interface : construisez **avant** de déployer, et vérifiez que `databricks apps deploy` est bien lancé avec `-t <cible>` (sans cible, la CLI redéploie ce qui se trouve déjà dans le workspace, donc l'ancienne version) |
| `password authentication failed` après ~1 h | Jeton Lakebase expiré | Normalement géré automatiquement ; si cela persiste, redémarrez l'app et ouvrez un ticket |
| **404 sur toutes les pages sauf `/api/...`** | SPA non construite | `make build-frontend` puis redéployez ; `app/static/index.html` doit exister |
| **504 après 2 minutes, rien dans les journaux** | Requête dépassant les 120 s du proxy | Réduisez le volume importé par lot, ou augmentez la taille de compute |
| `Client de modèle indisponible` | Endpoint LLM non attaché ou sans `CAN_QUERY` | Attachez la ressource `serving-endpoint` |
| `La pièce justificative n'a pas pu être archivée` au scan d'une feuille | Le dépôt dans le volume a été refusé. Le plus souvent : le service principal de l'app n'a pas `WRITE VOLUME` — `make uc` crée le volume, pas le droit | Le message nomme la cause, le principal et le chemin. Pour un droit manquant, voir §7.4 ; pour un volume absent, rejouez `make uc`. L'échec est volontairement bloquant : sans l'image, la quantité lue n'a plus rien derrière elle |
| `PERMISSION_DENIED … USE CATALOG` au dépôt, « pourtant c'est le même catalogue que les tables » | Même catalogue, autre identité : les tables sont écrites par le job sous l'identité qui le lance, le volume par l'application sous son service principal. Et `WRITE VOLUME` seul ne suffit jamais — Unity Catalog traverse `USE CATALOG` → `USE SCHEMA` → `WRITE VOLUME` | Posez les **trois** grants (§7.4). Le message d'erreur les écrit désormais copiables tels quels, catalogue, schéma et volume tirés du chemin visé |
| `PERMISSION_DENIED: User does not have MANAGE on Catalog` en posant le GRANT | Vous n'êtes pas propriétaire du catalogue — cas ordinaire d'un catalogue partagé | `USE SCHEMA` et les droits du volume restent à votre portée ; le `USE CATALOG` doit être posé par le propriétaire, que `DESCRIBE CATALOG EXTENDED <catalogue>` nomme. **Propriétaire injoignable : `INV_EVIDENCE_STORE=lakebase` (§7.4 bis) archive dans la base de l'application, sans aucun grant** |
| `AttributeError : 'ImportService' object has no attribute 'check_duplicate'` sur **tout** chargement de fichier | Le découpage des services a déplacé la méthode vers `ImportBatches` ; le routeur, lui, appelait toujours le service. Les six grilles échouaient en 500, la détection de doublon étant faite avant l'import | Corrigé : une façade d'une ligne, comme `parse` et `preview`. Deux mille contrôles passaient malgré tout, parce qu'ils appellent les importeurs directement — `tests/test_router_service_seam.py` vérifie désormais que ce qu'un routeur appelle sur un service existe bel et bien |
| `AttributeError : 'bytes' object has no attribute 'seekable'` au dépôt | `files.upload` déclare `contents: BinaryIO` ; le SDK appelle `seekable()` dessus pour savoir s'il peut rejouer la requête | Corrigé : la charge utile part en flux. **Aucune pièce n'avait jamais été archivée** — en silence pour les imports, `storage_path` restant nul. Les pièces des campagnes antérieures sont définitivement perdues, le conteneur étant éphémère |
| `relation "campaign" does not exist` | Migrations non appliquées | Consultez les journaux de démarrage ; le rôle doit avoir `CREATE` sur le schéma |
| `La migration 001 a été modifiée après application` | Un fichier de migration déjà appliqué a été édité | Restaurez le fichier ; créez une **nouvelle** migration |
| L'app démarre puis s'arrête | Dépassement des 10 min de démarrage | Épinglez les versions, réduisez les dépendances |
| **Pas d'espace disque** pendant le build | Quota du conteneur atteint | Supprimez `frontend/node_modules` et les caches, relancez |
| `NameError: name '__file__' is not defined` au lancement d'un job | Le calcul serverless n'*importe* pas le fichier : il l'exécute par `exec(compile(source, chemin, "exec"))`, dans un espace de noms où ce global n'existe pas | Corrigé : les deux jobs déduisent leur répertoire du chemin de compilation (`co_filename`), et ne l'ajoutent au chemin d'import qu'après avoir vérifié que `lakebase.py` s'y trouve |
| `Hôte Lakebase inconnu, et le SDK … ne connaît pas l'API Lakebase Autoscaling` | Le runtime serverless fige `databricks-sdk` (0.49 ici) ; `w.postgres` n'apparaît qu'en 0.81 et la version ne peut pas être relevée | Corrigé : le job appelle l'API Lakebase en direct (`GET /api/2.0/postgres/{branche}/endpoints`) quand la façade typée manque. La découverte ne dépend plus de la version du SDK ; `--pg-host` reste le dernier recours |
| `[CAST_INVALID_INPUT] The value 'count_date' … cannot be cast to "DATE"` — la valeur est le **nom** de la colonne | La connexion est ouverte en `row_factory=dict_row` ; `fetch` rezippait ces dictionnaires avec les noms de colonnes, et itérer un dictionnaire rend ses clés | Corrigé : `fetch` rend les lignes telles quelles quand elles sont déjà des dictionnaires. Sur un schéma entièrement textuel, le défaut aurait publié une archive de noms de colonnes en se déclarant réussie |
| `CANNOT_DETERMINE_TYPE` à la publication d'une table | Une colonne vide sur **toutes** les lignes n'a pas de type déductible, et Spark refuse le DataFrame entier — cas ordinaire : une campagne en comptage n'a pas de date de clôture | Corrigé : ces colonnes sont retirées avant la construction et remises avec le type de la table. La valeur écrite reste NULL, mais typée |
| `TABLE_OR_VIEW_NOT_FOUND … inventory.publication` à la publication | Le schéma Unity Catalog du workspace est antérieur à la table demandée : `make uc` a été joué avant qu'elle n'entre dans `sql/00_unity_catalog.sql` | Rejouez `make uc WAREHOUSE_ID=<id> PROFILE=<profil>`. Le script est en `CREATE TABLE IF NOT EXISTS` : seules les tables manquantes sont créées. Le job vérifie désormais les dix tables **avant** d'écrire quoi que ce soit, et les nomme toutes d'un coup |
| `unknown command "sql" for "databricks"` — « Did you mean this? psql » | `databricks sql query` n'existe pas ; c'est pourtant ce que donnaient le README, le Makefile et l'en-tête du fichier SQL | Corrigé : `make uc` passe par `scripts/apply_unity_catalog.py`, qui découpe le fichier et l'exécute instruction par instruction avec le catalogue et le schéma courants |
| `SystemExit: 0` et la tâche marquée FAILED | Le calcul serverless exécute le fichier dans un espace de noms ipykernel : un `SystemExit(0)` n'y est pas une sortie de processus mais une exception que le noyau remonte | Corrigé : les jobs ne lèvent que sur un code non nul. **Le travail avait réussi** — vérifiez `inventory.publication` avant de republier (rejouer reste sans risque) |
| La synchronisation du miroir « réussit » sans rien copier | `sync_erp_mirror.py` n'avait aucun `if __name__ == "__main__"` : lancé comme tâche, il définissait `main` et s'arrêtait là | Corrigé : le point d'entrée manquant est en place. Un contrôle l'exige désormais sur les deux jobs |
| `ModuleNotFoundError: No module named 'databricks'` sur `make uc` | Le SDK n'est pas dans l'interpréteur qui lance le script | `python -m pip install databricks-sdk`, ou `make install` pour toutes les dépendances du projet. Le script le dit maintenant au lieu de laisser passer la trace |

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

### 8.2 Quelle version est réellement déployée ?

« J'ai redéployé et rien n'a changé » a trois causes qui, depuis un navigateur,
sont indiscernables. Une seule commande les départage :

```bash
curl -s https://<app>.databricksapps.com/api/health | jq '.frontend, .migrations'
```

```json
{ "bundle": "index-goTc8FJP.js", "builtAt": "2026-08-19T00:05:29Z", "assets": 6 }
{ "applied": ["001_initial_schema", "…", "009_stock_flow"], "pending": [], "error": null }
```

`bundle` est le nom du paquet JavaScript que le conteneur sert. Vite le dérive
du *contenu* : deux constructions des mêmes sources donnent le même nom, et le
moindre changement en donne un autre. Comparez-le au fichier présent dans
`app/static/assets/` après un `make build-frontend` local.

| Constat | Cause | Correction |
|---|---|---|
| Le nom est le même que localement | Le déploiement est bon ; c'est le navigateur | `Ctrl`+`Maj`+`R`. Ne devrait plus se produire : la coquille est désormais servie en `no-cache` |
| Le nom diffère | Le téléversement n'a pas emporté la nouvelle interface | Construisez avant de déployer (`make deploy` enchaîne les deux), et déployez avec `-t <cible>` |
| `bundle: null` | `app/static/` est absent du déploiement | Voir la ligne « Page L'API fonctionne… » du tableau ci-dessus |
| `migrations.pending` non vide | Le code est à jour, le schéma non | Consultez les journaux de démarrage : le rôle doit avoir `CREATE` sur le schéma |

> **Sans cible, `databricks apps deploy` n'est pas la commande du bundle.**
> `databricks apps deploy <nom> --source-code-path <chemin workspace>` redéploie
> ce qui se trouve **déjà** dans le workspace — c'est-à-dire la version
> précédente. Seul `databricks apps deploy -t <cible>` synchronise d'abord les
> fichiers locaux. C'est ce que fait `make deploy`.

### 8.3 Se connecter à Lakebase pour inspecter

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

L'ordre compte : `apps deploy` téléverse ce qui est sur le disque au moment où
il tourne, donc la construction vient **avant**. `make deploy TARGET=prod
PROFILE=PROD` enchaîne les deux et ne peut pas être fait dans le mauvais ordre.

Une fois l'app redémarrée, vérifiez que c'est bien la nouvelle version qui
répond — un déploiement réussi qui sert l'ancienne interface est un cas réel, et
le navigateur ne le dit pas (§8.2) :

```bash
curl -s https://<app>.databricksapps.com/api/health | jq '.frontend.bundle'
ls app/static/assets/index-*.js        # le même nom, sinon voir §8.2
```

Les nouvelles migrations SQL s'appliquent automatiquement au redémarrage. Elles
sont **en avant uniquement** : il n'y a pas de migration descendante, parce
qu'annuler un schéma dans un système dont la promesse est un journal d'audit
immuable n'est pas une garantie qu'on peut offrir. Pour revenir en arrière,
redéployez la version précédente du code — le schéma reste compatible tant
qu'aucune colonne n'a été supprimée.

> **La migration 014 écrit des données, pas seulement du schéma.** Elle affecte
> `younes.elhachi1@emotors.com` aux campagnes dont `created_by` est vide. Depuis
> que l'écriture suppose d'être propriétaire ou gestionnaire déclaré, une
> campagne sans propriétaire n'est modifiable par personne — pas même par celui
> qui l'a créée. Elle ne touche aucune campagne qui a déjà un auteur, et une
> seconde exécution ne trouve plus rien à corriger. Pour vérifier avant ou après
> le déploiement :
>
> ```sql
> SELECT code, created_by FROM inventory.campaign
>  WHERE coalesce(btrim(created_by), '') = '';
> ```

### 9.1 Sauvegarde avant une mise à jour majeure

```bash
# Publier toutes les campagnes ouvertes vers Delta (archive)
for code in $(databricks api post /api/2.0/sql/statements --profile PROD --json '{
    "warehouse_id": "'"$WAREHOUSE_ID"'",
    "statement": "SELECT code FROM emotors_data_champions.inventory.campaign",
    "wait_timeout": "30s"
  }' | jq -r '.result.data_array[][0]'); do
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
