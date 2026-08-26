# Comptages avancés

> Étude de conception. Rien de ce document n'est implémenté à ce jour : il
> décrit la logique à écrire, ce qu'elle change dans l'existant, et ce qu'elle
> demande à l'exploitation. Les algorigrammes correspondants sont dans
> [`08-algorigrammes.md`](08-algorigrammes.md).

---

## 1. Le besoin

Compter, un ou plusieurs jours avant le jour J, un sous-ensemble d'emplacements
— typiquement des zones lentes, des magasins extérieurs, du stock immobilisé —
pour alléger la charge du jour de l'inventaire général.

L'emplacement précompté est ensuite **balisé** : plus aucun mouvement physique,
plus aucun mouvement informatique. Sous cette hypothèse, le stock ERP chargé et
gelé le jour J pour ces emplacements est exactement égal au physique compté à
J-1 ou J-2.

L'exigence porte moins sur le cas nominal que sur son démenti : **il faut que
l'application détecte, chiffre et fasse traiter le cas où la barrière
opérationnelle n'a pas tenu.** Et il faut que preuves, écarts et analyses
restent dans **une seule campagne**.

---

## 2. Pourquoi le contournement actuel ne convient pas

L'approche envisageable aujourd'hui — une petite campagne dédiée par lot
précompté, puis, dans la campagne générale, forcer ces emplacements au stock
ERP — a un défaut de fond et plusieurs défauts de forme.

**Le défaut de fond.** « Forcer au stock ERP » est
`CountingService.enforce_book_stock`, et cette opération **remplace** les lignes
du journal par les quantités du snapshot :

```python
lines = [
    CountJournalLine(..., qty_manual=b.qty, source=DataSource.SYSTEM,
                     comment="Quantité forcée au stock ERP.")
    for b in by_key.get(journal.key, [])
]
ctx.journals.replace_lines_for_journal(journal_id, campaign.id, lines, ...)
```

L'écart devient nul *par construction* — c'est écrit tel quel dans
`JournalStatus.BOOK_ENFORCED`. Autrement dit : le seul mécanisme existant fait
exactement ce que l'utilisateur veut éviter. Si un mouvement a eu lieu malgré le
balisage, `BOOK_ENFORCED` l'efface, et rien à l'écran ne le signale. Le
mécanisme est correct pour ce pour quoi il a été écrit — un magasin extérieur
inventorié ailleurs, dont on accepte de reprendre le chiffre ERP — mais il ne
sait pas porter une **preuve de comptage** en face du chiffre ERP.

**Les défauts de forme**, qui suffiraient déjà :

| Problème | Conséquence |
|---|---|
| Référentiels, seuils et prix snapshotés par campagne | Deux campagnes peuvent diverger sur le prix d'un article ; la valorisation du lot avancé n'est plus celle de l'inventaire |
| L'IRA et les taux se calculent par campagne | Aucun indicateur global juste ; il faudrait recomposer à la main |
| L'archive Delta est partitionnée par `campaign_id` | Le dossier d'inventaire est éclaté en N archives, et rien ne dit qu'elles vont ensemble |
| L'assistant et l'analyse IA lisent une campagne | Ils ne voient jamais le lot avancé en même temps que le reste |
| La réconciliation de flux compare **deux** campagnes | Une campagne générale amputée de ses lots avancés fausse la comparaison suivante |

Conclusion : le comptage avancé doit vivre **dans** la campagne générale.

---

## 3. Le modèle à trois quantités

C'est le cœur de la conception. Pour un emplacement précompté, il n'y a pas deux
quantités mais **trois** :

| Symbole | Quantité | Origine |
|---|---|---|
| `livre@T0` | Stock ERP de l'emplacement **au moment du comptage avancé** | Chargement partiel du lot avancé |
| `compté@T0` | Physique relevé et posté lors du comptage avancé | Journal de comptage, scellé |
| `livre@J` | Stock ERP de l'emplacement dans le snapshot général **gelé le jour J** | Chargement général |

Il en découle **deux écarts**, de nature différente, qu'il ne faut jamais
additionner sans le dire :

```
écart d'inventaire      = compté@T0 − livre@T0
dérive post-scellement  = livre@J   − livre@T0
```

Le premier appartient à la campagne : c'est un écart d'inventaire ordinaire, il
s'analyse, il se classe par cause, il entre dans l'IRA. Le second n'est **pas**
un écart d'inventaire : c'est le constat qu'un mouvement a été enregistré sur un
emplacement censé être figé.

**Le piège à éviter.** L'application calcule aujourd'hui

```
physique = compté + ajusté
écart    = physique − livre
```

où `livre` est le snapshot gelé du jour J. Si l'on se contente de laisser
`compté@T0` dans le journal, l'écart affiché vaut
`compté@T0 − livre@J`, c'est-à-dire **écart d'inventaire − dérive**. Les deux se
mélangent dans un seul nombre, et plus personne ne sait en faire la part : un
écart d'inventaire de +10 masqué par une dérive de +10 s'affiche à zéro.

D'où la première règle de conception : **`livre@T0` doit être conservé**, dans
une table à lui, pour que la décomposition reste calculable jusqu'à la clôture
et dans l'archive.

---

## 4. Pourquoi la dérive ne se tranche pas automatiquement

Quand `livre@J ≠ livre@T0`, trois causes possibles :

1. **Mouvement informatique seul.** Saisie tardive d'un mouvement antérieur au
   comptage, régularisation, backflush d'une production consommée avant T0. Le
   physique n'a pas bougé : `compté@T0` reste l'image juste du stock, et l'écart
   d'inventaire reste `compté@T0 − livre@T0`.
2. **Mouvement physique réel** malgré le balisage : un prélèvement, une
   réception rangée là par habitude. `compté@T0` n'est plus l'image du stock au
   jour J.
3. **Les deux à la fois**, et rien ne dit dans quelles proportions.

**Aucune donnée disponible ne permet de distinguer ces cas.** Dans les trois,
la seule trace est un mouvement ERP. Même le miroir `erp_mouvements` ne tranche
pas : une régularisation informatique et un prélèvement réel y ont la même
forme — un type de mouvement, une quantité, une date. Prétendre déduire
l'explication reviendrait à inventer.

C'est donc une **décision humaine**, et l'application doit l'exiger, pas la
deviner. Le produit tient déjà ce principe ailleurs : le backflush n'efface pas
l'écart, il en explique une part, et ce qui reste est nommé `inexpliqué`.

### Les trois dispositions

Exclusives, tracées, avec commentaire obligatoire.

| Disposition | Ce qu'elle signifie | Effet |
|---|---|---|
| **Recompter** | On ne fait plus confiance au comptage avancé | Le journal est **descellé** (geste tracé, motif obligatoire), recompté le jour J. Le comptage avancé reste dans l'audit comme pièce d'historique ; le comptage du jour J fait foi. |
| **Ajuster** | On reconnaît le mouvement physique et on l'enregistre | Ligne d'ajustement (`AdjustmentKind.RECOUNT` ou `ADJUSTMENT`), donc `physique = compté@T0 + ajusté`. C'est exactement ce que `VarianceLine.physical_qty` modélise déjà : « mouvements réels enregistrés pendant l'analyse ». |
| **Accepter** | La dérive est jugée purement informatique | `compté@T0` est conservé tel quel. L'écart de campagne, calculé contre `livre@J`, **contient** la dérive — mais elle est nommée, chiffrée, attribuée à une cause dédiée, et le rapport la sépare. |

**Ce qu'il ne faut pas faire pour la disposition « Accepter ».** La tentation est
de neutraliser la dérive par une ligne d'ajustement automatique, pour que
l'écart tombe à zéro. C'est à refuser : cela fabriquerait un mouvement de stock
qui n'a pas eu lieu, dans une table dont le sens documenté est « mouvements
réels post-comptage ». L'écart de la campagne doit rester
`physique − ERP du jour J` : c'est la seule définition qui soit comptablement
vraie et qui rende l'IRA comparable d'une campagne à l'autre. La dérive
s'explique, elle ne se soustrait pas.

### Les dérives sans ligne en face

Deux cas que le calcul doit prévoir explicitement, sous peine de les manquer :

- un article **apparaît** dans `livre@J` sur un emplacement scellé — il n'a donc
  jamais été compté, et il n'existe ni dans `livre@T0` ni dans `compté@T0` ;
- un article **disparaît** de `livre@J` alors qu'il figurait à T0.

Le calcul de dérive est donc une **jointure externe complète** sur
`(emplacement, article)` entre `livre@T0` et `livre@J`, pas une jointure interne.

### Le seuil de matérialité

Une dérive de 0,000001 ne doit rien bloquer. Réutiliser les **seuils de campagne
existants**, par type d'article, plutôt qu'introduire un réglage de plus : c'est
déjà le référentiel de « ce qui mérite qu'on s'y arrête », et un second réglage
concurrent finirait par le contredire.

---

## 5. « Ces emplacements doivent-ils générer de nouveaux journaux le jour J ? »

**Non.** La règle reste : *un journal par emplacement et par campagne*. Le
journal du lot avancé est le journal de l'emplacement pour toute la campagne ;
le jour J il est simplement déjà rempli, posté et scellé.

Mais cela oblige à inverser un enchaînement. Aujourd'hui, les journaux sont
**créés par** le chargement du stock ERP :

```python
# import_book_stock, docstring
# 3. one PENDING counting journal is created per active location.
```

et un second chargement est refusé :

```python
if campaign.book_stock_frozen_at is not None:
    raise ConflictError("Le stock ERP est gelé pour cette campagne. ...")
```

Il faut donc distinguer deux modes de chargement :

| Mode | Périmètre | Comportement |
|---|---|---|
| **Avancé** (nouveau) | Les emplacements du lot | Additif. Complète le référentiel des emplacements, crée les journaux manquants **du lot seulement**, écrit `livre@T0` dans `early_count_baseline`. Ne gèle rien, ne touche pas `book_stock`. |
| **Général** (actuel) | Tout | Remplace intégralement `book_stock` — une photographie ne se fusionne pas. **Ne recrée pas** les journaux déjà présents, **n'écrase pas** leurs lignes. Calcule la dérive pour les emplacements scellés. Puis gel. |

Point important : `livre@T0` ne doit **pas** être rangé dans `book_stock`.
`book_stock` est « la photo du jour J » — c'est ce que lisent le calcul d'écart,
la publication Delta (`book_stock_snapshot`) et la réconciliation de flux. Y
mêler un snapshot d'une autre date rendrait ces trois lectures fausses en
silence.

---

## 6. Faut-il un nouveau statut de campagne ?

Le découpage proposé était : `PRÉPARATION → COMPTAGES AVANCÉS → COMPTAGE GÉNÉRAL
→ ANALYSE`.

**Recommandation : non.** Modéliser les comptages avancés comme une
**sous-phase de `COUNTING`**, pas comme un `CampaignStatus` de plus.

Trois raisons.

**La matrice de droits serait identique.** `COUNTING` autorise déjà exactement
ce qu'un comptage avancé demande — `book_stock`, `count_journals`,
`count_entries`, `locations`, `zones` ouverts ; articles, nomenclatures et
seuils fermés :

```python
CampaignStatus.COUNTING: Editable(
    thresholds=False, items=False, boms=False,
    locations=True, book_stock=True, zones=True,
    count_journals=True, count_sheets=True, count_entries=True,
    adjustments=False, analysis=False,
)
```

**Le référentiel doit être gelé, ce qui exclut `PREPARATION`.** Un article compté
à J-2 puis dont le prix change avant le jour J casserait la valorisation du lot.
`PREPARATION` laisse `items`, `boms` et `thresholds` ouverts : y placer un
comptage avancé serait un défaut, pas une commodité.

**Le coût d'un statut de plus est élevé et se paie partout.** Il traverse
`CAMPAIGN_TRANSITIONS`, `_EDITABILITY`, `campaign_transition_blockers`, le
contrat `Editable` côté serveur *et* côté navigateur, la barre latérale qui
affiche trois phases, l'ensemble des tests de workflow, la table Delta
`campaign`, et le libellé de phase dans tous les exports — pour aboutir à une
ligne recopiée de `COUNTING`.

**Ce qui remplace le statut :** un **jalon** dans la phase de comptage. Il en
existe déjà un — `campaign.book_stock_frozen_at` — auquel s'ajoute
`campaign.general_count_opened_at`. Avant le jalon, la campagne est en comptage
avancé ; après, en comptage général. La sous-phase est un état **dérivé**, pas
un statut de plus, exactement comme `ZoneStatus.PENDING` / `IN_PROGRESS` se
déduisent des quantités.

L'interface, elle, peut parfaitement afficher « Comptage — lots avancés » puis
« Comptage — général » : c'est un libellé, pas une machine à états.

---

## 7. Le scellement : la vraie nouveauté architecturale

Un comptage avancé posté doit cesser d'être modifiable — sinon la preuve du 22
ne vaut rien le 24.

Or **la matrice de gel est globale par statut de campagne, pas par objet.**
`mutability_of(status)` répond « peut-on écrire des journaux dans cette
campagne ? », et tant que la campagne est en `COUNTING` la réponse est oui pour
tous les journaux, y compris ceux qu'on vient de poster : `set_status` accepte
de repasser un journal en `IN_PROGRESS`.

Il faut donc introduire le **premier gel par objet** du produit :
`count_journal.sealed_at` / `sealed_by`. Deux règles pour que cela n'affaiblisse
pas le modèle existant :

1. **Le scellement ne fait que restreindre.** `mutability_of` reste consulté en
   premier et garde le dernier mot pour interdire ; le scellement s'y ajoute,
   il ne peut jamais rouvrir ce que la campagne a fermé. Sans cette règle, on se
   retrouverait avec deux sources de vérité qui se contredisent — exactement ce
   que `workflow.py` a été écrit pour éviter.
2. **Le descellement est un geste tracé**, avec motif obligatoire et événement
   d'audit dédié. C'est ce qui rend la disposition « Recompter » possible sans
   ouvrir une porte dérobée.

Même exigence pour les zones GENERIQUE si un lot avancé porte de la saisie
libre : la zone doit être déclarée terminée **et** scellée.

---

## 8. Modèle de données proposé

Migration `025_comptages_avances.sql` (nouvelle, forward-only — aucune migration
livrée n'est modifiée) :

```
early_count_batch              id, campaign_id, code, label, counted_on,
                               opened_at, closed_at, sealed_at, actor, deleted_at
early_count_batch_location     batch_id, campaign_id, warehouse_id, location_id
                               (clé composite (id, campaign_id) comme partout)

early_count_baseline           campaign_id, batch_id, warehouse_id, location_id,
                               item_number, qty, unit, import_batch_id
                               -- « livre@T0 », figé, jamais remplacé

early_count_drift              campaign_id, warehouse_id, location_id, item_number,
                               qty_baseline, qty_counted, qty_book_j,
                               drift_qty, drift_value, is_material,
                               disposition, cause_code, comment,
                               resolved_at, resolved_by

count_journal                  + early_batch_id, sealed_at, sealed_by
campaign                       + general_count_opened_at
```

Aucune colonne ajoutée à `book_stock` : il reste la photo du jour J.

Les conventions du dépôt s'appliquent : `NUMERIC(20,6)` pour les quantités,
`NUMERIC(20,2)` pour les valeurs, `deleted_at` plutôt que la suppression
physique, clés composites `(id, campaign_id)` pour qu'un enfant ne puisse pas
appartenir à la campagne d'un autre.

---

## 9. Impact sur le calcul de l'écart et l'affichage

`VarianceLine` ne change pas de définition : `physique = compté + ajusté`,
`écart = physique − livre@J`. C'est ce qu'il faut préserver.

Ce qui s'ajoute, **à côté** et seulement pour les lignes concernées :

```
écart affiché = (compté@T0 − livre@T0)   ← écart d'inventaire
              − (livre@J  − livre@T0)     ← dérive post-scellement
              + ajustements
```

Concrètement, dans la vue Écarts :

- deux colonnes supplémentaires, `Écart d'inventaire` et `Dérive`, vides pour
  les emplacements comptés normalement ;
- un indicateur de campagne : nombre d'emplacements précomptés, dont combien
  sans dérive — le cas nominal, qui est aussi la mesure de l'efficacité du
  balisage ;
- une cause pré-remplie lorsque la disposition « Accepter » est retenue.

**Référentiel des causes :** ajouter au moins `MOUVEMENT_APRES_SCELLEMENT`, et
probablement `SAISIE_ERP_TARDIVE`. Sans ces codes, l'exploitant classera ces
lignes en « autre » et le rapport perdra précisément la lisibilité qui justifie
tout ce travail.

---

## 10. Contrôles et blocages

| Moment | Code | Sévérité | Règle |
|---|---|---|---|
| Ouverture du comptage général | `EARLY_BATCH_NOT_SEALED` | Avertissement | Un lot avancé n'est ni clos ni scellé |
| Après chargement général | `EARLY_COUNT_ITEM_APPEARED` | À regarder | Article présent dans `livre@J` sur un emplacement scellé, absent à T0 |
| Après chargement général | `EARLY_COUNT_ITEM_VANISHED` | À regarder | Article présent à T0, absent de `livre@J` |
| Passage en `ANALYSIS` | `EARLY_COUNT_DRIFT_UNRESOLVED` | **Bloquant** | Une dérive matérielle n'a pas de disposition |
| Informatif, permanent | — | — | Taux d'emplacements précomptés sans dérive |

Le blocage à l'entrée en `ANALYSIS` s'ajoute aux trois existants
(`BOOK_STOCK_NOT_FROZEN`, `JOURNALS_NOT_POSTED`, `ZONES_NOT_DONE`) dans
`campaign_transition_blockers`, et suit la même forme : il est *retourné*, pas
levé, pour que l'interface puisse afficher « ce qui manque » sans tenter la
transition.

---

## 11. Publication et archive

Le job `publish_campaign_to_delta.py` écrit neuf tables. Il faut lui en ajouter
deux — `early_count_baseline` et `early_count_drift` — et faire remonter le
scellement dans `count_result`. Sans cela, l'archive contiendrait un écart
décomposé dont la décomposition n'est plus vérifiable : `livre@T0` n'existerait
nulle part, et la disposition retenue non plus.

Les tables cibles doivent être créées avant le premier passage, le job vérifiant
leur existence en tête (`_missing_tables`), et `publication` reste écrite en
dernier.

---

## 12. Le nouveau processus, étape par étape

### Avant le jour J

1. **Préparation** — inchangée. Référentiels, seuils, zones, feuilles. La
   campagne passe en **Comptage**.
2. **Créer un lot avancé** : un code, un libellé, une date de comptage prévue,
   et la liste des emplacements concernés.
3. **Charger le stock ERP du lot** (mode avancé, partiel). Effet : les
   emplacements manquants entrent au référentiel, les journaux du lot sont
   créés en `PENDING`, `livre@T0` est enregistré.
4. **Compter** ces emplacements comme n'importe quels autres — scan d'étiquettes
   ou saisie, feuilles GENERIQUE si nécessaire.
5. **Poster les journaux du lot.**
6. **Clore et sceller le lot.** À partir d'ici, ses journaux ne se modifient plus
   sans descellement tracé.
7. **Baliser physiquement** les emplacements. Cette étape n'est pas dans
   l'application, mais c'est elle qui rend tout le reste valable ; la date et
   l'auteur du scellement sont ce qui la documente.

Les étapes 2 à 6 se répètent autant de fois qu'il y a de lots (J-3, J-2, J-1…).

### Le jour J

8. **Ouvrir le comptage général** (`general_count_opened_at`). Les lots non
   scellés sont signalés.
9. **Charger le stock ERP général**, sur tout le périmètre. Effet :
   `book_stock` est remplacé, les journaux manquants sont créés, **les journaux
   scellés ne sont ni recréés ni modifiés**, et la dérive est calculée pour
   chaque emplacement scellé.
10. **Geler le stock ERP.**
11. **Traiter les dérives.** Pour chaque ligne matérielle, une disposition :
    recompter, ajuster, ou accepter avec cause. C'est la seule étape vraiment
    nouvelle pour l'exploitant.
12. **Compter le reste** normalement, poster, clore les zones.
13. **Passer en Analyse** — bloqué tant qu'une dérive matérielle n'a pas de
    disposition.

### Ensuite

14. Analyse, ajustements, causes, clôture, publication : inchangés, à ceci près
    que les écarts des emplacements précomptés portent leur décomposition et,
    le cas échéant, leur cause « mouvement après scellement ».

---

## 13. Points ouverts

Ce qui reste à trancher avant d'écrire le code.

**Qui a le droit de desceller ?** Le descellement annule une preuve. Il devrait
probablement être réservé au propriétaire de la campagne, et pas au premier
compteur qui trouve le bouton.

**Le périmètre change entre T0 et J.** Un emplacement désactivé après avoir été
précompté, ou activé après coup : les deux cas doivent avoir une réponse écrite,
sinon ils produiront des dérives fantômes.

**Un article compté à T0 qui n'est pas au référentiel.** Le chargement avancé
doit se comporter comme le chargement général — la ligne est dite en
avertissement, pas refusée — mais il faut vérifier que la baseline le tolère.

**La valorisation.** Les prix sont snapshotés par campagne et les référentiels
sont gelés dès l'entrée en comptage : la dérive se valorise donc au même prix
que l'écart. Rien à faire, mais il faut que cela reste vrai.

**Le miroir ERP.** `erp_stock_snapshot` conserve maintenant plusieurs jours
d'historique (paramètre `--stock-days`). `livre@T0` reste donc rechargeable a
posteriori depuis le miroir, ce qui est un filet utile — mais cela ne remplace
pas la baseline stockée dans la campagne, qui doit rester dans le dossier
d'inventaire et dans l'archive.

**Un comptage avancé avant que le stock ERP soit disponible.** Le cas est
possible pour un magasin extérieur. Il faudrait alors autoriser un lot sans
baseline, avec `livre@T0` inconnu — donc pas d'écart d'inventaire calculable,
seulement `compté@T0` face à `livre@J`. C'est le comportement de `BOOK_ENFORCED`
inversé, et il mérite d'être traité explicitement plutôt que découvert en
production.

**La granularité du scellement.** Ce document propose de sceller le journal. Il
serait aussi défendable de sceller l'emplacement (donc toute écriture le
concernant, journal *et* feuille GENERIQUE). Le second est plus sûr et plus
coûteux ; le choix dépend de la fréquence réelle des saisies libres sur les
zones précomptées.
