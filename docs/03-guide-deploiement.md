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
make test      # 204 tests, ~1 s, aucune base requise
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
| `INV_ERP_SCHEMA` | `emotors_data_champions.silver_erp_ye` |
| `INV_ERP_ITEMS_TABLE` | `silver_base_article` |
| `INV_ERP_BOM_TABLE` | `silver_bom` |

`INV_ASSISTANT_PROFILE` décide de ce que l'assistant de campagne reçoit et de
ce qu'on lui demande. Un seul profil est livré — `etendu` : le dossier complet
de la campagne, un raisonnement libre, des chiffres qui restent ceux du dossier.
La variable existe pour qu'en ajouter un autre, plus restreint pour un public
plus large par exemple, soit un redémarrage et non une livraison de code.

Les trois variables `INV_ERP_*` désignent les tables silver lues par
« Lire depuis l'ERP » sur les grilles Articles et Nomenclatures. La lecture
emprunte l'entrepôt SQL attaché (`DATABRICKS_WAREHOUSE_ID`) et les droits Unity
Catalog de l'application : sans entrepôt ou sans `SELECT` sur ces tables,
l'option apparaît désactivée avec sa raison, et le chargement par fichier reste
disponible.

### 8.3 bis — Quand le catalogue de l'ERP n'est pas ouvrable à l'application

La lecture directe suppose que le **service principal de l'App** — pas vous —
ait `USE CATALOG` sur le catalogue de l'ERP, puis `USE SCHEMA` et `SELECT`.
Sans quoi le chargement échoue en nommant la commande à faire exécuter :

```sql
GRANT USE CATALOG ON CATALOG emotors_data_champions              TO `<sp-de-l-app>`;
GRANT USE SCHEMA  ON SCHEMA  emotors_data_champions.silver_erp_ye TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.silver_base_article TO `<sp-de-l-app>`;
GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.silver_bom          TO `<sp-de-l-app>`;
```

Seul un propriétaire du catalogue peut les passer. Quand aucun n'est joignable
— et un inventaire garde sa date —, `INV_ERP_SOURCE=mirror` renverse la
contrainte : l'application lit une copie locale, dans sa propre base, alimentée
par le job `inventory_sync_erp_mirror` qui tourne, lui, avec une identité ayant
déjà accès à l'ERP.

| `INV_ERP_SOURCE` | Lit | Exige |
|---|---|---|
| `uc` (défaut) | les tables silver, en direct | `USE CATALOG` + `SELECT` pour le SP de l'App |
| `mirror` | `erp_base_article` / `erp_bom` (Lakebase) | que le job de synchronisation ait tourné |

```bash
# 1. déployer le job, puis l'exécuter une première fois
databricks bundle deploy -t prod --profile PROD
databricks bundle run inventory_sync_erp_mirror -t prod --profile PROD

# 2. basculer l'application sur le miroir
databricks apps deploy -t prod --profile PROD --var=erp_source=mirror
```

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

- Elle répond → l'identité a bien l'accès ; c'est l'environnement du job qui
  n'a pas le bon SDK. `w.postgres` n'existe qu'à partir de `databricks-sdk`
  0.81 ; le job l'épingle désormais, redéployez-le.
- Elle échoue → c'est l'accès au projet Lakebase, ou le chemin de branche.
  Corrigez `lakebase_project` / `lakebase_branch`, ou contournez avec
  `--lakebase-endpoint` et `--pg-host`, lus dans la console Lakebase.

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
