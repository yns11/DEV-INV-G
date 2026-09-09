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
| `Compil GENERIQUE.xlsx` — 54 onglets, 9 requêtes Power Query | Le moteur de consolidation GENERIQUE, avec éclatement BOM tracé et arbitrage outillé — et, pour le jour où l'application ne répond pas, un classeur de repli engendré qui refait le même calcul par formules |
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
  d'export corrompue : chacun produit un message explicite et une résolution,
  jamais une quantité perdue. Et une ligne de feuille laissée vide compte
  **zéro** : elle figure sur le papier parce qu'on s'attend à trouver la
  référence dans la zone, et n'y avoir rien trouvé est un écart à expliquer.
- **Les feuilles se préparent, elles ne s'improvisent pas.** Un fichier
  `[feuille, article, section]` crée les zones et pré-imprime leur liste, sur les
  deux passages. Un article absent du référentiel est une erreur de ligne, jamais
  un article créé par effet de bord — et la règle vaut pour le stock ERP comme
  pour les feuilles, dans les trois modes d'import.
- **La feuille se conçoit comme une page.** On y pose ses intertitres, ses
  lignes vides **et ses références** — prises dans le référentiel, à l'endroit
  choisi, dans la section choisie. Ce qui n'a rien à compter ne s'imprime pas :
  une zone sans en-cours ne sort plus avec un tiers de page consacré à deux
  sections qu'elle n'a pas. Et une zone mal nommée se renomme, au lieu de se
  supprimer et se refaire — ses feuilles, ses comptages et ses arbitrages
  tiennent à son identité, jamais à son code.
- **La feuille nomme les pièces comme l'atelier les nomme.** Les listes qui
  l'alimentent viennent des ateliers, et elles portent leurs désignations. Celle
  qu'on importe ou colle en conception de zone **remplace** celle du référentiel
  — sur la feuille, à l'écran, au papier, à l'arbitrage — et nulle part
  ailleurs : l'écart et l'export continuent de nommer l'article comme l'ERP le
  nomme, faute de quoi plus aucun rapprochement ne serait lisible.
- **La feuille est un document, pas une liste.** Intertitres — « Stock physique
  B6EST », « Stock physique B15 » — et lignes vides se posent en préparation, se
  voient dans l'aperçu avant impression, et se retrouvent à l'identique sur le
  papier et dans le formulaire de saisie. Un même article sous deux intertitres
  est deux comptages, à deux endroits : ce n'est plus refusé comme un doublon.
- **Une grille se reprend d'une campagne à l'autre.** Le référentiel d'un
  trimestre est celui du suivant à quelques lignes près, et les journaux de
  précomptage d'une campagne annulée n'ont aucune raison d'être ressaisis. La
  fenêtre de choix dit ce que chaque campagne porte sur la grille demandée —
  c'est le chiffre qui fait choisir. Les lignes rentrent **au même point** qu'un
  fichier : mêmes refus, même essai à blanc, même grille modifiable ensuite.
- **La photo du stock se désigne.** Le snapshot ERP est publié chaque jour ;
  c'est celui de la journée de comptage qui fait foi, pas celui du jour où on le
  charge. La campagne dit lequel elle a chargé, et l'historique le garde.
- **Deux comptages, un arbitrage outillé.** Les deux passages portent le **même
  document** : une référence retirée, un intertitre renommé, deux lignes
  échangées descendent sur la seconde feuille — sans toucher aux quantités
  qu'elle porte déjà. L'arbitrage est valorisé en euros, couvre les articles
  comptés par une seule équipe, se tranche en lot quand on sait laquelle des
  deux fait foi, et **se refait dès qu'un des deux comptages change** : une
  décision porte sur deux chiffres, et meurt avec eux.
- **Un classeur de repli, et il se recalcule.** L'export du dossier est une
  photo : on la classe, on ne la corrige pas. Un second classeur part avec lui,
  qui porte les *données* — le référentiel, les nomenclatures, une feuille par
  zone — et refait la consolidation GENERIQUE **par formules**. Corriger un
  comptage met à jour le relevé et le journal, sans ressaisie et sans
  l'application. C'est ce qu'était `Compil GENERIQUE.xlsx`, mais engendré à
  chaque export au lieu d'être maintenu à la main — et la recette le fait
  **recalculer pour de vrai** avant de le livrer, puis compare chaque ligne avec
  ce que le moteur produit.
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
- **Compter avant le jour J, sans éclater le dossier.** Certains emplacements se
  précomptent à J-1 ou J-2, en phase de préparation, et leur comptage se scelle.
  **La référence de la campagne reste unique** — le stock ERP du jour J, gelé,
  pour tout emplacement : un journal de précomptage est posté dans l'ERP avant
  que la photo ne soit prise, donc elle l'a déjà intégré, et lui opposer une
  seconde référence antérieure compterait deux fois la même correction. Un
  emplacement précompté montre alors un écart voisin de zéro, et c'est exact :
  sa correction a été enregistrée plus tôt, dans l'ERP, avant la campagne. Ce
  qui a bougé entre les deux dates se lit dans les Contrôles — sans action
  requise, et sans rien qui bloque.
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
fixtures/jeu-de-donnees/    Campagne de contrôle + calcul théorique indépendant
tests/                      3140 contrôles ; 290 exigent un PostgreSQL, ignorés sinon
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
| [`07-comptages-avances.md`](docs/07-comptages-avances.md) | Comptages avancés : la logique, le modèle et le processus |
| [`08-algorigrammes.md`](docs/08-algorigrammes.md) | Algorigrammes du processus actuel et du processus avec comptages avancés |
| [`09-jeu-de-donnees-de-controle.md`](docs/09-jeu-de-donnees-de-controle.md) | Le jeu de données de contrôle, son arithmétique posée, et comment confronter l'application |
| [`10-cahier-des-charges.md`](docs/10-cahier-des-charges.md) | Le besoin, indépendamment de cette implémentation : exigences, règles métier, recette, et ce qui reste à décider |

---

## Développement

```bash
make help            # tous les points d'entrée
make test            # 3140 contrôles ; 290 ignorés sans PostgreSQL
make lint            # ruff + tsc
make check           # les deux
make dev-api         # API avec rechargement, port 8000
make dev-ui          # Vite avec proxy vers l'API, port 5173

npm --prefix frontend run test   # 579 contrôles navigateur (vitest + jsdom)
npm --prefix frontend run e2e    # le parcours complet, Playwright, app démarrée
```

Une poignée de contrôles font **recalculer** le classeur de repli par
LibreOffice Calc et comparent son journal à celui du moteur. Ils s'ignorent
quand il n'est pas installé — comme ceux qui exigent un PostgreSQL — et les
contrôles de structure du même fichier, eux, tournent partout.

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
