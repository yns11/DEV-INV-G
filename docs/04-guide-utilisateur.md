# Guide utilisateur

Ce guide suit le déroulement réel d'une campagne, de la préparation à la clôture.

---

## Se repérer dans l'application

Toute la navigation tient dans la **barre latérale**, sur trois niveaux :

- la **phase** — Préparation, Comptage, Analyse — qui indique aussi où en est la
  campagne ;
- la **section**, c'est-à-dire l'écran ;
- la **sous-section**, dépliée sous la section ouverte. Elle figure dans
  l'adresse : un lien vers « la grille des seuils » se copie et s'envoie.

L'en-tête porte, sur tous les écrans, le carrousel d'indicateurs, l'interrupteur
« Mon périmètre » et le passage à la phase suivante.

Chaque **bloc** — filtres, graphique, grille — se replie par le chevron placé
devant son titre. Le pli est mémorisé par bloc et par navigateur : ce que vous
n'utilisez pas reste fermé d'une visite à l'autre, et ce que vous utilisez
remonte en haut de l'écran.

---

## Les grilles

Toutes les tables de l'application — articles, nomenclatures, zones, écarts,
ajustements — se manœuvrent de la même façon. Trois commandes, à droite au-dessus
de chaque grille.

**Choisir les colonnes** (l'icône à curseurs). Décochez ce dont vous n'avez pas
besoin ; *Tout afficher* revient au réglage d'origine. Le choix est mémorisé par
grille et par navigateur, et il vaut aussi pour l'**export Excel** : ce que vous
avez masqué à l'écran ne part pas dans le fichier. Seules les colonnes masquées
sont retenues, jamais la liste complète — une colonne ajoutée par une mise à
jour apparaît donc d'elle-même, au lieu de rester invisible parce qu'un réglage
d'il y a six mois ne la connaissait pas.

**Filtrer** (l'entonnoir). Chaque colonne reçoit le filtre qui correspond à ce
qu'elle contient :

| Contenu de la colonne | Filtre |
|---|---|
| Un nombre, une quantité, un prix | **De … à …** — l'une des deux bornes suffit |
| Un petit nombre de valeurs qui se répètent (type, unité, statut, programme) | **Liste à cocher**, chaque valeur avec son nombre de lignes |
| Une référence, une désignation, un commentaire | **Texte contenu**, insensible à la casse et aux accents |

Le classement est automatique et se fait sur les données affichées : une colonne
dont presque chaque ligne a une valeur différente — une référence article — reste
une recherche texte plutôt qu'une liste à cocher de mille entrées. Les filtres se
cumulent, la barre de recherche s'y ajoute, et le pied de grille rappelle combien
de lignes restent.

**Les totaux** s'affichent en pied de grille, sur les colonnes qui s'additionnent
— quantités, valeurs, écarts. Ils portent sur les **lignes affichées** : filtrez
sur un entrepôt et le total devient celui de cet entrepôt. C'est le chiffre qu'on
recopie dans un compte rendu, et il correspond à ce qu'on a sous les yeux.

---

## Retrouver une campagne

**Toutes les campagnes.**

La barre de filtres restreint la liste par **code ou libellé**, **statut**,
**propriétaire** et **date de comptage** (celle de l'inventaire, pas celle de
création). L'interrupteur **Mes campagnes** ne garde que celles que vous avez
créées.

Deux affichages, au choix, mémorisé : **icônes** — une carte par campagne, avec
l'état du gel du stock ERP — et **liste** — une grille triable et filtrable, qui
tient à deux cents campagnes.

**Supprimer** retire une campagne de la liste. Deux règles :

- seul **l'auteur** d'une campagne peut la supprimer ; le bouton est désactivé
  pour les autres, et dit qui contacter ;
- la suppression est **logique** : comptages, journaux, ajustements et journal
  d'audit restent en base, la suppression y est elle-même tracée, et le code
  redevient disponible.

---

## Le cycle de vie en un coup d'œil

```
PRÉPARATION ──────► COMPTAGE ──────► ANALYSE & AJUSTEMENTS ──────► CLÔTURE
     │                  │                      │                      │
 référentiels     stock ERP gelé       journaux gelés          tout gelé
 seuils           journaux + feuilles    ajustements
 zones            consolidation          causes
```

Le passage à l'étape suivante est **irréversible** et gèle des données. L'écran
de transition liste précisément ce qui sera gelé et ce qui bloque encore.

---

## 1. Préparation

### 1.1 Créer la campagne

**Campagnes → Nouvelle campagne.**

| Champ | Conseil |
|---|---|
| Code | `INV-AAAA-MM`. Identifiant métier, visible dans tous les exports. |
| Libellé | En clair, pour les non-initiés. |
| Date de comptage | Le jour J physique, pas la date de création. |

### 1.2 Repartir d'une campagne précédente

**Dupliquer** sur la vignette d'une campagne existante reprend :

- les seuils de matérialité ;
- le référentiel articles complet ;
- les nomenclatures ;
- le référentiel entrepôts/emplacements, **y compris les emplacements
  désactivés** ;
- les zones GENERIQUE et leurs listes d'articles pré-imprimées, **avec leur
  nombre de comptages** : une salle réglée sur un comptage unique ne redevient
  pas une zone à double comptage parce que le défaut de campagne le dit ;
- les gestionnaires, leurs identités et leurs périmètres — le personnel est
  stable d'une campagne à l'autre, et retaper neuf noms et quarante affectations
  chaque trimestre est exactement le travail que cette application supprime.

**Rien de mesuré n'est copié** : ni stock ERP, ni comptage, ni journal, ni
ajustement. Une campagne est une photographie d'un instant ; copier ses mesures
n'aurait aucun sens.

> C'est la fonction qui remplace les deux jours passés à reconstruire
> `Compil GENERIQUE` à chaque campagne.

### 1.3 Charger les articles

**Référentiels & seuils → Articles.** Trois sources, dans cet ordre :

1. **Lire depuis l'ERP** — le référentiel est lu directement dans la table
   `emotors_data_champions.silver_erp_ye.silver_base_article` d'Unity Catalog.
   Rien n'est retapé, et l'aller-retour export/ré-import qui produisait
   l'essentiel des erreurs de référentiel disparaît.
2. **Charger un fichier** — un export Excel ou CSV, quand l'ERP n'est pas
   joignable ou que la liste vient d'ailleurs.
3. **Copier / Coller** — un bloc collé depuis Excel.

Les trois passent par la **même vérification** : les lignes sont validées une à
une et le résultat s'affiche — acceptées, rejetées, pourquoi, à quelle ligne —
**avant** que quoi que ce soit ne soit enregistré. Et dans les trois cas la
grille reste modifiable ensuite : une désignation se corrige à la main, un prix
se rectifie, une exclusion se pose.

Ce que la lecture ERP traduit pour vous :

| Colonne ERP | Devient | Règle |
|---|---|---|
| `item_group_id` | Type d'article | COMPO → composant, PFINI → produit fini, PSMFI → semi-fini |
| `programme` | Programme + spécificité | `Commun` = article commun, sinon spécifique |
| `std_cost_price` ÷ `std_price_unit` | Prix standard | Ramené au prix d'**une** unité |
| `item_name` / `name_alias` / `item_description` | Désignation | La première renseignée |

La grille se filtre colonne par colonne — **type**, **programme**, **unité** et
**exclusion** par liste à cocher, **prix standard** par fourchette — ce qui rend
praticable le travail par lot sur un référentiel de plusieurs milliers de lignes :
isoler les semi-finis d'un programme, ou les articles au-dessus de mille euros,
puis agir sur la sélection.

Un groupe non stockable (`SSTRA` sous-traitance, `PRESTA` prestation) reste en
type *inconnu* plutôt que d'être rangé au jugé : valoriser une prestation comme
un composant fausserait l'écart. L'**exclusion** n'est jamais déduite de l'ERP —
c'est une décision de campagne, prise ici.

### 1.4 Charger les nomenclatures

**Référentiels & seuils → Nomenclatures.** Mêmes trois sources, l'ERP en tête :
la table `emotors_data_champions.silver_erp_ye.silver_bom` fournit chaque lien
parent → composant avec sa quantité, la désignation de l'assemblage étant jointe
au passage.

Les mêmes filtres qu'en Articles : **assemblage** et **composant** par recherche
texte, **quantité par assemblage** par fourchette, **unité** et **version** par
liste à cocher.

L'onglet **Santé des nomenclatures** signale immédiatement :

- les **cycles** (A contient B qui contient A) — bloquants ;
- les liens pointant vers un article absent du référentiel ;
- les semi-finis et produits finis **sans aucune nomenclature** — ils ne
  pourront pas être éclatés s'ils sont comptés en WIP.

> Traiter ces alertes en préparation coûte dix minutes. Les découvrir le jour J
> coûte un après-midi.

### 1.5 Régler les seuils

**Référentiels & seuils → Seuils.**

Un écart est *matériel* — c'est-à-dire digne d'attention — lorsqu'il franchit
**toutes** les barrières configurées de son type d'article :

| Barrière | Signification |
|---|---|
| Valeur absolue (€) | Impact financier minimal |
| Écart relatif | \|Δqté\| / quantité ERP minimal |

Exiger la **conjonction** (et non l'une ou l'autre) garde la liste d'exceptions
à une taille qu'une équipe peut réellement traiter le jour J.

Exception à cette règle : un article compté **alors que l'ERP n'en connaissait
aucun stock** est toujours matériel. Du stock inconnu du système n'est jamais
une différence d'arrondi.

### 1.6 Préparer les feuilles de comptage

**Référentiels & seuils → Feuilles de comptage.**

C'est ici qu'on décide *quoi* compter, des semaines avant le jour J. Chargez un
fichier à trois colonnes — **feuille, article, section** — et l'application en
déduit tout le reste :

| Colonne | Requis | Effet |
|---|---|---|
| Feuille | oui | Une feuille inconnue **crée** sa zone et ses passages ; une feuille connue est **complétée**, jamais recréée |
| Article | oui | Vérifié contre le référentiel articles ; un article absent est une **erreur de ligne**, jamais un article créé à la volée |
| Section | non | Vide = bord de ligne |
| Unité | non | PCE par défaut |

Trois sections, qui décident de la règle de consolidation :

| Section | Règle de consolidation |
|---|---|
| **Bord de ligne** | Compté tel quel |
| **WIP (à éclater)** | Ensemble non déclaré dans l'ERP → **éclaté en nomenclature** |
| **WIP assemblé** | Ensemble déclaré dans l'ERP → compté tel quel |

> Les anciens libellés `BDL`, `MOM waiting` et `MOM OK` sont reconnus à l'import
> pour permettre de reprendre un ancien classeur, mais l'interface et les
> rapports parlent désormais de **WIP**.

Un même article peut légitimement figurer **deux fois** sur une feuille dans
deux sections différentes — en bord de ligne *et* dans un en-cours. C'est le
trio feuille + article + section qui doit être unique, pas l'article.

Les lignes sont posées sur **les deux comptages**, quantités vides. Ne
pré-remplir que le n°1 rendrait le n°2 aveugle et fausserait l'arbitrage.

**Nombre de comptages.** Sélectionnez des zones dans la grille et choisissez
« Un seul comptage » ou « Double comptage ». Le double comptage est la règle ;
le comptage unique s'assume zone par zone, pour une aire où une seconde équipe
n'apporterait rien. Repasser à 1 supprime la feuille n°2 — l'opération est
refusée, en nommant les zones, si cette feuille porte déjà une quantité saisie.

**Feuille de saisie libre.** *Créer une zone* crée une feuille délibérément
vide : le compteur écrit ce qu'il trouve. Elle est marquée comme telle, ce qui
évite que les contrôles ne la signalent comme une préparation oubliée. Charger
une liste d'articles lève automatiquement la mention.

**Supprimer une zone.** La corbeille en bout de ligne retire une zone ; cochez
plusieurs lignes et *Supprimer* les retire d'un coup. Les feuilles de comptage de
la zone partent avec elle, et le message de confirmation dit combien : une zone
préparée par erreur ne laisse pas derrière elle des feuilles orphelines qu'on
retrouverait le jour J. La suppression est **logique** — la zone quitte les
listes, son historique reste en base.

Cette opération n'existe **qu'en Préparation**. Passé en comptage, une zone porte
des quantités saisies, et la faire disparaître effacerait un travail de terrain :
elle se ramène alors à un seul comptage, ou ses emplacements se désactivent (2.3),
mais elle ne s'efface plus.

### 1.7 Répartir le travail entre gestionnaires

**Référentiels & seuils → Gestionnaires**, puis **Affectation journaux** et
**Affectation zones**.

Neuf postes par campagne. Renseignez pour chacun son libellé et **son adresse
e-mail**. Cette adresse fait deux choses, à ne pas confondre.

**Elle donne le droit de modifier la campagne.** Une campagne se consulte par
tout le monde et ne se modifie que par son créateur et les gestionnaires qu'il a
déclarés ici. Pour les autres, les écrans restent lisibles et exportables, mais
tous les boutons d'écriture sont désactivés et une bande le dit. Décocher
**Actif** retire le droit sans effacer la trace du passage de la personne.

**Elle résout « Mon périmètre »** sans que le navigateur n'ait jamais à nommer
un gestionnaire.

Deux choses restent au seul créateur de la campagne : cette page — un
gestionnaire qui pourrait en déclarer d'autres s'accorderait le droit d'en
accorder — et la suppression de la campagne. Tout le reste, y compris le passage
d'une phase à la suivante, appartient aux gestionnaires autant qu'à lui : le jour
J commence à six heures, et le créateur n'est pas toujours devant son écran.

- *Affectation journaux* rattache les entrepôts. Un journal de comptage suit son
  entrepôt. La ligne **AUTRES** n'est pas un entrepôt : elle rattache d'un coup
  tous ceux sans affectation explicite, pour qu'un entrepôt découvert par un
  nouvel import de stock ERP ne tombe pas hors de tout périmètre.
- *Affectation zones* rattache les feuilles GENERIQUE, sur une sélection.

> **Un périmètre n'est pas un cloisonnement.** L'interrupteur « Mon périmètre »
> de l'en-tête réduit le bruit ; un gestionnaire garde le droit d'agir hors du
> sien — indispensable quand il faut couvrir un collègue à 6 h du matin. Ce qui
> décide du droit d'écrire, c'est d'être déclaré sur cette page, pas l'étendue
> du périmètre. Le filtrage se fait côté serveur : ce que le périmètre exclut
> n'est jamais envoyé au poste.

### 1.8 Imprimer les feuilles

**GENERIQUE → Imprimer les feuilles** produit un seul PDF, dans l'ordre des
zones — à imprimer la veille. Le même bouton existe sur chaque feuille prise
isolément, pour rééditer une page perdue.

Une feuille est **trois documents**, et l'écran n'offre que ceux qui existent :

| Document | Pour quelle zone | Quand |
|---|---|---|
| **Sans quantités** — la liste d'articles, colonne de comptage vide | zone avec liste pré-imprimée | dès la préparation |
| **Sans références** — une grille vide, *n* lignes (10 à 180) | zone en saisie libre | dès la préparation |
| **Avec quantités** — le relevé de ce qui est revenu | les deux | à partir du comptage |

Une zone dont la liste est connue ne se voit jamais proposer la grille vide :
elle ferait réécrire à la main une liste que l'application détient déjà. Une
zone en saisie libre n'a, symétriquement, aucune liste à imprimer.

La feuille à compter reçoit quelques lignes libres par section — **5** en bord
de ligne, **3** en WIP, **2** en WIP terminé : une pièce trouvée dans un coin
doit avoir où être écrite. Le relevé rempli n'en reçoit aucune : inviter à
écrire sur un relevé le rendrait discutable.

La feuille porte les sections séparées visuellement, une colonne de comptage
large, un bloc signature, et l'identité de la feuille rappelée en pied de
**chaque page** : une page séparée de sa liasse reste traçable.

Les marges sont serrées et les lignes hautes : un chiffre écrit avec des gants
a besoin de place, et chaque millimètre de papier récupéré est une ligne qui ne
déborde pas sur une seconde page. Les désignations sont tronquées plutôt que
repliées — un compteur identifie une pièce à sa référence, et une cellule sur
deux lignes diviserait par deux le nombre de lignes par page.

### 1.9 Passer en comptage

Le bouton **Passer à « Comptage »** ouvre un écran qui liste ce qui sera gelé :
articles, nomenclatures, seuils. Les zones GENERIQUE, elles, restent créables.

---

## 2. Comptage — le jour J

### 2.1 Charger le stock ERP

**Référentiels & seuils → Stock ERP.** Trois sources, comme pour les articles et
les nomenclatures, dans cet ordre :

1. **Lire depuis l'ERP** — la table
   `emotors_data_champions.silver_erp_ye.stock_snapshot` publie une photographie
   quotidienne du stock physique du site, une ligne par article × entrepôt ×
   emplacement. L'application en lit **la date la plus récente**, et elle seule :
   une campagne se compare à *un* état du système à *un* instant, jamais à un
   stock additionné sur trois mois.
2. **Charger un fichier** — l'export « Stock physique par emplacement », quand
   l'ERP n'est pas joignable.
3. **Copier / Coller**.

Quelle que soit la source, le chargement fait **trois choses en une
transaction** :

1. il remplace intégralement le snapshot (une photographie ne se fusionne pas) ;
2. il construit le **référentiel entrepôts/emplacements** à partir des données,
   en conservant les décisions d'activation déjà prises ;
3. il crée **un journal de comptage par emplacement actif**.

L'historique des imports nomme la source retenue : une reprise se relit sans
avoir à deviner si les quantités viennent de l'ERP ou d'un fichier.

Puis **Geler le stock ERP**. À partir de là, tout écart est reproductible.

### 2.2 Ne voir que son périmètre

L'interrupteur **« Mon périmètre »** de l'en-tête filtre les journaux
et les zones sur ce qui vous est affecté (voir 1.7). Il porte le décompte des
objets concernés, et le choix est mémorisé par navigateur.

Trois cas sont distingués explicitement, parce que rien ne les sépare
autrement :

- périmètre garni — les listes sont filtrées ;
- **périmètre vide** — « aucun objet ne vous est affecté », et non une liste
  vide qu'on prendrait pour une campagne sans données ;
- **identité non déclarée** — vous n'êtes rattaché à aucun gestionnaire ; le
  filtre ne laisse alors rien passer.

Le filtrage se fait côté serveur : ce que le périmètre exclut n'est jamais
envoyé au poste. Et il reste un filtre : coupez l'interrupteur et vous revoyez —
et pouvez traiter — toute la campagne.

### 2.3 Ajuster le périmètre des emplacements

**Journaux de comptage → Entrepôts & emplacements.**

Sélectionnez les emplacements hors périmètre et **désactivez-les**. Un
emplacement désactivé quitte **totalement** le périmètre : son journal est
supprimé, ses quantités et sa valeur sortent de tous les indicateurs, et il ne
compte plus dans le dénominateur d'avancement.

### 2.4 Charger les journaux ERP

**Journaux de comptage → Import ERP.**

Chargez l'export OData des lignes de journaux. À chaque rechargement :

- les **valeurs importées** sont rafraîchies ;
- les **corrections manuelles sont préservées** — c'est tout l'intérêt de garder
  les deux colonnes séparées ;
- un journal présent dans le fichier mais absent du référentiel est **créé
  automatiquement** (cas typique : stock ERP à zéro, stock compté positif) ;
- une ligne portant sur un emplacement **désactivé** est ignorée avec un
  avertissement explicite, jamais silencieusement ;
- un journal dont toutes les lignes sont marquées postées passe en **Posté**.

Rechargez autant de fois que nécessaire pendant la journée.

### 2.5 Suivre l'avancement

Le bandeau de campagne affiche deux jauges :

- **Avancement général** : journaux postés ou forcés / total des journaux ;
- **Avancement GENERIQUE** : zones terminées / total des zones.

### 2.6 Corriger une ligne

Ouvrez un journal, saisissez la quantité dans la colonne **Corrigé**. La valeur
importée reste visible à côté, et le badge de source passe à *Saisie manuelle*.

L'écran affiche aussi les **articles du stock ERP que personne n'a comptés**
sur cet emplacement, avec leur valeur : ce sont eux qui seront soldés à zéro à
la clôture. Ils n'apparaissaient auparavant que trois semaines plus tard.

### 2.7 Emplacements inventoriés avant le snapshot

Sélectionnez les journaux concernés → **Forcer au stock ERP**. Leur quantité
comptée devient celle du stock ERP : l'écart est nul **par construction**, et
non par accident. Les lignes sont matérialisées et tracées.

### 2.8 Compter les zones GENERIQUE

Pour chaque zone, le cycle est :

```
En attente → Comptage en cours → Encodage en cours → Terminée
```

- **Comptage en cours** : la feuille est remise au compteur.
- **Encodage en cours** : la feuille est revenue ; on saisit ou on scanne.
- **Terminée** : l'encodage est validé. Réversible d'un cran pour corriger.

> Le **comptage n°2 ne peut pas démarrer** tant que le comptage n°1 n'est pas
> revenu. Deux comptages simultanés ne sont pas deux comptages indépendants.

Une zone réglée sur **un seul comptage** n'a qu'une feuille et se termine dès
qu'elle est encodée : sans second avis, il n'y a rien à arbitrer.

À l'ouverture de la **feuille n°2**, une colonne « Comptage n°1 » affiche la
quantité du premier passage. Voir la divergence pendant la saisie transforme
l'encodage en vérification, au lieu de la découvrir plus tard dans une liste
d'arbitrages détachée du papier. Cette colonne ne figure évidemment **pas** sur
la feuille imprimée : le second comptage cesserait d'être indépendant.

### 2.9 Lire une feuille scannée

Ouvrez la feuille → **Importer un scan** (PDF ou photo).

Le modèle lit la feuille **en s'appuyant sur la liste d'articles pré-imprimée** :

- une référence qu'il croit lire mais qui n'est **pas** sur la feuille est
  signalée comme suspecte, jamais acceptée ;
- une case vide reste vide — elle ne devient jamais 0 ;
- chaque valeur porte une **confiance** ; celles sous 75 % sont mises en avant ;
- les articles attendus mais non lus apparaissent en ligne vide, à saisir.

Une feuille de **saisie libre** se scanne aussi, bien qu'elle n'ait aucune liste
à confronter : le modèle recopie alors la référence telle qu'elle est écrite, et
la garde se déplace d'un cran — c'est le **référentiel articles** qui tranche.
Une référence qu'il ne connaît pas est signalée, jamais créée.

Tout atterrit dans une grille modifiable, avec le badge *Extraction IA*.
**Rien n'est posté automatiquement.**

### 2.10 Arbitrer

**GENERIQUE → Arbitrages.**

Le tableau compare comptage n°1 et n°2 pour **chaque article présent dans l'un
ou l'autre** — y compris ceux qu'une seule équipe a comptés, que l'ancien
processus ne voyait pas.

Les lignes sont triées : décisions requises d'abord, puis par **impact en euros**.
Le désaccord le plus coûteux est traité en premier.

Pour chaque écart : saisissez la quantité retenue, ou cliquez **n°2** pour
préremplir avec le second comptage. Le bouton **Retenir le comptage n°2 partout**
traite la zone entière — chaque ligne reste enregistrée comme une décision
explicite, à votre nom.

### 2.11 Consolider

**GENERIQUE → Consolidation.**

L'aperçu montre en permanence ce que contiendrait le journal, quelles zones
manquent, et ce qui bloque. Le bouton **Consolider** :

1. reprend chaque zone terminée ;
2. applique la règle de chaque section (tel quel / éclaté) ;
3. exclut les articles hors périmètre GENERIQUE — **après** l'éclatement, pour
   ne pas perdre les composants d'un assemblage hors périmètre ;
4. alimente le journal INVV de `B06VRAC / GENERIQUE` ;
5. produit la **décomposition du WIP** : quel assemblage a produit quelle
   quantité de quel composant, dans quelle zone.

Le journal est ensuite exportable **au format d'import ERP** : il s'importe au
lieu d'être recopié à la main.

#### Si la consolidation est bloquée

Le cas le plus fréquent est *« WIP sans nomenclature »* : un assemblage compté
en WIP n'a aucune structure, donc l'éclater ferait disparaître la quantité
comptée. Comme les nomenclatures sont gelées pendant le comptage, la résolution
est proposée en un clic : **compter ces assemblages tels quels** (reclassement
en *WIP assemblé*).

### 2.12 Passer en analyse

Possible seulement quand **tous** les journaux sont postés ou forcés et **toutes**
les zones terminées. L'écran de transition liste ce qui manque encore.

---

## 3. Analyse et ajustements

### 3.1 Lire les indicateurs

Trois mesures de fiabilité sont affichées **côte à côte**, parce qu'elles
répondent à trois questions différentes :

| Indicateur | Question | Lecture |
|---|---|---|
| **Fiabilité nette** | Avons-nous gagné ou perdu de la valeur ? | Les excédents compensent les manques. Toujours la plus flatteuse. |
| **Fiabilité brute** | De combien nous sommes-nous trompés ? | Somme des écarts absolus. **C'est l'indicateur à piloter.** |
| **IRA** | Quelle part de nos enregistrements était juste ? | Standard WMS : part des couples article/emplacement dans la tolérance. |

Un écart de +100 k€ et un de −100 k€ ne font pas zéro erreur : ils font deux
erreurs. La fiabilité brute le dit, la nette le cache.

### 3.2 Travailler la liste des écarts

**Écarts & analyses → Écarts.**

Deux lectures, et l'ordre compte :

- **Par référence** — la lecture de référence, celle sur laquelle l'écran
  s'ouvre. Un transfert entre deux emplacements n'est pas une perte, donc les
  emplacements sont agrégés. C'est ce chiffre qui dit ce que le site a réellement
  perdu ou gagné.
- **Détail par emplacement** — vue opérationnelle. Dit *où* aller recompter. Un
  article déplacé d'un bac à l'autre y apparaît deux fois : en moins ici, en plus
  là.

La carte **« Perte sèche ou simple transfert ? »** mesure exactement l'écart
entre les deux lectures. Une part de transfert élevée signifie que le comptage
n'est pas d'accord avec l'ERP sur *où* est le stock, pas sur *combien* il y en
a : ça fait baisser l'IRA, mais ce n'est pas la même alarme qu'un manquant.

Le filtre **Au-delà des seuils uniquement** réduit à ce qui mérite une action.

La courbe de **concentration** montre combien d'articles portent 80 % de l'écart
absolu — typiquement moins de trente sur plusieurs centaines.

### 3.3 Charger les ajustements

**Écarts & analyses → Ajustements.**

Chargez l'export des transactions de stock, ou saisissez les ajustements postés
dans l'ERP. Quantité et valeur sont **signées** : négatif = diminution.

Un ajustement est un **mouvement de stock**, pas une correction d'écart : il
s'ajoute au comptage pour former le **stock physique**, et c'est ce dernier que
l'écart mesure face à l'ERP gelé. Un comptage de 100 suivi d'un ajustement de
−50 donne donc un physique de 50 et, contre un ERP de 150, un écart de −100.
Ce que le comptage seul montrait reste lisible à côté, sous **Avant ajust.**

Le cycle *analyser → agir sur le terrain → ajuster → recharger* se répète
autant de fois que nécessaire ; les indicateurs se mettent à jour à chaque fois.

### 3.4 Affecter les causes

**Écarts & analyses → Causes.**

Choisissez une cause dans le référentiel de site (14 causes standard).
Le graphique de répartition affiche explicitement la **part non affectée** :
c'est elle qui alimente le plan d'action de la campagne suivante.

Le bouton **Proposer des causes par IA** analyse les plus gros écarts et propose
un diagnostic avec sa confiance et sa justification. La proposition apparaît
**à côté** de la décision, jamais à sa place : vous l'acceptez ou non.

### 3.5 Exploiter les analyses avancées

**Écarts & analyses → Analyses & ML.**

| Analyse | Ce qu'elle vous donne |
|---|---|
| **ABC / XYZ** | Où est l'argent (ABC) croisé avec où est la confiance (XYZ). Le segment **AZ** — forte valeur, faible fiabilité — est celui à mettre en inventaire tournant. |
| **Écarts atypiques** | Écarts dont la *forme* est inhabituelle, pas seulement la taille. |
| **Familles de comportements** | Articles qui échouent de la même façon : une action corrective en couvre plusieurs. |
| **Priorité de recomptage** | Classement par \|écart €\| × probabilité que ce soit une erreur de comptage. Trier par montant seul envoie les équipes recompter des écarts structurels qui ne bougeront pas. |
| **Loi de Benford** | Les premiers chiffres des quantités comptées suivent-ils la distribution attendue d'un vrai comptage ? |
| **Biais d'arrondi** | Trop de multiples de 10, 50, 100 : des zones estiment au lieu de compter. |

### 3.6 Synthèse

**Écarts & analyses → Synthèse IA** rédige la note de comité de direction à
partir des chiffres calculés : message clé, chiffres, principaux contributeurs,
points de vigilance, actions priorisées avec leur enjeu en euros.

Elle est explicitement marquée comme générée automatiquement : relisez-la avant
diffusion.

### 3.7 Exporter

**Exporter le dossier** produit un classeur complet : indicateurs, écarts par
article et par emplacement, stock ERP, journaux, consolidation GENERIQUE,
décomposition WIP, ajustements, causes, contrôles et journal d'audit.

Le classeur porte un onglet **Provenance** : campagne, dates de gel, version du
moteur de calcul, auteur et date de génération — et l'avertissement que le
fichier est une photographie en lecture seule.

### 3.8 Comparer deux campagnes

**Comparaison.** Deux inventaires encadrent une période ; entre les deux, le
stock a été reçu, produit, expédié, consommé et rebuté. La question est fermée :

```
stock attendu = stock initial + réceptions + production
                              − expéditions − conso. théorique − rebuts
```

Choisissez la **campagne de départ** — la plus ancienne par date d'inventaire —
puis alimentez les cinq mesures de la période.

**Tout charger de l'ERP** les lit toutes d'un coup. Elles viennent désormais
d'une seule table de mouvements, à raison d'une colonne par flux :

| Mesure | Ce qu'elle compte |
|---|---|
| Réceptions | Ce qui est entré en stock sur la période |
| Expéditions | Ce qui est sorti vers le client |
| Rebuts | Ce qui a été mis au rebut |
| Production | Ce que l'usine a déclaré produire |
| Conso. théorique | Ce que les nomenclatures disent avoir été consommé |

Elles étant sur la même ligne, la lecture est **tout ou rien** : ou bien les
cinq sont écrites ensemble, ou bien elle échoue et le message dit pourquoi, les
quantités précédentes restant alors intactes. Chaque mesure garde son propre
bouton pour la recharger seule, et le chargement par fichier ou par collage
reste disponible pour les réceptions, les expéditions et les rebuts.

Seules les références **du référentiel de la campagne et non exclues du
périmètre** sont retenues ; le message de lecture indique combien de lignes ont
été écartées à ce titre.

**Les sous-sections** — Réceptions, Production & conso., Expéditions, Rebuts —
montrent chaque mesure ligne par ligne, dans une grille filtrable, exportable et
**éditable**. Un stock attendu qui dérape se débogue par la ligne, et corriger
une quantité repérée ne doit pas obliger à reconstruire tout un export.

Deux règles y valent d'être connues :

- **enregistrer remplace l'étape** : une ligne supprimée à l'écran disparaît, ce
  qui est le seul moyen pour la grille d'exprimer une suppression ;
- la colonne **Provenance** dit d'où vient chaque quantité — *lu dans l'ERP*,
  *chargé par fichier* ou *saisi à la main*. Enregistrer une grille marque toute
  l'étape comme saisie : une main y est passée et l'a validée.

**Quels stocks sont comparés** se choisit ensuite, et se change à tout moment :

| Paire | Ce qu'elle répond |
|---|---|
| Physique → Physique | Ce que l'usine a réellement perdu ou gagné. |
| ERP → ERP | Ce que le système croit avoir perdu. |
| ERP → Physique | L'écart accumulé depuis le solde ERP de départ. |
| Physique → ERP | Ce que l'ERP n'a pas suivi. |

« Physique » veut dire **compté, ajustements compris** — la même définition que
partout ailleurs. Basculer d'une paire à l'autre ne recharge rien : les
quantités saisies et l'instantané ERP gelé sont les mêmes dans les quatre cas.

Un article présent dans une seule des deux campagnes n'est pas un zéro : ces
lignes sont sorties des totaux et regroupées derrière la pastille **Présents
d'un seul côté**.

---

## 4. Interroger la campagne

**Assistant** accepte une question en français et répond à partir du **dossier
complet** de cette campagne : identité et phase, avancement, référentiel et
nomenclatures, journaux de comptage, zones et feuilles, indicateurs, écarts par
article / entrepôt / emplacement, causes, perte ou transfert, ajustements
postés, WIP éclaté, contrôles, provenance des imports et dernières actions du
journal d'audit. On peut y joindre un PDF, une image ou un fichier texte.

Le cadrage : les **chiffres** viennent du dossier, le **raisonnement** est libre.
L'assistant peut comparer, expliquer un mécanisme métier, formuler une
hypothèse — à condition de l'annoncer comme telle. Un chiffre absent du dossier
est déclaré absent, jamais estimé en silence.

Trois choses ne changent pas :

- **le modèle n'a ni base de données ni outil.** Il ne peut être juste ou faux
  que sur ce qui lui a été transmis, et chaque réponse indique sur quels blocs
  elle s'appuie ;
- **il ne modifie rien.** Aucune quantité, aucune cause, aucun statut ne change
  parce qu'on a posé une question ;
- **la question est tracée** au journal d'audit, avec le cadrage utilisé.

Le cadrage est une variable d'environnement (`INV_ASSISTANT_PROFILE`) et non une
décision figée dans le code : en ajouter un autre — plus restreint pour un
public plus large, par exemple — ne demande pas de livraison applicative.

Vérifiez tout chiffre avant de le porter dans une décision.

---

## 5. Clôture

**Passer à « Clôture »** gèle tout, définitivement.

Une campagne clôturée ne se rouvre pas : c'est ce qui garantit que les chiffres
publiés restent ceux qui ont été calculés. Pour poursuivre des travaux,
dupliquez la campagne.

---

## 6. Journal d'audit

**Journal d'audit** trace chaque action et chaque changement de statut, avec son
auteur et son horodatage. La table est en **ajout seul** au niveau de la base :
`UPDATE` et `DELETE` y sont neutralisés par des règles SQL. Ce que cet écran
montre est, par construction, ce qui s'est passé.

L'onglet **Historique des imports** conserve la provenance de chaque chargement :
fichier, empreinte, volumes acceptés et rejetés.

### Retrouver le fichier d'origine

Le nom du fichier y est **cliquable** quand l'original a été conservé : il se
retélécharge tel qu'il a été reçu, avant toute interprétation. C'est ce qui
permet de rejouer un chargement contesté — les lignes en base sont le résultat
d'une lecture, le fichier en est la source.

Un nom affiché en texte simple signifie qu'il n'y a pas de pièce. Trois cas :
un collage, dont le texte est déjà dans les lignes chargées ; une lecture ERP,
qui se rejoue par sa requête ; ou une campagne antérieure à la mise en service
de l'archive.

De la même façon, une feuille lue par l'IA garde **son scan**. Une quantité
extraite d'une image se défend en montrant l'image, et c'est la pile entière
qui est conservée quand plusieurs feuilles ont été scannées d'un coup.

---

## 7. Questions fréquentes

**Puis-je modifier un article après le passage en comptage ?**
Non. Les référentiels sont gelés pour que les exceptions signalées pendant le
comptage soient exactement celles de l'analyse. Créez une nouvelle campagne, ou
traitez le cas côté comptage (reclassement d'une ligne, correction manuelle).

**J'ai rechargé l'export ERP, mes corrections ont-elles disparu ?**
Non. Les corrections vivent dans une colonne distincte de la valeur importée.
Rechargez autant que vous voulez.

**Une case vide et un zéro, quelle différence ?**
Une case vide signifie « non compté » : la ligne ne produit rien et reste à
traiter. Un zéro explicite signifie « compté, il n'y a rien » : la ligne est
soldée. C'est une distinction que l'ancien outil effaçait.

**Pourquoi mon écart apparaît-il en « par emplacement » mais pas « par référence » ?**
Parce que c'est un transfert entre deux emplacements du même article : le stock
total est correct, seule sa localisation diffère. La vue par référence,
financière, ne le compte pas comme une perte ; la vue par emplacement,
opérationnelle, le montre pour que vous puissiez corriger la localisation. La
carte « Perte sèche ou simple transfert ? » chiffre précisément cette part.

**Le mode « Mon périmètre » m'empêche-t-il d'agir ailleurs ?**
Non, jamais. C'est un filtre d'affichage : les actions sont identiques dans les
deux modes, et couper l'interrupteur vous rend toute la campagne. Ce qui est
gelé l'est par la phase de la campagne, pas par un périmètre.

**Une zone peut-elle n'être comptée qu'une fois ?**
Oui, zone par zone, depuis *Référentiels & seuils → Feuilles de comptage*. Elle
n'a alors qu'une feuille et ne produit aucun arbitrage. Repasser à deux
comptages recrée la feuille n°2 avec la même liste d'articles ; ramener à un
seul est refusé si la feuille n°2 porte déjà une quantité saisie.

**L'IA peut-elle poster un comptage toute seule ?**
Non. Aucune sortie de modèle n'est écrite dans une colonne de décision, postée
dans un journal, ni utilisée pour clore une ligne sans intervention humaine.
