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
- les zones GENERIQUE et leurs listes d'articles pré-imprimées.

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

### 1.6 Créer les zones GENERIQUE

**GENERIQUE → Créer une zone**, une par aire physique réelle (bord de ligne,
picking, métrologie, laboratoire…).

Chaque zone reçoit automatiquement **deux feuilles de comptage**, pour les deux
équipes indépendantes.

Ouvrez chaque feuille et saisissez sa liste d'articles — celle qui sera
pré-imprimée. Trois sections :

| Section | Règle de consolidation |
|---|---|
| **Bord de ligne** | Compté tel quel |
| **WIP (à éclater)** | Ensemble non déclaré dans l'ERP → **éclaté en nomenclature** |
| **WIP assemblé** | Ensemble déclaré dans l'ERP → compté tel quel |

> Les anciens libellés `BDL`, `MOM waiting` et `MOM OK` sont reconnus à l'import
> pour permettre de reprendre un ancien classeur, mais l'interface et les
> rapports parlent désormais de **WIP**.

### 1.7 Imprimer les feuilles

**GENERIQUE → Imprimer toutes les feuilles n°1** produit un seul PDF, dans
l'ordre des zones — à imprimer la veille.

La feuille porte les sections séparées visuellement, une colonne de comptage
large, un bloc signature, et l'identité de la feuille rappelée en pied de
**chaque page** : une page séparée de sa liasse reste traçable.

Elle rappelle aussi la règle essentielle : **une case vide signifie « non
compté ». Pour déclarer une absence de stock, écrivez explicitement 0.**

### 1.8 Passer en comptage

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

### 2.2 Ajuster le périmètre

**Journaux de comptage → Entrepôts & emplacements.**

Sélectionnez les emplacements hors périmètre et **désactivez-les**. Un
emplacement désactivé quitte **totalement** le périmètre : son journal est
supprimé, ses quantités et sa valeur sortent de tous les indicateurs, et il ne
compte plus dans le dénominateur d'avancement.

### 2.3 Charger les journaux ERP

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

### 2.4 Suivre l'avancement

Le bandeau de campagne affiche deux jauges :

- **Avancement général** : journaux postés ou forcés / total des journaux ;
- **Avancement GENERIQUE** : zones terminées / total des zones.

### 2.5 Corriger une ligne

Ouvrez un journal, saisissez la quantité dans la colonne **Corrigé**. La valeur
importée reste visible à côté, et le badge de source passe à *Saisie manuelle*.

L'écran affiche aussi les **articles du stock livre que personne n'a comptés**
sur cet emplacement, avec leur valeur : ce sont eux qui seront soldés à zéro à
la clôture. Ils n'apparaissaient auparavant que trois semaines plus tard.

### 2.6 Emplacements inventoriés avant le snapshot

Sélectionnez les journaux concernés → **Forcer au stock livre**. Leur quantité
comptée devient celle du stock livre : l'écart est nul **par construction**, et
non par accident. Les lignes sont matérialisées et tracées.

### 2.7 Compter les zones GENERIQUE

Pour chaque zone, le cycle est :

```
En attente → Comptage en cours → Encodage en cours → Terminée
```

- **Comptage en cours** : la feuille est remise au compteur.
- **Encodage en cours** : la feuille est revenue ; on saisit ou on scanne.
- **Terminée** : l'encodage est validé. Réversible d'un cran pour corriger.

> Le **comptage n°2 ne peut pas démarrer** tant que le comptage n°1 n'est pas
> revenu. Deux comptages simultanés ne sont pas deux comptages indépendants.

### 2.8 Lire une feuille scannée

Ouvrez la feuille → **Importer un scan** (PDF ou photo).

Le modèle lit la feuille **en s'appuyant sur la liste d'articles pré-imprimée** :

- une référence qu'il croit lire mais qui n'est **pas** sur la feuille est
  signalée comme suspecte, jamais acceptée ;
- une case vide reste vide — elle ne devient jamais 0 ;
- chaque valeur porte une **confiance** ; celles sous 75 % sont mises en avant ;
- les articles attendus mais non lus apparaissent en ligne vide, à saisir.

Tout atterrit dans une grille modifiable, avec le badge *Extraction IA*.
**Rien n'est posté automatiquement.**

### 2.9 Arbitrer

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

### 2.10 Consolider

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

### 2.11 Passer en analyse

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

Deux granularités :

- **Par article** — vue financière. Un transfert entre deux emplacements n'est
  pas un écart, donc les emplacements sont agrégés.
- **Par emplacement** — vue opérationnelle. Dit *où* aller recompter.

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

**Pourquoi mon écart apparaît-il en « par emplacement » mais pas « par article » ?**
Parce que c'est un transfert entre deux emplacements du même article : le stock
total est correct, seule sa localisation diffère. La vue par article, financière,
ne le compte pas comme un écart ; la vue par emplacement, opérationnelle, le
montre pour que vous puissiez corriger la localisation.

**L'IA peut-elle poster un comptage toute seule ?**
Non. Aucune sortie de modèle n'est écrite dans une colonne de décision, postée
dans un journal, ni utilisée pour clore une ligne sans intervention humaine.
