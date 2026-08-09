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
sans base de données. La suite compte 287 tests ; c'est la propriété que le
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
| Articles, nomenclatures | ✅ | ❌ | ❌ | ❌ |
| Emplacements | ✅ | ✅ | ❌ | ❌ |
| Stock livre | ❌ | ✅ | ❌ | ❌ |
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

## 6. Sécurité et identité

- L'authentification est terminée par le proxy Databricks Apps ; l'identité
  arrive dans `x-forwarded-email`. C'est la **seule** source d'identité :
  l'application ne lit jamais un identifiant utilisateur dans un corps de requête.
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
| Pas d'accès root | Uniquement des roues PyPI ; pas de Poppler, donc les PDF scannés sont découpés page par page en PDF, pas rasterisés |
| Système de fichiers éphémère | Aucun état sur disque ; les preuves vont dans un volume UC |
| Seuls stdout/stderr sont capturés | Journalisation JSON structurée sur stdout |
| Démarrage en 10 min max | Dépendances épinglées, migrations idempotentes et rapides |

## 8. Reproductibilité

Trois mécanismes garantissent qu'un chiffre est recalculable à l'identique :

1. **Snapshots figés** : le stock livre, les articles, les nomenclatures et les
   prix sont copiés dans la campagne, pas référencés.
2. **Arithmétique décimale** : quantités en `Decimal` à 6 décimales, montants à
   2 décimales, arrondi *half-up*. Aucun flottant binaire dans le domaine.
   Un total est toujours égal à la somme des lignes affichées à côté de lui.
3. **Graines fixées** : `random_state=42` sur la forêt d'isolement et le
   k-means ; tri déterministe des nomenclatures. La même campagne réanalysée
   demain produit exactement les mêmes signalements.

La version du moteur de calcul (`engine_version`) est stampée sur la campagne et
sur chaque exécution de consolidation.
