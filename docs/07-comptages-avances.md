# Comptages avancés

> Étude de conception. Rien de ce document n'est implémenté à ce jour : il
> décrit la logique à écrire, ce qu'elle change dans l'existant, et ce qu'elle
> demande à l'exploitation. Les algorigrammes correspondants sont dans
> [`08-algorigrammes.md`](08-algorigrammes.md).

---

## 1. Le besoin

Compter, un ou plusieurs jours avant le jour J, un sous-ensemble d'emplacements
— zones lentes, magasins extérieurs, stock immobilisé — pour alléger la charge
du jour de l'inventaire général. L'emplacement précompté est ensuite **balisé** :
plus aucun mouvement, ni physique ni informatique.

Preuves, écarts et analyses doivent rester dans **une seule campagne**. Et
l'application doit détecter, chiffrer et faire traiter le cas où la barrière
opérationnelle n'a pas tenu.

---

## 2. Ce que fait réellement un journal de comptage

C'est le point de départ, et il change tout le raisonnement : **poster un
journal de comptage ne consigne pas un écart, il réaligne l'ERP sur le physique
compté.**

Pour le stock géré par lots, le mécanisme est le suivant, palette par palette :

| Situation | Ce que fait l'ERP |
|---|---|
| Palette scannée dans un emplacement | Cet emplacement devient son emplacement ERP officiel |
| Palette non scannée dans son emplacement théorique | Transfert automatique vers l'**emplacement tampon** |
| Palette retrouvée plus tard ailleurs | Transfert du tampon vers sa nouvelle localisation |
| Palette scannée nulle part | Elle reste au tampon |

Le tampon centralise donc, à la fin, **tous les écarts d'inventaire du stock
géré par lots**.

Deux conséquences immédiates, et ce sont elles qui commandent toute la suite.

**Le stock ERP d'un emplacement précompté vaut, après postage, le physique
compté.** Précisément :

```
livre@T0⁺ = compté@T0 + ajusté@T0
```

où `ajusté@T0` est le journal d'ajustement éventuel de l'emplacement, lui aussi
répercuté dans l'ERP.

**Ce qu'on attend le jour J n'est donc pas ce qu'on croirait.** On n'attend pas
`livre@J = livre@T0` au sens du stock ERP d'avant le comptage. On attend :

```
livre@J = physique@T0 = compté@T0 + ajusté@T0
```

C'est-à-dire : le stock ERP du jour J doit être égal au **stock physique** relevé
à T0, pas au stock ERP qui précédait le comptage.

> **Ce qui a changé depuis la première version de cette étude.** Elle raisonnait
> sur trois quantités et supposait le journal purement descriptif. Deux
> conclusions s'inversent : la référence de l'écart n'est plus le stock ERP du
> jour J mais celui d'avant le comptage avancé (§ 4 et 5), et la dérive se mesure
> contre le physique de T0, non contre le stock ERP de T0 (§ 6).

---

## 3. Les quatre quantités

Pour un emplacement précompté, il n'y a pas deux quantités, ni trois, mais
quatre — et la distinction décisive est **avant / après postage**.

| Symbole | Quantité | D'où elle vient |
|---|---|---|
| `livre@T0⁻` | Stock ERP **avant** postage du journal avancé | Chargement partiel, à faire avant de compter |
| `compté@T0` | Physique relevé lors du comptage avancé | Le journal, une fois posté |
| `ajusté@T0` | Ajustements saisis à T0 pour cet emplacement | Journal d'ajustement du lot |
| `livre@T0⁺` | Stock ERP **après** postage et réalignement | Vaut `compté@T0 + ajusté@T0` par construction |
| `livre@J` | Stock ERP du snapshot général gelé le jour J | Chargement général |

Deux grandeurs en découlent, de nature entièrement différente :

```
écart d'inventaire = physique@T0 − livre@T0⁻      ← le résultat de l'inventaire
dérive             = livre@J − physique@T0        ← attendu nul
```

où `physique@T0 = compté@T0 + ajusté@T0`.

---

## 4. Le piège central : sans rien faire, le précomptage efface l'inventaire

L'application calcule aujourd'hui `écart = physique − livre`, où `livre` est le
snapshot gelé du jour J.

Or pour un emplacement précompté, dans le cas **nominal**, `livre@J` vaut
`physique@T0`. L'écart calculé serait donc :

```
physique@T0 − livre@J = physique@T0 − physique@T0 = 0
```

**Zéro.** Le résultat de l'inventaire de tous les emplacements précomptés
disparaîtrait de la campagne — non pas parce qu'il n'y en a pas, mais parce que
l'ERP a déjà été réaligné dessus. Plus le précomptage serait étendu, moins la
campagne mesurerait quoi que ce soit, et l'IRA tendrait vers 100 % par
construction.

C'est le risque principal de cette évolution, et il n'a rien d'exotique : c'est
ce qui arrive si l'on ne fait rien de particulier.

---

## 5. La règle qui résout le piège

Elle est déjà écrite dans le code, dans la définition de l'écart :

```python
@property
def variance_qty(self) -> Decimal:
    """Physical minus book — *the* variance, adjustments included.

    The frozen ERP snapshot stays the reference on the other side: it is
    what the campaign was counted against, and moving it would make the
    variance irreproducible.
    """
```

*Ce contre quoi la campagne a été comptée.* Pour un emplacement ordinaire, c'est
le snapshot du jour J : rien n'a encore été compté quand il est pris. Pour un
emplacement précompté, c'est `livre@T0⁻` : c'est ce contre quoi son comptage
a eu lieu.

D'où la règle, qui n'est pas une exception mais l'énoncé général :

> **La référence d'un inventaire est le stock ERP tel qu'il était juste avant que
> le comptage ne le touche.** Pour la plupart des emplacements, c'est le jour J.
> Pour un emplacement précompté, c'est T0. Même règle, deux dates.

Concrètement, `book_stock` — la table de référence de la campagne — doit
contenir, par emplacement :

| Emplacement | Ce que porte `book_stock` |
|---|---|
| Ordinaire | `livre@J` |
| Précompté et scellé | `livre@T0⁻` |

et `livre@J` des emplacements scellés est stocké **à part**, dans la table de
dérive, où il ne sert qu'au contrôle du scellement.

**Une conséquence à assumer et à afficher.** Le total « stock ERP » de la
campagne devient composite : la plupart des lignes sont à la date du jour J, les
lignes scellées à leur date de précomptage. Quelqu'un qui rapprochera ce total
d'un état ERP tiré à une date unique trouvera une différence — égale à la somme
des écarts d'inventaire des lots avancés. L'écran doit donc porter la mention, et
l'export aussi : sans cela, la première question posée sur ce total n'aura pas de
réponse.

---

## 6. La dérive : ce qu'elle mesure, et ce qu'elle ne mesure pas

```
dérive = livre@J − physique@T0
```

Elle vaut zéro quand la barrière opérationnelle a tenu **et** que le réalignement
ERP s'est bien fait. Quatre causes possibles quand elle ne vaut pas zéro :

1. **Mouvement informatique seul** — régularisation, saisie tardive d'un
   mouvement antérieur, backflush d'une production consommée avant T0. Le
   physique n'a pas bougé.
2. **Mouvement physique réel** malgré le balisage, enregistré dans l'ERP. Le
   physique a bougé, et l'ERP le sait.
3. **Réalignement manqué** — le postage n'a pas été répercuté, ou l'a été
   partiellement. Ce n'est ni une défaillance d'exploitation ni un mouvement :
   c'est un incident informatique.
4. **Erreur de comptage à T0** — mauvaise palette scannée, palette scannée deux
   fois. L'ERP a fidèlement enregistré une erreur.

### Le cas 3 a une signature, et l'application peut la reconnaître

Si le réalignement n'a pas eu lieu du tout, l'ERP est resté à `livre@T0⁻`, donc :

```
dérive = livre@T0⁻ − physique@T0 = −(écart d'inventaire)
```

Une dérive exactement opposée à l'écart d'inventaire n'est donc pas une dérive :
c'est un postage qui n'a pas pris. L'application doit tester cette égalité et
proposer le diagnostic, parce que la disposition qui en découle — rejouer le
postage — n'est aucune des trois autres, et parce que laisser cet incident se
faire traiter comme un mouvement produirait un ajustement compensant une écriture
qui n'a jamais eu lieu.

### Les cas 1, 2 et 4 ne se distinguent pas

Dans les trois, la seule trace est un mouvement ERP. Même le miroir
`erp_mouvements` ne tranche pas : une régularisation, un prélèvement réel et la
correction d'un mauvais scan y ont la même forme — un type, une quantité, une
date. Prétendre en déduire l'explication reviendrait à inventer. **C'est une
décision humaine, et l'application doit l'exiger, pas la deviner.**

### Ce que la dérive ne verra jamais

C'est la limite honnête du dispositif, et elle doit être dite.

La dérive se calcule entre deux lectures de l'ERP. **Elle ne détecte donc que ce
que l'ERP a appris.** Une palette physiquement sortie d'un emplacement scellé
sans aucune transaction ERP laisse `livre@J = physique@T0` : dérive nulle,
scellement déclaré intact.

Deux issues pour cette palette :

- **elle est scannée ailleurs le jour J** — l'ERP la rattache à son nouvel
  emplacement, mais après le gel, donc `book_stock` n'en sait rien. La campagne
  la compte alors **deux fois** : une fois dans `physique@T0` de l'emplacement
  scellé, une fois dans le comptage du jour J. L'anomalie est visible, non pas
  comme une dérive, mais comme un sur-comptage sur la référence (§ 8) ;
- **elle n'est scannée nulle part** — et là, rien ne la voit. L'ERP la croit
  toujours dans l'emplacement scellé, la campagne le confirme, et la perte
  n'apparaîtra qu'à l'inventaire suivant.

Ce second cas mérite d'être regardé en face : comptée le jour J, cette palette
n'aurait pas été scannée, l'ERP l'aurait transférée au tampon, et le tampon aurait
porté la perte. **Le précomptage échange donc une part de pouvoir de détection
contre de la charge en moins, et l'ampleur de l'échange est la durée de la
fenêtre T0 → J.** Aucune écriture de code ne rattrape cela : seul le balisage
physique le fait. C'est un argument pour des fenêtres courtes et pour réserver le
précomptage aux emplacements réellement immobilisés — pas pour renoncer, mais
pour ne pas croire que l'application couvre ce qu'elle ne couvre pas.

---

## 7. Les dispositions

Exclusives, tracées, commentaire obligatoire.

| Disposition | Quand | Effet |
|---|---|---|
| **Rejouer le postage** | Dérive = −écart d'inventaire, ou réalignement partiel constaté | Ni recomptage ni ajustement : l'écriture ERP est reprise, puis la dérive est recalculée |
| **Recompter** | On ne fait plus confiance au comptage de T0 | Descellement tracé à motif obligatoire, recomptage le jour J. Le comptage avancé reste en audit ; celui du jour J fait foi, et la référence redevient `livre@J` |
| **Ajuster** | Le mouvement est réel et le physique a bougé | Ligne d'ajustement, donc `physique = physique@T0 + ajusté`. C'est exactement ce que documente `AdjustmentLine` : « un mouvement de stock réel ». Après quoi la campagne et l'ERP disent la même chose au jour J |
| **Accepter** | Le mouvement est purement informatique | `physique@T0` conservé. Cause obligatoire `MOUVEMENT_APRES_SCELLEMENT`, plus le commentaire |

**Ce qu'il faut savoir de « Accepter ».** Cette disposition laisse volontairement
la campagne et l'ERP en désaccord, de la valeur exacte de la dérive : la campagne
dit que l'emplacement porte `physique@T0`, l'ERP dit `livre@J`, et aucun nouveau
journal ne viendra les réaligner puisque l'emplacement n'est pas recompté. Ce
n'est pas un défaut du dispositif — c'est le sens même de la disposition — mais
c'est ce qui la rend coûteuse, et ce qui justifie la cause obligatoire.

**Ce qu'il ne faut pas faire.** Neutraliser la dérive par une ligne d'ajustement
automatique pour ramener l'écart à zéro. Cela fabriquerait un mouvement de stock
qui n'a pas eu lieu, dans une table dont le sens documenté est « mouvements
réels ». Une dérive s'explique ; elle ne se soustrait pas.

### Les dérives sans ligne en face

Un article peut **apparaître** dans `livre@J` sur un emplacement scellé (jamais
compté, absent à T0) ou en **disparaître**. Le rapprochement est donc une
**jointure externe complète** sur `(emplacement, article)`, jamais une jointure
interne.

Le premier cas est d'ailleurs déjà partiellement couvert : `BOOK_STOCK_NOT_COUNTED`
est un contrôle bloquant qui signale un article porteur de stock ERP sans aucun
comptage. Il se déclenchera de lui-même — mais avec un message qui parlera de
comptage manquant, alors que la cause est un mouvement après scellement. Le
message doit être spécialisé, sans quoi l'exploitant ira chercher au mauvais
endroit.

### Le seuil

Une dérive de 0,000001 ne bloque rien. Réutiliser les **seuils de campagne
existants**, par type d'article, plutôt qu'un réglage de plus qui finirait par
les contredire.

---

## 8. L'emplacement tampon

Il n'est aujourd'hui qu'un emplacement comme un autre pour l'application. Le
précomptage lui donne un rôle particulier, et impose trois règles.

**Le tampon ne se précompte jamais et ne se scelle jamais.** Il est, par
construction, l'emplacement dont le contenu change à mesure que le comptage
avance : chaque palette introuvable y atterrit, chaque palette retrouvée en
repart. Le sceller reviendrait à figer un compteur en cours d'incrémentation.

**Il se compte en dernier**, après tous les autres emplacements de la campagne,
lot avancé compris.

**Son stock ERP au jour J n'est comparable à rien.** Il contient déjà les
palettes déclarées introuvables lors des comptages avancés — ce qui est le
fonctionnement normal, et non une anomalie. Aucun contrôle de dérive ne doit
s'appliquer au tampon.

### Ce que le tampon fait à la lecture des écarts

Le tampon agrège les manquants de tous les emplacements. La lecture **par
emplacement** en devient structurellement trompeuse : un emplacement scellé
paraît juste, le tampon paraît catastrophique, alors qu'il ne s'agit que d'un
déplacement d'écriture.

L'application sait déjà cela et l'a déjà tranché — c'est le sens de la carte
« Perte sèche ou simple transfert ? » et de la lecture par référence, sur
laquelle l'écran d'analyse s'ouvre :

> L'écart vu par emplacement compte deux fois une palette déplacée. La
> différence avec l'écart par référence mesure exactement cette part-là.

Le précomptage ne change pas cette conclusion, il la renforce : **la lecture par
référence est la seule qui ait un sens sur un périmètre où le tampon travaille.**

Et c'est aussi l'instrument du sur-comptage décrit au § 6 : une palette comptée à
T0 dans un emplacement scellé puis re-scannée ailleurs le jour J produit un
excédent sur la référence. D'où un contrôle nouveau, `EARLY_COUNT_DOUBLE_COUNT`,
qui liste les références en excédent au niveau campagne dont une part est portée
par un emplacement scellé, avec l'emplacement scellé et l'emplacement du jour J
en regard. L'application ne peut pas trancher — elle ne connaît pas les palettes,
son grain est `(journal, article, entrepôt, emplacement)` — mais elle peut
désigner les deux emplacements à aller voir.

---

## 9. Pourquoi `BOOK_ENFORCED` ne convient pas

`enforce_book_stock` remplace les lignes du journal par les quantités du
snapshot, et l'écart devient nul par construction.

Avec le réalignement en tête, le défaut se formule plus précisément qu'on ne
l'aurait cru. Puisque `livre@J ≈ physique@T0`, forcer au stock ERP donne
`compté := livre@J ≈ physique@T0` : la **quantité** obtenue est à peu près la
bonne. Ce que l'opération détruit, ce n'est pas le comptage, c'est **la
référence** — l'écart `physique@T0 − livre@T0⁻`, c'est-à-dire le résultat de
l'inventaire, disparaît.

Et elle détruit la dérive avec : le comptage étant forcé à ce que dit l'ERP au
jour J, un mouvement survenu malgré le balisage est absorbé sans un mot. La
campagne adopte silencieusement la version de l'ERP.

`BOOK_ENFORCED` reste juste pour ce pour quoi il a été écrit : un magasin
extérieur dont on reprend le chiffre ERP **sans preuve de comptage**. Il ne sait
pas porter une preuve en face du chiffre.

### Et l'approche par campagnes séparées ?

Arithmétiquement, elle tient mieux qu'il n'y paraît : la petite campagne dédiée
capture correctement `physique@T0 − livre@T0⁻`, et la campagne générale n'a plus
d'écart à constater sur ces emplacements. Ce qu'elle perd :

| Problème | Conséquence |
|---|---|
| La dérive n'est mesurée nulle part | `BOOK_ENFORCED` aligne le comptage sur `livre@J` : un scellement rompu est invisible |
| Référentiels, seuils et prix snapshotés par campagne | La valorisation du lot n'est pas celle de l'inventaire |
| IRA et taux calculés par campagne | Aucun indicateur global juste |
| Archive Delta partitionnée par `campaign_id` | Le dossier est éclaté, et rien ne dit que les archives vont ensemble |
| L'assistant et l'analyse IA lisent une campagne | Ils ne voient jamais le lot en même temps que le reste |
| La réconciliation de flux compare deux campagnes | Une campagne générale amputée de ses lots fausse la comparaison suivante |

---

## 10. « Ces emplacements doivent-ils générer de nouveaux journaux le jour J ? »

**Non.** Un journal par emplacement et par campagne. Celui du lot avancé est le
journal de l'emplacement pour toute la campagne ; le jour J il est simplement
déjà rempli, posté et scellé.

Mais cela oblige à scinder le chargement du stock ERP en deux modes.

| Mode | Périmètre | Comportement |
|---|---|---|
| **Avancé** (nouveau) | Les emplacements du lot | Additif. Complète le référentiel des emplacements, crée les journaux manquants du lot, écrit `livre@T0⁻` dans `book_stock`. Ne gèle rien. |
| **Général** (actuel, amendé) | Tout | Remplace `book_stock` **sauf sur les emplacements scellés**, dont la référence T0 est préservée. Ne recrée pas les journaux existants, n'écrase pas leurs lignes. Écrit `livre@J` des emplacements scellés dans la table de dérive et calcule celle-ci. Puis gel. |

### Un séquencement devenu critique

`livre@T0⁻` doit être capturé **avant le postage du journal avancé**, et en
pratique avant même que le comptage commence. Après postage, l'ERP ne le porte
plus : le réalignement l'a écrasé.

Trois conséquences :

- ouvrir un lot avancé sur un emplacement dont le journal est déjà posté doit
  être **refusé** ;
- le chargement en mode avancé est une **précondition** du comptage du lot, pas
  une formalité qu'on peut faire après ;
- il existe un filet de rattrapage : le miroir conserve désormais plusieurs jours
  de `erp_stock_snapshot` (paramètre `--stock-days`, sept par défaut), de sorte
  qu'une baseline oubliée reste rechargeable a posteriori — dans la limite de la
  fenêtre. Ce filet ne dispense pas de stocker la baseline dans la campagne, qui
  doit figurer au dossier et à l'archive.

---

## 11. Faut-il un nouveau statut de campagne ?

Le découpage envisagé était `PRÉPARATION → COMPTAGES AVANCÉS → COMPTAGE GÉNÉRAL
→ ANALYSE`.

**Recommandation : non.** Une **sous-phase de `COUNTING`**, pas un
`CampaignStatus` de plus.

`COUNTING` porte déjà presque exactement les droits nécessaires — référentiels
gelés, `book_stock`, `count_journals`, `count_entries`, `locations`, `zones`
ouverts :

```python
CampaignStatus.COUNTING: Editable(
    thresholds=False, items=False, boms=False,
    locations=True, book_stock=True, zones=True,
    count_journals=True, count_sheets=True, count_entries=True,
    adjustments=False, analysis=False,
)
```

Et `PREPARATION` serait un mauvais choix : elle laisse `items`, `boms` et
`thresholds` ouverts, si bien qu'un prix modifié entre J-2 et J casserait la
valorisation du lot.

Un statut supplémentaire, lui, traverserait `CAMPAIGN_TRANSITIONS`,
`_EDITABILITY`, `campaign_transition_blockers`, le contrat `Editable` côté
serveur *et* navigateur, la barre latérale à trois phases, tous les tests de
workflow et la table Delta `campaign` — pour aboutir à une ligne recopiée de
`COUNTING`.

Ce qui le remplace : un **jalon**, `campaign.general_count_opened_at`, à côté du
`book_stock_frozen_at` qui existe déjà. Avant le jalon, comptage avancé ; après,
comptage général. La sous-phase est un état **dérivé**, comme
`ZoneStatus.PENDING` / `IN_PROGRESS` se déduisent des quantités. L'interface peut
afficher « Comptage — lots avancés » puis « Comptage — général » : c'est un
libellé, pas une machine à états.

---

## 12. Deux règles par objet, là où tout était global

C'est la vraie nouveauté architecturale, et elle apparaît deux fois.

### Le scellement

Un comptage avancé posté doit cesser d'être modifiable — sinon la preuve du 22 ne
vaut rien le 24. Or la matrice de gel est **globale par statut de campagne** :
tant que la campagne est en `COUNTING`, `set_status` accepte de repasser
n'importe quel journal en `IN_PROGRESS`.

D'où `count_journal.sealed_at` / `sealed_by`, premier gel par objet du produit,
avec deux garde-fous :

1. **le scellement ne fait que restreindre** — `mutability_of` est consulté en
   premier et garde le dernier mot pour interdire ; le scellement s'y ajoute et
   ne peut jamais rouvrir ce que la campagne a fermé. Sans cette règle, deux
   sources de vérité finiraient par se contredire, ce que `workflow.py` a été
   écrit pour éviter ;
2. **le descellement est un geste tracé**, motif obligatoire, événement d'audit
   dédié — c'est ce qui rend la disposition « Recompter » possible sans porte
   dérobée.

### Les ajustements pendant le comptage

`physique@T0 = compté@T0 + ajusté@T0` suppose qu'un journal d'ajustement puisse
être saisi **à T0**, donc pendant la phase de comptage. Or la matrice l'interdit :
`adjustments` n'est ouvert qu'en `ANALYSIS`.

Ouvrir `adjustments` en `COUNTING` globalement serait excessif — cela
autoriserait à ajuster n'importe quel emplacement en plein inventaire. Il faut
donc une règle portée par l'objet : **les ajustements sont permis en `COUNTING`
sur les seuls emplacements d'un lot avancé clos**.

Deux règles par objet, pour deux besoins indépendants : c'est le signe qu'il
s'agit d'un manque du modèle, pas d'un cas particulier.

---

## 13. Modèle de données proposé

Migration `025_comptages_avances.sql`, forward-only — aucune migration livrée
n'est modifiée.

```
early_count_batch            id, campaign_id, code, label, counted_on,
                             opened_at, closed_at, sealed_at, sealed_by,
                             actor, deleted_at
early_count_batch_location   batch_id, campaign_id, warehouse_id, location_id

early_count_drift            campaign_id, batch_id, warehouse_id, location_id,
                             item_number,
                             qty_book_before,   -- livre@T0⁻, recopié pour l'archive
                             qty_counted,       -- compté@T0
                             qty_adjusted,      -- ajusté@T0
                             qty_book_j,        -- livre@J
                             drift_qty, drift_value, is_material,
                             looks_unposted,    -- dérive = −écart : postage non pris
                             disposition, cause_code, comment,
                             resolved_at, resolved_by

count_journal                + early_batch_id, sealed_at, sealed_by
book_stock                   + reference_date, early_batch_id
campaign                     + general_count_opened_at
```

`book_stock` gagne la date de sa référence : c'est elle qui rend lisible le total
composite du § 5, et qui permet à l'export de dire de quand chaque ligne date.
`livre@T0⁻` n'a pas de table à lui — il **est** `book_stock` pour ces
emplacements, ce qui est exactement le sens de la règle du § 5. Sa recopie dans
`early_count_drift` sert la traçabilité de la décision, pas le calcul.

Conventions du dépôt appliquées : `NUMERIC(20,6)` pour les quantités,
`NUMERIC(20,2)` pour les valeurs, `deleted_at` plutôt que suppression physique,
clés composites `(id, campaign_id)` pour qu'un enfant ne puisse pas appartenir à
la campagne d'un autre.

---

## 14. Contrôles et blocages

| Moment | Code | Sévérité | Règle |
|---|---|---|---|
| Ouverture d'un lot | `EARLY_BATCH_JOURNAL_POSTED` | **Bloquant** | Le journal de l'emplacement est déjà posté : `livre@T0⁻` est perdu |
| Ouverture d'un lot | `EARLY_BATCH_BUFFER_LOCATION` | **Bloquant** | Le tampon ne se précompte pas |
| Ouverture du comptage général | `EARLY_BATCH_NOT_SEALED` | Avertissement | Un lot avancé n'est ni clos ni scellé |
| Après chargement général | `EARLY_COUNT_UNPOSTED_SUSPECTED` | À regarder | `dérive = −écart d'inventaire` : le postage n'a pas pris |
| Après chargement général | `EARLY_COUNT_ITEM_APPEARED` | À regarder | Article dans `livre@J` sur un emplacement scellé, absent à T0 |
| Après chargement général | `EARLY_COUNT_ITEM_VANISHED` | À regarder | Article présent à T0, absent de `livre@J` |
| Après le comptage général | `EARLY_COUNT_DOUBLE_COUNT` | À regarder | Référence en excédent dont une part vient d'un emplacement scellé |
| Passage en `ANALYSIS` | `EARLY_COUNT_DRIFT_UNRESOLVED` | **Bloquant** | Une dérive matérielle sans disposition |
| Passage en `ANALYSIS` | `BUFFER_NOT_COUNTED_LAST` | Avertissement | Le tampon a été compté avant un autre emplacement |
| Informatif | — | — | Taux d'emplacements précomptés sans dérive : la mesure de l'efficacité du balisage |

Le blocage à l'entrée en `ANALYSIS` s'ajoute aux trois existants
(`BOOK_STOCK_NOT_FROZEN`, `JOURNALS_NOT_POSTED`, `ZONES_NOT_DONE`) dans
`campaign_transition_blockers`, et suit la même forme : il est *retourné*, pas
levé, pour que l'interface affiche « ce qui manque » sans tenter la transition.

---

## 15. Publication et archive

Le job `publish_campaign_to_delta.py` écrit neuf tables. Il faut lui en ajouter
deux — `early_count_batch` et `early_count_drift` — faire remonter le scellement
dans `count_result`, et ajouter `reference_date` à `book_stock_snapshot`.

Sans `reference_date`, une archive relue dans deux ans laisserait croire que tout
le stock ERP a été photographié le même jour ; sans `early_count_drift`, la
disposition retenue pour chaque dérive n'existerait nulle part, et le
raisonnement ne serait plus rejouable.

Les tables cibles doivent exister avant le premier passage — le job le vérifie en
tête via `_missing_tables` — et `publication` reste écrite en dernier.

---

## 16. Le nouveau processus, étape par étape

### Avant le jour J

1. **Préparation** inchangée : référentiels, seuils, zones, feuilles. Passage en
   **Comptage**.
2. **Créer un lot avancé** : code, libellé, date prévue, liste d'emplacements —
   le tampon exclu.
3. **Charger le stock ERP du lot** (mode avancé, partiel). C'est `livre@T0⁻`, et
   c'est la référence définitive de ces emplacements. Refusé si un journal du lot
   est déjà posté.
4. **Compter** ces emplacements : scan d'étiquettes ou saisie.
5. **Poster les journaux du lot.** L'ERP se réaligne : les palettes scannées
   deviennent le stock officiel de leur emplacement, les introuvables partent au
   tampon.
6. **Saisir l'ajustement du lot** s'il y a lieu, et le poster.
7. **Clore et sceller le lot.** Ses journaux ne se modifient plus sans
   descellement tracé.
8. **Baliser physiquement** les emplacements. L'étape n'est pas dans
   l'application, mais c'est elle qui rend tout le reste valable ; la date et
   l'auteur du scellement sont ce qui la documente.

Les étapes 2 à 8 se répètent pour chaque lot (J-3, J-2, J-1…).

### Le jour J

9. **Ouvrir le comptage général.** Les lots non scellés sont signalés.
10. **Charger le stock ERP général.** `book_stock` est remplacé partout **sauf**
    sur les emplacements scellés ; `livre@J` de ceux-ci part dans la table de
    dérive ; la dérive est calculée.
11. **Geler le stock ERP.**
12. **Traiter les dérives** : pour chaque ligne matérielle, une disposition —
    rejouer le postage, recompter, ajuster, ou accepter avec cause. Seule étape
    réellement nouvelle pour l'exploitant.
13. **Compter le reste** : scan, saisie, feuilles GENERIQUE.
14. **Compter le tampon en dernier**, quand plus aucune palette ne peut en sortir.
15. **Poster, clore les zones.**
16. **Passer en Analyse** — bloqué tant qu'une dérive matérielle n'a pas de
    disposition.

### Ensuite

17. Analyse, ajustements, causes, clôture, publication : inchangés. Les écarts
    des emplacements précomptés portent leur date de référence, et le cas échéant
    leur cause « mouvement après scellement ». La lecture par référence reste
    celle sur laquelle l'écran s'ouvre.

---

## 17. Points ouverts

**Qui a le droit de desceller.** Le descellement annule une preuve. À réserver au
propriétaire de la campagne plutôt qu'au premier compteur qui trouve le bouton.

**L'identification du tampon.** L'application ne connaît pas la notion. Il faut
la déclarer — probablement dans la configuration de campagne, à côté de
`generic_key` qui résout déjà un besoin de même nature.

**Le grain palette.** Le journal de comptage porte
`(journal, article, entrepôt, emplacement)` et une quantité ; il n'y a pas
d'identifiant de palette. Toute la mécanique décrite au § 2 se joue donc dans
l'ERP, et l'application n'en voit que le résultat agrégé. C'est ce qui limite
`EARLY_COUNT_DOUBLE_COUNT` à une désignation d'emplacements plutôt qu'à une
preuve. Remonter l'identifiant de palette dans le journal serait un chantier
distinct, mais c'est lui qui transformerait ce contrôle en certitude.

**Le périmètre qui change entre T0 et J.** Un emplacement désactivé après avoir
été précompté, ou activé après coup : les deux cas doivent avoir une réponse
écrite, sinon ils produiront des dérives fantômes.

**Un article compté à T0 hors référentiel.** Le chargement avancé doit se
comporter comme le général — la ligne est dite en avertissement, pas refusée —
mais il faut vérifier que la référence T0 le tolère.

**Un comptage avancé avant que le stock ERP soit disponible.** Possible pour un
magasin extérieur. Il faudrait alors un lot sans référence T0 : pas d'écart
d'inventaire calculable, seulement `physique@T0` face à `livre@J`. À traiter
explicitement plutôt qu'à découvrir en production.

**La granularité du scellement.** Cette étude scelle le journal. Sceller
l'emplacement — donc toute écriture le concernant, journal *et* feuille
GENERIQUE — serait plus sûr et plus coûteux. Le choix dépend de la fréquence
réelle des saisies libres sur les zones précomptées.

**La durée de la fenêtre T0 → J.** C'est le paramètre qui gouverne le risque
décrit au § 6, et il est opérationnel, pas technique. Il mérite d'être une
décision explicite plutôt qu'une conséquence du planning.
