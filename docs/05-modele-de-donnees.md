# Modèle de données

## 1. Règles de modélisation

Elles sont issues du cahier des charges et appliquées sans exception.

| Règle | Application |
|---|---|
| Les identifiants métier ne sont jamais concaténés | `LocationKey(warehouse_id, location_id)` est une clé composite. Aucune colonne « ENTREPOT&EMPLACEMENT » nulle part. |
| Chaque dimension a sa colonne et une clé technique | Toutes les tables mutables portent un `id UUID` en plus de leur clé métier. |
| Article, unité, emplacement, BOM et prix sont snapshotés | Chaque campagne possède sa copie complète des référentiels. |
| Les valeurs calculées conservent la règle et la version de code | `engine_version` sur la campagne et sur chaque exécution de consolidation. |
| La preuve documentaire est référencée **et vérifiable** | `count_sheet.evidence_path` et `import_batch.storage_path` pointent vers le volume UC ; le chemin porte l'empreinte du contenu, et une feuille scannée conserve `evidence_sha256` / `evidence_bytes` / `evidence_mime`. Un volume se modifie depuis l'espace de travail : le chemin dit *où*, l'empreinte dit *lequel*. |
| Un enfant ne peut pas appartenir à la campagne d'un autre | Clés uniques `(id, campaign_id)` sur `count_journal`, `zone` et `count_sheet` ; clés étrangères **composites** depuis `count_journal_line`, `count_sheet`, `count_sheet_line` et `arbitration`. La permission d'écriture se vérifie sur la campagne de l'URL, les identifiants arrivent dans le corps : c'est Postgres qui garantit qu'ils désignent la même campagne. |
| Un chargement porte un seul identifiant de lot | Le stock ERP et l'écart backflush marquent leurs lignes avec l'identifiant que porte aussi leur ligne d'`import_batch`. Deux identifiants pour un chargement rendaient « d'où vient cette quantité » sans réponse. |
| Les suppressions logiques sont préférées aux suppressions physiques | `deleted_at` partout ; le journal d'audit résout donc toujours. Une campagne qui a une histoire ne se supprime plus **physiquement** : `audit_event.campaign_id` est `ON DELETE RESTRICT`, et un trigger `BEFORE TRUNCATE` refuse de vider la trace (migration 020). |
| Un chargement qui remplace n'écrit pas un ensemble amputé | Stock ERP, écart backflush et nomenclature en mode remplacement refusent d'écrire dès qu'une ligne est rejetée : les lignes manquantes deviendraient des lignes supprimées. Dérogation explicite par `allowPartial`, tracée dans le rapport du lot. |
| L'archive porte l'identifiant, jamais le code | Les tables Delta sont partitionnées par `campaign_id` : un code métier se réutilise après une suppression logique, et deux campagnes homonymes écrasaient leurs archives. La table `publication`, écrite en dernier, dit qu'un dossier est complet ; `campaign.published_at` le rapporte à Lakebase, où la clôture le consulte. |

## 2. Types numériques

```sql
qty       NUMERIC(20,6)   -- 6 décimales : grammes, mètres, ratios BOM (4,86 KG)
money     NUMERIC(20,2)   -- 2 décimales, arrondi half-up (convention comptable)
ratio     NUMERIC(10,6)   -- tolérances et pourcentages
```

Aucun flottant binaire dans le domaine. `0.1 + 0.2 ≠ 0.3` en binaire, et un
total qui ne correspond pas à la somme des lignes affichées à côté est
exactement le symptôme qui a fait perdre confiance dans le classeur Excel.

L'arrondi n'a lieu qu'aux frontières : persistance et affichage. L'arithmétique
intermédiaire ne perd jamais de précision.

## 3. Schéma opérationnel (Lakebase / PostgreSQL)

```
campaign ─┬─ threshold                    (seuils par type d'article)
          │
          ├─ item                         (référentiel articles figé)
          ├─ bom_link                     (nomenclatures figées)
          ├─ warehouse
          ├─ location                     PK (campaign, warehouse, location)
          │
          ├─ book_stock                   (snapshot ERP figé)
          │
          ├─ count_journal ──── count_journal_line
          │    1 par emplacement actif      qty_imported / qty_manual séparées
          │
          ├─ zone ──── count_sheet ──── count_sheet_line
          │    └─ closed_at / closed_by   (« zone terminée » : la seule donnée
          │                                d'état du parcours de comptage. Les
          │                                deux autres statuts d'une zone se
          │                                déduisent de ses quantités. La feuille,
          │                                elle, n'a plus d'état)
          │  passes 1|2   (1 par passage)      section : LINE_SIDE / WIP / WIP_OK
          │  free_entry
          │  manager_code
          │        └─ arbitration           (comparaison n°1 vs n°2)
          │
          ├─ manager                      (5 postes, avec l'identité de chacun)
          ├─ warehouse_manager            (entrepôt → gestionnaire, clé AUTRES)
          │
          ├─ consolidation_run ─┬─ consolidation_line   (journal GENERIQUE produit)
          │                     └─ wip_breakdown        (traçabilité de l'éclatement)
          │
          ├─ adjustment_line              (mouvements post-comptage)
          ├─ variance_analysis            (cause humaine + proposition IA, séparées)
          │
          ├─ campaign_backflush           (écart backflush figé sur une période)
          │
          ├─ stock_flow_run ─┬─ stock_flow_input   (1 par flux chargé : provenance
          │  comparaison     │                      ERP / fichier / saisie)
          │  avec une        └─ stock_flow_erp     (production et conso. théorique)
          │  campagne antérieure
          │
          ├─ import_batch                 (provenance de chaque chargement)
          └─ audit_event                  (append-only, UPDATE/DELETE neutralisés)

          └─ scan_job                    (lecture d'un scan : statut, avancement,
                                            rapport, chronomètres. `sheet_id`
                                            renseigné = une feuille ; nul = une
                                            pile multi-feuilles)

assignable_cause                          (référentiel de site, hors campagne)
schema_migration                          (bookkeeping des migrations)

erp_base_article, erp_bom,                (miroirs Lakebase des tables Unity
erp_ecart_backflush, erp_mouvements,       Catalog, alimentés par un job — voir
erp_stock_snapshot                         le guide de déploiement)
```

### 3.1 Pourquoi `qty_imported` et `qty_manual` sont deux colonnes

```sql
qty_imported NUMERIC(20,6),   -- ce que l'ERP a envoyé
qty_manual   NUMERIC(20,6),   -- ce qu'un humain a décidé
CHECK (qty_imported IS NOT NULL OR qty_manual IS NOT NULL)
-- quantité retenue = COALESCE(qty_manual, qty_imported)
```

C'est ce qui permet de recharger l'export ERP autant de fois qu'on veut pendant
la journée sans jamais détruire une correction. Une colonne unique obligerait à
choisir entre « je perds les corrections » et « je ne rafraîchis plus ».

### 3.2 Pourquoi le nombre de comptages appartient à la zone

```sql
ALTER TABLE zone ADD COLUMN passes SMALLINT NOT NULL DEFAULT 2
    CHECK (passes IN (1, 2));
```

Porté par la campagne, il obligeait à compter deux fois **toutes** les zones ou
aucune. Une salle de métrologie à trois références n'a pas besoin du dispositif
d'un bord de ligne, et lui imposer un second comptage produisait surtout des
feuilles vierges. Le défaut reste 2 : le double comptage est la règle, le
comptage unique l'exception qu'on assume, zone par zone.

Conséquences en cascade, toutes portées par la même colonne :

* `resolve_zone_quantities` n'émet plus l'avertissement « un seul passage » pour
  une zone qui n'en attendait qu'un — il n'a de sens que lorsque deux étaient
  attendus ;
* une zone à un passage ne produit aucune ligne d'arbitrage : il n'y a pas de
  second avis à comparer, et en fabriquer un bloquerait la consolidation pour
  une décision que personne ne peut prendre ;
* ramener une zone à 1 supprime la feuille n°2, donc l'opération est **refusée**
  — en nommant les zones — si cette feuille porte déjà une quantité saisie.
  Repasser à 2 recrée la feuille *et* sa liste d'articles : la recréer vide
  rendrait le second comptage aveugle.

### 3.3 `free_entry` : une feuille vide voulue n'est pas une feuille oubliée

Les deux se ressemblent exactement en base — une feuille sans ligne. Sans le
drapeau, le contrôle de préparation `ZONE_WITHOUT_LINES` signalait les deux, et
un avertissement qui se déclenche à tort finit par ne plus être lu.

### 3.4 Gestionnaires et périmètres

```sql
manager           (campaign_id, code) + actor   -- l'identité transmise par le proxy
warehouse_manager (campaign_id, warehouse_id) → manager_code
zone.manager_code
```

`actor` est ce qui permet au serveur de résoudre « mon périmètre » sans que le
client ne nomme jamais un gestionnaire — c'est ce qui rend le filtrage
opposable. `warehouse_manager.warehouse_id` accepte la valeur réservée
**`AUTRES`** : elle rattache d'un coup tout entrepôt sans affectation explicite,
sinon un entrepôt découvert par un nouvel import de stock ERP tomberait hors
de tout périmètre sans que personne ne le voie. Aucune clé étrangère vers
`warehouse` : le référentiel des entrepôts naît du stock ERP, chargé *après*
la préparation.

Le périmètre est un **filtre, jamais une permission** : aucune écriture n'en
dépend, et la matrice de gel reste la seule autorité sur ce qui est modifiable.

### 3.5 Pourquoi l'audit est protégé au niveau du moteur

```sql
CREATE OR REPLACE RULE audit_event_no_update AS ON UPDATE TO audit_event DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_event_no_delete AS ON DELETE TO audit_event DO INSTEAD NOTHING;
```

Une convention de code se contourne par accident. Une règle SQL, non : même un
bug dans la couche service ne peut pas réécrire l'histoire.

### 3.6 Concurrence

Toutes les tables mutables portent `row_version BIGINT`. Les écritures qui
peuvent entrer en conflit (correction d'une ligne de comptage) comparent la
version attendue et renvoient un **409** plutôt qu'un dernier-arrivé-gagne
silencieux. Le jour J, dix personnes travaillent en parallèle : c'est le moment
où ça compte.

### 3.7 Index

Ils suivent les chemins réellement empruntés :

| Index | Requête servie |
|---|---|
| `count_journal_uq (campaign, warehouse, location)` | Un journal par emplacement — garantie d'unicité, pas seulement performance |
| `cjl_journal_idx (journal_id) WHERE deleted_at IS NULL` | Ouverture d'un journal |
| `book_stock_uq (campaign, item, warehouse, location)` | Réconciliation |
| `item_name_idx (campaign, lower(name) text_pattern_ops)` | Recherche par désignation |
| `audit_campaign_idx (campaign, at DESC)` | Journal d'audit paginé |

Les index partiels (`WHERE deleted_at IS NULL`) évitent d'indexer les lignes
logiquement supprimées, qui ne sont jamais lues.

### 3.1 Les clés composites, et pourquoi elles existent

La garde d'écriture vérifie une permission sur la campagne de l'**URL**. Les
identifiants des objets, eux, arrivent dans le **corps** de la requête. Rien
dans cette architecture ne relie les deux : un gestionnaire habilité sur la
campagne A pouvait poster un journal, fermer une zone ou supprimer une ligne de
la campagne B en connaissant son identifiant, et la garde n'y voyait rien
puisqu'elle avait bien vu A.

Le correctif est en deux couches, et les deux sont nécessaires.

**Applicative** — chaque écriture est portée par sa campagne :
`WHERE campaign_id = ? AND id = ?`. Neuf méthodes de dépôt exigent désormais
`campaign_id` en premier paramètre, sans valeur par défaut.

**Structurelle** — migration 018. `count_journal`, `zone` et `count_sheet`
portent une clé unique `(id, campaign_id)`, redondante avec leur clé primaire ;
c'est le prix d'une garantie qui reste vraie même si une requête future oublie
le filtre. Les clés étrangères simples de `count_journal_line`, `count_sheet`,
`count_sheet_line` et `arbitration` sont **remplacées** — pas doublées — par
des clés composites. Garder les deux laisserait croire que la garantie tient
alors que seule la plus faible s'appliquerait.

## 4. Schéma analytique (Delta / Unity Catalog)

Alimenté par `jobs/publish_campaign_to_delta.py`, partitionné par
`campaign_code`, écrit en `replaceWhere` pour être idempotent.

| Table | Contenu |
|---|---|
| `campaign` | Cycle de vie et horodatages de gel |
| `item_snapshot`, `bom_snapshot` | Référentiels de la campagne |
| `book_stock_snapshot` | Photographie ERP, valorisée |
| `count_result` | Comptages retenus, importé et manuel côte à côte |
| `wip_breakdown` | Traçabilité de l'éclatement du WIP |
| `adjustment` | Mouvements post-comptage |
| `variance_analysis` | Cause humaine + proposition IA |
| `audit_event` | Journal d'audit archivé |

### Vues

| Vue | Usage |
|---|---|
| `v_variance` | Écarts réconciliés par article — la base de tout le reste |
| `v_campaign_kpi` | Indicateurs de campagne, trois fiabilités distinctes |
| `v_variance_recurrence` | Récurrence inter-campagnes : fuite structurelle vs accident |
| `v_wip_contribution` | Où le WIP envoie de la valeur |

## 5. Les indicateurs, et pourquoi ils sont trois

C'est le point le plus important du modèle, et celui que le classeur legacy
confondait.

### Fiabilité nette

```
1 − |Σ écart €| / Σ stock ERP €
```

Les excédents compensent les manques. Répond à **« avons-nous gagné ou perdu de
la valeur ? »**. C'est la mesure comptable — et la plus flatteuse.

### Fiabilité brute

```
1 − Σ |écart €| / Σ stock ERP €
```

Chaque erreur compte, dans les deux sens. Répond à **« de combien nous
sommes-nous trompés ? »**.

> Un écart de +100 k€ et un de −100 k€ ne font pas zéro erreur : ils font deux
> erreurs. **C'est l'indicateur à piloter.**

### IRA — Inventory Record Accuracy

```
nombre de couples article/emplacement dans la tolérance / nombre total
```

Standard WMS. Ignore complètement les montants : un écart de 3 pièces sur une
vis compte autant qu'un écart de 3 stators. Répond à **« quelle part de nos
enregistrements était juste ? »** — c'est la mesure de la qualité du *processus*,
pas de son impact financier.

Les trois sont affichées **côte à côte** dans l'application, avec leur définition
en infobulle. Publier l'une sans les autres, c'est choisir sa conclusion avant
de calculer.

### Matérialité

Un écart est *matériel* quand il franchit **toutes** les barrières de son type :

```
|Δ€| ≥ valeur_absolue  ET  |Δqté| ≥ plancher  ET  |Δqté|/qté_livre ≥ ratio
```

La conjonction — et non la disjonction — garde la liste d'exceptions à une
taille exploitable. Une seule exception à cette règle : `qté_livre = 0` rend la
ligne toujours matérielle, parce que du stock inconnu du système n'est jamais
une différence d'arrondi.

## 6. Cycle de vie et gel

Voir la matrice complète dans [`02-architecture.md`](02-architecture.md#la-matrice-de-gel).
La source de vérité est `inventory.domain.workflow.mutability_of()`, utilisée
**à la fois** par le serveur (qui refuse) et par l'interface (qui désactive).

## 7. Vocabulaire

| Terme legacy | Terme actuel | Signification |
|---|---|---|
| `BDL` | Bord de ligne (`LINE_SIDE`) | Composant compté tel quel |
| `MOM waiting`, `Éclaté` | WIP (`WIP`) | En-cours non déclaré → éclaté en nomenclature |
| `MOM OK` | WIP assemblé (`WIP_OK`) | Ensemble déclaré dans l'ERP → compté tel quel |
| `INVE` | (inchangé) | Inventaire par étiquette, généré par scan |
| `INVV` | (inchangé) | Inventaire vrac, saisi ou consolidé |

Les anciens libellés sont reconnus **uniquement à l'import**
(`legacy_section_alias`), pour permettre de reprendre un ancien classeur.
L'interface, les exports et la base ne parlent que le vocabulaire actuel.
