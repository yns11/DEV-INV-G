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

Chaque colonne est une **puce d'une ligne** ; le détail s'ouvre au clic,
au-dessus du tableau, sans le déplacer :

- une **liste à cocher** porte le nombre de lignes de chaque valeur — « Terminée
  412 / En cours 3 » dit d'un coup d'œil où est la matière — avec une recherche
  au-delà de sept valeurs, et *Tout cocher* / *Effacer* ;
- les valeurs proposées sont celles que la colonne **affiche** : on coche
  « Composant », pas `COMPONENT` ;
- une **fourchette** rappelle les bornes réellement présentes dans la colonne,
  de quoi savoir quoi taper ;
- un filtre posé colore sa puce et porte une croix qui le retire seule.

Une fois la barre refermée, les critères en cours restent affichés en **puces
retirables** au-dessus du tableau. Un compteur « Filtres (3) » dit qu'il y a
trois critères ; il ne dit pas lesquels, et un tableau amputé des deux tiers de
ses lignes reste alors inexplicable tant qu'on n'a pas rouvert le panneau.

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
3. **Copier / Coller** — un bloc collé depuis Excel. Dans cette zone, la touche
   **Tab insère une tabulation** au lieu de passer au bouton suivant : c'est le
   séparateur de colonnes du presse-papier d'Excel, donc le caractère dont on a
   besoin pour compléter une ligne à la main. Pour ressortir du champ au
   clavier : **Échap**, puis Tab (ou Maj+Tab, qui n'a jamais changé).

4. **Reprendre d'une autre campagne** — le référentiel d'un trimestre est celui
   du suivant à quelques lignes près. La fenêtre de choix liste les campagnes
   existantes **avec ce que chacune porte sur cette grille** : « INV-2026-06 ·
   4 128 articles » se choisit d'un coup d'œil, et une campagne qui ne porte
   rien se voit sans être ouverte. Le geste existe sur toutes les grilles qu'une
   campagne sait redonner — articles, nomenclatures, stock ERP, emplacements,
   zones, feuilles, **journaux de comptage avancés**, ajustements.

   > À ne pas confondre avec la duplication de campagne (1.2), qui *crée* une
   > campagne à partir d'une autre. Celle-ci remplit une grille d'une campagne
   > qui existe déjà.

Les quatre passent par la **même vérification** : les lignes sont validées une à
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

### 1.5 Régler les paramètres

**Gestion → Paramètres.**

#### Accepter des formules dans les comptages

Devant trois palettes de quarante-huit et un fond de bac de sept, un compteur
écrit `3*48+7` — et c'est la bonne façon de compter : le calcul reste devant
les yeux de qui relira, ce qu'un « 151 » nu ne permet plus.

Activé, ces opérations sont évaluées comme dans un tableur, à la saisie
**comme à la lecture d'un scan**, et le texte d'origine est conservé à côté du
résultat : la feuille affiche `151` avec `3*48+7` en dessous. C'est ce qui
permet de recompter six mois plus tard, et de s'apercevoir qu'une palette n'en
contenait que quarante-six.

| Accepté | Refusé |
|---|---|
| `3*48+7`, `=(10+2)/4`, `2,5*4`, `1 200 + 30`, `-4` | tout le reste : noms, appels, puissances |

Désactivé — c'est le réglage par défaut — seuls des nombres sont acceptés, et
une opération est refusée en le disant. Une usine qui veut que ses feuilles
portent un nombre et un seul a raison de l'exiger ; ce qui ne se défendait pas,
c'est que le refus parlait d'une quantité illisible sans jamais dire qu'un
réglage existait.

Ce réglage reste modifiable **pendant le comptage**, contrairement aux seuils :
le besoin apparaît le jour de l'inventaire, devant la première feuille qui
porte un calcul.

#### Les seuils de matérialité

Un écart est *matériel* — c'est-à-dire digne d'attention — lorsqu'il franchit
**toutes** les barrières configurées de son type d'article :

| Barrière | Signification |
|---|---|
| Valeur absolue (€) | Impact financier minimal |
| Écart relatif | \|Δqté\| / quantité ERP minimal |

Exiger la **conjonction** (et non l'une ou l'autre) garde la liste d'exceptions
à une taille qu'une équipe peut réellement traiter le jour J.

> **Un article exclu du périmètre ne produit aucun écart**, quoi qu'on ait
> compté dessus. C'est ce que l'exclusion veut dire : ni son stock ERP, ni son
> comptage, ni ses ajustements n'entrent dans le calcul. Si quelqu'un l'a
> compté malgré tout, la vue *Contrôles* le signale — « exclu du périmètre mais
> compté en zone B12 » — avec les deux gestes possibles : lever l'exclusion sur
> la grille Articles, ou retirer la ligne de la feuille. La quantité n'est
> jamais perdue en silence.

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

**Vider une section.** L'aperçu de la feuille, comme l'écran de saisie, permet
de retirer d'un geste toutes les lignes d'une section. Une section se refait
parfois de zéro — un bord de ligne réorganisé, un WIP qui a changé d'atelier —
et la vider ligne à ligne sur quatre-vingts références est le genre de travail
qui fait renoncer, donc garder une feuille fausse. Rien n'est écrit avant
« Enregistrer ».

**Réordonner ne touche pas ce qui a été compté.** L'aperçu montre le document —
l'ordre des lignes, les intertitres, les sections — et n'affiche ni les
quantités, ni les commentaires, ni les désignations. Il ne les modifie donc pas
non plus : déplacer une ligne, renommer un intertitre ou insérer une
respiration en pleine phase de comptage laisse intacts les relevés de terrain,
y compris sur la ligne déplacée. La règle vaut dans les deux sens — un écran
qui **affiche** la colonne comptage en dispose : y vider une case la vide bien
en base.

**Nombre de comptages.** Sélectionnez des zones dans la grille et choisissez
« Un seul comptage » ou « Double comptage ». Le double comptage est la règle ;
le comptage unique s'assume zone par zone, pour une aire où une seconde équipe
n'apporterait rien. Repasser à 1 supprime la feuille n°2 — l'opération est
refusée, en nommant les zones, si cette feuille porte déjà une quantité saisie.

**Feuille de saisie libre.** *Créer une zone* crée une feuille délibérément
vide : le compteur écrit ce qu'il trouve. Elle est marquée comme telle, ce qui
évite que les contrôles ne la signalent comme une préparation oubliée. Charger
une liste d'articles lève automatiquement la mention.

**Lignes vierges, section par section.** La colonne *Lignes vierges* de la
grille dit combien de lignes chaque section imprimera sur la feuille vierge, et
s'ouvre d'un clic pour les régler — de 0 à 120 par section. **Une section à 0
n'est pas imprimée du tout** : une zone qui ne compte que des en-cours ne sort
plus avec un bandeau « bord de ligne » et quarante cases vides sous lesquelles
elle n'a rien à compter, et une zone qui compte des en-cours peut enfin le
demander. Les mêmes trois champs sont proposés à la création. Une zone qui ne
déclare rien — c'est le cas de toutes celles créées avant ce réglage — se
comporte comme avant : le nombre demandé au moment d'imprimer va tout entier au
bord de ligne.

**Créer un lot de zones.** À côté de *Créer une zone*, *Créer un lot de zones*
ouvre un champ où l'on colle la liste venue d'un tableur : autant de zones que
de lignes collées. **Seul le code est obligatoire.** Les autres colonnes se
reconnaissent à leur en-tête — *Libellé*, *Lignes BDL*, *Lignes WIP*, *Lignes
WOP OK* — dans n'importe quel ordre ; sans en-tête reconnaissable, les colonnes
sont lues dans l'ordre code, BDL, WIP, WIP OK, celui de la feuille imprimée.
L'écran annonce avant d'écrire combien de zones seront créées, quelles lignes
n'ont donné aucun code, et ce qui dépasse les bornes. La création est **tout ou
rien** : un code en double avec une zone existante ou avec une autre ligne du
collage arrête le lot entier, en nommant les codes fautifs.

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
| **Sans références** — une grille vide, *n* lignes (10 à 180), ou le nombre déclaré par la zone section par section | zone en saisie libre | dès la préparation |
| **Avec quantités** — le relevé de ce qui est revenu | les deux | à partir du comptage |

Une zone dont la liste est connue ne se voit jamais proposer la grille vide :
elle ferait réécrire à la main une liste que l'application détient déjà. Une
zone en saisie libre n'a, symétriquement, aucune liste à imprimer.

La feuille à compter reçoit quelques lignes libres par section — **5** en bord
de ligne, **3** en WIP, **2** en WIP terminé : une pièce trouvée dans un coin
doit avoir où être écrite. Le relevé rempli n'en reçoit aucune : inviter à
écrire sur un relevé le rendrait discutable.

La grille vide, elle, suit ce que la zone a déclaré section par section (2.4) :
seules les sections dont le nombre de lignes est supérieur à zéro s'impriment.
Le nombre demandé dans la fenêtre d'impression ne sert que pour une zone qui n'a
rien déclaré, et va alors au bord de ligne.

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

## 2. Comptage

Le jour J, on charge le stock ERP général et on compte. Ce qui a été
**précompté** l'a été plus tôt, en phase de préparation : voir le § 2.0, qui a
changé de place avec l'écran qu'il décrit.

Si vous ne précomptez rien, sautez le § 2.0 — le reste est inchangé.

### 2.0 Comptages avancés — compter avant le jour J

**Comptages avancés**, dans la barre latérale, en **fin de préparation**. Un
emplacement précompté l'est des jours avant le jour J, avant même que le stock
ERP n'existe : l'écran vivait dans la phase de comptage, ce qui obligeait à y
passer la campagne — donc à geler le référentiel — pour compter deux
emplacements.

L'intérêt : alléger la charge du jour J sur des emplacements qui ne bougent pas
— zones lentes, magasins extérieurs, stock immobilisé. Tout reste dans la même
campagne : preuves, écarts et analyses ne se répartissent pas entre plusieurs
dossiers.

**Ce qu'il faut savoir avant de commencer.** Un précomptage **apporte un
comptage, et rien d'autre**. Il ne pose aucune référence : la référence de la
campagne est unique — le stock ERP du jour J, gelé — et elle couvre tous les
emplacements, précomptés compris.

La raison est dans l'ordre des faits. Votre journal de précomptage est **posté
dans l'ERP** avant que la photo du jour J ne soit prise, donc cette photo l'a
déjà intégré. **Conséquence à connaître : un emplacement précompté montrera un
écart voisin de zéro.** Sa correction d'inventaire n'est pas perdue — elle a été
enregistrée plus tôt, dans l'ERP, avant la campagne.

L'écran n'attend rien d'autre que le **référentiel articles** chargé. En
particulier il n'attend ni la phase de comptage ni le stock ERP général :
celui-là arrive le jour J, c'est-à-dire après les précomptages. C'est aussi
pourquoi le panneau d'import des journaux se trouve ici et pas seulement sur
l'écran des journaux de comptage.

Le déroulé, pour chaque journal de précomptage :

1. **Comptez et postez le journal dans l'ERP**, puis validez-le. Il y a peu de
   journaux de précomptage et ils n'ont pas l'urgence du jour J : on a le temps
   de ne charger que du définitif.
2. **Exécutez le notebook** sur la fenêtre de dates du comptage, puis chargez son
   export depuis le panneau d'import de l'onglet *Journaux ERP*. Chaque import
   remplace les journaux qu'il rapporte et laisse les autres intacts ; l'heure du
   dernier s'affiche en tête de l'écran.
3. **Déclarez le périmètre**, onglet *Journaux ERP*. L'application propose les
   emplacements candidats — ceux des lignes du journal, moins le tampon
   `INV / 01`, moins ceux déjà pris par un autre journal — le plus probable en
   tête. Vous cochez.

   **Déclarer scelle.** Les deux gestes n'en font qu'un : dire quels
   emplacements ce journal couvre, c'est dire lesquels sont comptés et ne
   bougeront plus. Dans la foulée, l'application pose leur **comptage** — la
   colonne « Qté Comptée » du journal, agrégée par emplacement et article — et
   démarre leur journal de comptage. Aucune référence n'est posée : elle arrivera
   avec le stock ERP du jour J.

   Un journal réel couvre parfois cinquante emplacements ou plus : la colonne
   *Périmètre* en affiche le nombre et les deux premiers. La liste entière
   s'obtient au survol, et part telle quelle dans le filtre et l'export Excel.
3 bis. **Ouvrir un journal** montre ses lignes brutes, telles que l'ERP les a
   produites : emplacement, étiquette, numéro de série, stock ERP, quantité
   comptée, écart. La colonne **Périmètre** dit lesquelles
   comptent — un journal porte des lignes sur des emplacements qu'il ne couvre
   pas, et celles-là sont la trace d'un déplacement. La grille se filtre, se
   trie et s'exporte comme les autres.
4. **Balisez physiquement** les emplacements. Cette étape n'est pas dans
   l'application, mais c'est elle qui rend tout le reste valable.

**Les emplacements que vous ne cochez pas ne sont pas comptés par ce journal.**
Un journal ERP porte des lignes sur des emplacements qu'il ne couvre pas : elles
matérialisent un déplacement. Tant que le périmètre n'est pas déclaré,
l'application ne sait pas les distinguer et crée un journal de comptage pour
chacune ; **la déclaration fait le tri** et retire ceux que vous n'avez pas
retenus. Deux exceptions, et elles protègent votre travail : un emplacement où
quelqu'un a saisi une quantité à la main, ou qu'un autre journal touche aussi,
est conservé. Les lignes brutes, elles, restent toutes dans le journal ERP —
c'est la trace, et c'est ce que le contrôle par étiquette relit.

**Recharger un journal déjà scellé est permis, et normal.** L'import remplace ses
lignes, recalcule le comptage et rescelle : la dernière lecture de l'ERP est la
plus juste. Le chargement du **stock ERP général**, lui, couvre tous les
emplacements, scellés compris — c'est la référence unique de la campagne.

**Desceller** est possible — c'est ce qui rend un emplacement au comptage du
jour J — mais demande un motif : le descellement annule une preuve datée. Le
périmètre part avec ; redéclarer est le geste qui rescelle. Le bouton est sur la
ligne du journal, à côté de *Modifier*, sur les seuls journaux scellés.

**Il n'y a pas de bouton *Supprimer* sur un journal ERP, et c'est délibéré.** Un
journal n'est pas une saisie mais le reflet d'un document de l'ERP : le supprimer
ne le ferait pas disparaître de l'ERP, et laisserait derrière lui un emplacement
scellé sans le journal qui justifie sa référence — donc impossible à desceller,
et impossible à donner à un autre journal. Ce qu'on veut vraiment faire dans ce
cas se dit autrement :

| Ce que vous vouliez | Le geste |
|---|---|
| Défaire un périmètre coché de travers | **Desceller**, puis redéclarer |
| Remplacer des lignes fausses | **Réimporter** le journal : chaque import remplace les journaux qu'il rapporte |
| Retirer de l'écran un journal chargé par erreur | Rien à faire : sans périmètre déclaré, il ne produit ni référence, ni comptage, ni écart |

### Et le jour J ? On ne déclare rien

**Le gel du stock ERP ferme la fenêtre du précomptage, et c'en est la
définition** : précompter veut dire *avant* la référence générale. Une fois le
stock gelé, les journaux ERP que vous importez sont ceux du jour J, et il n'y a
ni périmètre à déclarer, ni emplacement à sceller :

| | Précomptage (avant le gel) | Jour J (après le gel) |
|---|---|---|
| Sa référence | Le stock ERP gelé — la même que pour tout le monde | Le stock ERP gelé |
| Son comptage | **Déclarer et sceller** son périmètre, ou l'import de ses lignes | L'import de ses lignes |
| Le geste à faire | Déclarer, puis baliser physiquement | **Aucun** — importer suffit |

L'écran le dit : une fois le stock gelé, la colonne *Périmètre* affiche
« Comptage du jour J » au lieu de « À déclarer », le bouton *Déclarer et
sceller* disparaît, et un bandeau rappelle pourquoi. Déclarer quand même est
refusé — auparavant le geste écrivait une seconde référence sur un emplacement
qui en avait déjà une, et l'écran remontait une erreur technique.

**Desceller reste possible après le gel**, et c'est ce qui rend un emplacement
précompté au comptage du jour J.

**Où voir les emplacements comptés et leur journal ERP :** *Comptage →
Journaux*. La colonne **N° ERP** porte le ou les journaux ERP dont viennent les
lignes de chaque emplacement, avec le nombre de lignes, la quantité comptée et
le statut. C'est la vue d'avancement du jour, et elle part telle quelle dans
l'export Excel.

**La colonne Scellement** y répond à la question du matin — lesquels
reste-t-il à compter ? — en trois valeurs :

| Valeur | Ce qu'elle dit |
|---|---|
| **Non scellé** | À compter le jour J, avec le reste |
| **Scellé sans dérive** | Précompté : son comptage est fait, daté et figé, et l'ERP du jour J dit la même chose |
| **Scellé avec dérives** | Précompté, mais quelque chose a bougé depuis. Rien n'est requis : vous voudrez peut-être aller voir avant de clore (§ 2.7) |

Elle se filtre comme les autres colonnes, ce qui donne en un clic la liste de ce
qui reste à faire.

**Un emplacement n'appartient qu'à un journal.** Si un second comptage avancé
passe par un emplacement déjà scellé, la liste proposée ne vous l'offre pas, et
le déclarer quand même est refusé en nommant le journal propriétaire. Ses lignes
sont conservées — c'est la trace du déplacement — mais **elles ne comptent pas** :
seul le journal qui possède l'emplacement le compte, sans quoi la quantité d'un
journal viendrait remplacer celle d'un autre. Pour changer de propriétaire,
descellez le premier journal puis déclarez le second : le comptage bascule avec. Et l'ordre n'a pas d'importance — si les deux
journaux sont entrés avant qu'aucun ne soit déclaré, déclarer recalcule le
comptage sur le seul propriétaire.

**Les écarts sont visibles tout de suite.** Dès qu'un précomptage est scellé, la
vue **Écarts** s'ouvre et le carrousel affiche les planches *Stock et écarts* et
*Couverture*, sans attendre le chargement ni le gel du stock ERP général : la
référence et le comptage de ces emplacements sont déjà là, et ne bougeront plus.
Un bandeau rappelle sur combien d'emplacements portent les chiffres, et les
titres du carrousel le disent aussi. Le reste de la campagne s'y ajoute au
chargement général. C'est le but même du précomptage : voir l'écart quand on
peut encore aller voir sur le terrain.

**Ce que le carrousel additionne alors.** Une fois les deux en place, chaque
emplacement figure **une fois** dans le stock ERP, avec la référence contre
laquelle il a réellement été compté — le snapshot du jour J pour un emplacement
ordinaire, la colonne « Stock ERP » de son propre journal pour un emplacement
précompté et scellé.

| Indicateur | Ce qu'il additionne |
|---|---|
| **Stock ERP** | Les lignes de référence, une par (article, entrepôt, emplacement) |
| **Stock physique** | Ce qui a été compté, plus les ajustements postés depuis |
| **Écart net** | Stock physique − Stock ERP, signé : les surplus compensent les manques |
| **Écart brut** | La même différence en valeur absolue : deux erreurs de sens contraire sont deux erreurs |

Les emplacements **désactivés** (`INV / 01`) et les articles **exclus** ne sont
dans aucun des deux, ni en quantités ni en valeurs. Côté compté, un emplacement
n'entre que si son journal est démarré ou posté — un journal *En attente* est un
emplacement qu'on n'a pas encore touché, et le compter à zéro inventerait un
manquant. C'est pourquoi sceller un précomptage démarre aussi son journal de
comptage : sans cela il apportait sa référence et rien d'autre.

**Les valeurs se calculent toutes de la même façon : `prix standard × quantité`**,
pour le stock ERP comme pour le stock compté. Un écart en euros mesure donc une
différence de quantité, et rien d'autre. Corriger un prix dans la grille
Articles met à jour toute la campagne, sans rien recharger.

**Une seule chose à savoir sur ce total** : il est composite **en dates** — la
plupart des lignes au jour J, les lignes scellées à leur date de précomptage. Un
rapprochement avec un état ERP tiré à une date unique trouvera une différence,
égale à la somme des écarts des précomptages. La date de référence de chaque
ligne est affichée et exportée.

### 2.1 Charger le stock ERP

**Référentiels & seuils → Stock ERP.** Le référentiel articles doit être chargé
avant — l'écran le refuse et le dit, parce que chaque ligne de stock est vérifiée
contre lui (voir plus bas).

Trois sources, comme pour les articles et les nomenclatures, dans cet ordre :

1. **Lire depuis l'ERP** — la table
   `emotors_data_champions.silver_erp_ye.stock_snapshot` publie une photographie
   quotidienne du stock physique du site, une ligne par article × entrepôt ×
   emplacement.
2. **Charger un fichier** — l'export « Stock physique par emplacement », quand
   l'ERP n'est pas joignable.
3. **Copier / Coller**.

**Choisissez la photo.** La liste « Photo du », à côté du bouton, propose les
journées effectivement publiées, la plus récente en tête et sélectionnée par
défaut. Une seule est chargée, jamais deux : une campagne se compare à *un* état
du système à *un* instant, pas à un stock additionné sur trois mois.

> Le défaut n'est pas la règle. La journée de comptage a commencé samedi matin,
> la reprise se fait le lundi : c'est la photo de **samedi** qui fait foi, et
> c'est elle qu'il faut désigner. Charger celle du lundi compterait comme écarts
> deux jours de mouvements normaux.

**Le référentiel articles fait foi**, comme pour les feuilles de comptage, et
**quel que soit le mode d'import**. Deux refus, chacun avec son geste :

| La ligne porte | Elle est rejetée parce que | À faire |
|---|---|---|
| une référence inconnue | sans article, elle n'a ni désignation, ni prix, ni type — son écart s'afficherait en quantité nue, hors de toute règle de matérialité | compléter la grille **Articles** ; un import de stock ne crée jamais d'article |
| un article **exclu** du périmètre | l'exclusion est une décision de campagne ; charger son stock la reprendrait par la fenêtre, et l'écart vaudrait la totalité du stock | lever l'exclusion sur la grille **Articles**, si elle n'a plus lieu d'être |

Les lignes refusées sont listées avec leur numéro et leur raison, comme pour tout
import — le reste du fichier passe.

Quelle que soit la source, le chargement fait **trois choses en une
transaction** :

1. il remplace intégralement le snapshot (une photographie ne se fusionne pas) ;
2. il construit le **référentiel entrepôts/emplacements** à partir des données,
   en conservant les décisions d'activation déjà prises ;
3. il crée **un journal de comptage par emplacement actif**.

L'historique des imports nomme la source **et la photo** : « … stock_snapshot au
2026-08-29 ». Six mois plus tard, savoir à quel jour la campagne s'est comparée
n'est plus une question qu'on pose à quelqu'un.

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

Ouvrez un journal : la grille se manœuvre comme les autres — filtres par
colonne, choix des colonnes, totaux en pied, export. Saisissez la quantité dans
la colonne **Corrigé**. La valeur
importée reste visible à côté, et le badge de source passe à *Saisie manuelle*.

L'écran affiche aussi les **articles du stock ERP que personne n'a comptés**
sur cet emplacement, avec leur valeur : ce sont eux qui seront soldés à zéro à
la clôture. Ils n'apparaissaient auparavant que trois semaines plus tard.

### 2.7 Regarder les dérives et les étiquettes des emplacements précomptés

**Contrôles → Dérives** et **Contrôles → Étiquettes.** Les deux listes vivaient
sur l'écran des comptages avancés, où elles portaient des décisions à prendre.
Elles n'en portent plus, et c'est pourquoi elles sont ici : **aucune action
n'est requise, et rien ne bloque.** Ce sont des indices sur ce qui a bougé entre
le précomptage et le jour J, à l'usage de qui veut aller voir.

#### Les dérives

```
dérive = stock ERP du jour J − ce que le précomptage avait compté
```

**Attendue nulle**, et pour une raison précise : votre journal de précomptage a
été posté dans l'ERP avant que la photo du jour J ne soit prise, donc cette
photo l'a déjà intégré. **La liste ne montre que ce qui a dérivé** — une ligne à
zéro est le cas normal, donc l'absence d'information. La confrontation, elle, a
bien lieu sur chaque ligne et reste en base.

Ce qui reste après ce réalignement est ce qui a bougé entre les deux dates : une
sortie, une réception, une correction saisie entre-temps. **Ce n'est pas un
écart d'inventaire** — celui-là se mesure contre le stock ERP du jour J, qui est
la référence unique de la campagne — et il n'y a donc rien à trancher.

> L'écran proposait auparavant deux issues, *conserver le comptage avancé* ou
> *recompter le jour J*, et le passage en analyse les attendait. Elles reposaient
> sur une seconde référence, celle du précomptage, qui n'existe plus : la mesurer
> revenait à compter deux fois la même correction. Si vous voulez malgré tout
> recompter un emplacement, **descellez son journal** : il rejoint le comptage du
> jour J.

**Ce que la dérive ne voit pas.** Elle se calcule entre deux lectures de l'ERP :
une pièce sortie d'un emplacement scellé sans aucune transaction laisse une
dérive nulle. C'est l'onglet **Étiquettes** qui la montre — si la pièce est
re-scannée ailleurs, son étiquette apparaît dans un second journal, et
l'application désigne les deux emplacements à aller voir. Reste le cas où elle
n'est scannée nulle part : rien ne la voit, et seul le balisage physique
l'évite.

#### Les étiquettes comptées ailleurs

Une étiquette scellée sur un emplacement, retrouvée comptée **à un autre
emplacement**. La liste dit lesquelles, où, et dans quel journal. **Elle ne
retire rien d'aucun comptage** : une pièce comptée deux fois se règle sur le
terrain, pas en excluant une ligne d'une somme.

**Les emplacements vrac n'ont pas d'étiquette.** Les lignes d'un journal `INVV`
portent toutes la même valeur générique — littéralement « VRAC » : un
emplacement vrac se compte en quantité, pas en lots identifiés. Ces lignes sont
donc hors du contrôle par étiquette. Sans cela, deux emplacements vrac
quelconques devenaient « la même étiquette comptée aux deux endroits », et la
liste se remplissait de centaines de faux doublons qui noyaient les vrais
déplacements.

**Ce que la liste ne contient pas.** Une étiquette n'y figure que si elle a été
comptée **à un autre emplacement**. Quand deux journaux ont compté le *même*
emplacement scellé, la pièce n'a pas bougé : il n'y a pas de déplacement à
montrer. Ces emplacements-là sont résumés dans un bandeau au-dessus de la liste,
avec le journal retenu et celui qui ne l'est pas — c'est le seul renseignement
utile, et sans lui les retirer de la liste les cacherait.

### 2.7 bis Emplacements inventoriés ailleurs

Sélectionnez les journaux concernés → **Forcer au stock ERP**. Leur quantité
comptée devient celle du stock ERP : l'écart est nul **par construction**, et
non par accident. Les lignes sont matérialisées et tracées.

Réservé au cas pour lequel il existe : un magasin extérieur dont on reprend le
chiffre ERP **sans preuve de comptage**. Pour un emplacement que vous avez
réellement compté avant le jour J, passez par les comptages avancés : forcer au
stock ERP effacerait le résultat de son inventaire.

### 2.8 Compter les zones GENERIQUE

**Deux affichages, au choix, mémorisé.** *Icônes* donne une carte par zone, ses
feuilles l'une sous l'autre — la lecture qui va bien jusqu'à une dizaine de
zones. *Liste* donne une grille triable, filtrable et exportable, qui tient
encore à quatre-vingts. La ligne y est la **feuille**, et la colonne « Zone »
les regroupe. Les boutons restent épinglés au bord droit, atteignables sans
faire défiler.

**Une feuille n'a pas d'état.** Deux boutons suffisent, sur chaque feuille :

| Bouton | Ce qu'il fait |
|---|---|
| ✏️ **Crayon** | Ouvre la feuille pour saisir, coller ou scanner. Actif pendant toute la phase Comptage |
| 🖨️ **Imprimante** | Imprime la feuille — vierge ou remplie |

Il fallait auparavant cliquer *Commencer le comptage*, puis *Commencer
l'encodage*, avant de pouvoir écrire la première quantité, puis *Valider* pour
finir — quatre clics par feuille, huit par zone à double comptage. Aucune
écriture n'en dépendait : le papier partait au comptage que le bouton ait été
cliqué ou non, et les quantités s'enregistraient dans tous les cas.

**Une quantité peut s'écrire comme une opération**, si le réglage
*Gestion → Paramètres → Accepter des formules dans les comptages* est activé.
Tapez `3*48+7` dans la case : elle enregistre `151` et affiche `3*48+7` en
dessous. Le calcul reste lisible, ce qui est tout l'intérêt — un « 151 » nu ne
se recompte pas. La même règle s'applique aux feuilles scannées.

**Une zone a trois états**, et c'est le seul suivi qui reste :

| État | Ce qu'il veut dire |
|---|---|
| **À compter** | Aucune quantité relevée dans la zone |
| **En cours** | Des quantités sont là ; la zone n'est pas déclarée finie |
| **Terminée** | Quelqu'un l'a déclarée finie : elle entre dans la consolidation |

Les deux premiers se **déduisent des quantités** : rien à cliquer, saisir la
première quantité *est* le démarrage. Le troisième est une décision, prise sur
la carte de la zone par **Terminer la zone**, et **Rouvrir** la défait. Cette
décision-là ne peut pas se déduire : une ligne qu'on ne peut légitimement pas
compter — l'article a disparu, l'emplacement est inaccessible — laisserait sinon
la zone ouverte pour toujours, et avec elle le passage de la campagne en analyse.

> Terminer une zone dont les deux comptages se contredisent encore est
> **refusé**, en le disant : sans arbitrage, la consolidation ne sait pas quelle
> quantité retenir. Rouvrir, en revanche, ne se refuse jamais — c'est le geste
> qui répare une clôture trop rapide.

Une zone réglée sur **un seul comptage** n'a qu'une feuille : sans second avis,
il n'y a rien à arbitrer, et rien ne s'oppose à sa clôture.

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
- une case vide reste vide : le modèle transcrit, il n'invente pas un 0
  qu'il n'a pas lu — la ligne comptera zéro de toute façon, mais vous verrez
  qu'il n'a rien lu dessus ;
- chaque valeur porte une **confiance** ; celles sous 75 % sont mises en avant ;
- les articles attendus mais non lus apparaissent en ligne vide, à saisir ;
- une case qui porte une **opération** — `3*48+7` — est calculée, si le réglage
  *Gestion → Paramètres* l'autorise ; sinon elle reste vide, à saisir à
  l'écran. Une lecture ne fait jamais échouer les cent autres lignes d'une
  feuille pour une case douteuse.

Le rapprochement se fait sur le trio **feuille + article + section**, jamais sur
la seule référence. Un même article figure légitimement deux fois sur une
feuille — en bord de ligne pour les bacs, en WIP non déclaré pour ce qui est
monté sur un assemblage — et ce sont deux comptages distincts, posés sur deux
tableaux différents du papier. Quand la référence ne figure qu'une fois, la
section lue ne sert à rien et n'est pas exigée ; quand elle figure deux fois et
que la section est illisible, la ligne est **signalée plutôt que posée au
hasard** : se tromper de tableau fausse deux quantités d'un coup, et rien en
aval ne peut le rattraper.

**Retrouver un scan.** Chaque scan déposé est conservé *avant* d'être lu, et
l'onglet **Audit ▸ Scans archivés** les liste tous : la date du dépôt, le nom du
fichier, son poids, son empreinte, et les feuilles que chacun justifie. Le nom
du fichier est le lien de téléchargement. Une pile déposée d'un coup apparaît
**une fois** — c'est un seul document, et c'est lui qui justifie toutes les
feuilles qu'on y a lues.

C'est ce qui permet de défendre une quantité contestée six mois plus tard :
l'image que le modèle a lue est là, et son empreinte dit que c'est bien
celle-là. Une feuille comptée à la main n'y figure pas, faute de scan.

Une feuille de **saisie libre** se scanne aussi, bien qu'elle n'ait aucune liste
à confronter : le modèle recopie alors la référence telle qu'elle est écrite, et
la garde se déplace d'un cran — c'est le **référentiel articles** qui tranche.
Une référence qu'il ne connaît pas est signalée, jamais créée.

La lecture **dit où elle en est** : le bouton passe en « Lecture en cours… » et
une barre affiche l'étape — archivage de la pièce, rendu des pages, lecture par
le modèle (avec le nombre de pages et de lignes attendues), écriture des
quantités. Cette lecture dure de dix secondes à plus d'une minute selon la
longueur de la liste pré-imprimée, et un bouton grisé ne distingue pas un
travail qui avance d'un appel qui a calé. La barre est **indéterminée** parce
que l'essentiel du temps part dans un seul appel au modèle, dont personne ne
connaît l'avancement : afficher un pourcentage qui saute de 0 à 100 % ne
mesurerait rien. Si vous rechargez la page pendant la lecture, l'écran retrouve
le travail en cours et reprend son suivi, au lieu de vous inviter à relancer un
scan qui tourne déjà.

Tout atterrit dans une grille modifiable, avec le badge *Extraction IA*.
**Rien n'est posté automatiquement.**

**Toute la pile d'un coup.** *Compil B06VRAC → Importer un scan multi-feuilles*
accepte le PDF sorti du scanner avec l'ensemble des feuilles dedans — jusqu'à
deux cent cinquante pages, soit environ cent vingt feuilles recto-verso. Chaque
page est rattachée à sa feuille par ce que l'application a imprimé en pied de
page. Le modèle **recopie** ce pied — identifiant, zone, numéro de comptage —
sans rien vérifier ; c'est l'application qui rapproche ensuite la lecture des
feuilles de la campagne. L'identifiant suffit à lui seul ; s'il est illisible,
la paire **zone + numéro de comptage** rattrape la page dès qu'elle ne désigne
qu'une feuille. Deux lectures qui se contredisent, ou un pied vraiment
illisible, sont **signalés, jamais devinés** : une page classée dans la mauvaise
zone verse un comptage sur du stock qui n'y a jamais été. Le rapport affiche ce
qui a été lu (`lu : feuille … · zone … · comptage n°…`), de quoi voir tout de
suite s'il s'agit d'un pied abîmé ou d'une page étrangère à la pile.

Le dépôt répond **tout de suite**, et la lecture continue derrière. Vous pouvez
fermer la fenêtre : les feuilles se remplissent au fur et à mesure, et l'écran
affiche l'étape en cours et le nombre de feuilles lues. Ce qui se passe ensuite
n'a pas changé — les quantités arrivent en *Extraction IA*, dans des grilles
modifiables, et rien n'est posté.

Trois refus explicites, plutôt qu'un silence :

| Cas | Ce qui se passe |
|---|---|
| Une feuille dont vous avez **déjà corrigé** les valeurs lues par l'IA | Elle est **préservée**. Cette relecture est l'étape la plus coûteuse de la chaîne. « Lire et écraser » la relit quand même, et le rapport dit combien de corrections cela a coûté |
| Une feuille que le modèle n'a pas pu lire | Nommée dans le rapport, avec ses pages et la raison. Les autres aboutissent : une feuille perdue ne perd pas la pile |
| Une pile au-delà du plafond | **Refusée en le disant**, avec les deux nombres. Scannez en deux fois : chaque page porte son identité, l'ordre des piles n'a aucune importance |

Les pages partent au routage **par lots**. Un lot qui revient en erreur n'est
plus perdu : il est **recoupé en deux et redemandé**, jusqu'à la page seule. Ce
qui fait échouer un appel — une réponse trop longue, une bande qui fait dérailler
le modèle — disparaît presque toujours à la moitié, et seule la page qui échoue
encore, seule dans son appel, part en non attribuée. Une pile de soixante-quinze
pages dont six lots sur sept échouaient rendait soixante-douze pages à la main ;
elle passe désormais entière, au prix de quelques appels de plus.

> Si l'application redémarre pendant la lecture, le travail est marqué en échec
> et vous invite à recharger le scan. Les feuilles déjà lues avant
> l'interruption sont enregistrées ; elles seront simplement relues.

### 2.10 Arbitrer

**GENERIQUE → Arbitrages.**

Le tableau ne liste que les **désaccords** — une ligne sur laquelle les deux
équipes s'accordent n'appelle aucune décision, et sur une zone de quatre cents
références elle enterrait les neuf qui en appellent une. Il couvre **chaque
article présent dans l'un ou l'autre** passage, y compris ceux qu'une seule
équipe a comptés, que l'ancien processus ne voyait pas.

Une référence comptée dans un passage et pas dans l'autre affiche **0** de ce
côté-là, pas un tiret : les deux passages portent le même document, donc ne pas
trouver la ligne sur la feuille n°2 dit que l'équipe n'y a rien inscrit — et une
case vide compte zéro partout ailleurs.

Les lignes sont triées : décisions requises d'abord, puis par **impact en euros**.
Le désaccord le plus coûteux est traité en premier. La grille se filtre comme
toutes les autres, et porte la colonne **Zone** : la vue peut couvrir la
campagne entière.

**Trois boutons, et deux d'entre eux n'écrivent rien.**

| Bouton | Ce qu'il fait |
|---|---|
| **Tout le n°1** / **Tout le n°2** | Posent la quantité de ce passage dans **chaque champ**. Rien n'est enregistré : vous relisez, vous corrigez, puis vous validez |
| **Valider tout** | Enregistre **les quantités affichées**, telles qu'elles sont à l'écran |

Chaque champ est prérempli d'office avec le comptage n°2 — le plus tardif, donc
le mieux informé. Ligne à ligne, les boutons **n°1** et **n°2** reprennent l'un
ou l'autre, et **Valider** enregistre. Une ligne déjà tranchée à la main n'est
jamais retouchée par un geste de lot.

> **Un arbitrage meurt avec les chiffres qu'il tranche.** Si l'un des deux
> comptages change après coup — une saisie, un scan, un import, un reclassement
> de WIP —, la décision est rouverte : elle garde sa proposition, pour ne pas
> faire retaper le chiffre, mais perd la signature qui la validait. Cela vaut
> **quel que soit le statut de la zone**, y compris sur une zone déjà déclarée
> terminée : c'est même le cas où l'oubli coûterait le plus cher, puisque plus
> rien en aval ne reposerait la question.

### 2.11 Consolider

**GENERIQUE → Consolidation.**

L'aperçu montre en permanence ce que contiendrait le journal, quelles zones
manquent, et ce qui bloque. Le bouton **Consolider** :

1. reprend chaque zone terminée ;
2. applique la règle de chaque section (tel quel / éclaté) ;
3. exclut les articles hors périmètre GENERIQUE — **après** l'éclatement, pour
   ne pas perdre les composants d'un assemblage hors périmètre ;
   **et écarte les articles absents du référentiel** : sans article, une ligne
   n'a ni désignation, ni prix, ni type, donc rien pour la valoriser, et postée
   dans l'ERP elle y désignerait une référence que la campagne ne connaît pas.
   La quantité n'est pas perdue — la pastille **Hors référentiel** la nomme,
   la chiffre et dit de quelles zones elle vient ;
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

Le modèle lit désormais l'**écart backflush** de chaque article — la part que la
consommation déclarée explique, ce qu'il en reste, et sur quelle période la
mesure a été faite. C'est ce qui lui permet de choisir « Écart consommation
(backflush) », une cause du référentiel qu'il ne pouvait pas fonder auparavant :
faute de ces chiffres, un écart de consommation ressemblait à une erreur de
comptage. Il sait aussi d'où vient la quantité comptée — comptée telle quelle, ou
reconstituée depuis une nomenclature en éclatant un en-cours — et ce que
« significatif » veut dire sur cette campagne, c'est-à-dire vos seuils.

La même règle vaut pour **Expliquer cet écart**, sur une ligne de la vue Écarts.

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

**Les réponses sont mises en forme** : titres, listes, gras, et surtout de vrais
**tableaux**, alignés et lisibles comme ceux du reste de l'application. Dès que
la question compare plusieurs articles, zones ou périodes sur les mêmes mesures,
la réponse arrive en tableau — colonnes de chiffres alignées à droite — plutôt
qu'en énumération. C'est aussi le cas de la **Synthèse IA** de clôture. Vous
pouvez le demander explicitement : « présente-moi ça en tableau ».

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
Aucune sur la quantité : **une case vide compte pour zéro**. La ligne est sur
la feuille parce qu'on s'attend à trouver la référence dans la zone ; n'y avoir
rien trouvé est un écart à expliquer, pas une mesure manquante. L'écarter du
total laissait l'article avec son stock ERP en face de rien — ni compté, ni
manquant.

La distinction subsiste ailleurs, et à un seul endroit : **l'avancement**. Une
zone dont aucune ligne n'a été touchée est « à compter » ; dès qu'une valeur y
est saisie — zéro compris — elle passe « en cours ».

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
