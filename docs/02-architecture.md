# Architecture

## 1. Vue d'ensemble

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          Databricks App (1 conteneur)                     │
│                                                                           │
│   ┌─────────────────────┐        ┌───────────────────────────────────┐    │
│   │  SPA React / TS     │  /api  │  FastAPI                          │    │
│   │  servie en statique │◄──────►│  routers → services → domaine     │    │
│   └─────────────────────┘        └────┬──────────┬──────────┬────────┘    │
│                                       │          │          │             │
└───────────────────────────────────────┼──────────┼──────────┼─────────────┘
                                        │          │          │
                         ┌──────────────┘          │          └──────────────┐
                         ▼                         ▼                         ▼
              ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
              │ Lakebase (Postgres)│   │  SQL warehouse     │   │ Serving endpoint   │
              │ écritures, CRUD,   │   │  Delta / UC :      │   │ LLM vision :       │
              │ statuts, audit     │   │  archives, analyses│   │ lecture des scans  │
              └─────────┬──────────┘   └────────▲───────────┘   └────────────────────┘
                        │                       │
                        └──── Lakeflow job ─────┘
                          publish_campaign_to_delta
```

Un seul processus sert l'API **et** la SPA, parce que la plateforme n'expose
qu'un seul port (`DATABRICKS_APP_PORT`).

## 2. Découpage en couches

La règle de dépendance est stricte et à sens unique :

```
inventory.api        routers HTTP, schémas de requête/réponse, dépendances
      ↓
inventory.services   cas d'usage : garde de phase, transaction, audit
      ↓
inventory.db         dépôts SQL (le seul endroit qui connaît du SQL)
inventory.ingest     parsing des fichiers et collages
inventory.ai         appels au modèle
inventory.analytics  statistiques et machine learning
      ↓
inventory.domain     règles métier pures — n'importe aucun driver, aucun framework
```

**`inventory.domain` n'importe rien du reste.** C'est ce qui permet de tester
l'intégralité des règles métier — éclatement BOM, consolidation, écarts,
contrôles, machine à états, matrice d'impression — en une fraction de seconde
sans base de données. La suite compte 2389 contrôles ; c'est la propriété que le
classeur Excel n'avait pas.

Dernier arrivé dans cette couche : `domain/printing.py`, qui décide lequel des
trois documents une feuille peut produire selon la zone et la phase. Une règle
qui vit à la fois dans l'API, dans l'écran et dans les tests finit par vivre
trois vies légèrement différentes ; celle-ci n'en a qu'une, et l'écran reçoit
le résultat (`zone.printModes`) au lieu de le recalculer.

### Ce que chaque couche a le droit de faire

| Couche | Peut | Ne peut pas |
|---|---|---|
| `domain` | Calculer, valider, lever une exception métier | Lire/écrire quoi que ce soit, connaître HTTP |
| `db` | Écrire du SQL, mapper des lignes vers des modèles | Contenir une règle métier |
| `services` | Orchestrer, ouvrir une transaction, écrire l'audit | Connaître FastAPI, formater pour l'écran |
| `api` | Valider une entrée HTTP, appeler **un** service, sérialiser | Contenir une règle métier ou du SQL |

## 3. Pourquoi deux stockages

| | Lakebase (PostgreSQL) | Delta / Unity Catalog |
|---|---|---|
| **Rôle** | Source de vérité opérationnelle | Archive gouvernée et surface analytique |
| **Écritures** | Toutes celles de l'application | Aucune depuis l'app (job uniquement) |
| **Latence** | Millisecondes | Secondes |
| **Utilisation** | CRUD ligne à ligne, statuts, verrouillage optimiste, audit | Requêtes inter-campagnes, tableaux de bord AI/BI, partage avec d'autres équipes |

Un inventaire est un processus **transactionnel** le jour J : deux cents
journaux changent de statut, des lignes sont corrigées une par une, dix
personnes travaillent en parallèle. Delta n'est pas fait pour ça. Inversement,
comparer cinq campagnes sur trois ans est une requête analytique que Postgres ne
devrait pas porter.

La réconciliation se fait par un job idempotent (`replaceWhere` sur la partition
de la campagne), jamais par une double écriture synchrone — qui ferait dépendre
chaque clic de la disponibilité du warehouse et laisserait les deux stockages en
désaccord au premier échec partiel.

## 4. Le modèle « une campagne = un dossier immuable »

Chaque campagne possède **sa propre copie** des référentiels :

```
campaign ──┬── item          (référentiel articles figé)
           ├── bom_link      (nomenclatures figées)
           ├── warehouse / location
           ├── book_stock    (snapshot ERP figé)
           ├── count_journal ── count_journal_line
           ├── zone ── count_sheet ── count_sheet_line
           │                       └── arbitration
           ├── consolidation_run ── consolidation_line / wip_breakdown
           ├── adjustment_line
           ├── variance_analysis
           └── audit_event
```

Un prix standard qui change après le comptage ne modifie pas la valorisation
d'une campagne close. C'est ce qui rend un chiffre défendable des mois après.

### La matrice de gel

`inventory.domain.workflow.mutability_of()` est l'unique source de vérité sur ce
qui est modifiable, et elle sert **à la fois** au serveur (qui refuse l'écriture)
et à l'interface (qui désactive le contrôle). Une divergence entre les deux est
structurellement impossible.

| | PRÉPARATION | COMPTAGE | ANALYSE | CLÔTURÉE |
|---|:---:|:---:|:---:|:---:|
| Seuils | ✅ | ❌ | ❌ | ❌ |
| Paramètres (formules) | ✅ | ✅ | ❌ | ❌ |
| Articles, nomenclatures | ✅ | ❌ | ❌ | ❌ |
| Emplacements | ✅ | ✅ | ❌ | ❌ |
| Stock ERP | ❌ | ✅ | ❌ | ❌ |
| Zones GENERIQUE | ✅ | ✅ | ❌ | ❌ |
| Journaux de comptage | ❌ | ✅ | ❌ | ❌ |
| Feuilles de comptage | ✅ | ✅ | ❌ | ❌ |
| Ajustements | ❌ | ❌ | ✅ | ❌ |
| Analyse des écarts | ❌ | ❌ | ✅ | ❌ |

Les zones GENERIQUE restent créables pendant le comptage : une aire physique que
personne n'avait listée est découverte à chaque campagne.

## 5. Provenance des données

Toute quantité porte sa source, et les valeurs importées et saisies vivent dans
**deux colonnes distinctes** :

```sql
qty_imported  NUMERIC(20,6)   -- ce que l'ERP a envoyé
qty_manual    NUMERIC(20,6)   -- ce qu'un humain a décidé
-- quantité retenue = COALESCE(qty_manual, qty_imported)
```

Recharger l'export ERP dix fois dans la journée rafraîchit `qty_imported` sans
jamais effacer une correction humaine. L'interface affiche les deux côte à côte
avec un badge de provenance (`Import ERP`, `Saisie manuelle`, `Extraction IA`,
`Consolidation`, `Arbitrage`, `Système`).

### Une commande métier écrit tout, ou rien

Une commande qui touche plusieurs tables les touche dans **une** transaction.
La liste n'est pas décorative : c'est celle des états que rien dans
l'application ne saurait décrire s'ils survenaient.

| Commande | Ce qu'un incident au milieu laissait derrière |
|---|---|
| Création d'une zone | Une zone sans feuilles, que l'écran présente comme prête à compter |
| Saisie d'une quantité | Un chiffre sans la trace de qui l'a saisi, dans une application dont c'est la raison d'être |
| Suppression d'un lot de lignes | Une trace annonçant « 40 lignes supprimées » quand 12 sont parties |
| Lecture d'un scan | Des quantités lues par le modèle et un chemin de preuve manquant |
| Consolidation | Un calcul « courant » dont le journal GENERIQUE est resté vide |

Le dernier cas était le plus retors : le refus « aucun journal GENERIQUE
n'existe » se déclenchait **après** l'enregistrement du calcul. Une campagne mal
configurée repartait donc avec une consolidation courante et rien pour la
porter.

La lecture d'une **pile** de scans fait exception, et délibérément : une
transaction par feuille, pas une pour la pile. Le rapport nomme les feuilles
traitées une à une, et trente feuilles ne doivent pas perdre les vingt-neuf qui
ont abouti parce que la trentième a échoué.

### Un chargement qui remplace n'écrit pas un ensemble amputé

Un import produit des lignes acceptées et des lignes rejetées. Écrire les
premières malgré les secondes est anodin pour un chargement qui **complète** —
trois lignes refusées sur quatre mille sont trois lignes manquantes, visibles
dans le rapport, que le prochain fichier apportera.

C'est tout autre chose pour un chargement qui **remplace**. Le snapshot de stock
ERP, l'écart backflush et une nomenclature chargée en mode remplacement effacent
l'ensemble précédent avant d'écrire le nouveau : les trois lignes refusées
deviennent trois lignes *supprimées*, la nomenclature passe de 4 000 liens à
3 997, et plus rien ne dit lesquels ont disparu. L'éclatement du WIP se fait
ensuite contre une nomenclature incomplète.

Ces trois-là refusent donc d'écrire dès qu'une ligne est rejetée. La dérogation
existe — `allowPartial` — se voit dans le rapport du lot, et n'est pas le défaut.

### La clôture exige ce qu'elle promet

Le parcours contrôlait sérieusement l'entrée en analyse et ne demandait rien
pour la clôture, qui est pourtant le seul geste irréversible. Trois exigences
s'y ajoutent :

| Bloqueur | Pourquoi |
|---|---|
| `MATERIAL_VARIANCES_UNEXPLAINED` | Un écart matériel sans cause ni acceptation explicite est un écart que personne n'a expliqué — et c'est la première chose qu'un contrôle demande |
| `IMPORTS_WITH_REJECTS` | Une grille encore sur un chargement à rejets fige un référentiel amputé |
| `PUBLICATION_NOT_DONE` | La base opérationnelle est vivante ; l'archive est ce qui reste |

Ces faits coûtent un calcul d'écarts : ils ne sont consultés qu'à la clôture,
pas à chaque affichage du panneau « ce qui manque pour avancer ».

### Une lecture ERP n'est jamais coupée en silence

`LIMIT n` ramène `n` lignes que la source en ait `n` ou dix mille, et rien dans
la réponse ne distingue les deux cas. Une campagne pouvait donc partir avec un
référentiel amputé sans qu'aucun écran ne l'annonce : le comptage se faisait
contre un stock qui ne couvrait pas l'usine, et l'écart qui en sortait n'était
l'écart de rien — une faute qu'on ne découvre qu'à la réunion des écarts, quand
il est trop tard pour recompter.

Chaque lecture demande désormais **une ligne de plus** que son plafond. Si elle
revient, la source en avait davantage, et la lecture est refusée en nommant la
table et le plafond. La ligne excédentaire est lue puis jetée : le coût d'une
ligne contre celui d'un inventaire faux.

Une seule lecture garde le droit d'être coupée — la liste des dates de snapshot
proposée à l'écran, où la troncature *est* l'intention.

## 6. Sécurité et identité

- L'authentification est terminée par le proxy Databricks Apps ; l'identité
  arrive dans `x-forwarded-email`. C'est la **seule** source d'identité :
  l'application ne lit jamais un identifiant utilisateur dans un corps de requête.
- **Sans en-tête d'identité, la requête est refusée** (401), et non attribuée à
  une identité générique. L'application en inventait une auparavant : les
  écritures partaient sous un nom que personne n'avait authentifié, et la
  barrière d'identité — propriétaire ou gestionnaire déclaré — ne protégeait
  alors rien. En environnement local, et là seulement, l'identité `local@dev`
  tient lieu de proxy.
- **La trace d'audit ne se réécrit ni ne se vide.** Deux règles PostgreSQL
  neutralisent `UPDATE` et `DELETE` depuis l'origine. Elles ne couvraient pas
  `TRUNCATE`, qui ne passe pas par la réécriture de requête : un trigger
  `BEFORE TRUNCATE` le refuse désormais (migration 020). La suppression physique
  d'une campagne échouait déjà, mais sur un message d'intégrité référentielle
  illisible ; `ON DELETE RESTRICT` le dit franchement. Aucune de ces protections
  ne tient devant le propriétaire du schéma : la réponse à cette menace-là est
  l'archive Delta, hors de cette base.
- **La campagne de l'URL est celle qui est écrite.** La permission se vérifie
  sur la campagne de l'URL, les identifiants arrivent dans le corps : chaque
  écriture porte donc `WHERE campaign_id = ? AND id = ?`, et des clés étrangères
  composites l'imposent au niveau du schéma (voir *Modèle de données*, §3.1).
- Les accès aux ressources (warehouse, endpoint LLM, Lakebase) passent par le
  service principal de l'app, dont les permissions sont accordées
  automatiquement par la plateforme à partir des déclarations de `databricks.yml`.
- Aucun identifiant, aucun nom de workspace, aucun identifiant de warehouse
  n'est présent dans le code : tout arrive par variable d'environnement.
- Le jeton Lakebase tourne (≈ 1 h) ; le pool renouvelle les connexions avant
  expiration et rafraîchit le credential en cas d'échec d'authentification.

## 7. Contraintes de la plateforme prises en compte

| Contrainte | Traitement |
|---|---|
| Un seul port, `DATABRICKS_APP_PORT` | Un processus sert l'API et la SPA |
| Proxy : 120 s par requête | Budget de 100 s côté app ; les traitements lourds sont bornés et paginés |
| 6 Go de RAM, 2 vCPU | Lecture des `.xlsx` en flux, écriture `constant_memory`, pool de 8 connexions, `n_jobs=1` sur scikit-learn |
| 10 Mo par fichier | Bundle SPA de 109 Ko compressé, aucun `node_modules` dans la charge utile |
| Un endpoint `async` tourne sur la boucle | FastAPI n'exécute dans un pool de fils que les endpoints `def`. Les cinq endpoints qui reçoivent un fichier renvoient leur travail synchrone au pool (`offload`) : un import de deux cent mille lignes ou une question à l'assistant y immobilisaient l'application entière |
| Une page PDF au MediaBox démesuré | `render()` alloue son bitmap hors de portée de la garde anti-bombe de PIL. Une page de deux cents pouces produit 900 Mpx à 150 dpi ; la résolution est réduite pour tenir sous `INV_SCAN_MAX_PIXELS`, plutôt que la page refusée |
| Téléversements non bornés | Lecture par tranches d'1 Mio, interrompue dès le plafond `INV_MAX_UPLOAD_BYTES` franchi. Le refus coûte une tranche, pas un fichier — il arrivait auparavant après que tout avait été chargé en mémoire, et seulement sur la route d'import |
| Pas d'accès root | Uniquement des roues PyPI ; pas de Poppler, donc les PDF scannés sont découpés page par page en PDF, pas rasterisés |
| Système de fichiers éphémère | Aucun état sur disque ; les preuves vont dans un volume UC — ou dans la base (`INV_EVIDENCE_STORE=lakebase`) quand le `USE CATALOG` du volume n'est pas obtenable |
| Seuls stdout/stderr sont capturés | Journalisation JSON structurée sur stdout |
| Sondes de la plateforme | `/api/health/live` (jamais de dépendance : une base en panne ne doit pas faire recycler des conteneurs sains) et `/api/health/ready` (503 tant que la base, les migrations ou le démarrage ne suivent pas). `/api/health` reste la page de diagnostic, toujours 200 |
| Démarrage en 10 min max | Dépendances épinglées, migrations idempotentes et rapides |

## 8. Reproductibilité

Trois mécanismes garantissent qu'un chiffre est recalculable à l'identique :

1. **Snapshots figés** : le stock ERP, les articles, les nomenclatures et les
   prix sont copiés dans la campagne, pas référencés.
2. **Arithmétique décimale** : quantités en `Decimal` à 6 décimales, montants à
   2 décimales, arrondi *half-up*. Aucun flottant binaire dans le domaine.
   Un total est toujours égal à la somme des lignes affichées à côté de lui.
3. **Graines fixées** : `random_state=42` sur la forêt d'isolement et le
   k-means ; tri déterministe des nomenclatures. La même campagne réanalysée
   demain produit exactement les mêmes signalements.

La version du moteur de calcul (`engine_version`) est stampée sur la campagne et
sur chaque exécution de consolidation.
