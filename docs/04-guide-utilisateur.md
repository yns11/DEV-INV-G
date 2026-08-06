# Guide utilisateur

Ce guide suit le déroulement réel d'une campagne, de la préparation à la clôture.

---

## Le cycle de vie en un coup d'œil

```
PRÉPARATION ──────► COMPTAGE ──────► ANALYSE & AJUSTEMENTS ──────► CLÔTURE
     │                  │                      │                      │
 référentiels     stock livre gelé       journaux gelés          tout gelé
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
  stable d'une campagne à l'autre, et retaper cinq noms et quarante affectations
  chaque trimestre est exactement le travail que cette application supprime.

**Rien de mesuré n'est copié** : ni stock livre, ni comptage, ni journal, ni
ajustement. Une campagne est une photographie d'un instant ; copier ses mesures
n'aurait aucun sens.

> C'est la fonction qui remplace les deux jours passés à reconstruire
> `Compil GENERIQUE` à chaque campagne.

### 1.3 Charger les articles

**Référentiels & seuils → Articles.**

La grille affiche d'abord les **colonnes attendues**, avant tout chargement.
Trois façons d'alimenter :

1. **Charger un fichier** `.xlsx` ou `.csv` — les en-têtes sont reconnus
   automatiquement, y compris les intitulés de l'export ERP
   (`Numéro d'article`, `Groupe d'articles`, …) ;
2. **Coller un bloc** copié depuis Excel (Ctrl+C / Ctrl+V), avec ou sans ligne
   d'en-tête ;
3. **Télécharger le modèle**, le remplir, le recharger.

**Rien n'est enregistré avant votre confirmation.** L'application analyse le
fichier, affiche combien de lignes sont acceptées, combien sont rejetées, et
**pour chaque rejet : la ligne, la colonne, la valeur et le motif**. Vous
confirmez ensuite.

#### Les trois niveaux d'exclusion

| Valeur | Effet |
|---|---|
| *(vide)* | L'article participe partout |
| `GENERIC` | Exclu de la consolidation GENERIQUE et de son analyse |
| `BOM` | Ignoré dans les nomenclatures (jamais crédité par un éclatement) |
| `ALL` | Hors périmètre complet : ni comptage, ni analyse, ni valorisation |

Plusieurs valeurs peuvent être combinées : `GENERIC,BOM`.

### 1.4 Charger les nomenclatures

**Référentiels & seuils → Nomenclatures.**

Une ligne par couple assemblage/composant. La quantité est celle consommée par
**une** unité de l'assemblage.

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
| Écart relatif | \|Δqté\| / qté livre minimal |
| Plancher quantité | En deçà, jamais d'exception |
| Tolérance IRA | Tolérance de l'indicateur d'exactitude des enregistrements |

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

### 1.7 Répartir le travail entre gestionnaires

**Référentiels & seuils → Gestionnaires**, puis **Affectation journaux** et
**Affectation zones**.

Cinq postes par campagne. Renseignez pour chacun son libellé et **son adresse
e-mail** : c'est elle qui permet à l'interrupteur « Mon périmètre » de savoir
qui demande, sans que le navigateur n'ait jamais à nommer un gestionnaire.

- *Affectation journaux* rattache les entrepôts. Un journal de comptage suit son
  entrepôt. La ligne **AUTRES** n'est pas un entrepôt : elle rattache d'un coup
  tous ceux sans affectation explicite, pour qu'un entrepôt découvert par un
  nouvel import de stock livre ne tombe pas hors de tout périmètre.
- *Affectation zones* rattache les feuilles GENERIQUE, sur une sélection.

> **Un périmètre n'est pas une habilitation.** L'interrupteur « Mon périmètre »
> de la barre supérieure réduit le bruit ; il ne cloisonne rien. Chacun garde le
> droit d'agir hors de son périmètre — indispensable quand il faut couvrir un
> collègue à 6 h du matin. Le filtrage se fait côté serveur : ce que le
> périmètre exclut n'est jamais envoyé au poste.

### 1.8 Imprimer les feuilles

**GENERIQUE → Imprimer toutes les feuilles n°1** produit un seul PDF, dans
l'ordre des zones — à imprimer la veille.

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

### 2.1 Charger le stock livre

**Référentiels & seuils → Stock livre.**

Chargez l'export ERP « Stock physique par emplacement ». Ce chargement fait
**trois choses en une transaction** :

1. il remplace intégralement le snapshot (une photographie ne se fusionne pas) ;
2. il construit le **référentiel entrepôts/emplacements** à partir des données,
   en conservant les décisions d'activation déjà prises ;
3. il crée **un journal de comptage par emplacement actif**.

Puis **Geler le stock livre**. À partir de là, tout écart est reproductible.

### 2.2 Ne voir que son périmètre

L'interrupteur **« Mon périmètre »** de la barre supérieure filtre les journaux
et les zones sur ce qui vous est affecté (voir 1.7). Il porte le décompte des
objets concernés, et le choix est mémorisé par navigateur.

Trois cas sont distingués explicitement, parce que rien ne les sépare
autrement :

- périmètre garni — les listes sont filtrées ;
- **périmètre vide** — « aucun objet ne vous est affecté », et non une liste
  vide qu'on prendrait pour une campagne sans données ;
- **identité non déclarée** — vous n'êtes rattaché à aucun des cinq
  gestionnaires ; le filtre ne laisse alors rien passer.

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
  automatiquement** (cas typique : stock livre à zéro, stock compté positif) ;
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

L'écran affiche aussi les **articles du stock livre que personne n'a comptés**
sur cet emplacement, avec leur valeur : ce sont eux qui seront soldés à zéro à
la clôture. Ils n'apparaissaient auparavant que trois semaines plus tard.

### 2.7 Emplacements inventoriés avant le snapshot

Sélectionnez les journaux concernés → **Forcer au stock livre**. Leur quantité
comptée devient celle du stock livre : l'écart est nul **par construction**, et
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
dans l'ERP. Quantité et valeur sont **signées** : négatif = diminution. Chaque
mouvement réduit l'**écart résiduel** — ce qui reste inexpliqué.

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
article et par emplacement, stock livre, journaux, consolidation GENERIQUE,
décomposition WIP, ajustements, causes, contrôles et journal d'audit.

Le classeur porte un onglet **Provenance** : campagne, dates de gel, version du
moteur de calcul, auteur et date de génération — et l'avertissement que le
fichier est une photographie en lecture seule.

---

## 4. Clôture

**Passer à « Clôture »** gèle tout, définitivement.

Une campagne clôturée ne se rouvre pas : c'est ce qui garantit que les chiffres
publiés restent ceux qui ont été calculés. Pour poursuivre des travaux,
dupliquez la campagne.

---

## 5. Journal d'audit

**Journal d'audit** trace chaque action et chaque changement de statut, avec son
auteur et son horodatage. La table est en **ajout seul** au niveau de la base :
`UPDATE` et `DELETE` y sont neutralisés par des règles SQL. Ce que cet écran
montre est, par construction, ce qui s'est passé.

L'onglet **Historique des imports** conserve la provenance de chaque chargement :
fichier, empreinte, volumes acceptés et rejetés.

---

## 6. Questions fréquentes

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
