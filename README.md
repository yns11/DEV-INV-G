# Campagnes Inventaire

Application Databricks qui remplace le dispositif Excel des inventaires
physiques par un processus outillé, traçable et reproductible : préparation,
comptage, analyse & ajustements, clôture.

```
PRÉPARATION ──────► COMPTAGE ──────► ANALYSE & AJUSTEMENTS ──────► CLÔTURE
 référentiels      stock ERP gelé        journaux gelés            tout gelé
 seuils            journaux + feuilles   ajustements
 zones             consolidation         causes
```

---

## Ce que ça remplace

| Fichier actuel | Remplacé par |
|---|---|
| `Compil GENERIQUE.xlsx` — 54 onglets, 9 requêtes Power Query | Le moteur de consolidation GENERIQUE, avec éclatement BOM tracé et arbitrage outillé |
| `BILAN INVENTAIRE.xlsx` — 13 onglets, 17,6 Mo, `#REF!` en production | Le module d'analyse : écarts recalculés, contrôles, analytics, causes |
| `STOCK AVANT INVENTAIRE.xlsx` | Le snapshot gelé, horodaté et opposable |
| Les copier/coller vers l'ERP | Des exports au format d'import ERP |
| 30 diapositives refaites à la main | Le tableau de bord et la synthèse générée |

L'analyse détaillée de l'existant — avec les défauts constatés sur les fichiers
réels de juin 2026 — est dans [`docs/01-analyse-existant.md`](docs/01-analyse-existant.md).

## Ce que ça apporte

- **Une campagne = un dossier immuable.** Référentiels, snapshot, comptages,
  journaux et analyses sont versionnés ensemble et restent recalculables à
  l'identique des mois plus tard.
- **Rien ne disparaît en silence.** Un assemblage sans nomenclature, une ligne
  d'export corrompue, une case vide : chacun produit un message explicite et une
  résolution, jamais une quantité perdue.
- **Les feuilles se préparent, elles ne s'improvisent pas.** Un fichier
  `[feuille, article, section]` crée les zones et pré-imprime leur liste, sur les
  deux passages. Un article absent du référentiel est une erreur de ligne, jamais
  un article créé par effet de bord — et la règle vaut pour le stock ERP comme
  pour les feuilles, dans les trois modes d'import.
- **La photo du stock se désigne.** Le snapshot ERP est publié chaque jour ;
  c'est celui de la journée de comptage qui fait foi, pas celui du jour où on le
  charge. La campagne dit lequel elle a chargé, et l'historique le garde.
- **Deux comptages, un arbitrage outillé.** Valorisé en euros, couvrant aussi les
  articles comptés par une seule équipe. Le nombre de comptages appartient à la
  zone : le double comptage est la règle, le comptage unique l'exception qu'on
  assume, zone par zone.
- **Lecture pour tous, écriture pour ceux qui la portent.** Une campagne se
  consulte et s'exporte par tout le monde ; elle ne se modifie que par son
  créateur et les neuf gestionnaires qu'il a déclarés. Le contrôle est posé au
  même endroit que le gel des phases, en une seule règle : les deux barrières
  ne peuvent pas diverger.
- **Chacun voit son périmètre, personne n'est cloisonné.** Entrepôts et zones
  s'affectent aux gestionnaires ; l'interrupteur « Mon périmètre » filtre côté
  serveur — ce qu'il exclut n'est jamais envoyé au poste. C'est un filtre, pas
  une habilitation : un gestionnaire garde le droit d'agir hors du sien, ce
  qu'exige la couverture d'un collègue à six heures du matin.
- **Un transfert entre bacs n'est pas une perte.** L'analyse s'ouvre sur l'écart
  par référence et chiffre explicitement la part qui n'est qu'un déplacement.
- **Le WIP est explorable.** Chaque quantité éclatée est traçable jusqu'à
  l'assemblage et la zone qui l'ont produite.
- **L'IA propose, l'humain décide.** Lecture des feuilles scannées, suggestions
  de causes, synthèse — toujours en proposition, jamais en décision.
- **Toute la pile part au scanner.** Cent feuilles, deux cents pages : chaque
  page se rattache à la sienne par l'identifiant imprimé en pied de page, les
  lectures partent en parallèle, et le dépôt rend la main tout de suite — la
  progression s'affiche pendant que les feuilles se remplissent.
- **Chaque action est tracée.** Journal d'audit en ajout seul, protégé au niveau
  du moteur de base de données.

---

## Démarrage rapide

### En local (aucun workspace requis)

```bash
docker run -d --name inv-pg -e POSTGRES_PASSWORD=inventaire \
    -e POSTGRES_DB=inventaire -p 5432:5432 postgres:16

cp .env.example .env
make install
make run                  # http://127.0.0.1:8000
```

### Sur Databricks

```bash
# 1. Créer le schéma, le volume, les tables et les vues Unity Catalog
make uc WAREHOUSE_ID=<id> PROFILE=PROD

# 2. Construire la SPA, valider, déployer et démarrer
make deploy TARGET=prod PROFILE=PROD
```

Le guide complet — quatre variantes de déploiement, provisionnement,
dépannage — est dans
[`docs/03-guide-deploiement.md`](docs/03-guide-deploiement.md).

---

## Structure du dépôt

```
app/                        Charge utile déployée sur Databricks Apps
  main.py                   Point d'entrée uvicorn
  app.yaml                  Manifeste runtime (port, commande, ressources)
  requirements.txt          Dépendances épinglées
  inventory/
    domain/                 Règles métier pures — aucun driver, aucun framework
      enums.py              Vocabulaires contrôlés + pont vers les libellés legacy
      models.py             Entités et invariants
      quantities.py         Arithmétique décimale
      workflow.py           Machines à états et matrice de gel
      bom.py                Index et éclatement des nomenclatures
      consolidation.py      Consolidation GENERIQUE
      variance.py           Écarts, matérialité, KPI
      controls.py           Moteur de contrôles
    db/                     Lakebase : pool, migrations, dépôts SQL
    ingest/                 Contrats de colonnes, parsing, mapping
    ai/                     Lecture des scans, suggestions, synthèse
    analytics/              Features, ABC/XYZ, anomalies, clustering, Benford
    services/              Cas d'usage : garde de phase, transaction, audit
    reporting/              Exports Excel et feuilles imprimables
    api/                    Routers FastAPI, schémas, dépendances
  static/                   SPA construite (généré, non versionné)

frontend/                   React + TypeScript + Vite
  src/design/               Jetons de design, thèmes clair et sombre
  src/components/           Primitives UI, grille éditable, graphiques SVG
  src/features/             Écrans, un par phase du processus
  src/lib/                  Client HTTP typé, formatage français

sql/00_unity_catalog.sql    Schéma, volume, tables Delta et vues analytiques
jobs/                       Job Lakeflow de publication vers Delta
tests/                      2375 contrôles, ~45 s ; 64 exigent un PostgreSQL, ignorés sinon
docs/                       Analyse, architecture, déploiement, guide, Top 20
databricks.yml              Asset Bundle (app + job)
Makefile                    Points d'entrée développeur
```

---

## Documentation

| Document | Contenu |
|---|---|
| [`01-analyse-existant.md`](docs/01-analyse-existant.md) | Le processus Excel actuel, ses points faibles, mesurés sur les fichiers réels |
| [`02-architecture.md`](docs/02-architecture.md) | Couches, choix de stockage, contraintes plateforme, reproductibilité |
| [`03-guide-deploiement.md`](docs/03-guide-deploiement.md) | Déploiement pas à pas : local, CLI, interface graphique, CI/CD, dépannage |
| [`04-guide-utilisateur.md`](docs/04-guide-utilisateur.md) | Le processus vu par l'utilisateur, de la préparation à la clôture |
| [`05-modele-de-donnees.md`](docs/05-modele-de-donnees.md) | Schémas, types, index, définition exacte des indicateurs |
| [`06-top20-ameliorations.md`](docs/06-top20-ameliorations.md) | Revue critique : 20 améliorations priorisées, séquencées |

---

## Développement

```bash
make help            # tous les points d'entrée
make test            # 2375 contrôles, ~45 s ; 64 ignorés sans PostgreSQL
make lint            # ruff + tsc
make check           # les deux
make dev-api         # API avec rechargement, port 8000
make dev-ui          # Vite avec proxy vers l'API, port 5173

npm --prefix frontend run test   # 383 contrôles navigateur (vitest + jsdom)
npm --prefix frontend run e2e    # le parcours complet, Playwright, app démarrée
```

Trois bancs, trois portées. Les contrôles Python tiennent les règles et l'API ;
`vitest` tient le TypeScript — grille, formats, collage — sans démarrer quoi
que ce soit ; le parcours Playwright traverse une campagne de bout en bout dans
un vrai navigateur, contre une vraie base.

La couche `inventory.domain` n'importe **rien** du reste du projet : c'est ce
qui permet de tester l'intégralité des règles métier — éclatement BOM,
consolidation, écarts, contrôles, machine à états — sans base de données ni
workspace, en une fraction de seconde.

---

## Pile technique

| Couche | Choix | Pourquoi |
|---|---|---|
| Hébergement | Databricks Apps | Gouvernance Unity Catalog, authentification intégrée, proximité des données |
| Écritures | Lakebase (PostgreSQL) | Transactionnel, latence milliseconde, verrouillage optimiste |
| Archive & analyse | Delta / Unity Catalog | Requêtes inter-campagnes, partage gouverné, tableaux de bord |
| Backend | FastAPI (Python 3.11) | Le domaine, l'ingestion de fichiers et le machine learning sont en Python |
| Frontend | React 18 + TypeScript + Vite | 109 Ko compressés, aucune dépendance de graphiques |
| IA | Endpoint de serving (modèle vision) | Lecture des feuilles scannées, suggestions de causes |
| ML | scikit-learn, scipy | Non supervisé, graines fixées, reproductible |
