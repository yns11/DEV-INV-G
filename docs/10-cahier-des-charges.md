# Cahier des charges — Campagnes Inventaire

> **Objet.** Décrire ce que l'application doit accomplir, pourquoi, et selon
> quelles règles métier — indépendamment de l'architecture, du code et des
> écrans actuels. Ce document est destiné à un repreneur qui n'a assisté à
> aucune de nos conversations et qui doit pouvoir **concevoir, construire et
> recetter** la solution librement.
>
> Ce document est **normatif** des sections A à I. L'annexe 1 décrit l'existant
> et n'impose rien. Les annexes 2 à 4 préparent une décision, elles ne la
> prennent pas.

---

## 0. Provenance, fiabilité et limites

### 0.1 Ce qui a été examiné

| | |
|---|---|
| Dépôt | `yns11/DEV-INV-G` |
| Branche | `claude/campagnes-inventaire-v2-0ewyzf` |
| Commit | `553f48651f5aecb343448996597f7846a54efecd` (2026-09-05) |
| Volume | 365 fichiers versionnés — 127 `app/`, 104 `tests/`, 96 `frontend/`, 9 `docs/`, 15 `fixtures/` |
| Bancs de contrôle | 2 808 contrôles Python (273 exigent un PostgreSQL), 482 contrôles navigateur, un parcours Playwright |

Sources dépouillées : les neuf documents de `docs/`, le `README.md`, la couche
`app/inventory/domain/` (règles métier pures), les services, les contrats
d'import, les migrations, la liste complète des routes HTTP, les 104 fichiers de
contrôle, le jeu de données de contrôle de `fixtures/jeu-de-donnees/`, et le
transcript de la dernière session de développement.

### 0.2 Les limites de traçabilité, énoncées d'emblée

Elles sont réelles et elles pèsent sur la lecture de tout ce document.

**Un cahier des charges d'origine a existé et n'est pas accessible.**
`docs/05-modele-de-donnees.md` §1 dit de ses règles de modélisation qu'« elles
sont issues du cahier des charges », et `docs/01-analyse-existant.md` §2.5
mentionne « l'export OData joint au cahier des charges ». Ce document n'est pas
dans le dépôt et n'a pas été retrouvé. **Une part importante des exigences
fondatrices — cycle de vie, gel, seuils, exclusions, indicateurs — y trouve
probablement son origine sans que je puisse le prouver.** Elles sont donc
classées ici d'après ce que j'ai pu vérifier, et non d'après ce qu'elles
valaient dans ce document.

**Un seul transcript de conversation subsiste** (la dernière session). Les
sessions antérieures, où l'essentiel de l'application a été conçu, ne sont plus
lisibles. Deux résumés de compaction internes à ce transcript **citent
verbatim** un lot de demandes plus anciennes : ces citations sont utilisées ici
et signalées comme *traçabilité de seconde main*.

**Conséquence pratique pour le repreneur.** Le statut « exigence confirmée »
n'est attribué dans ce document qu'à ce dont je tiens une trace. Une règle
classée « comportement observé » n'est pas pour autant illégitime — beaucoup
viennent très probablement du cahier des charges perdu. Elle signifie
exactement : *personne ne peut aujourd'hui produire la décision qui la fonde.*
**Ces règles doivent être revalidées avant d'être reconduites ou supprimées**
(voir §6.1).

### 0.3 Barème de fiabilité

| Marque | Signification |
|---|---|
| **[EC]** | **Exigence confirmée** — demande ou décision explicite du commanditaire, citée et sourcée |
| **[CO]** | **Comportement observé** — constaté dans le code, la documentation ou les contrôles, sans décision métier retrouvée |
| **[CE]** | **Contrainte externe confirmée** — obligation indépendante de l'implémentation (plateforme, ERP, format) |
| **[HV]** | **Hypothèse à valider** — interprétation plausible, non confirmée |
| **[PF]** | **Proposition facultative** — amélioration envisageable, hors périmètre confirmé |

Les sources sont citées de façon compacte : `guide §2.7`, `domain/workflow.py`,
`test_zone_closure.py`, `demande 2026-09 n°3`. Une exigence marquée **[EC]**
porte toujours une citation ou une référence datée.

> **Avertissement de méthode.** Le code décrit l'existant ; il ne prouve pas le
> besoin. Un contrôle automatisé ne vaut pas davantage validation métier : il
> prouve qu'une règle est appliquée, pas qu'elle a été voulue.

---

# A. Vision et périmètre

## A.1 Le problème

Une usine de moteurs électriques réalise des inventaires physiques
périodiques. Le dispositif en place au moment de l'étude est un assemblage de
trois classeurs Excel et de copier/coller manuels vers l'ERP (Microsoft
Dynamics 365). Les défauts en sont **mesurés**, pas supposés — voir
[`01-analyse-existant.md`](01-analyse-existant.md) et l'annexe 1 :

- des quantités comptées **disparaissent sans trace** (jointures internes) ;
- des formules `#REF!` **en production** sur l'onglet qui pilote l'analyse ;
- aucune traçabilité, aucun gel, aucun contrôle d'accès, aucune reproductibilité ;
- un classeur de 17,6 Mo et ~104 000 lignes de formules, à la limite de l'utilisable.

## A.2 Les objectifs

| Objectif | Résultat observable |
|---|---|
| Aucune quantité comptée ne disparaît en silence | Toute quantité écartée d'un calcul produit un message nommant l'article, la zone et le geste de résolution — **[CO]** *(voir RG-CONS-4)* |
| Un chiffre reste défendable des mois plus tard | Recalcul à l'identique depuis le dossier gelé ; auteur et horodatage de chaque action |
| L'écart mesure une différence de quantité, et rien d'autre | Valorisation unique `prix standard × quantité`, ERP comme compté — **[EC]** |
| Le double comptage devient une décision outillée | Chaque désaccord tranché explicitement, valorisé, attribuable |
| Le jour J est tenable | Précomptage anticipé, imports répétables, saisie et scan, avancement lisible |

**Aucun objectif chiffré n'est fixé ici** : ni délai de campagne, ni cible de
fiabilité, ni volumétrie contractuelle. Aucun n'a été retrouvé (voir §6.1).

## A.3 Périmètre confirmé

Le processus complet d'une campagne d'inventaire physique, en quatre phases :
**Préparation → Comptage → Analyse & ajustements → Clôture**, incluant :

1. la constitution des référentiels de la campagne (articles, nomenclatures,
   entrepôts/emplacements) ;
2. la préparation et l'impression des feuilles de comptage papier ;
3. le précomptage d'emplacements avant le jour J, et son articulation avec le
   comptage général ;
4. la saisie, le collage, l'import ERP et la **lecture assistée de feuilles
   scannées** ;
5. le double comptage des zones de l'emplacement GENERIQUE, son arbitrage et
   sa consolidation en un journal au format d'import ERP ;
6. le calcul des écarts, des indicateurs, des contrôles, l'affectation de
   causes, les analyses statistiques ;
7. la comparaison de deux campagnes par les flux de la période ;
8. l'export du dossier, l'archivage et la clôture.

## A.4 Exclusions explicites

| Exclu | Fondement |
|---|---|
| **L'écriture directe dans l'ERP.** Le journal consolidé est *exporté au format d'import ERP*, il n'est pas poussé | **[CO]** — l'écriture directe est listée comme amélioration n°1 non réalisée (`06-top20 §1`) |
| **Toute décision prise par l'IA.** Aucune sortie de modèle n'est écrite dans une colonne de décision, postée, ni utilisée pour clore une ligne sans intervention humaine | **[CO]** — posé comme règle absolue dans deux documents (`guide §7`, `06-top20 §Ce que je ne recommande pas`), **mais aucune décision du commanditaire n'a été retrouvée**. Voir Q-19 |
| **La réouverture d'une campagne clôturée** | **[CO]** — `domain/workflow.py`, `CAMPAIGN_TRANSITIONS[CLOSED] = ∅` |
| **Le comptage mobile / scan de codes-barres sur le terrain** | **[CO]** — amélioration n°3 non réalisée |
| **L'inventaire tournant (cycle counting)** | **[CO]** — amélioration n°4 non réalisée |
| **Toute langue autre que le français** dans l'interface et les documents produits | **[CO]** — constaté sans exception dans tout le produit ; **à confirmer** comme exigence (Q-12) |

## A.5 Sujets non décidés

Listés en §6.1 et §6.2. Les principaux : la granularité du scellement, le droit
de desceller, le devenir d'un emplacement dont le périmètre change entre le
précomptage et le jour J, la durée de la fenêtre de précomptage, et les seuils
de volumétrie et de performance.

## A.6 Glossaire

| Terme | Sens dans ce document |
|---|---|
| **Campagne** | Un inventaire, avec ses référentiels, son stock de référence, ses comptages, ses écarts et sa clôture. Unité d'isolement de toute donnée |
| **Phase** | L'un des quatre états du cycle de vie d'une campagne. Décide de ce qui est modifiable |
| **Gel** | Interdiction définitive de modifier une catégorie de données, déclenchée par un passage de phase |
| **Stock ERP** (ou *référence*) | Ce que le système d'information affirme détenir. Le chiffre contre lequel le comptage est confronté |
| **Snapshot** | La photographie du stock ERP retenue par la campagne, à une date donnée |
| **Stock compté** | Ce que les équipes ont relevé physiquement |
| **Stock physique** | Stock compté **+ ajustements postés**. C'est lui que l'écart oppose au stock ERP |
| **Écart** | Stock physique − Stock ERP, en quantité et en valeur, signé |
| **Écart net / brut** | Somme signée / somme des valeurs absolues. Deux erreurs opposées font deux erreurs, pas zéro |
| **Matérialité** | Le fait qu'un écart mérite une action. Voir RG-VAR-2 |
| **IRA** | *Inventory Record Accuracy* — part des couples article/emplacement exacts |
| **Emplacement** | Le grain de comptage : un couple (entrepôt, emplacement). Jamais une chaîne concaténée |
| **Journal de comptage** | L'unité de comptage, de progression et de gel : **un par emplacement actif** |
| **Journal ERP** | Le document tel que l'ERP le tient : **un entrepôt, un à plusieurs dizaines d'emplacements**. Objet distinct du précédent |
| **Périmètre d'un journal ERP** | Les emplacements que ce journal couvre réellement. **Déclaré, jamais déduit** |
| **Étiquette** | Identifiant logistique d'un lot physique (UC, UM, palette) porté par une ligne de journal ERP |
| **Précomptage** (*comptage avancé*) | Comptage d'emplacements avant le jour J, scellé, portant sa propre référence |
| **Scellement** | L'acte qui fige la référence et le comptage d'un emplacement précompté |
| **Dérive** | Stock ERP du jour J − physique posté au précomptage. Attendue nulle |
| **GENERIQUE** | Un emplacement ERP unique couvrant des dizaines d'aires physiques comptées sur papier |
| **Zone** | Une aire physique de GENERIQUE, comptée par une ou deux équipes |
| **Feuille de comptage** | Le document papier d'un passage sur une zone |
| **Passage** (*comptage n°1 / n°2*) | L'un des deux relevés indépendants d'une même zone |
| **Section** | Bord de ligne / WIP / WIP assemblé. Décide de la règle de consolidation |
| **Sous-section (intertitre)** | Un titre intercalaire à l'intérieur d'une section, qui organise la feuille |
| **Arbitrage** | La décision humaine qui tranche un désaccord entre les deux passages |
| **Consolidation** | L'agrégation des zones GENERIQUE en un journal unique, avec éclatement du WIP |
| **Éclatement (WIP)** | Le remplacement d'un ensemble compté par ses composants, via la nomenclature |
| **Ajustement** | Un mouvement de stock posté après le comptage. **Pas** une correction d'écart |
| **Backflush** | Écart de consommation : ce que la production déclarée explique d'un écart |
| **Cause** | Explication d'un écart, choisie dans un référentiel de site de 14 causes |
| **Gestionnaire** | Une personne déclarée sur la campagne, autorisée à la modifier |
| **Périmètre (d'un gestionnaire)** | Les entrepôts et zones qui lui sont affectés. **Un filtre, jamais une permission** |

---

# B. Acteurs et responsabilités

## B.1 Les rôles

Trois rôles, **relatifs à une campagne** et non globaux : la même personne est
propriétaire de la sienne et simple lectrice de celle du trimestre précédent.
**[CO]** — `domain/access.py`.

| Rôle | Qui | Ce qu'il peut |
|---|---|---|
| **Propriétaire** | Celui qui a créé la campagne | Tout ce qu'un gestionnaire peut, **plus** deux actions exclusives |
| **Gestionnaire** | Déclaré par le propriétaire, par son identité, et **actif** | Charger, compter, ajuster, analyser, **changer de phase** |
| **Lecteur** | Tout le reste | Consulter et exporter. Aucune écriture |

## B.2 Les deux actions réservées au propriétaire

| Action | Justification retrouvée |
|---|---|
| **Déclarer les gestionnaires** | Un gestionnaire pouvant en déclarer d'autres s'accorderait le droit d'en accorder, et rien n'empêcherait d'en retirer le propriétaire — **[CO]**, `domain/access.py` |
| **Supprimer la campagne** | Une campagne qui disparaît sous les pieds de celui qui la mène est un accident bien pire qu'une campagne qui traîne — **[CO]** |

Tout le reste, **y compris le passage d'une phase à la suivante**, appartient
aux gestionnaires autant qu'au propriétaire : le jour J commence à six heures et
le créateur n'est pas toujours devant son écran. **[CO]** — `domain/access.py`,
`guide §1.7`.

## B.3 La règle d'écriture

```
peut écrire  =  la phase l'autorise  ET  l'acteur est propriétaire ou
                                          gestionnaire déclaré et actif
```

Les deux barrières **se cumulent et ne se remplacent pas** : un propriétaire
n'écrit pas dans une campagne clôturée, un lecteur n'écrit nulle part.
**[CO]** — `domain/access.py`, contrôlé par `test_access.py`.

Un gestionnaire **désactivé** ne compte plus : c'est la façon de retirer
quelqu'un sans effacer la trace de son passage. **[CO]**

## B.4 Autorisation métier ≠ mécanisme d'authentification

La distinction est demandée explicitement et elle est structurante.

| | Autorisation métier | Authentification |
|---|---|---|
| **Question** | Cette personne a-t-elle le droit de modifier *cette campagne* ? | Qui est cette personne ? |
| **Réponse** | Propriétaire ou gestionnaire déclaré actif, **et** phase ouverte | Une identité vérifiée, fournie par l'environnement d'hébergement |
| **Liberté du repreneur** | **Aucune** sur la règle : elle est métier | **Totale** sur le mécanisme |

**Exigence** : l'identité doit être **établie par l'infrastructure, jamais
déclarée par le client**. Une requête sans identité établie est refusée, et
**non** attribuée à une identité générique. **[CO]** — `02-architecture §6` ;
la justification y est écrite : sans cela, la barrière propriétaire/gestionnaire
ne protège rien.

**Ce qui est libre** : le protocole (proxy d'entreprise, OIDC, SSO…), le format
d'identité, la présence ou non d'un annuaire.

**Contrainte externe [CE]** : sur la plateforme d'hébergement retenue
aujourd'hui, l'authentification est terminée en amont de l'application et
l'identité arrive dans un en-tête HTTP. Cette contrainte **disparaît** si
l'hébergement change (voir §6.3).

## B.5 Le périmètre : un filtre, jamais un cloisonnement

Entrepôts et zones s'affectent aux gestionnaires. Un interrupteur « Mon
périmètre » réduit l'affichage à ce qui est affecté.

| Exigence | Statut |
|---|---|
| Le périmètre **ne conditionne aucune écriture**. Un gestionnaire garde le droit d'agir hors du sien | **[CO]** — justifié par un besoin opérationnel nommé : couvrir un collègue à six heures du matin (`guide §1.7`, `§2.2`) |
| Le filtrage est **appliqué côté serveur** : ce que le périmètre exclut n'est jamais transmis au poste | **[CO]** — `guide §2.2` |
| Trois situations sont distinguées explicitement : périmètre garni, **périmètre vide**, **identité non déclarée** | **[CO]** — sans quoi une liste vide se confond avec une campagne sans données |
| Une valeur réservée rattache d'un coup **tout entrepôt sans affectation explicite** | **[CO]** — sinon un entrepôt découvert par un nouvel import tombe hors de tout périmètre sans que personne ne le voie (`05-modele §3.4`) |

**Le nombre de postes de gestionnaires** vaut 9 dans le code
(`services/manager_service.py`) et le guide utilisateur dit « neuf ». Le
document de modèle de données dit « 5 postes ». **Contradiction — voir annexe 4,
CT-1.** Le nombre lui-même n'est pas une exigence métier retrouvée : c'est un
plafond d'interface. **[HV]**

---

# C. Architecture fonctionnelle

> Cette section décrit **ce que le système doit savoir faire et quels objets il
> manipule**. Elle n'impose ni découpage technique, ni base de données, ni
> framework, ni écrans. Toute mention d'une table ou d'un module dans ce
> document est descriptive de l'existant, jamais prescriptive.

## C.1 Les grandes capacités

| # | Capacité | Responsabilité |
|---|---|---|
| **C-1** | **Cycle de vie de campagne** | Porter les quatre phases, les gels associés, les préconditions de passage, la duplication d'une campagne antérieure |
| **C-2** | **Référentiels de campagne** | Constituer et figer articles, nomenclatures, entrepôts/emplacements ; santé des nomenclatures |
| **C-3** | **Référence de stock** | Charger une photographie ERP datée, la valider ligne à ligne, la geler ; gérer une référence composite en dates |
| **C-4** | **Comptages avancés** | Importer des journaux ERP, déclarer leur périmètre, sceller, détecter et trancher les dérives et les conflits d'étiquette |
| **C-5** | **Comptage général** | Un journal par emplacement actif ; import, saisie, correction, forçage, postage, avancement |
| **C-6** | **Feuilles GENERIQUE** | Concevoir, imprimer, saisir, scanner ; deux passages, arbitrage, clôture de zone |
| **C-7** | **Consolidation GENERIQUE** | Retenir une quantité par (article, section), appliquer la règle de section, éclater le WIP, produire un journal exportable et sa traçabilité |
| **C-8** | **Écarts et indicateurs** | Réconcilier, valoriser, qualifier la matérialité, calculer les indicateurs, distinguer perte et transfert |
| **C-9** | **Contrôles** | Un catalogue de constats typés, avec sévérité, portée et geste de résolution |
| **C-10** | **Ajustements et causes** | Enregistrer les mouvements post-comptage, affecter des causes du référentiel de site |
| **C-11** | **Analyses avancées** | ABC/XYZ, atypiques, familles, priorité de recomptage, Benford, biais d'arrondi |
| **C-12** | **Assistance IA** | Lecture des feuilles scannées, propositions de causes, synthèse, questions/réponses sur le dossier — **toujours en proposition** |
| **C-13** | **Comparaison inter-campagnes** | Confronter deux inventaires par les flux de la période |
| **C-14** | **Exports et documents** | Feuilles imprimables, classeur de dossier, journal au format d'import ERP, exports de grilles |
| **C-15** | **Traçabilité** | Journal d'audit inaltérable, historique des imports, conservation des pièces d'origine |
| **C-16** | **Habilitation et périmètres** | Rôles, gestionnaires, affectations, filtrage serveur |
| **C-17** | **Archivage et clôture** | Publication d'une copie opposable, liste de contrôle de clôture, gel définitif |

## C.2 Les objets métier et leurs cycles de vie

### C.2.1 Campagne

```
PRÉPARATION ──► COMPTAGE ──► ANALYSE & AJUSTEMENTS ──► CLÔTURE
```

Strictement avant, jamais en arrière. **[CO]** — `domain/workflow.py`. Un
retour en arrière n'est pas une transition mais une **nouvelle campagne
dupliquée** de l'ancienne.

Un **jalon interne** à la phase Comptage sépare le précomptage du comptage
général : le chargement du stock ERP général. Ce n'est **pas** un état
supplémentaire — c'est une conséquence d'un fait. **[CO]** — décision motivée
en détail dans `07-comptages-avances §14`.

### C.2.2 Emplacement et journal de comptage

Un emplacement est actif ou désactivé. **Désactivé, il quitte totalement le
périmètre** : son journal disparaît, ses quantités et sa valeur sortent de tous
les indicateurs, et il ne compte plus au dénominateur d'avancement. **[CO]**

Un journal de comptage suit quatre états : *en attente* → *en cours* → *posté*,
avec *forcé au stock ERP* comme alternative au postage. **[CO]**

### C.2.3 Journal ERP et son périmètre — deux grains distincts

C'est le point de modélisation le plus important du dossier, et il est
**mesuré**, pas supposé :

- un journal ERP appartient à **un entrepôt** mais couvre **un à cinquante-quatre
  emplacements** — sur l'export réel du 13 juin 2026, 48 journaux sur 73
  couvrent plus d'un emplacement ;
- les emplacements de ses lignes **ne suffisent pas** à dire son périmètre :
  1 932 lignes sur 58 345 ne portent un autre emplacement que pour matérialiser
  un déplacement ou une écriture d'ajustement ; 36 journaux voient plus d'un
  entrepôt dans leurs lignes.

**[CE]** — c'est la structure du document ERP, pas un choix de l'application.
`07-comptages-avances §3`.

**Conséquence structurante** : le grain *journal ERP* et le grain *unité de
comptage* sont **deux choses différentes**. Le repreneur est libre du modèle
physique, mais **pas** de les confondre : la première réalisation les avait
confondus et le §3 du document cité expose ce que cela produit.

### C.2.4 Zone, feuille, passage

Une zone porte **une ou deux** feuilles selon son nombre de comptages. Elle a
**trois états**, dont deux se déduisent des quantités et un seul est une
décision humaine (voir RG-ZON-1). Une feuille, elle, **n'a pas d'état**.
**[CO]** — justifié : les quatre états qu'une feuille portait devaient être
avancés à la main sans qu'aucune écriture n'en dépende.

### C.2.5 Cycles de vie résumés

| Objet | États | Qui les fait avancer |
|---|---|---|
| Campagne | Préparation / Comptage / Analyse / Clôturée | Décision humaine, sous préconditions |
| Journal de comptage | En attente / En cours / Posté / Forcé au stock ERP | Import, saisie, décision |
| Emplacement | Actif / Désactivé | Décision humaine |
| Emplacement précompté | Non déclaré / Scellé / Descellé | Déclaration du périmètre / descellement motivé |
| Zone | À compter / En cours / Terminée | Les deux premiers se déduisent ; le troisième est décidé |
| Ligne d'arbitrage | Ouverte / Tranchée / **Rouverte par changement** | Décision humaine ; réouverture automatique (RG-ARB-3) |
| Dérive | Non tranchée / Comptage avancé conservé / À recompter | Décision humaine, cause obligatoire dans un cas |
| Écart | Sans cause / Avec cause / Accepté explicitement | Décision humaine, proposition IA à côté |

## C.3 Systèmes externes et frontières de responsabilité

| Système | Sens | Ce qui est échangé | Source de vérité |
|---|---|---|---|
| **ERP (D365)** | → app | Référentiel articles, nomenclatures, photographie de stock, lignes de journaux de comptage, mouvements de la période, écart backflush | **L'ERP**, pour ces données |
| **ERP** | ← app | Le journal consolidé GENERIQUE, **au format d'import ERP**. L'import lui-même est un geste humain | **L'application**, pour le comptage |
| **Entrepôt de données analytique** | ← app | Copie archivée du dossier complet, partitionnée par campagne | **L'application** ; l'archive est une copie |
| **Modèle de langage / vision** | ↔ app | Images de feuilles scannées, contexte de campagne ; propositions | **Aucune** — le modèle ne fait jamais foi |
| **Stockage de pièces** | ← app | Fichiers d'origine des imports, scans des feuilles | L'application |

**Frontière ferme** : *l'application ne crée jamais un article, ni un
emplacement, à partir d'une donnée de comptage.* Le référentiel fait foi, et
une référence inconnue est une erreur de ligne. **[CO]** — voir RG-REF-1, règle
posée explicitement et à trois reprises (`guide §1.6`, `§2.1`, `§2.9`).

---

# D. Exigences fonctionnelles

> Identifiants stables, de la forme `EX-<domaine>-<n>`. Ils sont conçus pour
> être cités dans une conception ou un plan de recette et ne doivent pas être
> renumérotés.
>
> Les exigences ci-dessous sont **fonctionnelles**. Une exigence ne prescrit ni
> écran, ni geste d'interface, sauf quand la disposition elle-même a fait
> l'objet d'une demande explicite — auquel cas c'est dit et sourcé.

## D.1 Campagne et cycle de vie

### EX-CAM-1 — Créer une campagne
- **Acteur** : toute personne authentifiée. **Déclencheur** : création.
- **Informations** : un code métier, un libellé, une **date de comptage** (le jour J physique, distincte de la date de création).
- **Résultat** : une campagne en phase Préparation, dont le créateur est propriétaire.
- **Critères d'acceptation** : le code est visible dans tous les exports ; le créateur obtient le rôle propriétaire ; la campagne est isolée de toute autre.
- **Statut** : **[CO]** — `guide §1.1`.

### EX-CAM-2 — Dupliquer une campagne antérieure
- **Besoin** : ne pas reconstruire à la main, chaque trimestre, ce qui est stable.
- **Est copié** : seuils, référentiel articles complet, nomenclatures, référentiel entrepôts/emplacements **y compris les emplacements désactivés**, zones GENERIQUE **avec leurs listes d'articles et leur nombre de comptages**, gestionnaires avec leurs identités et leurs périmètres.
- **N'est jamais copié** : stock ERP, comptages, journaux, ajustements. *Une campagne est une photographie d'un instant ; copier ses mesures n'aurait aucun sens.*
- **Exception nommée** : une zone réglée sur un comptage unique **ne redevient pas** une zone à double comptage parce que le défaut de campagne le dit.
- **Critères d'acceptation** : après duplication, aucune mesure n'existe et tous les référentiels sont présents ; une zone à comptage unique le reste.
- **Statut** : **[CO]** — `guide §1.2`.

### EX-CAM-3 — Passer à la phase suivante
- **Préconditions** : celles de RG-PHA-2. **Résultat** : gel irréversible des données de la phase quittée.
- **Exigence d'ergonomie** : l'écran de transition **liste précisément ce qui sera gelé et ce qui bloque encore**, sans tenter le passage.
- **Critères d'acceptation** : un passage refusé nomme chaque blocage et le nombre d'objets concernés ; aucun gel partiel n'est possible.
- **Statut** : **[CO]** — `domain/workflow.py`, `test_closure_blockers.py`.

### EX-CAM-4 — Supprimer une campagne
- **Acteur** : le **propriétaire seul**. Le refus dit qui contacter.
- **Résultat** : la campagne quitte les listes ; comptages, journaux, ajustements et **journal d'audit restent** ; la suppression est elle-même tracée ; le code métier redevient disponible.
- **Statut** : **[CO]** — `guide §Retrouver une campagne`, `test_campaign_deletion.py`.

### EX-CAM-5 — Régler les paramètres de campagne
- **Seuils de matérialité** par type d'article — modifiables **en Préparation uniquement**.
- **Acceptation des formules dans les comptages** — modifiable **pendant le comptage**, contrairement aux seuils.
- **Justification retrouvée de cette asymétrie** : les seuils décident de ce qui sera signalé comme exception, les changer en cours de route changerait la liste sous les yeux de qui la traite ; le réglage des formules décide seulement de ce qu'un champ accepte, et le besoin apparaît **le jour de l'inventaire, devant la première feuille qui porte un calcul**.
- **Statut** : **[CO]** — `domain/workflow.py`, champ `settings`, justification en commentaire.

## D.2 Référentiels

### EX-REF-1 — Charger le référentiel articles
- **Trois modes d'entrée équivalents** : lecture directe depuis la source ERP, chargement de fichier, copier/coller.
- **Les trois passent par la même vérification** : les lignes sont validées une à une et le **résultat s'affiche — acceptées, rejetées, pourquoi, à quelle ligne — avant tout enregistrement**.
- **Dans les trois cas la grille reste modifiable ensuite** : une désignation se corrige, un prix se rectifie, une exclusion se pose.
- **Règle de typage** : un groupe non stockable reste en type *inconnu* plutôt que rangé au jugé — valoriser une prestation comme un composant fausserait l'écart.
- **Règle d'exclusion** : l'exclusion n'est **jamais** déduite de l'ERP. C'est une décision de campagne.
- **Colonnes exposées** : doivent inclure le **groupe d'articles** — **[EC]** *(demande 2026-09 : « Rajouter la colonne Groupe (item_group_label) à la liste des colonnes exposées de la base articles »)*.
- **Statut** : **[CO]** sauf mention ; `guide §1.3`, `test_colonne_groupe.py`.

### EX-REF-2 — Charger les nomenclatures
- Mêmes trois modes. Un lien parent → composant avec sa quantité par assemblage.
- **Santé des nomenclatures** — l'application signale, avant le jour J : les **cycles** (bloquants), les liens vers un article absent du référentiel, les semi-finis et produits finis **sans aucune nomenclature** (ils ne pourront pas être éclatés s'ils sont comptés en WIP).
- **Critère d'acceptation** : un cycle est détecté à l'import et l'éclatement le refuse ; un assemblage sans nomenclature est signalé en préparation, pas découvert le jour J.
- **Statut** : **[CO]** — `guide §1.4`, `domain/bom.py`, `test_domain_bom.py`.

### EX-REF-3 — Constituer le référentiel entrepôts/emplacements
- **Il naît du chargement du stock ERP**, pas d'une saisie préalable, **en conservant les décisions d'activation déjà prises**.
- **Statut** : **[CO]** — `guide §2.1`.

### EX-REF-4 — Désactiver des emplacements hors périmètre
- **Résultat** : sortie **totale** du périmètre (journal supprimé, quantités et valeur hors de tous les indicateurs, hors dénominateur d'avancement).
- **Statut** : **[CO]** — `guide §2.3`.

## D.3 Référence de stock

### EX-STK-1 — Charger la photographie du stock ERP
- **Précondition** : le référentiel articles est chargé — **et le refus le dit**, parce que chaque ligne est vérifiée contre lui.
- **Une seule photographie est chargée, jamais deux** : une campagne se compare à *un* état du système à *un* instant.
- **La journée doit être choisie explicitement**, la plus récente étant seulement proposée par défaut. *Exemple donné : la journée de comptage a commencé samedi, la reprise se fait lundi — c'est la photo de samedi qui fait foi ; charger celle du lundi compterait comme écarts deux jours de mouvements normaux.*
- **Le chargement fait trois choses indissociables** : il remplace intégralement le snapshot, construit le référentiel entrepôts/emplacements, et crée **un journal de comptage par emplacement actif**.
- **Deux refus de ligne, chacun avec son geste** : référence inconnue du référentiel (→ compléter les articles) ; article **exclu** du périmètre (→ lever l'exclusion si elle n'a plus lieu d'être).
- **La provenance conserve la source *et la journée*** : « … au 2026-08-29 ».
- **Statut** : **[CO]** — `guide §2.1`, `test_stock_snapshot_window.py`.

### EX-STK-2 — Geler la référence
- **Résultat** : à partir de là, tout écart est reproductible. Le gel ferme aussi la fenêtre du précomptage (RG-AVC-4).
- **Statut** : **[CO]**.

## D.4 Comptages avancés (précomptage)

### EX-AVC-1 — Importer des journaux ERP
- **Précondition** : campagne en Comptage et **référentiel articles chargé**. **Explicitement pas** le stock ERP général : celui-là arrive le jour J, c'est-à-dire *après* les lots avancés.
- **Chaque import remplace les journaux qu'il rapporte et laisse les autres intacts.** L'heure du dernier import est affichée.
- **Recharger un journal déjà scellé est permis et normal** : l'import remplace ses lignes, recalcule la référence et rescelle. La dernière lecture de l'ERP est la plus juste.
- **Statut** : **[CO]** — `guide §2.0`, `domain/sequence.py`. La justification du prérequis est écrite et documentée comme une erreur de conception corrigée (`07 §14`).

### EX-AVC-2 — Déclarer le périmètre d'un journal, ce qui le scelle
- **Le système propose les candidats** : les emplacements des lignes du journal, **moins l'emplacement tampon**, **moins ceux déjà pris par un autre journal**, le plus probable en tête. L'utilisateur coche.
- **Déclarer scelle** : les deux gestes n'en font qu'un. Dire quels emplacements ce journal couvre, c'est dire lesquels sont comptés et ne bougeront plus.
- **Dans la foulée**, la référence de ces emplacements est posée : lue dans la colonne « Stock ERP » des lignes du journal, valorisée au prix standard, et **datée par la colonne « Date de comptage » de ces lignes**. Aucune date n'est retapée.
- **Les emplacements non cochés ne sont pas comptés par ce journal.** Leurs lignes restent dans le journal ERP — c'est la trace du déplacement, et c'est ce que le contrôle par étiquette relit.
- **Deux exceptions qui protègent le travail** : un emplacement où quelqu'un a saisi une quantité à la main, ou qu'un autre journal touche aussi, est conservé.
- **Critères d'acceptation** : déclarer un emplacement déjà déclaré ailleurs est **refusé en nommant le journal propriétaire** ; l'ordre des déclarations est indifférent (voir RG-AVC-2).
- **Statut** : **[CO]** — `guide §2.0`, `07 §3`, `test_early_count_sealing.py`.

### EX-AVC-3 — Desceller
- **Motif obligatoire** : le descellement annule une preuve datée.
- **Résultat** : le périmètre part avec ; l'emplacement rejoint le comptage du jour J et sa référence redevient le stock ERP du jour J. **Redéclarer est le geste qui rescelle.**
- **Reste possible après le gel du stock ERP** — c'est même ce qui rend un emplacement précompté au comptage du jour J.
- **Statut** : **[CO]**. **Qui a le droit de desceller est un point ouvert** (Q-2).

### EX-AVC-4 — Suivre les emplacements comptés et leur journal ERP
- **Besoin exprimé** : *« Faut-il un endroit où l'on voit tous les emplacements ayant été comptés et leur numéro de journal ERP ? … il faut bien la penser et gérer sans erreur en tenant compte du process opérationnel et du besoin de suivre l'avancement et les écarts »*.
- **Résultat attendu** : une vue d'avancement portant, par emplacement, le ou les journaux ERP d'origine, le nombre de lignes, la quantité comptée et le statut ; exportable telle quelle.
- **Statut** : **[EC]** — citation de résumé de session, traçabilité de seconde main.

### EX-AVC-5 — Trancher les dérives
- **Définition** : `dérive = stock ERP du jour J − physique posté au précomptage`. **Attendue nulle** : l'emplacement était balisé, et poster son journal a réaligné l'ERP.
- **Deux issues, et une seule question posée** — *quelle quantité fait foi au jour J ?*

| Issue | Quand | Ce qu'elle engage |
|---|---|---|
| **Conserver le comptage avancé** | Le mouvement est purement informatique | Le physique de T0 est retenu. **Une cause est obligatoire** : la campagne et l'ERP resteront en désaccord de la valeur de la dérive, et personne ne doit le découvrir plus tard |
| **Recompter le jour J** | On ne fait plus confiance au comptage avancé | L'emplacement est descellé et rejoint le comptage général ; sa référence redevient le stock ERP du jour J |

- **Précondition** : à faire une fois le stock ERP général chargé, **avant le passage en analyse, qui l'exige**.
- **Limite déclarée** : la dérive se calcule entre deux lectures de l'ERP — une pièce sortie sans aucune transaction laisse une dérive nulle. C'est le contrôle par étiquette qui la rattrape ; si elle n'est scannée nulle part, **rien ne la voit**, et seul le balisage physique l'évite. *Cette limite doit être écrite dans le produit, pas seulement dans un document.*
- **Statut** : **[CO]** — `guide §2.7`, `test_early_count_drift.py`.

### EX-AVC-6 — Trancher les conflits d'étiquette
- **Déclencheur** : une étiquette scellée sur un emplacement est comptée **à un autre emplacement**.
- **Trois issues** :

| Issue | Sens | Effet |
|---|---|---|
| **La mettre au nouvel emplacement** | La pièce est bien là où elle a reparu | Elle sort de l'emplacement scellé, qui perd la quantité — **sa référence comme son comptage**, sans quoi la décision creuserait l'écart qu'elle tranche |
| **L'enlever du nouvel emplacement** | Elle n'a pas bougé | C'est la ligne de l'autre journal qui sort du comptage |
| **Signaler : à rescanner** | On ne tranche pas sur pièce | Rien n'est retiré ; l'emplacement **scellé** entre dans une liste d'où on le descelle |

- **L'issue survit aux réimports** : une décision prise à neuf heures ne se retrouve pas vierge à neuf heures cinq.
- **Deux exclusions de la liste, et elles sont métier** :
  1. **Les emplacements vrac n'ont pas d'étiquette.** Les lignes d'un journal vrac portent toutes la même valeur générique. Elles sont **hors du contrôle par étiquette** — sans cela, deux emplacements vrac quelconques deviennent « la même étiquette comptée aux deux endroits » et la liste se remplit de centaines de faux doublons qui noient les vrais déplacements. **[EC]** — demande explicite avec capture d'écran à l'appui.
  2. **Deux journaux ayant compté le *même* emplacement** : la pièce n'a pas bougé, il n'y a rien à trancher. Ces cas sont **résumés** (journal retenu, journal écarté) plutôt que masqués.
- **Statut** : **[EC]** pour le point 1, **[CO]** pour le reste.

## D.5 Comptage général

### EX-CPT-1 — Importer les lignes de journaux de comptage
- **À chaque rechargement** : les valeurs importées sont rafraîchies, **les corrections manuelles sont préservées** ; un journal présent dans le fichier mais absent du référentiel est **créé** (cas typique : stock ERP à zéro, compté positif) ; une ligne sur un emplacement **désactivé** est ignorée **avec un avertissement explicite, jamais silencieusement** ; un journal dont toutes les lignes sont postées passe en *Posté*.
- **Rechargeable autant de fois que nécessaire pendant la journée.**
- **Critère d'acceptation** : après dix rechargements, aucune correction manuelle n'a été perdue.
- **Statut** : **[CO]** — `guide §2.4`, `test_journal_import_snapshots.py`, `test_snapshot_reload.py`.

### EX-CPT-2 — Corriger une ligne de comptage
- La valeur importée reste **visible à côté** de la valeur corrigée, et la provenance de la ligne passe à *saisie manuelle*.
- L'écran montre aussi **les articles du stock ERP que personne n'a comptés** sur cet emplacement, avec leur valeur : ce sont eux qui seront soldés à zéro à la clôture. *Ils n'apparaissaient auparavant que trois semaines plus tard.*
- **Statut** : **[CO]** — `guide §2.6`.

### EX-CPT-3 — Forcer un journal au stock ERP
- **Cas d'usage réservé** : un magasin extérieur dont on reprend le chiffre ERP **sans preuve de comptage**.
- **Résultat** : l'écart est nul **par construction, et non par accident** ; les lignes sont matérialisées et tracées.
- **Mise en garde à porter dans le produit** : pour un emplacement réellement compté avant le jour J, il faut passer par les comptages avancés — forcer effacerait le résultat de son inventaire.
- **Statut** : **[CO]** — `guide §2.7 bis`.

### EX-CPT-4 — Suivre l'avancement
- Deux mesures : journaux postés ou forcés / total ; zones terminées / total.
- **Statut** : **[CO]**.

## D.6 Feuilles de comptage GENERIQUE

### EX-ZON-1 — Préparer les listes d'articles
- **Entrée minimale** : trois colonnes — **feuille, article, section** — dont l'application déduit tout le reste.

| Colonne | Requis | Effet |
|---|---|---|
| Feuille | oui | Une feuille inconnue **crée** sa zone et ses passages ; une feuille connue est **complétée, jamais recréée** |
| Article | oui | Vérifié contre le référentiel ; un article absent est une **erreur de ligne** |
| Section | non | Vide = bord de ligne |
| Sous-section | non | Voir EX-ZON-3 |
| Unité | non | Valeur par défaut |

- **Les lignes sont posées sur les deux comptages, quantités vides.** *Ne pré-remplir que le n°1 rendrait le n°2 aveugle et fausserait l'arbitrage.*
- **Statut** : **[CO]**, sauf la sous-section (**[EC]**, EX-ZON-3).

### EX-ZON-2 — Régler le nombre de comptages par zone
- **Le double comptage est la règle ; le comptage unique s'assume zone par zone**, pour une aire où une seconde équipe n'apporterait rien.
- **Repasser à 1 supprime la feuille n°2** : l'opération est **refusée, en nommant les zones**, si cette feuille porte déjà une quantité saisie.
- **Repasser à 2 recrée la feuille *et* sa liste d'articles** — la recréer vide rendrait le second comptage aveugle.
- **Statut** : **[CO]** — `05-modele §3.2`, `test_domain_workflow.py`.

### EX-ZON-3 — Concevoir la feuille comme un document
- **Besoin exprimé, avec une capture d'une ancienne feuille Excel à l'appui** : *« le bouton ouvrir doit afficher un aperçu éditable de la feuille telle qu'elle sera imprimée … possibilité de changer le texte qui s'affiche en entête de chaque section … possibilité d'insérer / supprimer des lignes vides et des lignes séparateurs (sous-sections) avec du texte personnalisé (pour reproduire le comportement des anciennes feuilles Excel) »*.
- **Trois natures de ligne** : article, **sous-section** (intertitre porteur de texte), **ligne vide** (séparateur de mise en forme, qui n'est *pas* une sous-section).
- **Les sous-sections et les lignes vides apparaissent à la fois sur la feuille imprimée et dans le formulaire de saisie/scan.** **[EC]**
- **Une sous-section ne s'imprime pas en colonne** : *« le faire uniquement dans le modèle et l'import, mais pas dans le rendu imprimé où la sous-section apparaît comme séparateur (ne pas ajouter de colonnes dans les feuilles imprimées, juste une ligne qui indique la sous-section) »*. **[EC]**
- **N'importe quelle ligne doit pouvoir être supprimée**, pas seulement les lignes vides et les intertitres. **[EC]**
- **Un intertitre et une ligne vide n'affichent aucune donnée, ni en lecture ni en modification.** **[EC]**
- **L'ordre des lignes est une donnée de la feuille** et doit être respecté partout : impression, saisie, lecture de scan. **[EC]**
- **Statut** : **[EC]** — demandes explicites, citations de résumé de session et transcript direct.

### EX-ZON-4 — Le même document sur les deux passages
- **Besoin exprimé** : *« les feuilles du 1er et du 2ème comptage (si zone à deux comptages) doivent être exactement les mêmes … s'assurer que toute modification d'une zone (conception de la feuille avec les réf, les intertitres, les entêtes, l'ordre des lignes, etc.) s'applique également à la feuille de comptage n°2 si elle existe »*.
- **Exigence complémentaire dérivée** : la propagation ne doit **pas** détruire les quantités déjà relevées au second passage. *(Interprétation nécessaire : la demande porte sur la structure ; effacer les mesures serait cesser de compter deux fois.)* **[HV]** — à confirmer (Q-9).
- **Conséquence d'affichage** : une vue « toutes les lignes » n'a pas à présenter les deux passages, puisqu'ils portent la même liste. **[EC]**
- **Statut** : **[EC]** pour la propagation, **[HV]** pour la préservation des quantités.

### EX-ZON-5 — Imprimer les feuilles
- **Trois documents possibles, et l'écran n'offre que ceux qui existent** :

| Document | Pour quelle zone | Quand |
|---|---|---|
| **Sans quantités** — la liste, colonne de comptage vide | zone avec liste pré-imprimée | dès la préparation |
| **Sans références** — une grille vide de *n* lignes | zone en saisie libre | dès la préparation |
| **Avec quantités** — le relevé de ce qui est revenu | les deux | à partir du comptage |

- **Une zone dont la liste est connue ne se voit jamais proposer la grille vide** — elle ferait réécrire à la main une liste que l'application détient. Symétriquement pour l'inverse.
- **La feuille à compter reçoit quelques lignes libres par section** ; **le relevé rempli n'en reçoit aucune** — inviter à écrire sur un relevé le rendrait discutable.
- **Exigences de lisibilité terrain** : sections séparées visuellement, colonne de comptage large, bloc signature, **identité de la feuille rappelée en pied de chaque page** (une page séparée de sa liasse reste traçable), marges serrées et lignes hautes (*un chiffre écrit avec des gants a besoin de place*), désignations **tronquées plutôt que repliées** (une cellule sur deux lignes diviserait par deux le nombre de lignes par page).
- **Répartition des largeurs** : la référence doit primer sur la désignation, le comptage et l'unité. **[EC]** — demande chiffrée : *« diminuer la taille des colonnes désignation, comptage et unité de 10 %, 5 % et 20 % respectivement pour augmenter la taille de la colonne Référence »*. **Le repreneur doit retenir l'intention — priorité à la référence — et non ces pourcentages, qui étaient relatifs à une mise en page donnée.**
- **Les commentaires doivent tenir dans leur case** sur le relevé imprimé. **[EC]**
- **Statut** : **[CO]** pour la matrice des trois documents, **[EC]** pour les largeurs et les commentaires.

### EX-ZON-6 — Saisir les quantités
- **Aucun geste préalable n'est requis pour écrire une quantité.** *Il fallait auparavant quatre clics par feuille, huit par zone à double comptage, dont aucune écriture ne dépendait.*
- **Les sections doivent être clairement séparées** dans l'écran de saisie, et non distinguées par une seule colonne. **[EC]**
- **À l'ouverture du second passage, la quantité du premier est visible** — voir la divergence pendant la saisie transforme l'encodage en vérification. **Cette colonne ne figure pas sur la feuille imprimée** : le second comptage cesserait d'être indépendant. **[CO]**
- **Statut** : mixte, voir ci-dessus.

### EX-ZON-7 — Accepter une quantité écrite comme une opération
- **Besoin** : devant trois palettes de quarante-huit et un fond de bac de sept, un compteur écrit `3*48+7` — *et c'est la bonne façon de compter : le calcul reste devant les yeux de qui relira, ce qu'un « 151 » nu ne permet plus.*
- **Comportement** : la valeur est évaluée **et le texte d'origine conservé à côté du résultat**. C'est ce qui permet de recompter six mois plus tard et de s'apercevoir qu'une palette n'en contenait que quarante-six.
- **La règle vaut identiquement à la saisie et à la lecture d'un scan**, feuille par feuille **comme pile entière**. **[EC]** — *« Pourquoi l'évaluation de formules fonctionne bien pour une feuille scannée mais est ignorée si c'est un scan multi-feuilles ? »*
- **Réglable, et désactivé par défaut** : une usine qui veut que ses feuilles portent un nombre et un seul a raison de l'exiger. **Le refus doit dire qu'un réglage existe.**
- **Statut** : **[CO]** pour le principe, **[EC]** pour l'uniformité entre les deux voies de scan.

### EX-ZON-8 — Lire une feuille scannée
- **Le modèle s'appuie sur la liste pré-imprimée** :
  - une référence qu'il croit lire mais qui **n'est pas** sur la feuille est signalée comme suspecte, **jamais acceptée** ;
  - **une case vide reste vide** : le modèle transcrit, il n'invente pas un 0 qu'il n'a pas lu — la ligne comptera zéro de toute façon, mais on doit voir qu'il n'a rien lu dessus ;
  - chaque valeur porte une **confiance**, les basses étant mises en avant ;
  - les articles attendus mais non lus apparaissent en ligne vide, à saisir.
- **Rapprochement sur le trio feuille + article + section**, jamais sur la seule référence. Quand la référence ne figure qu'une fois, la section n'est pas exigée ; quand elle figure plusieurs fois et que la section est illisible, **la ligne est signalée plutôt que posée au hasard** — se tromper de tableau fausse deux quantités d'un coup.
- **Feuille de saisie libre** : la garde se déplace d'un cran — c'est le **référentiel articles** qui tranche. Une référence inconnue est signalée, jamais créée.
- **Rien n'est posté automatiquement.** Tout atterrit dans une grille modifiable, marquée comme issue d'une extraction.
- **Statut** : **[CO]** — `guide §2.9`, `test_scan_pipeline.py`, `test_scan_free_entry.py`.

### EX-ZON-9 — Lire une pile de feuilles d'un coup
- **Besoin** : le PDF sorti du scanner avec l'ensemble des feuilles dedans.
- **Chaque page se rattache à sa feuille par ce que l'application a imprimé en pied de page.** Le modèle **recopie** ce pied sans rien vérifier ; c'est l'application qui rapproche. L'identifiant suffit seul ; à défaut, la paire zone + numéro de comptage rattrape la page **dès qu'elle ne désigne qu'une feuille**.
- **Deux lectures contradictoires, ou un pied illisible, sont signalés, jamais devinés** : une page classée dans la mauvaise zone verse un comptage sur du stock qui n'y a jamais été.
- **Le dépôt rend la main immédiatement** ; la lecture continue en arrière-plan avec un avancement consultable, et survit à la fermeture de la fenêtre.
- **Trois refus explicites plutôt qu'un silence** : une feuille **déjà corrigée à la main est préservée** (avec une option explicite de relecture qui dit ce que cela a coûté) ; une feuille illisible est **nommée avec ses pages et la raison**, les autres aboutissant ; une pile au-delà du plafond est **refusée en disant les deux nombres**.
- **Résilience exigée** : un lot en erreur est **recoupé et redemandé** jusqu'à la page seule, plutôt que perdu en bloc. *Une pile de soixante-quinze pages dont six lots sur sept échouaient rendait soixante-douze pages à saisir à la main.*
- **Les lignes ajoutées à la main sur le papier doivent revenir dans l'ordre de la feuille scannée**, pas à la fin ni au hasard. **[EC]**
- **Statut** : **[CO]** sauf l'ordre des lignes manuscrites (**[EC]**).

### EX-ZON-10 — Déclarer une zone terminée
- **Trois états, dont deux se déduisent** (RG-ZON-1). *Terminée* est **une décision**, et elle ne peut pas se déduire : une ligne qu'on ne peut légitimement pas compter — l'article a disparu, l'emplacement est inaccessible — laisserait sinon la zone ouverte pour toujours, et avec elle le passage de la campagne en analyse.
- **Terminer une zone dont les deux comptages se contredisent encore est refusé, en le disant.** **Rouvrir ne se refuse jamais** — c'est le geste qui répare une clôture trop rapide.
- **Statut** : **[CO]** — `domain/workflow.py`, `test_zone_closure.py`.

### EX-ZON-11 — Supprimer une zone
- **Possible en Préparation uniquement.** Passé en comptage, une zone porte des quantités saisies et la faire disparaître effacerait un travail de terrain : elle se ramène alors à un seul comptage, ou ses emplacements se désactivent.
- Les feuilles partent avec la zone, **et le message de confirmation dit combien**.
- **Statut** : **[CO]** — `test_zone_deletion.py`, `test_delete_dialogs.py`.

## D.7 Arbitrage et consolidation

### EX-ARB-1 — Comparer les deux passages
- Le tableau couvre **chaque article présent dans l'un ou l'autre**, y compris ceux qu'une seule équipe a comptés — *que l'ancien processus ne voyait pas*.
- **Tri** : décisions requises d'abord, puis par **impact en euros**. Le désaccord le plus coûteux est traité en premier.
- **Statut** : **[CO]** — identifié comme un défaut mesuré de l'existant (`01-analyse §2.4`).

### EX-ARB-2 — Trancher
- **Ligne par ligne**, en saisissant la quantité retenue ou en reprenant l'un des deux passages.
- **En lot** : appliquer le comptage n°1 **ou** le n°2 à toute la zone. **[EC]**
- **Un bouton « Valider tout »**, parce que *« dans certains cas, la validation ligne par ligne peut être inutilement longue à faire »*. **[EC]**
- **Chaque ligne reste enregistrée comme une décision explicite, à son auteur.**
- **Exigence dérivée** : une ligne déjà tranchée à la main n'est pas écrasée par un geste de lot. **[HV]** — interprétation, à confirmer (Q-10).
- **Statut** : **[EC]**.

### EX-ARB-3 — Refaire un arbitrage que les chiffres ont périmé
- **Besoin exprimé, en majuscules dans la demande** : *« Si les données d'une feuille de comptage n°1 ou n°2 sont modifiées après arbitrage, ce dernier DOIT être refait. »*
- **Résultat attendu** : la décision cesse de valoir dès que l'un des deux chiffres qu'elle tranchait a changé ; la ligne redevient à trancher, et l'utilisateur en est informé.
- **Critère d'acceptation** : après modification d'une quantité arbitrée, la zone ne peut plus être déclarée terminée sans un nouvel arbitrage.
- **Statut** : **[EC]** — `test_arbitration_freshness.py`.

### EX-CONS-1 — Consolider les zones en un journal
- **Aperçu permanent** : ce que contiendrait le journal, quelles zones manquent, ce qui bloque — **sans rien écrire**.
- **La consolidation** reprend chaque zone terminée, applique la règle de section, exclut les articles hors périmètre **après l'éclatement**, alimente le journal de l'emplacement GENERIQUE, et produit la **décomposition du WIP** : quel assemblage a produit quelle quantité de quel composant, dans quelle zone.
- **Le journal est exportable au format d'import ERP** : il s'importe au lieu d'être recopié à la main.
- **Statut** : **[CO]** — `domain/consolidation.py`, `test_domain_consolidation.py`.

### EX-CONS-2 — Débloquer un WIP sans nomenclature
- **Cas le plus fréquent** : un assemblage compté en WIP n'a aucune structure, donc l'éclater ferait disparaître la quantité comptée.
- **Les nomenclatures étant gelées pendant le comptage**, la résolution proposée est de **compter ces assemblages tels quels** (reclassement), en un geste.
- **Preuve du besoin** : en rejouant la campagne réelle de juin 2026, **4 assemblages comptés en WIP n'avaient aucune nomenclature**, pour 8 lignes de comptage. Sous Excel, ces quantités ont été perdues sans que personne ne le sache.
- **Statut** : **[CO]**, appuyé sur une mesure.

## D.8 Écarts, analyses, ajustements

### EX-ANA-1 — Deux lectures de l'écart, et l'ordre compte
- **Par référence** — la lecture de référence, celle sur laquelle l'écran s'ouvre. *Un transfert entre deux emplacements n'est pas une perte*, donc les emplacements sont agrégés. C'est le chiffre qui dit ce que le site a réellement perdu ou gagné.
- **Par emplacement** — vue opérationnelle : dit *où* aller recompter.
- **La part de simple transfert est chiffrée explicitement** : une part élevée signifie que le comptage n'est pas d'accord avec l'ERP sur *où* est le stock, pas sur *combien* — ce n'est pas la même alarme qu'un manquant.
- **Statut** : **[CO]** — `guide §3.2`, `domain/variance.py`.

### EX-ANA-2 — Décomposer un chiffre agrégé
- **Besoin exprimé** : *« Dans les fenêtres qui s'ouvrent pour afficher la décomposition d'une quantité donnée … n'affiche que les lignes qui portent une quantité non nulle. »*
- **Statut** : **[EC]**.

### EX-ANA-3 — Les trois indicateurs de fiabilité, côte à côte
- **Fiabilité nette**, **fiabilité brute**, **IRA** — trois questions différentes, affichées ensemble avec leur définition. *Publier l'une sans les autres, c'est choisir sa conclusion avant de calculer.*
- **La fiabilité brute est désignée comme l'indicateur à piloter.**
- **Statut** : **[CO]** — `05-modele §5`.

### EX-ANA-4 — Charger les ajustements
- **Un ajustement est un mouvement de stock, pas une correction d'écart** : il s'ajoute au comptage pour former le stock physique, et c'est ce dernier que l'écart oppose à l'ERP gelé.
- **Quantité et valeur sont signées.** Ce que le comptage seul montrait reste lisible à côté.
- **Le cycle analyser → agir sur le terrain → ajuster → recharger se répète autant de fois que nécessaire.**
- **Statut** : **[CO]** — `guide §3.3`.

### EX-ANA-5 — Affecter des causes
- Référentiel de **site**, hors campagne, de **14 causes standard**, repris de l'existant.
- **La part non affectée est affichée explicitement** : c'est elle qui alimente le plan d'action de la campagne suivante.
- **Une proposition IA apparaît à côté de la décision, jamais à sa place.**
- **Statut** : **[CO]**.

### EX-ANA-6 — Analyses avancées
ABC/XYZ, écarts atypiques, familles de comportements, priorité de recomptage,
loi de Benford, biais d'arrondi. **Exigence transverse** : résultats
**reproductibles** — la même campagne réanalysée demain produit exactement les
mêmes signalements. **[CO]**

### EX-ANA-7 — Comparer deux campagnes par les flux
- **Identité comptable** : `stock attendu = stock initial + réceptions + production − expéditions − consommation théorique − rebuts`.
- **Quatre paires comparables** (physique/ERP × physique/ERP), basculables sans rien recharger.
- **Lecture ERP tout-ou-rien** : les cinq mesures sont écrites ensemble ou l'appel échoue en disant pourquoi, les quantités précédentes restant intactes.
- **Un article présent d'un seul côté n'est pas un zéro** : ces lignes sortent des totaux et sont regroupées à part.
- **Seules les références du référentiel de la campagne et non exclues** sont retenues, et le nombre de lignes écartées est dit.
- **Statut** : **[CO]** — `guide §3.8`, `test_stock_flow.py`.

## D.9 Contrôles, clôture, traçabilité

### EX-CTL-1 — Un catalogue de contrôles typés
- Chaque constat porte **un code stable, une sévérité (bloquant / avertissement / information), l'entité concernée, un message en clair et, quand il existe, le geste de résolution**.
- **48 codes** sont définis aujourd'hui (liste en annexe 1.4).
- **Exigence de fond** : un avertissement qui se déclenche à tort finit par ne plus être lu. *Illustration retrouvée : une feuille volontairement vide et une feuille oubliée se ressemblent exactement ; sans un marqueur distinguant les deux, le contrôle signalait les deux.*
- **Statut** : **[CO]** — `domain/controls.py`.

### EX-CLO-1 — Préparer la clôture
- **La clôture est le seul geste irréversible du parcours.** Une liste de contrôle en donne l'état des lieux, sur trois tons : ce qui bloque, ce qu'il faut regarder, ce qui est acquis.
- **Trois blocages exigés** : un écart matériel **sans cause ni acceptation explicite** ; l'absence d'archive publiée ; (voir annexe 4, CT-3 pour le troisième, dont le statut a changé).
- **Justification du premier** : *clôturer figerait un écart que personne n'a expliqué, et c'est précisément ce qu'un contrôle demandera six mois plus tard.*
- **Justification du second** : *la base opérationnelle est vivante, l'archive est ce qui reste.*
- **Statut** : **[CO]** — `domain/closure.py`, `test_closure_checklist.py`.

### EX-TRC-1 — Journal d'audit inaltérable
- Chaque action et chaque changement de statut, avec **auteur et horodatage**.
- **Exigence forte** : la trace ne doit pouvoir être **ni réécrite ni vidée**, y compris par un défaut de l'application elle-même. *Une convention de code se contourne par accident.*
- **Statut** : **[CO]** — `test_audit_immutability.py`. Le *moyen* (règles du moteur de base) est un choix d'implémentation ; **l'exigence est l'inaltérabilité**.

### EX-TRC-2 — Conserver la pièce d'origine
- **Le fichier reçu est conservé tel quel, avant toute interprétation**, et re-téléchargeable : *les lignes en base sont le résultat d'une lecture, le fichier en est la source.*
- **Une feuille lue par IA garde son scan** ; la pile entière est conservée quand plusieurs feuilles ont été scannées d'un coup.
- **Les cas sans pièce sont nommés** (collage, lecture directe, antériorité) plutôt que présentés comme une pièce manquante.
- **Statut** : **[CO]** — `test_evidence.py`.

### EX-TRC-3 — Provenance de chaque quantité
- Toute quantité porte **sa source** : import ERP, saisie manuelle, extraction IA, consolidation, arbitrage, système.
- **La source appartient à la ligne, pas à la feuille** : *« Si une ligne a été modifiée à la main après extraction IA, seule cette ligne doit changer de source et non pas toute la feuille. »* **[EC]**
- **Statut** : **[EC]** pour le grain, **[CO]** pour le vocabulaire.

## D.10 Assistance IA

### EX-IA-1 — Le cadrage général
| Règle | Statut |
|---|---|
| **L'IA propose, l'humain décide.** Aucune sortie de modèle n'est écrite dans une colonne de décision, postée, ni utilisée pour clore une ligne sans intervention humaine | **[CO]** — voir Q-19 |
| Toute production IA est **marquée comme telle** | **[CO]** |
| L'assistant **ne modifie rien** : aucune quantité, aucune cause, aucun statut ne change parce qu'on a posé une question | **[CO]** |
| **Les chiffres viennent du dossier, le raisonnement est libre.** Un chiffre absent du dossier est **déclaré absent, jamais estimé en silence** | **[CO]** |
| Chaque question est **tracée** | **[CO]** |
| Une lecture douteuse ne fait jamais échouer les cent autres lignes d'une feuille | **[CO]** |

---

# E. Règles et décisions métier

> Les règles sont numérotées `RG-<domaine>-<n>`. Chacune porte sa justification
> **quand elle a été retrouvée**, et l'indication explicite du contraire sinon.

## E.1 Valorisation et écarts

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-VAL-1** | **Toute valeur se calcule `prix standard unitaire × quantité`, pour le stock ERP comme pour le stock compté.** Un écart en euros mesure donc une différence de quantité, et rien d'autre | *« Non il faut utiliser le coût standard unitaire x quantité partout pour avoir la valeur du stock (ERP et compté) »* | **[EC]** |
| **RG-VAL-2** | Corriger un prix met à jour toute la campagne sans rien recharger | Conséquence de RG-VAL-1 | **[CO]** |
| **RG-VAL-3** | Si le prix standard d'un article est nul, le coût porté par la ligne de stock sert de secours | Non retrouvée. **Justification inconnue** | **[CO]** |
| **RG-VAR-1** | `écart = stock physique − stock ERP`, avec `stock physique = compté + ajustements postés` | Un ajustement est un mouvement, pas une correction d'écart | **[CO]** |
| **RG-VAR-2** | **Un écart est *matériel* quand il franchit *toutes* les barrières configurées de son type d'article** — et non l'une d'elles | *Exiger la conjonction garde la liste d'exceptions à une taille qu'une équipe peut réellement traiter le jour J* | **[CO]** |
| **RG-VAR-3** | **Exception : un écart sur un article dont l'ERP ne connaissait aucun stock est toujours matériel** | *Du stock inconnu du système n'est jamais une différence d'arrondi* | **[CO]** |
| **RG-VAR-4** | **Un article exclu du périmètre ne produit aucun écart**, quoi qu'on ait compté dessus : ni son stock ERP, ni son comptage, ni ses ajustements n'entrent dans le calcul | C'est ce que l'exclusion veut dire | **[CO]** |
| **RG-VAR-5** | S'il a néanmoins été compté, **un contrôle le signale avec les deux gestes possibles** (lever l'exclusion, ou retirer la ligne). *La quantité n'est jamais perdue en silence* | Cohérence avec l'objectif A.2 | **[CO]** |
| **RG-VAR-6** | Écart **net** = somme signée ; écart **brut** = somme des valeurs absolues. *Un écart de +100 k€ et un de −100 k€ ne font pas zéro erreur : ils font deux erreurs* | Défaut mesuré du dispositif Excel | **[CO]** |

**Le détail exact des barrières de matérialité fait l'objet d'une contradiction
entre la documentation et le code — voir annexe 4, CT-2. Cette contradiction doit
être tranchée par le commanditaire avant toute implémentation** (Q-1).

## E.2 Comptage et cases vides

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-CPT-1** | **Une case laissée vide sur une feuille de comptage compte pour ZÉRO, partout.** Il n'existe plus de notion de « non compté » en quantité | *« Ne différencie plus le stock non compté et la quantité 0. Partout où rien n'est renseigné en quantité dans les feuilles de comptage, met 0 au lieu de non compté »*. Et le fond : *la ligne est sur la feuille parce qu'on s'attend à trouver la référence dans la zone ; n'y avoir rien trouvé est un écart à expliquer, pas une mesure manquante* | **[EC]** |
| **RG-CPT-2** | **La distinction « quelqu'un a-t-il écrit dans cette case ? » subsiste à un seul endroit : l'avancement d'une zone.** Une zone dont aucune ligne n'a été touchée est « à compter » ; dès qu'une valeur y est saisie — zéro compris — elle passe « en cours » | Cette distinction ne pèse sur aucune quantité ; elle dit seulement si une zone reste à compter | **[EC]** |
| **RG-CPT-3** | **Une correction manuelle n'est jamais détruite par un rechargement de l'export ERP** | Recharger dix fois dans la journée est le mode normal du jour J | **[CO]** |
| **RG-CPT-4** | Une référence **absente** n'est pas une référence **nulle** : « l'ERP n'en connaît aucun stock » et « l'ERP annonce zéro » sont deux faits distincts | Les confondre ferait d'un article que l'ERP ignore un écart franc | **[CO]** |
| **RG-CPT-5** | Un journal *en attente* est un emplacement qu'on n'a pas encore touché : **le compter à zéro inventerait un manquant**. Il n'entre pas dans le stock compté | Explicite | **[CO]** |

> **Attention au repreneur.** RG-CPT-1 et RG-CPT-4 ne se contredisent pas et
> portent sur deux objets différents : RG-CPT-1 sur une **case de feuille de
> comptage** (le compteur est passé, la ligne était pré-imprimée) ; RG-CPT-4 sur
> la **référence ERP** d'une ligne (l'ERP n'a rien dit du tout). Les mélanger
> est l'erreur naturelle à cet endroit.

## E.3 Le référentiel fait foi

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-REF-1** | **L'application ne crée jamais un article à partir d'une donnée de comptage.** Une référence inconnue du référentiel est une **erreur de ligne**, jamais un article créé par effet de bord. La règle vaut pour le stock ERP comme pour les feuilles, **dans les trois modes d'import** | *Sans article, une ligne n'a ni désignation, ni prix, ni type — son écart s'afficherait en quantité nue, hors de toute règle de matérialité* | **[CO]** |
| **RG-REF-2** | Elle vaut aussi pour l'extraction IA : une référence lue mais absente du référentiel est **signalée, jamais créée** | Même fondement | **[CO]** |
| **RG-REF-3** | **Exception décidée** : une référence manuscrite absente de la feuille imprimée mais **présente au référentiel de la campagne** devient une ligne de comptage, placée après les lignes pré-imprimées | *« La créer si elle existe au référentiel »* — réponse explicite à une question posée | **[EC]** |
| **RG-REF-4** | Un **emplacement** n'est pas davantage créé par un comptage : le référentiel entrepôts/emplacements naît du chargement du stock ERP | Cohérence avec RG-REF-1 | **[CO]** |

## E.4 Unicité, doublons et rapprochement

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-UNI-1** | **Une ligne de feuille de comptage est identifiée par le quadruplet `feuille + article + section + sous-section`.** Cette contrainte d'unicité **doit être appliquée en toutes circonstances** | Décision explicite en réponse à une question posée : *« La contrainte d'unicité référence + section + sous-section doit être toujours appliquée »* | **[EC]** |
| **RG-UNI-2** | Un même article peut donc légitimement figurer **plusieurs fois** sur une feuille : deux sections différentes, ou deux sous-sections différentes. **Ce n'est pas un doublon** | *En bord de ligne pour les bacs, en WIP pour ce qui est monté sur un assemblage : ce sont deux comptages distincts, posés sur deux tableaux différents du papier* | **[EC]** |
| **RG-UNI-3** | **Une ligne vide séparatrice n'est pas une sous-section** : elle ne participe pas à la clé, elle formate la feuille | Décision explicite | **[EC]** |
| **RG-UNI-4** | **Un emplacement n'appartient qu'à un seul journal ERP.** Seul le journal propriétaire le compte | *Sans quoi vous liriez le stock ERP d'un journal contre le comptage d'un autre* | **[CO]** |
| **RG-UNI-5** | Une seule photographie de stock est chargée par campagne | *Une campagne se compare à un état du système à un instant, pas à un stock additionné sur trois mois* | **[CO]** |
| **RG-UNI-6** | Les clés métier sont **normalisées** (casse, espaces) à toutes les frontières d'entrée ; **les identifiants transportés — étiquettes, numéros de série — ne le sont pas** | Mesuré : sur 478 articles reconstitués, la normalisation a fusionné **5 doublons de casse**. À l'inverse, « 001609231 » perd trois caractères au premier passage par un entier, et une étiquette tronquée ne se rattache plus à rien | **[CO]** |
| **RG-UNI-7** | **Les identifiants métier ne sont jamais concaténés.** Un emplacement est un couple (entrepôt, emplacement) | Mesuré sur l'existant : deux emplacements homonymes dans deux entrepôts n'étaient distingués que par une concaténation, et un espace de trop faisait échouer la correspondance en silence | **[CO]** |

## E.5 Sections et consolidation

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-SEC-1** | **Trois sections, qui décident chacune de la règle de consolidation** : bord de ligne (compté tel quel) ; WIP non déclaré (**éclaté en nomenclature**) ; WIP assemblé (déclaré dans l'ERP, compté tel quel) | Reprise de l'existant, désignée comme *une vraie règle métier, qui reflète l'état de la déclaration dans l'ERP* | **[CO]** |
| **RG-SEC-2** | La section est une **donnée typée**, pas un texte libre | Défaut mesuré : une faute de frappe changeait silencieusement la règle de calcul appliquée à la ligne | **[CO]** |
| **RG-SEC-3** | Les anciens libellés sont reconnus **uniquement à l'import**, pour reprendre un ancien classeur. L'interface, les rapports et le stockage ne parlent que le vocabulaire actuel | Explicite | **[CO]** |
| **RG-SEC-4** | **Les textes d'en-tête de section sont modifiables par zone**, avec des valeurs par défaut | Demande explicite, avec les libellés par défaut fournis mot pour mot puis corrigés une fois | **[EC]** |
| **RG-CONS-1** | **La résolution d'une quantité de zone suit un ordre, la première règle qui s'applique gagne** : (1) une décision d'arbitrage explicite ; (2) les deux passages s'accordent → cette valeur ; (3) un seul passage existe → celui-là, **signalé seulement si deux étaient attendus** ; (4) sinon la zone n'est pas résolvable et c'est **bloquant** | Une zone à comptage unique n'est pas une zone incomplète | **[CO]** |
| **RG-CONS-2** | **Seules les décisions effectivement prises comptent.** Une quantité préremplie en lot est une suggestion posée dans un champ ; la poster comme si quelqu'un l'avait choisie défait le but de la question | Explicite | **[CO]** |
| **RG-CONS-3** | **Les exclusions de périmètre s'appliquent APRÈS l'éclatement**, pour ne pas perdre les composants d'un assemblage hors périmètre | Explicite | **[CO]** |
| **RG-CONS-4** | **Un assemblage compté sans nomenclature bloque la consolidation** avec un message explicite et une résolution, **au lieu de disparaître** | Défaut mesuré de l'existant : 4 assemblages, 8 lignes, perdus en silence | **[CO]** |
| **RG-CONS-5** | **L'éclatement traverse les niveaux et s'arrête au premier article porteur de stock** ; un cycle est détecté et refusé | Défaut mesuré : éclatement mono-niveau, structures fantômes, et un cycle ferait recalculer indéfiniment | **[CO]** |
| **RG-CONS-6** | **Un produit fini compté en bord de ligne est écarté de la consolidation** : il n'entre que par la porte du WIP | *En bord de ligne il compterait une deuxième fois ce que ses composants comptent* | **[CO]** |
| **RG-CONS-7** | **La sortie est déterministe** : mêmes entrées → journal identique | C'est ce qui rend une campagne reproductible des mois plus tard | **[CO]** |
| **RG-CONS-8** | **L'éclatement est tracé** : quel assemblage a produit quelle quantité de quel composant, dans quelle zone | Défaut mesuré : aucune trace sous Excel | **[CO]** |

## E.6 Exclusions

Quatre portées, dont deux facettes indépendantes. **[CO]** — `domain/enums.py`.

| Portée | Effet |
|---|---|
| **Aucune** | L'article participe partout |
| **GENERIQUE** | Exclu de la consolidation GENERIQUE et de son analyse — **mais il garde son stock ERP et produit donc son écart** |
| **Nomenclature** | Ignoré lors de l'éclatement d'un parent |
| **Totale** | Exclu de tout comptage et de toute analyse |

**Règle de cohérence** : la portée totale **remplace** les deux facettes au lieu
de coexister avec elles — sans quoi la même intention se stocke de trois façons
et un écran présentant l'ensemble brut dit trois choses différentes de trois
articles identiques. **[CO]**

## E.7 Phases, gel et séquence

### RG-PHA-1 — La matrice de gel

**Le tableau suivant est une exigence fonctionnelle.** Sa forme d'implémentation
— une fonction unique consultée par le serveur et par l'interface — est libre ;
**l'exigence est que serveur et interface ne puissent pas diverger**.

| Ce qui est modifiable | Préparation | Comptage | Analyse | Clôturée |
|---|:---:|:---:|:---:|:---:|
| Seuils de matérialité | ✅ | ❌ | ❌ | ❌ |
| Réglages de saisie (formules) | ✅ | ✅ | ❌ | ❌ |
| Articles, nomenclatures | ✅ | ❌ | ❌ | ❌ |
| Emplacements (activation) | ✅ | ✅ | ❌ | ❌ |
| Stock ERP | ❌ | ✅ | ❌ | ❌ |
| Zones GENERIQUE (structure) | ✅ | ✅ | ❌ | ❌ |
| Feuilles de comptage (structure) | ✅ | ✅ | ❌ | ❌ |
| **Quantités** saisies sur les feuilles | ❌ | ✅ | ❌ | ❌ |
| Journaux de comptage | ❌ | ✅ | ❌ | ❌ |
| **Comptages avancés** | ❌ | ✅ | ❌ | ❌ |
| Ajustements | ❌ | ❌ | ✅ | ❌ |
| Analyse des écarts (causes) | ❌ | ❌ | ✅ | ❌ |
| Écart backflush | ✅ | ✅ | ✅ | ❌ |
| Comparaison inter-campagnes | ✅ | ✅ | ✅ | **✅** |

**Quatre points méritent attention, chacun avec sa justification retrouvée :**

1. **Les zones GENERIQUE restent créables pendant le comptage** — *une aire
   physique que personne n'avait listée est découverte à chaque campagne.*
2. **La structure d'une feuille et les quantités qu'elle porte sont deux
   aspects distincts** — sans quoi une feuille était remplissable en
   préparation, produisant des comptages d'une campagne qui n'avait pas commencé.
3. **Les comptages avancés sont un aspect distinct des journaux de comptage.**
   Même fenêtre, prérequis différent (voir RG-SEQ-1). *Les avoir confondus est
   l'erreur de conception documentée en `07 §14`.*
4. **La comparaison inter-campagnes reste ouverte même sur une campagne
   clôturée** — *comparer deux inventaires par les flux de la période entre eux
   est quelque chose qu'on fait une fois les deux finis ; la geler à la clôture
   interdisait l'usage principal de la fonction.* Elle n'écrit rien qui entre
   dans un chiffre validé.

### RG-PHA-2 — Les préconditions de passage

| Vers | Ce qui bloque |
|---|---|
| **Comptage** | Rien de structurel : le stock ERP se charge *pendant* le comptage |
| **Analyse** | Stock ERP non gelé ; journaux non postés ni forcés ; **dérives matérielles non tranchées** ; zones non terminées |
| **Clôture** | Écarts matériels sans cause ni acceptation explicite ; archive non publiée |

**[CO]** — `domain/workflow.py`.

**Une règle a été explicitement retirée des blocages, et la justification est
écrite** : les lignes refusées à l'import ne bloquent plus la clôture. *Un
chargement laisse des lignes refusées pour des raisons que l'exploitant connaît
et assume ; exiger zéro refus rendait la clôture impossible sur un manque que
personne n'avait le pouvoir de combler, et poussait à recharger un fichier pour
faire taire un point plutôt que pour corriger quelque chose.* **Le constat reste
affiché — ce qui change est le pouvoir d'arrêt, pas la visibilité.** **[CO]**

### RG-SEQ-1 — L'ordre à l'intérieur d'une phase

Une phase autorise ; elle n'ordonne pas. Or les étapes ont un ordre, et les
faire dans le désordre produit **du travail qui a l'air fait et ne l'est pas**.

| Étape | Attend d'abord |
|---|---|
| Nomenclatures, zones, feuilles, seuils | Le référentiel articles (et les zones, pour les seuils) |
| Stock ERP | Le référentiel articles — *sinon l'import ne rejetterait pas une ligne ou deux, il les rejetterait toutes, en reprochant à chacune une absence dont la cause est ailleurs* |
| Journaux de comptage, quantités de feuilles | Le stock ERP chargé |
| **Comptages avancés** | **Le référentiel articles seulement** — le comptage avancé *précède* le chargement général et se mesure contre la référence que son propre journal transporte |
| Poster un journal | Le stock ERP chargé **et gelé** — *poster un comptage contre une référence qui peut encore bouger rend l'écart irreproductible* |

**Trois incidents réels ont motivé cette règle**, tous du même après-midi : des
quantités saisies en préparation ; un journal consolidé produit à partir de ces
quantités ; tous les journaux postés avant que le stock ERP ne soit chargé.
**Aucun des trois n'avait rien déclenché.** **[CO]** — `domain/sequence.py`.

## E.8 Comptages avancés

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-AVC-1** | **Le journal de précomptage porte sa propre référence** : la colonne « Stock ERP » de ses lignes donne le stock d'avant comptage. **Il n'y a aucun stock à charger séparément** pour un lot avancé | Structure du document ERP | **[CE]** |
| **RG-AVC-2** | **Le périmètre se déclare, il ne se devine pas.** L'ordre des gestes est indifférent : si deux journaux entrent avant qu'aucun ne soit déclaré, l'emplacement porte leur somme, et **déclarer recalcule le comptage sur le seul propriétaire** | Mesuré : 1 932 lignes sur 58 345 ne portent un autre emplacement que pour matérialiser un déplacement | **[CE]** pour le fait, **[CO]** pour le traitement |
| **RG-AVC-3** | **Le tri des lignes se fait ligne par ligne, pas emplacement par emplacement** : un même fichier apporte les lignes du propriétaire et celles des journaux de passage | *Écarter la clé entière priverait l'emplacement scellé de sa quantité comptée* | **[CO]** |
| **RG-AVC-4** | **Le gel du stock ERP ferme la fenêtre du précomptage, et c'en est la définition** : précompter veut dire *avant* la référence générale. Après le gel, il n'y a ni périmètre à déclarer ni emplacement à sceller | Définitionnelle, pas prudentielle | **[CO]** |
| **RG-AVC-5** | **Le chargement du stock ERP général ne touche pas aux emplacements scellés** | *Sinon le résultat de leur inventaire disparaîtrait le jour J* | **[CO]** |
| **RG-AVC-6** | **Sceller un précomptage démarre aussi son journal de comptage** | Sans cela il apportait sa référence et rien d'autre, et un journal *en attente* n'entre pas dans le compté | **[CO]** |
| **RG-AVC-7** | **Un journal ERP ne se supprime pas.** Le geste inverse de « déclarer et sceller » est **desceller** | Un journal ERP n'est pas une saisie mais le reflet d'un document de l'ERP : le supprimer ne le retirerait pas de l'ERP, et laisserait un emplacement scellé sans le journal qui justifie sa référence — donc indéclarable et indescellable | **[CO]**, en réponse à une question explicite du commanditaire |
| **RG-AVC-8** | **La référence porte sa date.** Une campagne qui précompte a une **référence composite en dates** : le jour J pour la plupart des emplacements, la date du précomptage pour les emplacements scellés | La référence est *ce contre quoi la campagne a été comptée* | **[CO]** |
| **RG-AVC-9** | **Cette composition doit être dite.** Un rapprochement avec un état ERP tiré à une date unique trouvera une différence, égale à la somme des écarts des précomptages. **La date de référence de chaque ligne est affichée et exportée** | Sinon la différence est inexplicable | **[CO]** |
| **RG-AVC-10** | **Les écarts des emplacements scellés sont visibles immédiatement**, sans attendre le chargement ni le gel du stock ERP général | *C'est le but même du précomptage : voir l'écart quand on peut encore aller voir sur le terrain* | **[CO]** |

## E.9 Zones et arbitrage

| # | Règle | Justification | Statut |
|---|---|---|---|
| **RG-ZON-1** | **Une zone a trois états, dont deux se déduisent des quantités** (à compter / en cours) **et un seul est une décision** (terminée) | Les états déduits ne peuvent pas mentir ; le troisième ne peut pas se déduire sans condamner une campagne pour une ligne qu'on ne peut légitimement pas compter | **[CO]** |
| **RG-ZON-2** | **Une feuille de comptage n'a pas d'état.** | Elle en a eu quatre, avancés à la main deux fois par zone, sans qu'aucune écriture n'en dépende | **[CO]** |
| **RG-ZON-3** | **Terminer une zone dont les deux comptages se contredisent est refusé. Rouvrir ne se refuse jamais** | Sans arbitrage la consolidation ne sait pas quelle quantité retenir ; rouvrir est le geste qui répare une clôture trop rapide | **[CO]** |
| **RG-ARB-1** | **L'arbitrage couvre chaque article présent dans l'un ou l'autre passage**, y compris ceux comptés par une seule équipe | Défaut mesuré : *une référence comptée par une seule équipe passait inaperçue* | **[CO]** |
| **RG-ARB-2** | Sans tolérance configurée, **toute différence exige une décision humaine** | *Pratique WMS conservatrice pour un double comptage à l'aveugle* | **[CO]** |
| **RG-ARB-3** | **Une décision d'arbitrage meurt avec les chiffres qu'elle tranchait.** Si le comptage n°1 ou n°2 change après coup, l'arbitrage doit être refait | *« Si les données d'une feuille de comptage n°1 ou n°2 sont modifiées après arbitrage, ce dernier DOIT être refait »* | **[EC]** |
| **RG-ARB-4** | Une zone à **un seul comptage** ne produit aucune ligne d'arbitrage | Il n'y a pas de second avis à comparer, et en fabriquer un bloquerait la consolidation pour une décision que personne ne peut prendre | **[CO]** |

## E.10 Conditions de modification, suppression et réouverture

| Objet | Modifiable | Supprimable | Réouvrable |
|---|---|---|---|
| **Campagne** | Selon la matrice de gel | Par le **propriétaire seul**, logiquement ; la trace reste | **Jamais** une fois clôturée |
| **Zone** | Structure : Préparation + Comptage | **Préparation seulement** ; logiquement ; le message dit combien de feuilles partent | *Terminée* → *rouverte* : toujours permis |
| **Ligne de feuille** | Comptage | **N'importe quelle ligne** — **[EC]** | — |
| **Journal de comptage** | Comptage | Disparaît si son emplacement est désactivé | Non défini — **[HV]** |
| **Journal ERP** | Par réimport, qui remplace ses lignes | **Jamais** (RG-AVC-7) | — |
| **Périmètre déclaré / scellement** | — | **Descellement motivé**, possible même après le gel | Redéclarer rescelle |
| **Arbitrage** | Tant que la zone n'est pas terminée | — | **Automatiquement, si un comptage change** (RG-ARB-3) |
| **Emplacement** | Activation : Préparation + Comptage | Désactivation = sortie totale du périmètre | Réactivation — **conséquences non documentées**, voir Q-5 |

## E.11 Exceptions et arbitrages validés

Les exceptions suivantes sont des dérogations **explicitement décidées** à une
règle plus générale. Elles sont regroupées ici parce que ce sont elles qu'une
réimplémentation perd en premier.

| Exception | À quelle règle elle déroge | Fondement |
|---|---|---|
| Un article compté alors que l'ERP n'en connaissait aucun stock est **toujours** matériel | Aux seuils de matérialité | **[CO]** |
| Une zone à comptage unique n'est pas signalée comme incomplète | À l'avertissement « un seul passage » | **[CO]** |
| Les lignes de journaux **vrac** sont hors du contrôle par étiquette | Au contrôle de doublon d'étiquette | **[EC]** |
| Un emplacement scellé où une quantité a été **saisie à la main** est conservé lors du tri du périmètre | Au retrait des emplacements non déclarés | **[CO]** |
| Une pile de scans traite **une transaction par feuille**, pas une pour la pile | À la règle « une commande écrit tout ou rien » | **[CO]** — *trente feuilles ne doivent pas perdre les vingt-neuf qui ont abouti parce que la trentième a échoué* |
| La comparaison inter-campagnes reste ouverte après la clôture | Au gel total de la clôture | **[CO]** |
| Une lecture de liste de dates peut être tronquée | À l'interdiction de troncature silencieuse | **[CO]** — *la troncature y est l'intention* |
| Une référence manuscrite inconnue de la feuille mais **connue du référentiel** est créée comme ligne ; inconnue du référentiel, elle reste refusée | À la garde anti-invention de l'extraction IA | **[EC]** — *« La créer si elle existe au référentiel »* |

---

# F. Workflows de bout en bout

> Les étapes ci-dessous sont **métier**. Celles qui n'existent que par la
> disposition actuelle des écrans sont marquées *(interface)* : **le repreneur
> peut les supprimer, les fusionner ou les réorganiser librement** — les règles
> validées, elles, ne bougent pas.

## F.1 Parcours nominal complet

```
[Préparation]
  1. Créer la campagne (ou dupliquer la précédente)
  2. Charger le référentiel articles                     ← prérequis de tout
  3. Charger les nomenclatures + traiter leur santé
  4. Régler les seuils de matérialité
  5. Préparer les feuilles : zones, listes, sections, sous-sections, en-têtes
  6. Déclarer les gestionnaires et leurs périmètres
  7. Imprimer les feuilles                                (la veille)
  ──► Passage en Comptage : gel des référentiels et des seuils

[Comptage — précomptages]          (facultatif ; sautable entièrement)
  8. Compter et poster les journaux dans l'ERP
  9. Importer les journaux ERP
 10. Déclarer le périmètre de chacun  →  scelle, pose la référence datée
 11. Baliser physiquement                                 (hors application)

[Comptage — général, le jour J]
 12. Charger la photographie du stock ERP de la bonne journée
 13. Désactiver les emplacements hors périmètre
 14. Geler le stock ERP                    ← ferme la fenêtre du précomptage
 15. Importer les journaux de comptage, aussi souvent que nécessaire
 16. Corriger les lignes, forcer au stock ERP les cas prévus
 17. Compter les zones GENERIQUE : saisie, collage, ou scan
 18. Arbitrer les désaccords entre les deux passages
 19. Terminer les zones
 20. Consolider GENERIQUE  →  journal exportable au format d'import ERP
 21. Traiter les dérives des emplacements précomptés
 22. Trancher les conflits d'étiquette
  ──► Passage en Analyse : gel de tout ce qui précède

[Analyse & ajustements]
 23. Lire les indicateurs et les écarts (par référence, puis par emplacement)
 24. Agir sur le terrain, poster les ajustements dans l'ERP, les recharger  ⟲
 25. Affecter les causes
 26. Exploiter les analyses avancées, produire la synthèse
 27. Exporter le dossier ; publier l'archive
  ──► Passage en Clôture : gel définitif
```

## F.2 Variantes métier

| Variante | Ce qui change |
|---|---|
| **Aucun précomptage** | Les étapes 8 à 11 disparaissent. Rien d'autre ne change |
| **Zone à comptage unique** | Une seule feuille ; aucun arbitrage ; l'étape 18 ne la concerne pas |
| **Zone en saisie libre** | Aucune liste pré-imprimée ; le document produit est une grille vide ; à la lecture du scan, c'est le référentiel articles qui fait la garde |
| **Emplacement inventorié ailleurs** | Forçage au stock ERP au lieu d'un comptage — **réservé au cas sans preuve de comptage** |
| **Campagne sans zones GENERIQUE** | Les étapes 5, 17 à 20 disparaissent |
| **Comparaison de deux campagnes** | Parcours indépendant, exécutable **après** clôture des deux |

## F.3 Erreurs, corrections et reprises

| Situation | Comportement attendu |
|---|---|
| **Un import comporte des lignes refusées** | Elles sont listées avec leur numéro et leur raison **avant enregistrement** ; le reste du fichier passe — **sauf** pour un chargement qui *remplace* (RG-IMP-1 ci-dessous) |
| **Un chargement qui remplace comporte un refus** | **Il refuse d'écrire** : les lignes manquantes deviendraient des lignes supprimées, et plus rien ne dirait lesquelles ont disparu. Une dérogation explicite existe, tracée, et n'est pas le défaut — **RG-IMP-1**, **[CO]** |
| **La consolidation est bloquée** | Le motif est nommé, avec le geste de résolution. Le cas dominant a une résolution en un clic |
| **Une lecture de scan échoue** | La feuille est nommée avec ses pages et la raison ; les autres aboutissent |
| **L'application redémarre pendant une lecture** | Le travail est marqué en échec et invite à recharger ; **les feuilles déjà lues avant l'interruption sont conservées** |
| **On recharge la page pendant une lecture** | L'écran retrouve le travail en cours et reprend son suivi, au lieu d'inviter à relancer un scan qui tourne déjà |
| **Une décision d'étiquette est prise puis le journal réimporté** | La décision survit au réimport |
| **Une clôture de zone était trop rapide** | Rouvrir, toujours permis |
| **Un périmètre a été coché de travers** | Desceller (motif obligatoire), puis redéclarer |

## F.4 Actions concurrentes

**Le jour J, une dizaine de personnes travaillent en parallèle.** **[CO]**

| Exigence | Statut |
|---|---|
| **Deux écritures concurrentes sur la même donnée ne peuvent pas se perdre silencieusement.** La seconde est refusée avec un message compréhensible plutôt que d'écraser la première | **[CO]** — le *moyen* (verrouillage optimiste par version de ligne) est libre |
| **Une commande métier écrit tout, ou rien.** Un incident au milieu ne laisse pas une zone sans feuilles, un chiffre sans son auteur, ou un calcul consolidé dont le journal est resté vide | **[CO]** — la liste des cinq cas et de ce que chacun laissait derrière lui est dans `02-architecture §5` |
| **Une lecture bornée ne peut pas être coupée en silence.** Si une source contient plus que le plafond, la lecture est **refusée en nommant la table et le plafond**, jamais tronquée | **[CO]** — *une campagne pouvait partir avec un référentiel amputé sans qu'aucun écran ne l'annonce ; l'écart qui en sortait n'était l'écart de rien* |
| **Une écriture porte toujours sur la campagne dont l'autorisation a été vérifiée** | **[CO]** — `test_cross_campaign_writes.py` |

---

# G. Données et intégrations

> **Cette section décrit l'information métier à conserver, pas un schéma
> physique.** Le repreneur est libre du modèle de stockage, des types, des
> index, du nombre de tables et de leur nom.

## G.1 Les informations métier à porter

| Domaine | Informations indispensables |
|---|---|
| **Campagne** | Code métier, libellé, date de comptage, phase, horodatages de gel, propriétaire, version du moteur de calcul ayant produit ses chiffres |
| **Article** | Référence, désignation, type, groupe, catégorie, programme et spécificité, unité, **prix standard**, portées d'exclusion |
| **Nomenclature** | Parent, composant, quantité par assemblage, unité, version/statut |
| **Entrepôt / emplacement** | Couple identifiant **non concaténé**, type (à étiquettes / vrac), état d'activation |
| **Référence de stock** | Article × entrepôt × emplacement, quantité, unité, coût porté, **date de référence**, origine (snapshot général ou journal de précomptage) |
| **Journal ERP** | Numéro, entrepôt, type, dates, **périmètre déclaré**, scellement (date, auteur, motif de descellement) |
| **Ligne de journal ERP** | Numéro de ligne, date de comptage, emplacement, **étiquette**, **numéro de série**, article, **stock ERP avant comptage**, quantité comptée, unité, statut qualité, postage |
| **Journal de comptage** | Un par (campagne, entrepôt, emplacement), statut, avancement |
| **Ligne de comptage** | Article, **quantité importée et quantité corrigée conservées séparément**, référence ERP (absente ≠ nulle), provenance |
| **Zone** | Code, libellé, nombre de comptages, saisie libre ou non, gestionnaire, **textes d'en-tête de section**, décision de clôture (date, auteur) |
| **Ligne de feuille** | **Nature** (article / sous-section / ligne vide), article, section, sous-section, libellé, unité, **ordre d'affichage**, quantité, **texte de formule d'origine**, commentaire, confiance, **provenance de la ligne** |
| **Arbitrage** | Article, section, quantités des deux passages, quantité retenue, auteur, date, **et de quoi savoir si les chiffres ont bougé depuis** |
| **Consolidation** | Exécution horodatée, lignes produites, **décomposition parent → composant → zone** |
| **Ajustement** | Article, emplacement, date physique, nature, quantité et valeur **signées**, motif, commentaire |
| **Analyse d'écart** | Cause humaine **et** proposition IA, **séparées**, acceptation explicite éventuelle avec commentaire |
| **Dérive / décision d'étiquette** | L'emplacement, le constat, l'issue choisie, son auteur, sa cause |
| **Provenance d'import** | Source, fichier, empreinte du contenu, **journée de la photographie**, volumes acceptés et rejetés, **pièce d'origine conservée** |
| **Audit** | Acteur, action, entité, horodatage, contenu du changement |

## G.2 Contraintes de qualité et d'identité

| Contrainte | Justification |
|---|---|
| **Aucun identifiant métier concaténé** | RG-UNI-7 |
| **Clés métier normalisées ; identifiants transportés jamais normalisés** | RG-UNI-6 |
| **Aucun flottant binaire dans les calculs.** Un total doit toujours être égal à la somme des lignes affichées à côté de lui | *C'est exactement le symptôme qui a fait perdre confiance dans le classeur Excel.* Quantités à 6 décimales (grammes, mètres, ratios de nomenclature), montants à 2 décimales, arrondi *half-up*, **arrondi uniquement aux frontières** — **[CO]** |
| **Une donnée enfant ne peut jamais appartenir à une autre campagne que son parent** | Une autorisation vérifiée sur une campagne ne doit pas permettre d'écrire dans une autre — **[CO]** |
| **Suppressions logiques**, pour que le journal d'audit résolve toujours ses références | **[CO]** |
| **Un chargement porte un identifiant unique** repris par les lignes qu'il a écrites | Sans quoi « d'où vient cette quantité » n'a pas de réponse — **[CO]** |

## G.3 Systèmes externes et contrats réellement imposés

### G.3.1 L'ERP

**[CE]** — les colonnes suivantes sont **imposées par la source** et ne sont pas
un choix de l'application. Les noms cités sont ceux de l'export réel.

| Flux | Ce qui est imposé |
|---|---|
| **Lignes de journaux de comptage** | Un numéro de journal, un numéro de ligne, une date de comptage, site/entrepôt/emplacement, **étiquette** et **numéro de série**, l'article, `OnHandQuantity` (**le stock ERP avant comptage**), `CountedQuantity`, l'unité, le statut qualité, l'indicateur de postage, le **type de journal** (par étiquette / vrac) |
| **Photographie de stock** | Article × entrepôt × emplacement, quantité, unité, coût unitaire |
| **Référentiel articles** | Référence, désignations multiples, groupe d'articles, programme, prix standard **et son unité de prix** (le prix doit être ramené à *une* unité) |
| **Nomenclatures** | Parent, composant, quantité par assemblage |
| **Mouvements de la période** | Réceptions, expéditions, rebuts, production, consommation théorique — **portés par une même ligne**, d'où la lecture tout-ou-rien |

**Défaut de qualité connu de la source, à traiter et non à subir** : l'export
réel contient au moins **une ligne dont l'emplacement est nul** (ligne 635,
article `P-00324093`, 15 unités, journal `NPEM-523609`). Toutes les autres
lignes de ce journal portant le même emplacement, la déduction est univoque.
**Exigence** : récupérer la ligne **et l'afficher comme une correction
automatique à vérifier**. **[CO]**

### G.3.2 Sortie vers l'ERP

**Le journal consolidé doit être exportable au format d'import de l'ERP**, de
sorte qu'il s'importe au lieu d'être recopié à la main. **[CO]** — c'est le
remplacement direct d'un copier/coller identifié comme défaut.

### G.3.3 Modèle de langage

Utilisé pour : la lecture des feuilles scannées, la proposition de causes, la
synthèse, et les questions sur le dossier. **Aucun engagement de disponibilité
n'a été retrouvé.** Une indisponibilité doit dégrader le service — saisie
manuelle — **jamais bloquer une campagne**. **[HV]** — à confirmer (Q-11).

### G.3.4 Contraintes de la plateforme d'hébergement actuelle

**[CE] tant que l'hébergement ne change pas.** Elles sont listées ici parce
qu'elles ont façonné des choix visibles, **et parce qu'elles cessent de
s'appliquer si le repreneur héberge ailleurs** (voir Q-13) :

| Contrainte | |
|---|---|
| Un seul port exposé | Un unique processus sert l'interface et l'API |
| **120 s par requête** (proxy) | Tout traitement long doit être borné, paginé ou renvoyé en tâche de fond |
| 6 Go de RAM, 2 vCPU | Lecture des fichiers en flux ; pas de chargement intégral en mémoire |
| **10 Mo par fichier de charge utile** | Interdit d'embarquer des dépendances volumineuses |
| Pas d'accès privilégié | Uniquement des paquets installables sans droits système |
| **Système de fichiers éphémère** | Aucun état sur disque ; les pièces vont dans un stockage externe |
| Démarrage en 10 minutes maximum | Dépendances figées, migrations rapides et idempotentes |
| Sondes de disponibilité | Une sonde de vie **sans dépendance** (une base en panne ne doit pas faire recycler des conteneurs sains) et une sonde de disponibilité qui refuse tant que les dépendances ne suivent pas |

## G.4 Données existantes et migration

**Aucune installation en production n'a été identifiée dans le dépôt.** Les
éléments suivants sont donc des *conditions* à vérifier plutôt qu'un plan.

| Question | Réponse actuelle |
|---|---|
| Y a-t-il des campagnes en base à reprendre ? | **Inconnu** — voir Q-14 |
| Que faut-il conserver si oui ? | **L'information métier** listée en G.1 : campagnes clôturées avec leurs référentiels gelés, leurs comptages, leurs écarts, leurs causes et **leur journal d'audit**. *Pas les tables* |
| Qu'est-ce qui est réellement critique ? | Le **journal d'audit** et les **pièces d'origine** : ce sont les seules données non recalculables |
| Une archive existe-t-elle déjà ? | Le produit publie une copie de chaque campagne dans un entrepôt analytique, partitionnée par campagne. **Une reconstruction doit dire si elle continue d'alimenter ce même schéma** — voir Q-15 |

> **Distinction demandée, et elle est ici décisive** : conserver l'*information
> métier* d'une campagne close n'oblige à conserver ni ses tables, ni ses
> fichiers, ni son format. Cela oblige à pouvoir **répondre aux mêmes questions
> avec les mêmes chiffres**.

---

# H. Ergonomie et exigences non fonctionnelles

## H.1 Objectifs d'usage

> Aucun de ces objectifs ne prescrit un écran ni un nombre de clics. Ils
> décrivent ce que l'utilisateur doit pouvoir faire et comprendre.

| # | Objectif | Fondement |
|---|---|---|
| **U-1** | **Aucun geste ne doit exister sans qu'une écriture en dépende.** Quatre clics par feuille pour tenir à jour une donnée que personne ne lisait ont été supprimés | **[CO]** — cas documenté |
| **U-2** | **Un refus dit toujours quoi faire**, pas seulement que c'est refusé. *Le refus d'une quantité illisible parlait de la quantité sans jamais dire qu'un réglage existait* | **[CO]** |
| **U-3** | **L'état d'un traitement long est visible**, y compris après rechargement de la page. *Un bouton grisé ne distingue pas un travail qui avance d'un appel qui a calé* | **[CO]** |
| **U-4** | **Un filtre posé doit être lisible.** Un compteur « Filtres (3) » ne dit pas lesquels, et un tableau amputé des deux tiers de ses lignes reste inexplicable | **[CO]** |
| **U-5** | **Les totaux affichés portent sur les lignes affichées** : c'est le chiffre qu'on recopie dans un compte rendu, et il doit correspondre à ce qu'on a sous les yeux | **[CO]** |
| **U-6** | **Ce qui est masqué à l'écran ne part pas dans l'export.** Et une colonne ajoutée par une mise à jour apparaît d'elle-même, au lieu de rester invisible parce qu'un réglage d'il y a six mois ne la connaissait pas | **[CO]** |
| **U-7** | **Une valeur proposée par l'IA se distingue toujours d'une valeur décidée** | **[CO]** |
| **U-8** | **Le travail se prépare à l'avance et se reprend** : ce qui est réglé en préparation ne se retape pas le jour J | **[CO]** |
| **U-9** | **Les documents papier sont conçus pour le terrain** : lisibles avec des gants, traçables page par page, économes en pages | **[CO]** |
| **U-10** | **Aucune donnée ne disparaît d'un écran sans que sa disparition soit dite** | Transverse |

**Demandes d'interface explicites et datées** — elles sont **[EC]** en tant que
demandes, mais ce sont des préférences de disposition, pas des règles métier :
placer « Comptages avancés » avant « Stock ERP » dans le panneau latéral ;
agrandir la fenêtre de saisie ; retirer un encart jugé trop encombrant.
**Le repreneur peut les satisfaire autrement dès lors que l'intention — l'ordre
du processus, la place disponible, la densité — est respectée.**

## H.2 Exigences non fonctionnelles connues

| Domaine | Ce qui est exigé | Ce qui n'est pas fixé |
|---|---|---|
| **Reproductibilité** | Un chiffre doit être **recalculable à l'identique** des mois plus tard : référentiels copiés dans la campagne et non référencés, arithmétique décimale, analyses à graines fixées, version du moteur enregistrée | — |
| **Traçabilité** | Auteur et horodatage de chaque action ; trace **inaltérable** ; pièce d'origine conservée | La durée de rétention — **à déterminer** |
| **Sécurité** | Identité établie par l'infrastructure ; double barrière phase + identité ; aucune écriture hors de la campagne autorisée ; aucun secret dans le code | Le protocole d'authentification |
| **Concurrence** | Écritures concurrentes détectées, jamais un dernier-arrivé-gagne silencieux | Le mécanisme |
| **Disponibilité** | Sondes distinctes « vivant » et « prêt » | **Aucun engagement de disponibilité retrouvé — à déterminer** |
| **Performance** | Toute opération doit tenir dans la limite de la plateforme (**120 s**), ou devenir une tâche de fond suivie | **Aucun objectif de temps de réponse retrouvé — à déterminer** |
| **Volumétrie** | Ordres de grandeur **observés**, à confirmer comme cibles : ~1 400 à 4 000 lignes de stock ; **58 345 lignes de journaux par photographie**, réimportées très souvent le jour J ; ~480 articles sur le jeu réel étudié ; jusqu'à 80 zones ; **250 pages par pile de scan** | **Aucune cible contractuelle. Le recalcul complet des écarts à chaque import demande d'être mesuré avant d'être promis** — c'est écrit comme un point ouvert |
| **Exploitation** | Journalisation structurée sur la sortie standard ; migrations idempotentes | Le format, l'outillage |
| **Accessibilité / internationalisation** | **Aucune exigence retrouvée.** Le produit est intégralement en français | **À déterminer** — Q-12 |

> **Aucun seuil n'est inventé dans cette section.** Les valeurs présentes sont
> soit des contraintes de plateforme mesurables, soit des volumétries observées
> sur des données réelles et signalées comme telles.

---

# I. Recette

> Les critères ci-dessous portent sur des **résultats observables**. Aucun
> n'exige de reproduire un écran, une route, un nom de table ou un mécanisme
> interne.

## I.1 Le socle : un jeu de données et un oracle indépendant

Le dépôt contient un actif directement réutilisable et **conçu pour être
indépendant de l'implémentation** :

| Pièce | Rôle |
|---|---|
| `fixtures/jeu-de-donnees/*.csv` | Une campagne complète, au format des imports réels |
| `fixtures/jeu-de-donnees/oracle.py` | Le résultat attendu, **calculé sans l'application** |
| `docs/09-jeu-de-donnees-de-controle.md` | L'arithmétique posée à la main, ligne à ligne |

**Recommandation forte au repreneur : reprendre ce triangle tel quel.** Une
implémentation nouvelle qui charge les mêmes CSV et retrouve les mêmes chiffres
est validée sur le fond, quelle que soit son architecture. Et la règle qui
l'accompagne doit être reprise aussi : *si les deux divergent, l'une des deux
implémentations a tort — et il faut décider laquelle avant de toucher à quoi que
ce soit. Aligner l'oracle sur l'application retirerait au jeu de données la
seule chose qu'il apporte.*

Le jeu couvre délibérément : précomptage scellé, lignes de passage, forçage au
stock ERP, article exclu, article au prix nul, emplacement désactivé, article
compté sans stock ERP, article ERP jamais compté, arbitrage décidé, éclatement
de WIP, produit fini écarté, ajustement posté, conflit d'étiquette.

## I.2 Scénarios essentiels

### R-1 — Une campagne complète rend les chiffres attendus
**Entrée** : les douze fichiers du jeu de contrôle.
**Attendu** (extrait ; le détail est dans `09-jeu-de-donnees-de-controle.md`) :

| Mesure | Valeur |
|---|---|
| Stock ERP | **11 434,00 €** pour 1 267 unités |
| Stock physique | **10 825,00 €** pour 1 183 unités |
| Écart net | **−609,00 €** pour −84 unités |
| Écart brut | **609,00 €** pour 84 unités |
| Lignes d'écart | **7** |
| Lignes matérielles | **3** (P-100, P-300, P-800) |
| Comptés seuls / ERP seuls | **0** / **1** |

**Critère** : chaque ligne d'écart est juste **en quantité et en valeur** — un
total peut être juste par compensation, une ligne non.

### R-2 — La référence d'un emplacement scellé résiste au jour J
**Entrée** : trois emplacements précomptés et scellés ; un snapshot du jour J
portant **délibérément** des lignes à 999 sur ces mêmes emplacements.
**Attendu** : les lignes à 999 **n'ont pas gagné** ; la référence retenue est
celle du précomptage, à sa date.
**Pourquoi c'est le test décisif** : c'est exactement le piège que le
précomptage crée — sans cette règle, poster le journal réaligne l'ERP, l'écart
tombe à zéro, **et le résultat de l'inventaire disparaît**.

### R-3 — La consolidation GENERIQUE
**Entrée** : une zone à deux passages, un accord, un désaccord arbitré à 105,
un WIP de 5 `SF-10`, un produit fini compté en bord de ligne, un article exclu
`GENERIQUE`.
**Attendu** : `P-100 = 50` (30 comptés + 20 éclatés), `P-300 = 115`
(105 arbitrés + 10 éclatés) ; le produit fini écarté ; l'article exclu retiré
**après** l'éclatement mais **conservant son stock ERP**, donc son écart de −20.

### R-4 — Une ligne de passage ne compte pas
**Entrée** : un journal de précomptage portant 3 unités sur un emplacement
**qu'il ne couvre pas**.
**Attendu** : la ligne reste dans le journal ERP comme trace ; elle **ne compte
pas** dans le stock compté de cet emplacement.

### R-5 — Une case vide compte zéro
**Entrée** : une feuille pré-imprimée où une ligne d'article est laissée vide.
**Attendu** : l'article apparaît dans le stock compté à **0** et produit son
écart ; nulle part n'apparaît un statut « non compté » ; la zone contenant cette
ligne **est** signalée « en cours » dès qu'une autre valeur y a été saisie.

### R-6 — Rechargement répété sans perte
**Entrée** : un journal importé, une ligne corrigée à la main, puis dix
réimports du même export.
**Attendu** : la correction est intacte après le dixième ; la valeur importée
est à jour ; la ligne porte la provenance « saisie manuelle », **et les autres
lignes de la feuille ou du journal ne l'ont pas prise**.

### R-7 — Un arbitrage périmé se refait
**Entrée** : un désaccord arbitré, puis modification de la quantité du
passage n°1.
**Attendu** : la ligne redevient à trancher ; la zone ne peut plus être déclarée
terminée sans un nouvel arbitrage ; l'utilisateur voit pourquoi.

### R-8 — Le même document sur les deux passages
**Entrée** : une zone à deux comptages ; on modifie la feuille n°1 (ajout d'une
référence, renommage d'un intertitre, suppression d'une ligne, changement
d'ordre) alors que la feuille n°2 porte **déjà** des quantités.
**Attendu** : la feuille n°2 présente exactement la même structure et le même
ordre ; **ses quantités déjà relevées sont intactes**.
*(Ce dernier point relève de EX-ZON-4 et est marqué **[HV]** — à confirmer.)*

### R-9 — Un emplacement n'appartient qu'à un journal
**Entrée** : deux journaux de précomptage dont les lignes touchent le même
emplacement.
**Attendu** : déclarer le second sur cet emplacement est **refusé en nommant le
premier** ; si les deux ont été importés avant toute déclaration, déclarer
**recalcule** le comptage sur le seul propriétaire ; desceller le premier puis
déclarer le second fait basculer **référence et comptage ensemble**.

### R-10 — La lecture d'un scan n'invente rien
**Entrée** : une feuille scannée où le modèle « lit » une référence absente de
la liste pré-imprimée, une case vide, et un article présent deux fois dans deux
sous-sections avec une section illisible.
**Attendu** : la référence absente est **signalée, jamais acceptée** ; la case
vide reste vide (et comptera zéro) ; la ligne ambiguë est **signalée plutôt que
posée au hasard** ; une référence manuscrite **connue du référentiel** devient
une ligne, une référence inconnue est refusée.

### R-11 — Une pile de scans ne se perd pas en bloc
**Entrée** : une pile où une feuille est illisible et une autre a déjà été
corrigée à la main.
**Attendu** : la feuille corrigée est **préservée** ; la feuille illisible est
**nommée avec ses pages et la raison** ; toutes les autres aboutissent ; les
lignes manuscrites reviennent **à leur place dans l'ordre de la feuille**.

### R-12 — Le double gel
**Entrée** : un lecteur non déclaré, puis un gestionnaire, sur une campagne en
Analyse.
**Attendu** : le lecteur ne peut écrire nulle part et l'interface le lui dit ;
le gestionnaire peut écrire les ajustements et les causes, **et rien d'autre** ;
un propriétaire sur une campagne clôturée ne peut rien écrire non plus.

### R-13 — La trace ne se réécrit pas
**Attendu** : une tentative de modification ou de suppression d'un événement
d'audit **ne produit aucun effet**, y compris si elle vient d'un défaut de
l'application elle-même ; une campagne portant une histoire ne peut pas être
effacée physiquement.

## I.3 Cas limites

| # | Cas | Attendu |
|---|---|---|
| **R-14** | Article au **prix standard nul** | Le coût porté par la ligne de stock sert de secours (RG-VAL-3) — **règle dont la justification est inconnue : à confirmer avant reprise** |
| **R-15** | Article compté alors que l'ERP n'en connaît **aucun stock** | Toujours matériel, quels que soient les seuils |
| **R-16** | **Emplacement désactivé** | Sort de tout : indicateurs, quantités, valeurs, dénominateur d'avancement, et son journal disparaît |
| **R-17** | Ligne d'export ERP **sans emplacement** | Récupérée par déduction univoque **et signalée comme correction automatique à vérifier** |
| **R-18** | Journal **vrac** dont toutes les lignes portent la même étiquette générique | **Hors du contrôle par étiquette** ; aucun faux doublon produit |
| **R-19** | Zone à **comptage unique** | Aucune ligne d'arbitrage ; aucun avertissement « un seul passage » ; clôture possible |
| **R-20** | **Cycle de nomenclature** | Détecté à l'import ; l'éclatement refuse ; le constat est bloquant |
| **R-21** | **Source plus volumineuse que le plafond de lecture** | La lecture est **refusée en nommant la table et le plafond**, jamais tronquée en silence |
| **R-22** | Chargement en **mode remplacement** avec une ligne rejetée | **Rien n'est écrit** ; la dérogation existe, se voit dans le rapport, et n'est pas le défaut |
| **R-23** | Retour à **un seul comptage** sur une zone dont la feuille n°2 porte des quantités | **Refusé, en nommant les zones** |
| **R-24** | Quantité écrite comme `3*48+7` | Enregistre 151 **et conserve le texte** ; identique à la saisie, au scan d'une feuille **et au scan d'une pile** ; refusée avec mention du réglage si le réglage est inactif |
| **R-25** | **Décomposition** d'un chiffre agrégé | Seules les lignes de quantité non nulle sont présentées |
| **R-26** | **Intertitre et ligne vide** dans la grille de saisie | Aucune donnée affichée, **ni en lecture ni en modification** ; aucune quantité ne leur est attachée |

## I.4 Erreurs métier

| # | Situation | Attendu |
|---|---|---|
| **R-27** | Terminer une zone dont les comptages divergent | Refusé, en disant combien de lignes restent à trancher |
| **R-28** | Passer en Analyse avec une dérive matérielle non tranchée | Refusé, en nommant le nombre |
| **R-29** | Clôturer avec un écart matériel sans cause ni acceptation | Refusé, en nommant le nombre |
| **R-30** | Déclarer un périmètre **après le gel** du stock ERP | Refusé, avec l'explication — **et le geste ne doit pas être offert** |
| **R-31** | Supprimer une zone en phase Comptage | Refusé ; les alternatives sont nommées |
| **R-32** | Écrire dans la campagne B en étant habilité sur la campagne A | Refusé |
| **R-33** | Requête sans identité établie | Refusée ; **non** attribuée à une identité générique |

## I.5 Non-régression sur les règles confirmées

Toute réimplémentation doit démontrer, avant mise en service, chacune de ces
huit règles — ce sont les **[EC]** dont l'oubli ne se voit pas immédiatement :

1. Valorisation `prix standard × quantité` **partout** (RG-VAL-1) → R-1
2. Case vide = zéro **partout**, sauf pour l'avancement (RG-CPT-1/2) → R-5
3. Unicité `feuille + article + section + sous-section` **toujours** (RG-UNI-1) → R-10
4. Sous-section rendue en **séparateur**, jamais en colonne imprimée → R-8 + revue visuelle
5. Structure identique sur les deux passages, quantités préservées (EX-ZON-4) → R-8
6. Provenance **par ligne**, jamais par feuille (EX-TRC-3) → R-6
7. Arbitrage invalidé par un changement de chiffre (RG-ARB-3) → R-7
8. Formules traitées identiquement sur les trois voies de saisie (EX-ZON-7) → R-24

---

# Annexe 1 — Analyse critique de l'existant *(informative)*

> **Cette annexe ne prescrit rien.** Elle décrit ce qui existe, ce qui est
> éprouvé, ce qui est défectueux et ce qui est incomplet. Elle distingue
> systématiquement les défauts **démontrés** des **soupçons**.

## A1.1 L'architecture technique actuelle

| Couche | Choix |
|---|---|
| Hébergement | Une application conteneurisée sur une plateforme de données, un seul port |
| Interface | Application monopage React + TypeScript, servie en statique par le même processus |
| Serveur | FastAPI / Python 3.11, découpage strict `api → services → db/ingest/ai/analytics → domain` |
| Écritures | PostgreSQL managé, migrations forward-only idempotentes |
| Archive et analyse | Tables Delta / Unity Catalog, alimentées par un job idempotent |
| IA | Un point de service de modèle (vision pour les scans, texte pour le reste) |
| ML | scikit-learn / scipy, graines fixées |

**Le point d'architecture le plus solide** est la couche `domain` : elle
n'importe rien du reste du projet, ce qui permet de tester **l'intégralité des
règles métier** — éclatement, consolidation, écarts, contrôles, machine à
états, matrice d'impression — sans base de données ni service externe, en une
fraction de seconde. C'est très exactement la propriété que le classeur Excel
n'avait pas.

**La séparation en deux stockages** — transactionnel pour l'exploitation,
analytique pour l'archive — est justifiée par le profil réel : le jour J est
transactionnel (des centaines de changements de statut, dix éditeurs
concurrents), la comparaison inter-campagnes est analytique. La réconciliation
se fait par un job idempotent, jamais par double écriture synchrone.

## A1.2 Composants éprouvés, potentiellement réutilisables

Classés par valeur décroissante pour un repreneur, **indépendamment du langage
retenu** :

| Composant | Pourquoi il vaut d'être repris |
|---|---|
| **Le jeu de données de contrôle et son oracle** | Le seul actif qui valide une implémentation **sans supposer laquelle**. À reprendre en priorité |
| **La couche `domain`** | ~6 700 lignes de règles pures, sans dépendance, avec leurs justifications en commentaire. Même en changeant de langage, c'est la spécification exécutable la plus dense du dossier |
| **Le catalogue de contrôles** | 48 constats typés avec sévérité et geste de résolution — le fruit d'un travail de terrain qui ne se redécouvre pas |
| **Les contrats d'import** | Les alias de colonnes réellement rencontrés dans les exports ERP. Se reconstituent difficilement sans les fichiers d'origine |
| **La matrice de gel et les préconditions** | Petites, denses, motivées par des incidents réels |
| **Le moteur d'éclatement de nomenclature** | Multi-niveaux, traverse les structures fantômes, détecte les cycles, trace le résultat |
| **La chaîne de lecture de scans** | Rattachement par pied de page, découpe adaptative des lots en échec, reprise après redémarrage. Beaucoup d'apprentissage empirique y est figé |
| **Les documents `docs/`** | ~5 650 lignes hors ce cahier des charges ; c'est là que vivent les *pourquoi*, absents du code |

## A1.3 Défauts constatés

### A1.3.1 Défauts **démontrés** — avec leur preuve

| Défaut | Preuve | Conséquence |
|---|---|---|
| **La documentation dérive du code** | Trois divergences trouvées en une lecture : nombre de gestionnaires, formule de matérialité, définition de l'IRA (annexe 4) | Un repreneur qui suit la documentation implémente autre chose que ce qui tourne |
| **Le défaut récurrent est « existe mais n'est pas branché »** | Nommé comme tel dans l'historique et rencontré à répétition : un composant d'aperçu posé sur le mauvais écran ; un réglage absent d'une des deux voies de scan, si bien que **toute formule devenait une case vide sans erreur ni avertissement** | Une fonction est écrite, testée, documentée — et inatteignable ou inopérante par un chemin |
| **Des contrôles peuvent passer à côté du vrai chemin** | Le contrôle de câblage de l'aperçu vérifiait un écran et passait, tandis que le chemin réellement emprunté par l'utilisateur était cassé | Le banc de tests donne une assurance qu'il n'a pas |
| **Une fuite d'isolement entre modules de test** | Une base jetable survivait à sa propre suppression dans un cache de configuration, faisant échouer des modules ultérieurs pour une raison qui n'était pas la leur | Diagnostic coûteux, confiance érodée |
| **Le déploiement dépend d'un outil externe dont une version est cassée** | Un défaut d'une version précise de l'outil en ligne de commande fait échouer le déploiement sur un masque de mise à jour, sans rapport avec le projet ; les fichiers sont pourtant envoyés avant l'échec, donc l'espace de travail contient le nouveau code pendant que l'application sert l'ancien | Un échec de déploiement laisse un état trompeur |

### A1.3.2 Défauts **soupçonnés** — sans preuve, à ne pas traiter comme acquis

| Soupçon | Ce qui manque pour conclure |
|---|---|
| Le recalcul complet des écarts à chaque réimport pourrait ne pas tenir le jour J | **Aucune mesure.** C'est écrit comme un point ouvert : *« demande d'être mesuré avant d'être promis »* |
| La sélection du périmètre sur ~73 journaux pourrait coûter trop de gestes le jour J | Le geste est court mais se répète ; aucune mesure de terrain |
| Le volume de code de certains services (jusqu'à ~1 250 lignes) pourrait nuire à la maintenance | Des plafonds automatiques existent et sont respectés ; aucun incident de maintenance imputé |
| Le produit dépend d'un modèle de langage externe pour une étape du jour J | Aucun incident constaté ; aucun engagement de disponibilité connu |

**Ce qui n'est *pas* un défaut** : la séparation en deux stockages, la couche
domaine pure, la matrice de gel, la séparation valeur importée / valeur
corrigée. Chacune répond à un problème nommé et mesuré. *Une architecture n'est
pas mauvaise parce qu'une autre serait possible.*

## A1.4 Le catalogue de contrôles existant

48 codes, cités ici comme **inventaire du travail métier accumulé**, non comme
une spécification à recopier :

`ACCEPTED_WITHOUT_COMMENT`, `AI_SUGGESTIONS_UNTOUCHED`, `ARBITRATION_PENDING`,
`ASSEMBLY_BOM_RETIRED`, `ASSEMBLY_WITHOUT_BOM`, `BOM_CHILD_UNKNOWN`,
`BOM_CYCLE`, `BOM_DEPTH_TRUNCATED`, `BOM_PARENT_UNKNOWN`,
`BOOK_STOCK_DUPLICATE_KEY`, `BOOK_STOCK_EMPTY`, `BOOK_STOCK_FROZEN`,
`BOOK_STOCK_NEGATIVE`, `BOOK_STOCK_NOT_COUNTED`, `BOOK_STOCK_NOT_FROZEN`,
`BOOK_STOCK_OUT_OF_SCOPE`, `BOOK_STOCK_UNKNOWN_ITEM`,
`BOOK_STOCK_UNKNOWN_LOCATION`, `COUNTED_WITHOUT_BOOK_STOCK`,
`DUPLICATE_COUNT_LINE`, `DUPLICATE_JOURNAL`, `EARLY_COUNT_DRIFT_UNRESOLVED`,
`EXCLUDED_ITEM_COUNTED`, `FINISHED_IN_WIP_OK`, `FINISHED_ON_LINE_SIDE`,
`IMPORTS_WITH_REJECTS`, `ITEMS_WITHOUT_PRICE`, `JOURNALS_NOT_POSTED`,
`JOURNALS_POSTED`, `JOURNAL_ON_DISABLED_LOCATION`, `JOURNAL_UNKNOWN_ITEM`,
`MATERIAL_VARIANCE`, `MATERIAL_VARIANCES_UNEXPLAINED`, `NEGATIVE_COUNT`,
`NET_ZERO_CONSOLIDATION`, `POSTED_JOURNAL_EMPTY`, `PUBLICATION_NOT_DONE`,
`SHEETS_CHANGED_AFTER_CONSOLIDATION`, `SINGLE_PASS_ONLY`,
`UNCOUNTED_WITH_BOOK_STOCK`, `UNIT_MISMATCH`, `UNKNOWN_ITEM`,
`WIP_OK_NOT_ASSEMBLY`, `WIP_WITHOUT_BOM`, `ZONES_DONE`, `ZONES_NOT_DONE`,
`ZONE_MISSING_SHEET`, `ZONE_WITHOUT_LINES`.

## A1.5 Contournements historiques, et leur raison

| Contournement | Raison connue |
|---|---|
| **Les PDF scannés sont découpés page par page en PDF plutôt que rasterisés** | Pas d'accès privilégié sur la plateforme, donc pas d'outil système de rendu |
| **La résolution d'une page au format démesuré est réduite plutôt que la page refusée** | Une page de deux cents pouces produit ~900 mégapixels et déborde la garde anti-bombe de la bibliothèque d'images |
| **Les endpoints recevant un fichier renvoient leur travail synchrone à un pool de fils** | Un import volumineux ou une question à l'assistant immobilisaient l'application entière |
| **Chaque lecture externe demande une ligne de plus que son plafond** | C'est le seul moyen de distinguer « il y en avait exactement *n* » de « il y en avait davantage » |
| **Les pièces peuvent aller en base plutôt que dans le stockage de fichiers** | Le droit d'accès au volume n'est pas toujours obtenable |
| **Une valeur réservée rattache les entrepôts sans affectation** | Sans elle, un entrepôt découvert par un import tombe hors de tout périmètre |
| **Une clé unique redondante sur (identifiant, campagne)** | Pour que la garantie tienne même si une requête future oublie un filtre |

## A1.6 Approches essayées et leurs limites

| Approche | Ce qu'elle a donné |
|---|---|
| **Rattacher les comptages avancés au même aspect que les journaux de comptage** | Échec net : l'écran restait verrouillé jusqu'après le moment où il sert, avec un message de prérequis que le comptage avancé n'a jamais eu. Corrigé en séparant l'aspect |
| **Déduire le périmètre d'un journal de ses lignes** | Impossible : 1 932 lignes sur 58 345 ne sont là que pour matérialiser un déplacement |
| **Faire porter le nombre de comptages par la campagne** | Obligeait à compter deux fois toutes les zones ou aucune ; produisait des feuilles vierges. Déplacé sur la zone |
| **Donner quatre états à une feuille de comptage** | Quatre clics par zone pour une donnée que personne ne lisait. Supprimé |
| **Bloquer la clôture sur les lignes refusées à l'import** | Rendait la clôture impossible sur un manque que personne ne pouvait combler. Rétrogradé en constat visible |
| **Un lot de pages de scan en erreur = un lot perdu** | 72 pages sur 75 rendues à la saisie manuelle. Remplacé par une découpe récursive |
| **Une transaction pour toute une pile de scans** | Aurait perdu 29 feuilles abouties pour une trentième en échec. Exception délibérée |

## A1.7 Parties incomplètes ou non vérifiées

| Sujet | État |
|---|---|
| **L'écriture directe dans l'ERP** | Non réalisée ; le journal s'exporte au format d'import |
| **Le champ « stock ERP » des journaux du jour J** | **Non vérifié sur l'export réel** : il reste à confirmer qu'il est renseigné par étiquette sur les journaux par étiquette du jour J. Reporté comme point à vérifier |
| **Le comportement d'un emplacement dont le périmètre change entre le précomptage et le jour J** | **Sans réponse écrite** ; signalé comme produisant sinon des « dérives fantômes » |
| **La granularité du scellement** (journal vs emplacement) | Choix non tranché |
| **Le droit de desceller** | Suggéré de le réserver au propriétaire, **non décidé** |
| **La performance du recalcul d'écarts sous réimports fréquents** | **Jamais mesurée** |
| **Les instructions de mise à jour du catalogue analytique sur une installation existante** | Reportées comme non appliquées |
| **Accessibilité, internationalisation** | Non traitées |
| **La rétention des pièces et de l'audit** | Non définie |

---

# Annexe 2 — Reprise, refonte progressive ou reconstruction

> **Cette annexe ne tranche pas.** Elle rassemble ce qui est observable et
> nomme ce qui manque pour décider.

## A2.1 Ce que les faits disent

| Critère | Constat observable | Ce qu'il implique |
|---|---|---|
| **Couplage** | Dépendance à sens unique `api → services → db/ingest/ai → domain`, vérifiée automatiquement ; la couche métier n'importe rien | **Faible.** Le cœur métier est extractible sans le reste |
| **Couverture des parcours métier** | Tous les parcours de la section F sont implémentés ; un jeu de données de bout en bout est confronté à un oracle indépendant | **Élevée.** Il n'y a pas de trou fonctionnel connu |
| **Qualité des tests** | 2 808 contrôles Python (273 avec base), 482 navigateur, un parcours complet en navigateur réel ; plafonds architecturaux automatisés | **Élevée en volume.** Avec la réserve démontrée en A1.3.1 : un contrôle peut viser à côté du chemin réel |
| **Dépendances** | Fortes vis-à-vis de la plateforme d'hébergement (port unique, 120 s, 10 Mo, pas de root, disque éphémère), du catalogue analytique et d'un point de service de modèle | **Élevées, mais externes.** Elles pèseront **identiquement** sur toute reconstruction hébergée au même endroit |
| **Migration** | Aucune installation en production identifiée ; les seules données non recalculables sont l'audit et les pièces | **Faible risque** — *sous réserve de Q-14* |
| **Capacité de validation** | Un oracle indépendant de l'implémentation existe déjà | **Déterminante** : elle rend une reconstruction vérifiable au même titre que l'existant |

## A2.2 Les trois trajectoires

| | **Amélioration de l'existant** | **Refonte progressive** | **Reconstruction complète** |
|---|---|---|---|
| **Ce que c'est** | On garde tout, on corrige et on ajoute | On garde la couche métier et le jeu de contrôle ; on refait l'interface, l'accès aux données, ou les deux | On repart de ce cahier des charges |
| **Ce qui plaide pour** | La couverture est là ; les *pourquoi* sont écrits ; le coût est le plus bas ; le risque de perdre une exception métier est **quasi nul** | Le cœur métier est déjà découplé et testable seul, donc réellement extractible ; permet de traiter les défauts démontrés (dérive documentaire, câblage) sans tout rejouer | Liberté totale d'architecture, de technologies et d'ergonomie ; c'est ce que la mission demande d'évaluer |
| **Ce qui plaide contre** | Ne répond pas à la question posée ; laisse en place ce qui a produit le défaut « existe mais n'est pas branché » | Demande de décider **où** passe la frontière, ce qui est un travail de conception en soi | **Le risque principal** : les exceptions métier de §E.11 et les huit règles de §I.5 sont exactement ce qu'une réécriture perd sans s'en apercevoir, parce qu'aucune ne se voit sur un écran |
| **Ce qui le rend vérifiable** | Le banc existant | Le banc existant, appliqué à la partie conservée | **Le jeu de contrôle et son oracle** — sans eux, une reconstruction n'est pas validable en fond |

## A2.3 Ce qui manque pour décider

Ces informations **changeraient la comparaison**, et aucune n'est disponible
aujourd'hui :

1. **Le cahier des charges d'origine.** S'il est retrouvé, une grande partie des
   règles **[CO]** deviendraient **[EC]** et le risque d'une reconstruction
   baisserait nettement (Q-16).
2. **L'existence de données en production** (Q-14).
3. **Le motif réel de la reconstruction** : coût de maintenance ? technologies ?
   performance ? autonomie vis-à-vis de la plateforme ? Chacun désigne une
   trajectoire différente (Q-17).
4. **La mesure de performance** sous réimports fréquents. Si elle est
   défaillante, elle oriente vers une refonte ciblée du calcul plutôt que vers
   une reconstruction totale.
5. **La contrainte d'hébergement future** (Q-13). Si la plateforme reste la même,
   sept contraintes fortes s'appliqueront à l'identique et une part du travail
   sera nécessairement refaite à l'identique.

## A2.4 Si une reconstruction est retenue

**Capacités à préserver sans exception** : les 17 capacités de §C.1, les
exceptions métier de §E.11, et les huit règles de §I.5.

**Conditions de migration** :
- établir d'abord s'il existe des données en production (Q-14) ;
- si oui, ce qui doit survivre est l'**information métier** — dont l'audit et
  les pièces d'origine, seules données non recalculables — pas les tables ;
- décider si l'archive analytique existante continue d'être alimentée dans le
  même schéma, ou si un nouveau schéma est publié (Q-15).

**Critères de bascule à valider avant mise en service** :

1. Les 33 scénarios de recette §I passent sur la nouvelle implémentation ;
2. le jeu de contrôle rend **les mêmes chiffres à l'euro et à l'unité** ;
3. **une campagne réelle est rejouée en parallèle sur les deux systèmes et les
   écarts sont expliqués un à un** — c'est le seul test que l'oracle ne remplace
   pas, parce qu'il porte sur des données que personne n'a écrites pour l'essai ;
4. les huit règles de §I.5 sont démontrées individuellement ;
5. le journal d'audit de l'ancien système reste consultable.

> **Aucune fonctionnalité ni donnée ne doit être supprimée sur la seule base de
> ce document.** Toute suppression envisagée doit être soumise au commanditaire,
> en particulier pour les règles marquées **[CO]**, dont l'absence de décision
> retrouvée ne vaut **pas** absence de besoin.

---

# Annexe 3 — Questions ouvertes et liberté laissée au repreneur

## 6.1 Décisions métier à obtenir du commanditaire

### Impact fort — bloquent une conception correcte

| # | Question | Pourquoi elle bloque |
|---|---|---|
| **Q-1** | **Quelle est la définition exacte de la matérialité ?** Deux barrières (valeur absolue + écart relatif) comme dans le code, ou trois avec un plancher de quantité comme dans la documentation ? | Détermine la liste d'exceptions que l'équipe traite le jour J. Voir annexe 4, CT-2 |
| **Q-2** | **Qui a le droit de desceller un emplacement ?** | Le descellement annule une preuve datée. Suggéré au propriétaire seul, jamais décidé |
| **Q-3** | **Quelle est la granularité du scellement** : le journal, ou l'emplacement (donc toute écriture le concernant, journal *et* feuille) ? | Plus sûr et plus coûteux. Le choix dépend de la fréquence des saisies libres sur les zones précomptées |
| **Q-4** | **Quelle est la durée de la fenêtre entre précomptage et jour J ?** | C'est le paramètre qui gouverne le risque résiduel de pièces déplacées sans transaction. **Opérationnel, pas technique** |
| **Q-5** | **Que devient un emplacement désactivé après avoir été précompté, ou activé après coup ?** | Sans réponse écrite, les deux cas produiront des dérives fantômes |
| **Q-6** | **L'unité de progression reste-t-elle l'emplacement**, alors qu'un journal ERP peut en couvrir cinquante ? | Décide de tout l'affichage d'avancement du jour J |

### Impact moyen — orientent la conception

| # | Question |
|---|---|
| **Q-7** | Faut-il un mode « accepter toutes les propositions évidentes » pour la déclaration des périmètres (un seul candidat, aucun conflit) ? Le geste est court mais se répète sur ~73 journaux |
| **Q-8** | La tolérance d'arbitrage doit-elle rester à zéro (toute différence exige une décision) ou devenir configurable ? |
| **Q-9** | **Confirmation demandée** : lorsqu'une modification de structure se propage à la feuille n°2, ses quantités déjà relevées doivent-elles être préservées ? *(Interprétation retenue : oui.)* |
| **Q-10** | **Confirmation demandée** : un arbitrage en lot doit-il laisser intactes les lignes déjà tranchées à la main ? *(Interprétation retenue : oui.)* |
| **Q-11** | Que doit-il se passer si le service de modèle est indisponible le jour J ? *(Interprétation retenue : dégradation en saisie manuelle, jamais blocage.)* |
| **Q-12** | Le français exclusif est-il une exigence, ou une conséquence de l'usage actuel ? Y a-t-il un besoin d'accessibilité ? |
| **Q-18** | La règle « prix standard nul → le coût de la ligne de stock sert de secours » est-elle voulue ? **Aucune justification n'a été retrouvée** |
| **Q-19** | **« L'IA propose, l'humain décide » est-elle une exigence du commanditaire ?** Elle est posée comme absolue dans deux documents du dossier et appliquée sans exception, mais **aucune décision tracée ne la fonde**. Elle structure tout le périmètre de l'assistance IA — c'est la plus importante des règles restées **[CO]** |

### Impact fort sur la décision de reconstruction

| # | Question |
|---|---|
| **Q-13** | La plateforme d'hébergement est-elle imposée ? Sept contraintes fortes en dépendent |
| **Q-14** | Existe-t-il des données en production à reprendre ? Des campagnes clôturées à conserver ? |
| **Q-15** | L'archive analytique existante doit-elle continuer d'être alimentée dans le même schéma ? |
| **Q-16** | **Le cahier des charges d'origine peut-il être retrouvé ?** C'est l'information qui changerait le plus la fiabilité de ce document |
| **Q-17** | **Quel est le motif réel de la reconstruction** — coût, technologies, performance, autonomie ? Chacun désigne une trajectoire différente |

## 6.2 Inconnues pouvant bloquer la mise en service

| Inconnue | Conséquence si elle reste ouverte |
|---|---|
| **Aucun objectif de performance ni de volumétrie contractuel** | Impossible de dire si une implémentation est acceptable le jour J |
| **Le recalcul complet des écarts à chaque réimport n'a jamais été mesuré** | Le risque le plus concret sur le déroulement du jour J |
| **Aucun engagement de disponibilité** | Aucun plan de repli formalisé |
| **Le champ « stock ERP » des journaux du jour J n'a pas été vérifié sur l'export réel** | Une hypothèse de lecture non confirmée sur la source |
| **Aucune politique de rétention** | Ni pour les pièces, ni pour l'audit |
| **Le mode de mise à jour d'une installation existante n'a pas été appliqué** | Des instructions de catalogue restent en attente |

## 6.3 Ce que le repreneur décide seul

**Sans consulter le commanditaire** — ces choix n'engagent aucune règle métier :

| Domaine | Liberté |
|---|---|
| **Langage, framework, bibliothèques** | Totale |
| **Architecture applicative** | Totale : monolithe, services, fonctions, autre |
| **Modèle physique de données** | Totale : moteur, schéma, nombre de tables, clés, index. Les *informations* de §G.1 doivent être portées ; leur forme est libre |
| **Mécanismes internes** | Transactions, verrouillage, cache, files, tâches de fond, pagination |
| **Écrans, navigation, interactions** | Totale, dans le respect des objectifs d'usage §H.1. **Le nombre d'étapes peut être réduit et les écrans réorganisés** |
| **Réutilisation ou remplacement** | Chaque composant de A1.2 peut être repris, réécrit ou remplacé |
| **Format et outillage des exports** | Libres, tant que le journal reste importable par l'ERP |
| **Stratégie de tests** | Libre, sous réserve que les scénarios §I soient démontrables |
| **Rendu des documents imprimés** | Libre, dans le respect des exigences de lisibilité terrain (EX-ZON-5) |
| **Technologie du modèle de langage** | Libre, sous réserve du cadrage §D.10 |

**Ce que le repreneur ne décide pas seul** : toute règle marquée **[EC]**, toute
exception de §E.11, les huit règles de §I.5, et toute suppression de capacité ou
de donnée.

## 6.4 Améliorations facultatives — hors périmètre confirmé **[PF]**

Vingt améliorations ont été analysées et priorisées dans
[`06-top20-ameliorations.md`](06-top20-ameliorations.md). **Aucune n'est
demandée ; aucune ne doit devenir implicitement obligatoire.** Les cinq
premières : écriture directe des journaux dans l'ERP ; lecture automatique du
stock et des mouvements ; comptage mobile hors ligne avec scan de code-barres ;
inventaire tournant piloté par le risque ; réconciliation par identité
comptable entre deux campagnes.

**Trois orientations sont explicitement déconseillées dans ce même document, et
la mise en garde est reproduite ici parce qu'un repreneur libre les
envisagera** :

1. **Un agent IA autonome qui poste les journaux** — *un inventaire physique est
   un acte comptable opposable : la valeur de l'outil vient de ce qu'une décision
   est attribuable à une personne.*
2. **Unifier les écritures sur le stockage analytique** — il n'est pas fait pour
   des centaines de changements de statut par heure et dix éditeurs concurrents.
3. **Rendre les seuils configurables par utilisateur** — *deux personnes
   obtiennent deux listes d'exceptions différentes sur la même campagne, et la
   discussion porte sur les seuils au lieu des écarts.*

---

# Annexe 4 — Contradictions relevées, non tranchées

> Conformément à la consigne, ces contradictions sont **exposées et non
> arbitrées**. Chacune oppose des sources qui devraient s'accorder.

### CT-1 — Le nombre de postes de gestionnaires

| Source | Ce qu'elle dit |
|---|---|
| `services/manager_service.py` | **9** postes |
| `04-guide-utilisateur.md §1.7` | « **Neuf** postes par campagne » |
| `05-modele-de-donnees.md §3` | « `manager` (**5 postes**, avec l'identité de chacun) » |

**Lecture** : la documentation de modèle est vraisemblablement restée sur une
valeur antérieure. **Impact faible** — c'est un plafond d'interface, pas une
règle métier. **Aucune décision retrouvée sur le nombre lui-même.**

### CT-2 — La définition de la matérialité *(impact fort)*

| Source | Formule |
|---|---|
| `domain/variance.py` + `domain/models.py` | écart en valeur absolue ≥ *valeur_absolue* **ET** (écart de quantité en valeur absolue ÷ quantité ERP) ≥ *ratio* — **deux barrières** (défauts : 1 000 € et 2 %) |
| `05-modele-de-donnees.md §Matérialité` | les deux mêmes barrières **ET** une troisième : écart de quantité en valeur absolue ≥ *plancher* — **trois barrières** |
| `04-guide-utilisateur.md §1.5` | Deux barrières |
| `09-jeu-de-donnees §5` | Deux barrières |

**Lecture** : trois sources sur quatre disent deux barrières ; le document de
modèle en décrit trois, avec un « plancher de quantité » qui **n'existe nulle
part dans le code**. **Cette contradiction doit être tranchée avant toute
implémentation** — elle change directement la liste d'exceptions traitée le
jour J. Voir Q-1.

*Les deux sources s'accordent en revanche sur l'exception : une quantité ERP
nulle rend la ligne toujours matérielle.*

### CT-3 — Les lignes refusées à l'import bloquent-elles la clôture ?

| Source | Ce qu'elle dit |
|---|---|
| `02-architecture.md §La clôture exige ce qu'elle promet` | `IMPORTS_WITH_REJECTS` est listé comme **bloqueur** de clôture |
| `domain/workflow.py` | Le blocage a été **retiré**, avec une justification écrite : *« Ce qui change est le pouvoir d'arrêt, pas la visibilité »* |
| `domain/closure.py` | Le constat reste présent dans la liste de contrôle, en « à regarder » |

**Lecture** : le code a évolué et la documentation d'architecture ne l'a pas
suivi. **Le comportement effectif est celui du code** — le constat est affiché
mais ne bloque plus. La décision qui a produit ce changement est documentée en
commentaire mais **n'est pas tracée à une demande du commanditaire**.

### CT-4 — La définition de l'IRA

| Source | Ce qu'elle dit |
|---|---|
| `05-modele-de-donnees.md` et `04-guide-utilisateur.md` | « part des couples article/emplacement **dans la tolérance** » |
| `domain/variance.py` | **Aucune tolérance** : *« a record that is off by one is a record that was wrong »*. Une quantité ERP nulle est exacte seulement si rien n'a été compté |
| `09-jeu-de-donnees §5` | « **Il n'y a pas de tolérance** — une ligne fausse d'une unité est une ligne fausse » |

**Lecture** : deux documents laissent croire à une tolérance paramétrable qui
n'existe pas. **Le comportement effectif est l'égalité stricte.** L'IRA est un
indicateur publié : cette imprécision peut faire diverger une lecture de
l'indicateur d'une lecture de sa définition.

### CT-5 — Une case vide dans la documentation interne du moteur

| Source | Ce qu'elle dit |
|---|---|
| Demande du commanditaire, et comportement effectif | Une case vide **compte zéro** |
| Docstring de `domain/consolidation.py`, tableau comparatif | « Blank means *not counted* and is reported » |

**Lecture** : commentaire résiduel d'avant le changement de règle. **Aucun
impact fonctionnel** ; signalé parce qu'un repreneur lisant ce module en tirerait
la règle inverse de celle qui s'applique.

---

## Journal du document

| Version | Date | Objet |
|---|---|---|
| 1.0 | 2026-09-05 | Rédaction initiale, sur la branche `claude/campagnes-inventaire-v2-0ewyzf`, commit `553f486` |

**Ce document ne modifie aucun code applicatif.** Il formalise le besoin tel
qu'il a pu être établi, distingue ce qui est décidé de ce qui est constaté, et
laisse visibles les contradictions et les incertitudes.
