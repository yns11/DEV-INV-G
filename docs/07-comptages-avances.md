# Comptages avancés

> **Implémenté.** Ce document a d'abord été une étude ; il décrit maintenant ce
> que l'application fait. Les algorigrammes correspondants sont dans
> [`08-algorigrammes.md`](08-algorigrammes.md), le mode d'emploi au § 2.0 et au
> § 2.7 du [guide utilisateur](04-guide-utilisateur.md).
>
> **Révision : le lot a disparu.** L'étude interposait un objet « lot » entre le
> journal ERP et le scellement. Le métier a tranché : **un précomptage couvre
> exactement un journal ERP**, qui couvre un ou plusieurs emplacements. Quatre
> conséquences, qui remplacent partout ce que les sections ci-dessous disent des
> lots :
>
> 1. **Déclarer le périmètre d'un journal *scelle* ses emplacements.** Un seul
>    geste : dire ce que le journal couvre, c'est dire ce qui est compté et ne
>    bougera plus. Ouvrir, clore et sceller n'existent plus.
> 2. **La date de comptage vient des lignes du journal**, colonne « Date de
>    comptage », et n'est plus retapée. C'est elle qui date la référence.
> 3. **Le postage n'est plus exigé pour sceller.** Un journal de précomptage se
>    charge une fois posté et validé dans l'ERP ; le cas ne se rencontre pas, et
>    une garde qui ne se déclenche jamais est une garde qu'on ne sait pas
>    maintenir.
> 4. **Un réimport remplace et met à jour.** Recharger le journal, ou en charger
>    un autre qui touche un emplacement déjà scellé, recalcule le comptage et
>    rescelle.
>
> **Seconde révision : une seule référence, et plus rien ne se calcule à partir
> d'un comptage avancé.** Le stock ERP de référence est unique — le snapshot du
> jour J, gelé, pour tout emplacement — et le scellement n'en pose aucune. La
> dérive et les étiquettes retrouvées ailleurs deviennent deux listes en
> **affichage seul**, sans action requise ni constat bloquant. Les § 7 à 9
> ci-dessous décrivent cet état ; les sections antérieures qui parlent d'`ERP@T0`
> sont à lire comme de l'histoire.
>
> Reste un écart entre l'étude et la réalisation, assumé et expliqué plus bas :
> le journal ERP est un objet **à côté** de `count_journal`, qui reste un par
> emplacement (§ 3).
>
> Les constats chiffrés viennent de l'export post-campagne du 13 juin 2026
> (58 345 lignes, 73 journaux). Ce sont des ordres de grandeur, pas des règles.

---

## 1. Le besoin

Compter, un ou plusieurs jours avant le jour J, un sous-ensemble d'emplacements
— zones lentes, magasins extérieurs, stock immobilisé — pour alléger la charge
du jour de l'inventaire général. L'emplacement précompté est ensuite **balisé** :
plus aucun mouvement, ni physique ni informatique.

Preuves, écarts et analyses doivent rester dans **une seule campagne**. Et
l'application doit signaler ce qui aurait bougé malgré le balisage.

---

## 2. Ce que contient réellement un journal de comptage ERP

Le journal n'est pas une liste de quantités comptées : **il porte sa propre
référence**. Chaque ligne d'`InventoryCountingJournalLines` donne, pour un
article dans un emplacement :

| Colonne ERP | Libellé | Sens |
|---|---|---|
| `OnHandQuantity` | Stock ERP | Le stock ERP **avant** comptage |
| `CountedQuantity` | Qté Comptée | Le physique compté ou scanné |
| `SILlabelID` | Etiquette | L'étiquette logistique — UC, UM, palette |
| `ItemSerialNumber` | Numéro de série | Pour les pièces sérialisées |
| `WarehouseId` / `WarehouseLocationId` | Entrepôt / Emplacement | La localisation de la ligne |
| `InventoryStatusId` | Statut qualité | |
| `JournalNameId` | Type Journal | `INVE` étiquettes, `INVV` vrac |
| `IsPosted` / `PostedDateTime` | Est posté ERP / Date de postage | |

D'où, ligne à ligne :

```
écart de ligne = Qté Comptée − Stock ERP
```

**Une ligne en écart n'est pas une anomalie de stock.** Un écart négatif dans un
emplacement et positif dans un autre, c'est le déplacement physique d'une même
pièce. C'est le cas dominant : sur l'export du 13 juin, 18 696 lignes portent une
arrivée (ERP 0, compté > 0) et 17 971 un départ (ERP > 0, compté 0), pour
21 373 lignes sans écart.

### Deux types de journaux

| Type | Désignation | Granularité |
|---|---|---|
| `INVE` | Étiquettes | article + entrepôt + emplacement + **étiquette** + éventuellement numéro de série. Quantités souvent 0 ou 1 pour les pièces sérialisées |
| `INVV` | Vrac | article + entrepôt + emplacement. Pas d'identification unitaire — l'étiquette y vaut littéralement `VRAC` |

Sur l'export : 62 journaux `INVE` pour 57 936 lignes, 11 journaux `INVV` pour
409 lignes. Le comptage par étiquette est donc l'essentiel du volume.

### Ouvert ou posté

Un journal **ouvert** (non posté) est encore modifiable et en attente de
validation ERP : résultat provisoire, **mais qui doit entrer dans la vision
globale de la campagne**. Un journal **posté** est validé dans l'ERP ; sa date et
son heure de postage doivent être visibles.

---

## 3. Le périmètre d'un journal ne se déduit pas de ses lignes

Un journal appartient à **un seul entrepôt**, mais couvre **plusieurs
emplacements** de cet entrepôt. Sur l'export : 48 journaux sur 73 couvrent plus
d'un emplacement, jusqu'à 54 pour l'un d'eux ; 25 seulement en couvrent un.

Et les emplacements présents sur les lignes **ne suffisent pas** à dire le
périmètre : certaines lignes portent un autre entrepôt ou emplacement uniquement
pour matérialiser un déplacement ou une écriture d'ajustement. Toujours sur
l'export, 36 journaux voient plus d'un entrepôt dans leurs lignes, pour
1 932 lignes au total.

C'est un écart de fond avec le modèle actuel de l'application, où
`count_journal` est créé **un par emplacement** à partir du chargement du stock
ERP. La réalité est : un journal ERP, un entrepôt, un à cinquante-quatre
emplacements, plus des lignes de passage.

### Le périmètre se déclare, il ne se devine pas

> **Tel que réalisé.** `erp_journal` et `erp_journal_scope` portent le journal
> ERP et son périmètre, **à côté** de `count_journal`, qui reste un par
> (campagne, entrepôt, emplacement). C'est l'unité de comptage, de progression
> et de gel dont tout le produit dépend — la clé du journal, le forçage au stock
> ERP, les quantités comptées, les écrans. Les lignes brutes vivent dans
> `erp_journal_line`, au grain de l'étiquette ; l'application agrège vers
> l'emplacement.
>
> Le périmètre non déclaré est signalé en tête du rapport d'import
> (`scopeUndeclared`) et **bloque la déclaration du périmètre**, pas l'import
> lui-même.

À chaque nouveau journal importé, l'application **propose** les entrepôts et
emplacements susceptibles d'être les siens :

- les emplacements présents dans ses lignes,
- **hors `INV / 01`**,
- **hors emplacements déjà alloués à un autre journal**,

et l'utilisateur sélectionne le ou les bons. C'est une saisie courte — souvent un
seul choix évident — et c'est elle qui rend calculables toutes les règles qui
suivent.

Le périmètre décide de deux choses :

| | Ligne **dans** le périmètre | Ligne **hors** périmètre |
|---|---|---|
| Sa quantité comptée | Compte pour l'emplacement | Ne compte pas ici : elle appartient à l'histoire d'un autre emplacement |
| Son `Stock ERP` | Fait la référence de l'emplacement | N'en fait pas |
| Devenir | — | Conservée, signalée, jamais supprimée |

### Un emplacement n'appartient qu'à un journal

Deux comptages avancés à deux jours d'écart peuvent passer par le même
emplacement, et le second n'y fait souvent que déplacer une palette. La question
« lequel des deux le compte ? » a une seule réponse : **celui dont le périmètre
le contient**.

Trois chemins mènent au cas, et chacun a son traitement.

| Situation | Ce qui se passe |
|---|---|
| **Déclarer un emplacement déjà déclaré ailleurs** | Refusé, en nommant le journal propriétaire : « ATP / SOL appartient déjà au périmètre du journal NPEM-A. Descellez-le pour le lui reprendre. » La liste proposée ne l'offrait déjà pas ; le refus est le filet pour l'appel direct |
| **Un autre journal, non déclaré, dont les lignes touchent l'emplacement** | Ses lignes entrent dans `erp_journal_line` — c'est la trace du déplacement, et le contrôle par étiquette la relit — mais **elles ne comptent pas**. Seul le propriétaire compte son emplacement |
| **Transférer l'emplacement d'un journal à un autre** | Descellez le premier (motif obligatoire), déclarez le second. Référence **et** comptage basculent ensemble sur le nouveau journal |

Le tri se fait **ligne par ligne**, pas emplacement par emplacement : un même
fichier apporte les lignes du propriétaire et celles des journaux de passage, et
écarter la clé entière priverait l'emplacement scellé de sa quantité comptée.

### La fenêtre du précomptage se ferme au gel

`declare_scope` refuse une fois `campaign.book_stock_frozen_at` posé, et le
refus se lit. La raison n'est pas prudentielle, elle est définitionnelle :
précompter veut dire *avant* la référence générale. Après le gel, l'emplacement
a déjà la sienne, le journal du jour apporte son comptage par l'import, et il
n'y a rien à sceller.

Le geste était pourtant offert sur tous les journaux, y compris ceux du jour J,
et il écrivait alors une seconde référence sur des clés déjà servies par le
chargement général : `book_stock_uq`, donc un **500** sur une action que
l'application proposait elle-même.

Deux corrections, et elles ne se remplacent pas :

* **le geste disparaît quand il n'a plus de sens** — l'écran affiche « Comptage
  du jour J » au lieu de « À déclarer », et un bandeau dit pourquoi ;
* **l'écriture, elle, remplace au lieu de heurter** — `replace_for_journal`
  supprimait ses seules lignes, il supprime maintenant aussi celles qui portent
  les mêmes clés. C'est ce que le domaine dit depuis le début : « la référence
  d'un emplacement scellé est celle de son précomptage ». Sceller après un
  chargement partiel est un ordre inhabituel, pas une faute, et il ne doit pas
  produire une erreur technique.

**Ce que le gel ne ferme pas** : le descellement, et le rafraîchissement d'un
journal déjà scellé. L'import rescelle au passage — le fermer couperait le
réimport de tous les journaux du jour.

### Pourquoi un journal ERP ne se supprime pas

Le geste inverse de « déclarer et sceller » est **desceller**, pas supprimer, et
c'est le seul offert. Un journal ERP n'est pas une saisie : c'est le reflet d'un
document de l'ERP. Le supprimer ne le retirerait pas de l'ERP, et le prochain
import le ramènerait.

Ce qu'une suppression laisserait derrière elle est pire que le journal qu'elle
retire. `erp_journal.deleted_at` existe — hérité du gabarit de toutes les tables
— mais aucun dépôt, aucune route et aucun écran ne l'écrit, et le poser à la main
en base produirait :

| Ce qui reste | Effet |
|---|---|
| `count_journal.sealed_at` | L'emplacement reste **scellé**, sans journal sur l'écran pour le desceller |
| `erp_journal_scope` | Le périmètre n'est pas en cascade sur un effacement logique : l'emplacement reste **pris**, et `candidate_locations` continue de l'écarter pour tout autre journal |
| `book_stock.erp_journal_id` | La **référence survit** au journal qui la justifiait, sans lien pour la relire |
| `scope_owners` | Ne voit plus le propriétaire : les lignes d'un autre journal **recommencent à compter** l'emplacement |

Un emplacement scellé, sans propriétaire, indéclarable et indescellable : la
suppression fabriquerait exactement l'incohérence que le scellement existe pour
empêcher. Les trois besoins réels ont chacun leur geste — desceller pour un
périmètre coché de travers, réimporter pour des lignes fausses, et ne rien faire
pour un journal chargé par erreur, qui sans périmètre déclaré ne produit ni
référence, ni comptage, ni écart.

L'ordre des gestes est indifférent. Si les deux journaux entrent avant qu'aucun
ne soit déclaré, l'import ne sait pas encore trier et l'emplacement porte leur
somme ; **déclarer le périmètre recalcule le comptage** sur le seul
propriétaire, exactement comme il en pose la référence. Les deux nombres d'un
même écart sortent de la même agrégation.

> Sans cette règle, l'emplacement affichait le stock ERP d'un journal contre le
> comptage d'un autre — un écart entre deux journaux, et rien pour le dire. Et
> déclarer deux fois le même emplacement remontait une violation d'unicité
> brute, c'est-à-dire un 500 devant lequel il n'y a rien à faire.

---

## 4. Ce que devient une pièce absente de son emplacement

Trois situations, et elles se lisent dans les lignes.

**Cas A — trouvée dans le périmètre du journal.** L'ERP la croit ailleurs, elle
est physiquement comptée dans un des emplacements couverts. Le stock existe, sa
localisation ERP est fausse, et la ligne positive donne le nouvel emplacement.

**Cas B — absente de son emplacement ERP.** Elle est théoriquement dans un
emplacement du périmètre, elle n'y est pas comptée : la quantité comptée de
l'ancien emplacement passe à zéro.

- **B.1 — retrouvée dans un autre journal encore ouvert.** Les pièces ou
  l'étiquette sont comptées ailleurs, dans un journal non posté. Il faut donc
  considérer le stock comme absent de l'ancien emplacement. *Si l'autre journal
  avait déjà été posté, l'étiquette n'apparaîtrait pas comme stock ERP sur
  celui-ci* — ce qui est aussi ce qui rend le rapprochement possible.
- **B.2 — retrouvée nulle part.** Elle est affectée à l'emplacement tampon :
  entrepôt `INV`, emplacement `01`.

---

## 5. `INV / 01`, l'emplacement tampon

Il est **entièrement virtuel** : aucun emplacement physique de l'usine ne lui
correspond, et l'ERP n'y crée aucun journal de comptage.

Dans l'application :

- le journal de cet emplacement est créé au chargement du stock ERP du jour J,
  puis **désactivé par l'exploitant** — la fonction de désactivation existe
  déjà ;
- ses lignes sont **importées et conservées** pour la traçabilité.

### Pourquoi la désactivation n'est pas un détail

Une pièce introuvable produit deux lignes : un départ de son emplacement réel
(ERP 1, compté 0) et une arrivée au tampon (ERP 0, compté 1). Additionnées à
l'échelle de l'article, elles se compensent exactement — et la perte
disparaîtrait.

Désactiver `INV / 01` retire l'emplacement du périmètre « quantités, valeurs et
dénominateur de progression », comme le documente `set_location_status`. Il ne
reste alors que la ligne de départ, et **la perte redevient un écart visible**.

L'ERP et l'application représentent donc la même chose de deux façons : l'ERP
concentre les pertes dans un emplacement, l'application les laisse à
l'emplacement où elles ont été constatées. La désactivation est ce qui fait le
pont, et c'est pour cela qu'elle est obligatoire.

Ordre de grandeur, sur l'export du 13 juin : 1 843 lignes `INV / 01` réparties
sur 32 journaux, dont 1 719 en arrivée.

**Le tampon ne se précompte jamais et ne se scelle jamais**, et aucun contrôle de
dérive ne s'y applique.

---

## 6. Étiquette et numéro de série : deux dimensions nouvelles

À ajouter dans l'application : `SILlabelID` (étiquette) et `ItemSerialNumber`
(numéro de série).

**Règles de typage, sans exception :** conservés comme du **texte**, zéros
initiaux préservés, jamais convertis en nombres, et portés jusqu'aux écrans, aux
exports et aux traces d'audit. Les étiquettes de l'export ressemblent à
`001609231` : la moindre conversion numérique en détruirait la moitié.

**Le grain de calcul ne change pas.** Tous les calculs et toutes les analyses
continuent de se faire par **emplacement + numéro d'article**. Les dimensions
fines servent à deux choses, et à deux choses seulement :

1. la traçabilité ;
2. **signaler qu'une étiquette enregistrée sur un emplacement précompté et scellé
   se retrouve comptée à un *autre emplacement*.**

C'est le seul contrôle du dispositif qui descende au grain de l'étiquette, et il
est proportionné : sur l'export du 13 juin, 433 étiquettes sur 39 558 apparaissent
dans plus d'un journal — environ 1 %, une liste qu'on peut réellement traiter.

### Un journal vrac ne compte pas des lots

Les lignes d'un journal `INVV` portent toutes la même étiquette générique —
littéralement « VRAC » dans l'export. Ce n'est pas l'identité d'une palette,
c'est un remplissage de colonne : un emplacement vrac se gère **en quantité**.

Le contrôle la lisait pourtant comme une identité, et deux emplacements vrac
quelconques devenaient donc « la même étiquette comptée aux deux endroits » —
quatre cents lignes de faux doublons sur une campagne réelle, à trancher une par
une, devant lesquelles il n'y a rien à faire.

La règle porte sur le **type de journal**, pas sur la valeur de l'étiquette :
c'est ce que le métier dit, et non une chaîne de caractères qui pourrait changer
au prochain export. Elle est écrite une seule fois, dans la définition des
lignes qui portent une étiquette identifiante, et les deux contrôles — les deux
côtés de chacun — la lisent de là. Écrite quatre fois, il aurait suffi de
l'oublier d'un côté pour que le contrôle continue de lire les journaux vrac de
l'autre.

### Deux journaux ne font pas un déplacement

La condition portait sur le journal seul : *une autre ligne, dans un autre
journal*. Deux journaux passant sur le même emplacement scellé remplissaient
donc l'écran de lignes dont les deux colonnes d'emplacement portaient la même
valeur — « ATP / SF1 comptée aussi en ATP / SF1 » — et l'application proposait de
mettre la pièce « au nouvel emplacement ». Il n'y en a pas.

Le contrôle exige maintenant **un autre emplacement**, et part du **journal
propriétaire** : celui dont le périmètre contient l'emplacement scellé. Sans
cela, la ligne de passage d'un troisième journal servait à son tour de point de
départ, et la même paire ressortait autant de fois que de journaux ayant touché
l'emplacement.

Ce qui en sort n'est pas perdu. Un second journal qui recompte un emplacement
scellé **au même endroit** est un fait réel — les quantités peuvent différer,
104 contre 93 sur l'export observé — et il est résumé à part, par emplacement et
par journal, avec celui qui est retenu et celui qui ne l'est pas. C'est le seul
renseignement utile : il n'y a rien à trancher, puisque la pièce est là où elle
doit être.

---

## 7. La référence est unique : le stock ERP du jour J

**Il n'y a qu'une référence dans une campagne** : le snapshot du stock ERP du
jour J, gelé, pour tout article et tout emplacement — précompté ou non.

Ce n'a pas toujours été le cas, et la version précédente de ce document décrit
l'inverse : un emplacement précompté portait sa propre référence, `ERP@T0`, lue
dans la colonne « Stock ERP » de son journal. La raison de l'abandon est dans
**l'ordre des faits**.

Un journal de précomptage est **posté dans l'ERP** avant que la photo du jour J
ne soit prise. Cette photo l'a donc déjà intégré : la correction d'inventaire
que le précomptage a produite est enregistrée, dans l'ERP, avant la campagne.
Mesurer une seconde fois contre un état antérieur revenait à **compter deux fois
la même correction**, dans le sens qui flatte le résultat. C'est aussi ce qui
rendait le dispositif si difficile à lire : deux axes qui se ressemblaient, six
gestes dont quatre ne changeaient aucun chiffre.

Un précomptage, c'est donc : importer le journal, et déclarer son périmètre — ce
qui le scelle. Le scellement dit **quels emplacements ce journal-là compte**, et
rien d'autre. Deux quantités suffisent :

| Symbole | Quantité | Origine |
|---|---|---|
| `compté@T0` | Ce que le précomptage a relevé | Colonne `Qté Comptée` du journal, dans son périmètre |
| `ERP@J` | La référence, unique | Chargement général du stock ERP, le jour J |

La colonne `Stock ERP` du journal n'est plus lue par personne : ce n'est pas une
référence, c'est ce que l'ERP annonçait au moment du relevé. Elle reste
consultable sur l'écran des lignes brutes.

---

## 8. La conséquence, et il faut l'annoncer

Un emplacement précompté montre désormais un **écart voisin de zéro**.

```
compté@T0 − ERP@J ≈ 0
```

Ce n'est pas une perte d'information, c'est le constat que le réalignement a eu
lieu. Le résultat de l'inventaire de cet emplacement **n'est pas perdu** : il a
été enregistré plus tôt, dans l'ERP, avant la campagne. La campagne, elle, mesure
ce qui reste à corriger au jour J — et sur un emplacement balisé et déjà posté,
il ne reste rien. Plus on précompte, plus l'IRA de la campagne est bon, et c'est
la vérité de ce qu'elle mesure.

C'est aussi pourquoi **aucun écart ni indicateur d'avancement n'est affiché tant
que le stock ERP n'est pas gelé**, même si des comptages avancés ont eu lieu. Un
écart a besoin d'une référence ; il n'y en a qu'une, et elle arrive le jour J.
L'écran affichait auparavant des « écarts partiels » sur les emplacements
scellés en tenant leur référence pour figée : elle ne l'était pas, et le chiffre
qu'il montrait comptait deux fois la même correction.

### Comment le total « Stock ERP » se calcule

Une seule table, `book_stock`, une seule ligne par (article, entrepôt,
emplacement) — l'index `book_stock_uq` l'impose — et **une seule origine** : le
chargement du stock ERP du jour J. Il couvre tout, emplacements scellés compris,
et remplace l'intégralité de la photo à chaque chargement.

#### Les quantités

```
Stock ERP        = Σ book_stock.qty                    (une ligne par clé)
Stock compté     = Σ quantités des journaux de comptage
                   + consolidation GENERIQUE en direct
Stock physique   = Stock compté + ajustements postés
Écart net        = Stock physique − Stock ERP
```

Deux exclusions, qui portent sur **les deux côtés à la fois** — quantités *et*
valeurs : les emplacements **désactivés** (`INV / 01`, le tampon) et les
articles **exclus du périmètre**.

Du côté compté, deux règles décident ce qui entre :

* seuls les journaux **`IN_PROGRESS` et `POSTED`** comptent. Un journal
  `PENDING` est un emplacement qu'on n'a pas encore touché, et le compter à
  zéro inventerait un manquant ;
* un journal **`BOOK_ENFORCED`** contribue la quantité du stock ERP elle-même —
  écart nul par construction, ce qui est exactement ce que ce statut promet.

> C'est pour cela que **sceller met le journal de comptage en `IN_PROGRESS` ou
> `POSTED`**, selon que le journal ERP est posté. Un emplacement scellé resté
> `PENDING` n'apportait rien au stock physique alors que la référence du jour J
> le couvre : un manquant fantôme de la totalité de sa quantité, sur un
> emplacement dont le scellement affirme précisément qu'il est compté.

Un emplacement précompté et scellé est donc présent **des deux côtés** : sa ligne
`book_stock` du jour J, et les lignes de son journal de comptage, posées par
l'import ou recalculées par la déclaration.

#### Les valeurs

**Une seule base de valorisation dans toute la campagne :**

```
valeur = prix standard du référentiel × quantité
```

pour le stock ERP **comme** pour le stock compté. C'est ce qui rend les deux
comparables : un écart en euros mesure alors une différence de *quantité*, et
rien d'autre.

Les lignes de stock portent bien un coût — celui que l'ERP tenait au gel — mais
il ne valorise plus rien. Le laisser décider avait deux effets, et aucun n'était
voulu : la valeur d'un article dépendait de l'ordre de ses lignes, et le stock
ERP se valorisait autrement que le comptage auquel on le compare. Ce coût ne sert
plus que de **secours** : article inconnu du référentiel, ou prix standard nul —
mieux vaut la valeur que l'ERP portait que zéro.

La règle vaut partout, et c'est le point : les écarts, les KPI du carrousel, la
grille Stock ERP et son total, l'export Excel et la liste des articles non
comptés lisent tous la même valorisation.

**Une seule date, désormais.** Le total ERP n'est plus composite : toutes les
lignes portent la date de la photo du jour J. La colonne `reference_date` reste
affichée et exportée — c'est ce qui permet de rapprocher la campagne d'un état
ERP tiré le même jour sans avoir à deviner lequel.

---

## 9. La dérive : un indice, pas un écart

```
dérive = ERP@J − compté@T0
```

par article et par emplacement scellé. **Attendue nulle**, pour la raison du § 7 :
poster le journal du précomptage a réaligné l'ERP avant que la photo du jour J ne
soit prise.

Ce qui subsiste après ce réalignement n'est pas un écart d'inventaire — celui-là
se mesure contre la référence unique — mais **ce qui a bougé entre les deux
dates** : une sortie, une réception, une correction saisie entre-temps.

**Aucune action requise, aucun constat bloquant.** La dérive se regarde. Elle ne
retient plus le passage en analyse, et il n'y a rien à trancher : la question
« quelle quantité fait foi au jour J ? » n'a plus d'objet, puisque la réponse est
toujours la même — celle du jour J.

Seules les dérives **non nulles** sont affichées : une ligne à zéro est le cas
normal, donc l'absence d'information, et sur un précomptage de cinquante
emplacements à trois cents références elle enterrait les quelques lignes qui
apprennent quelque chose. Le calcul, lui, les produit et les conserve toutes.

### Ce qui a été retiré, et pourquoi

Les versions précédentes proposaient deux issues — **conserver le comptage
avancé** et **recompter le jour J** — plus, avant elles, « rejouer le postage »
et « ajuster ».

Les quatre reposaient sur la même prémisse : qu'un emplacement scellé porte sa
propre référence, et qu'il faille arbitrer entre deux vérités. Cette prémisse est
tombée avec `ERP@T0` ; les issues sont tombées avec elle. Ce qui reste — la
colonne d'ajustement pour un mouvement réel, le descellement pour un emplacement
qu'on veut recompter — existe toujours, à sa place, et se déclenche pour ses
raisons propres plutôt que comme branche d'une dérive.

### Ce que la dérive ne verra pas

Elle se calcule entre deux lectures de l'ERP, donc **elle ne voit que ce que
l'ERP a appris**. Une pièce sortie d'un emplacement scellé sans aucune
transaction laisse `ERP@J = compté@T0` : dérive nulle, scellement déclaré intact.

C'est précisément le trou que montre le contrôle par étiquette du § 6 : si cette
pièce est re-scannée ailleurs le jour J, son étiquette apparaît dans un second
journal, et la liste la désigne — avec l'emplacement scellé et le nouveau, à
aller voir. Cette liste est elle aussi **en affichage seul** : elle n'exclut rien
d'aucune agrégation, parce qu'une pièce comptée deux fois se règle sur le
terrain, pas en retirant une ligne d'une somme.

Reste le cas où elle n'est scannée nulle part : rien ne la voit, l'ERP la croit
toujours dans l'emplacement scellé, et la perte n'apparaîtra qu'à l'inventaire
suivant. Comptée le jour J, elle serait partie au tampon et la perte aurait été
constatée. **Le précomptage échange donc une part de pouvoir de détection contre
de la charge en moins, et la fenêtre T0 → J en mesure l'ampleur.** Aucun code ne
rattrape cela ; seul le balisage physique le fait. C'est un argument pour des
fenêtres courtes et pour réserver le précomptage aux emplacements réellement
immobilisés.

---

## 10. L'import des journaux : photographies successives

Le notebook est exécuté ponctuellement pour charger les journaux des précomptages
avancés, puis **très régulièrement le jour J**. Chaque exécution fournit une
nouvelle photographie.

Les règles :

- l'application intègre **aussi bien les journaux ouverts que les journaux
  postés** — un comptage en cours fait partie de la vision globale ;
- les écarts et rapprochements sont **recalculés à chaque import réussi** ;
- **l'heure du dernier import réussi est affichée** en évidence ;
- l'application **n'additionne jamais plusieurs photographies** — sauf pour les
  comptages avancés.

Cette dernière règle se traduit d'une seule façon dans le code : **le
remplacement se fait par numéro de journal ERP, jamais globalement.** Un journal
présent dans la photographie voit ses lignes remplacées ; un journal absent garde
les siennes. Les journaux avancés, qui ne sont plus dans la fenêtre de dates du
jour J, survivent donc naturellement, et un journal scellé n'est de toute façon
jamais réécrit.

Un effet de bord à assumer : un journal supprimé dans l'ERP ne disparaît pas de
l'application, puisque son absence de la photographie est indiscernable d'un
journal simplement hors fenêtre. C'est le prix de la règle, et il est faible —
mais il vaut mieux l'écrire ici que le découvrir.

---

## 11. Contrôles

### Import

| Code | Sévérité | Règle |
|---|---|---|
| `JOURNAL_LINE_DUPLICATE` | **Bloquant** | Doublon sur les coordonnées de la ligne : `Journal ERP` + `Site` + `Entrepôt` + `Emplacement` + `Etiquette` + `Numéro d'article` |
| `IMPORT_STALE` | À regarder | Fraîcheur du dernier import : son heure et son âge |
| `IMPORT_INCOMPLETE` | À regarder | Complétude : journaux attendus absents de la photographie |
| `JOURNAL_MULTI_WAREHOUSE` | À regarder | Un journal dont le périmètre couvrirait plus d'un entrepôt |
| `JOURNAL_LINES_OUT_OF_SCOPE` | À regarder | Lignes hors périmètre — **conservées**, jamais supprimées |
| `JOURNAL_SCOPE_UNDECLARED` | **Bloquant** | Un journal importé dont le périmètre n'a pas été sélectionné |

### Comptages avancés

| Code | Sévérité | Règle |
|---|---|---|
| `EARLY_BATCH_BUFFER_LOCATION` | **Bloquant** | `INV / 01` dans le périmètre d'un journal de précomptage |
| `EARLY_LABEL_COUNTED_ELSEWHERE` | À regarder | **Une étiquette d'un emplacement scellé comptée dans un autre journal** |
| — | Informatif | Taux d'emplacements précomptés sans dérive : la mesure de l'efficacité du balisage |

**Aucun blocage n'est ajouté à l'entrée en `ANALYSE`.** Un
`EARLY_COUNT_DRIFT_UNRESOLVED` a existé — une dérive matérielle sans issue
arrêtait le passage en analyse — et il est parti avec les issues. Une dérive
n'entraîne aucune action requise et aucun constat bloquant : les trois blocages
restent `BOOK_STOCK_NOT_FROZEN`, `JOURNALS_NOT_POSTED` et `ZONES_NOT_DONE`.

---

## 12. Ce qui change dans le modèle de données

Migration `025_comptages_avances.sql`, forward-only — aucune migration livrée
n'est modifiée.

```
-- Le journal devient un objet ERP, avec un périmètre déclaré
count_journal              + erp_journal_number, erp_journal_name_id (INVE|INVV),
                             erp_is_posted, erp_posted_at,
                             early_batch_id, sealed_at, sealed_by
count_journal_scope          journal_id, campaign_id, warehouse_id, location_id
                             -- les emplacements que le journal couvre réellement

-- Les dimensions fines, en texte, pour la traçabilité et le contrôle étiquette
count_journal_line         + label_id TEXT, serial_number TEXT,
                             inventory_status_id TEXT, qty_on_hand
                             -- qty_on_hand = « Stock ERP », la référence de la ligne

-- Les précomptages
early_count_drift            campaign_id, erp_journal_id, warehouse_id, location_id,
                             item_number, qty_counted_t0, qty_erp_j,
                             drift_qty, drift_value

-- La référence porte sa date, et elle n'en a qu'une
book_stock                 + reference_date
campaign                   + general_count_opened_at, journals_imported_at
```

`qty_on_hand` sur la ligne ERP est ce que l'ERP annonçait au moment du relevé.
Elle se consulte, elle ne référence rien : la référence de la campagne est le
stock ERP du jour J.

Deux tables ont disparu avec les décisions qu'elles portaient : `early_count_batch`
— le journal ERP *est* le précomptage — et `early_count_label_decision` — une
étiquette scellée retrouvée ailleurs se regarde, elle ne se tranche plus.

Conventions du dépôt appliquées : `NUMERIC(20,6)` pour les quantités,
`NUMERIC(20,2)` pour les valeurs, `deleted_at` plutôt que suppression physique,
clés composites `(id, campaign_id)`. Étiquette et numéro de série en `TEXT`, sans
normalisation numérique.

---

## 13. Deux règles par objet, là où tout était global

### Le scellement

Un comptage avancé posté doit cesser d'être modifiable, sinon la preuve du 22 ne
vaut rien le 24. Or la matrice de gel est **globale par statut de campagne** :
tant que la campagne est en `COUNTING`, n'importe quel journal peut repasser en
`IN_PROGRESS`.

D'où `count_journal.sealed_at`, premier gel par objet du produit, avec deux
garde-fous :

1. **le scellement ne fait que restreindre** — `mutability_of` est consulté en
   premier et garde le dernier mot pour interdire ; le scellement s'y ajoute et
   ne peut jamais rouvrir ce que la campagne a fermé ;
2. **le descellement est tracé**, motif obligatoire, événement d'audit dédié —
   c'est ce qui rend l'issue « Recompter » possible sans porte dérobée.

Et une précondition : **on ne scelle qu'un journal posté dans l'ERP**.

### Les ajustements pendant le comptage

Le besoin est tombé avec `ERP@T0`. Il venait de `physique@T0 = compté@T0 +
ajusté@T0` : il fallait pouvoir corriger un précomptage à sa date, donc pendant
la phase de comptage, alors que la matrice n'ouvre `adjustments` qu'en
`ANALYSIS`.

`compté@T0` n'admet plus de second terme, et il n'y a donc rien à ouvrir. Un
mouvement réel se saisit par le mécanisme d'ajustement, qui porte sur le jour J
et s'ouvre quand il s'ouvre.

---

## 14. Faut-il un nouveau statut de campagne ?

**Non.** Une sous-phase de `COUNTING`, pas un `CampaignStatus` de plus.

`COUNTING` porte déjà les bons droits — référentiels gelés, `book_stock`,
`count_journals`, `count_entries`, `locations`, `zones` ouverts. `PREPARATION`
serait un mauvais choix : elle laisse `items`, `boms` et `thresholds` ouverts, et
un prix modifié entre J-2 et J casserait la valorisation du précomptage.

Un statut supplémentaire traverserait `CAMPAIGN_TRANSITIONS`, `_EDITABILITY`,
`campaign_transition_blockers`, le contrat `Editable` côté serveur *et*
navigateur, la barre latérale à trois phases, tous les tests de workflow et la
table Delta `campaign` — pour aboutir à une ligne recopiée de `COUNTING`.

Le jalon `campaign.general_count_opened_at` suffit : avant, comptage avancé ;
après, comptage général. L'interface affiche « Comptage — précomptages » puis
« Comptage — général » : c'est un libellé, pas une machine à états.

**En revanche, un aspect à part : `early_counts`.** Pas un statut, un aspect de
`Editable` — ouvert dès la **préparation**, où le précomptage a lieu, et jusqu'à
la fin du comptage ; fermé ensuite. `book_stock` et `count_journals`, eux,
restent fermés en préparation : un précomptage ne pose aucune référence, et
ouvrir `book_stock` en laisserait poser une par la porte d'à côté.

C'est le point où la première réalisation s'est trompée, et la faute mérite
d'être écrite. Tout le chantier s'était branché sur `count_journals`, dont le
prérequis de séquence est le **chargement du stock ERP**. Or ce chargement est
l'étape 10 du déroulé ci-dessus, et le comptage avancé occupe les étapes 2 à 8.
L'écran restait donc verrouillé, et son API refusait, jusqu'après le moment où
la fonction sert — la barre latérale affichant en prime « chargez d'abord le
stock ERP », prérequis que le comptage avancé n'a jamais eu.

`early_counts` n'attend que le **référentiel articles** : ses lignes s'y
rattachent, et le scellement les valorise au prix standard. Le partage
d'aspect ne pouvait pas tenir, parce que les deux comptages ne se mesurent pas
contre la même chose — le général contre le snapshot chargé, l'avancé contre la
colonne « Stock ERP » de son propre journal.

Un seul point ailleurs suit la même règle : **l'import des lignes de journaux**,
qui est le point d'entrée des deux comptages, est gardé par `early_counts`. Ce
qu'il fait est refléter l'ERP. Ce qui s'écrit dans l'application — corriger une
ligne à la main, changer un statut, forcer au stock ERP — reste gardé par
`count_journals`, et le postage exige toujours un stock chargé **et** gelé.

---

## 15. Le nouveau processus, étape par étape

### Avant le jour J, pour chaque journal de précomptage

1. **Préparation** : le référentiel articles chargé suffit. Nul besoin de passer
   la campagne en comptage — précompter *est* un geste de préparation, et l'y
   obliger revenait à geler le référentiel des semaines avant qu'il ne soit prêt.
2. **Compter dans l'ERP** les emplacements concernés, journal `INVE` ou `INVV`,
   puis le poster et le valider.
3. **Exécuter le notebook** sur la fenêtre de dates du comptage.
4. **Importer** : l'application découvre les journaux et, pour chacun, **propose
   les entrepôts et emplacements** candidats — ceux de ses lignes, hors
   `INV / 01`, hors emplacements déjà alloués. L'utilisateur **sélectionne le
   périmètre**.
5. **Déclarer le périmètre scelle.** Le comptage du journal est agrégé par
   emplacement et article et posé sur les journaux de comptage correspondants,
   qui cessent d'être en attente. Aucune référence n'est posée : elle arrivera
   le jour J. Les lignes hors périmètre sont conservées et signalées.
6. **Baliser physiquement** les emplacements. Hors application, mais c'est ce qui
   rend le reste valable ; la date et l'auteur du scellement le documentent.

### Le jour J

7. **Passer en Comptage**, puis **ouvrir le comptage général.**
8. **Charger le stock ERP général.** Il couvre **tout**, emplacements scellés
    compris : c'est la référence unique de la campagne. `INV / 01` reçoit son
    journal, que l'exploitant **désactive**.
9. **Geler la référence.** C'est à partir de là, et pas avant, que les écarts et
    les indicateurs d'avancement s'affichent.
10. **Compter**, et **réexécuter le notebook très régulièrement** : chaque import
    remplace les journaux qu'il contient, recalcule les écarts, et met à jour
    l'heure du dernier import.
11. **Regarder les dérives et les étiquettes**, dans les **Contrôles**. Aucune
    action n'est requise : ce sont des indices sur ce qui a bougé entre le
    précomptage et le jour J, à l'usage de qui veut aller voir. Rien ne bloque.
12. **Clore les zones**, vérifier que tous les journaux sont postés.
13. **Passer en Analyse.**

### Ensuite

14. Analyse, ajustements, causes, clôture, publication : inchangés. Les
    emplacements précomptés y montrent un écart voisin de zéro — leur correction
    a été enregistrée dans l'ERP avant la campagne. La lecture par référence
    reste celle sur laquelle l'écran s'ouvre — et elle le mérite plus
    que jamais, puisque le tampon concentre les manquants et qu'un déplacement
    compte deux fois par emplacement.

---

## 16. Publication et archive

`publish_campaign_to_delta.py` archive `early_count_drift`, `erp_journal_scope`,
les colonnes du précomptage sur `count_result` (étiquette, numéro de série,
`qty_on_hand`, scellement) et `reference_date` sur `book_stock_snapshot`.

Sans `reference_date`, une archive relue dans deux ans ne saurait plus contre
quel jour la campagne a été comptée. Sans le périmètre déclaré, on ne saurait
plus distinguer une ligne comptée d'une ligne de passage. Sans les dérives, ce
qui a bougé entre le précomptage et le jour J ne serait consigné nulle part.

---

## 17. Points ouverts

**Qui a le droit de desceller.** Le descellement annule une preuve : à réserver
au propriétaire de la campagne.

**Le journal multi-emplacements et le comptage par zone.** Un journal peut
couvrir 54 emplacements ; le suivi d'avancement de l'application est par
emplacement. Il faut décider si l'unité de progression reste l'emplacement — le
plus probable — et ce que devient l'affichage d'un journal qui en couvre
cinquante.

**La sélection du périmètre à 73 journaux.** Le geste est court, mais il se
répète. Un mode « tout accepter les propositions évidentes » — un seul candidat,
aucun conflit — éviterait soixante clics le jour J. À cadrer avec l'exploitation.

**Le volume.** 58 345 lignes par photographie, réimportées très régulièrement le
jour J. Le remplacement par journal limite l'écriture, mais le recalcul complet
des écarts à chaque import demande d'être mesuré avant d'être promis.

**Le périmètre qui change entre T0 et J.** Un emplacement désactivé après avoir
été précompté, ou activé après coup : les deux cas doivent avoir une réponse
écrite, sinon ils produiront des dérives fantômes.

**La granularité du scellement.** Cette étude scelle le journal. Sceller
l'emplacement — donc toute écriture le concernant, journal *et* feuille
GENERIQUE — serait plus sûr et plus coûteux. Le choix dépend de la fréquence des
saisies libres sur les zones précomptées.

**La durée de la fenêtre T0 → J.** C'est le paramètre qui gouverne le risque
résiduel du § 9, et il est opérationnel, pas technique. Il mérite d'être une
décision explicite plutôt qu'une conséquence du planning.
