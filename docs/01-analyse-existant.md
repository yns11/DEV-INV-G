# Analyse de l'existant — le dispositif Excel

> Analyse conduite sur les fichiers réels de la campagne du 13 juin 2026 :
> `Compil GENERIQUE.xlsx`, `BILAN INVENTAIRE.xlsx`, `STOCK AVANT INVENTAIRE.xlsx`,
> les six feuilles de comptage scannées, les deux *Inventory Executive Summary*
> et la documentation des requêtes Power Query.

---

## 1. Le processus tel qu'il fonctionne aujourd'hui

### 1.1 Cartographie des flux

```
                      ┌─────────────────────────────────────────┐
                      │              ERP (D365)                 │
                      └───┬──────────────┬──────────────┬───────┘
   export stock ERP     │              │              │  export mouvements
   ┌──────────────────────┘              │              └──────────────────┐
   ▼                                     │ journaux INVE (scan étiquettes) ▼
STOCK AVANT                              │ journaux INVV (saisie vrac)   BILAN
INVENTAIRE.xlsx                          │                            INVENTAIRE.xlsx
   │                                     │                             (13 onglets,
   └──── copier/coller ─────────────►    │    ◄──── copier/coller ────► ~100 000 formules)
                                         │
                          ┌──────────────┴───────────────┐
                          │  Emplacement B06VRAC/GENERIQUE│
                          └──────────────┬───────────────┘
                                         │
   40 feuilles papier (2 par zone) ──► Compil GENERIQUE.xlsx ──► onglet JOURNAL
   comptage n°1 et n°2                (40 onglets de zones,      │
   par deux équipes                    9 requêtes Power Query)   │
                                                                 └── copier/coller ──► ERP
```

### 1.2 Les trois classeurs et leur rôle

| Classeur | Rôle | Volume constaté |
|---|---|---|
| `STOCK AVANT INVENTAIRE.xlsx` | Snapshot du stock ERP ERP | 1 436 lignes, 20 colonnes |
| `Compil GENERIQUE.xlsx` | Compilation des 40 zones de l'emplacement GENERIQUE | 54 onglets, 9 requêtes Power Query, 2 113 liens BOM |
| `BILAN INVENTAIRE.xlsx` | Analyse des écarts et reporting | 13 onglets, 17,6 Mo, ~104 000 lignes d'ajustements |

### 1.3 La chaîne Power Query de `Compil GENERIQUE`

Documentée dans `REQUETES power query du compil generique.docx` :

| Requête | Rôle |
|---|---|
| `DATA` | Concatène les 40 onglets de zones, **filtre les lignes où `comptage` est vide ou nul** |
| `BDL` | Somme les composants en bord de ligne |
| `MOMOK` | Somme les ensembles déclarés, **jointure interne** avec la liste des semi-finis |
| `ECLATEE` | Éclate les ensembles « MOM waiting », **jointure interne** avec `BOMFINALE`, un seul niveau |
| `JOURNAL` | Concatène les trois, groupe par référence, **anti-jointure** avec `A EXCLURE` |

---

## 2. Les points faibles, et ce qu'ils coûtent

### 2.1 Défauts structurels de la chaîne de calcul

#### a) La jointure interne fait disparaître les quantités

`ECLATEE` et `MOMOK` utilisent `JoinKind.Inner`. Un assemblage compté sur le
terrain mais absent de `BOMFINALE` ou de `LISTE SF` **disparaît purement et
simplement** du journal envoyé à l'ERP. Pas d'alerte, pas de ligne, pas de trace.

> **Vérifié sur les données réelles.** En rejouant la campagne de juin 2026 dans
> la nouvelle solution, **4 assemblages comptés en WIP n'ont aucune nomenclature**
> (`MASS-00049745`, `MASS-00050167`, `P-00082963`, et un quatrième), pour un total
> de 8 lignes de comptage. Sous Excel, ces quantités ont été perdues sans que
> personne ne le sache. Dans la nouvelle solution, elles bloquent la
> consolidation avec un message explicite et une résolution en un clic.

#### b) L'éclatement est mono-niveau et sans détection de cycle

Une structure fantôme (un niveau de nomenclature qui ne porte pas de stock ERP)
crédite du stock à un article qui n'a pas de compte de stock : l'écart produit
est permanent et inexplicable. Un cycle dans la nomenclature ferait recalculer
le classeur indéfiniment.

#### c) Une ligne sans quantité est retirée du calcul

`Table.SelectRows(each [comptage] <> null and [comptage] <> 0)` : la ligne
disparaît de la consolidation. Or elle figure sur la feuille précisément parce
qu'on s'attend à trouver la référence dans la zone — n'y avoir rien trouvé est
un écart, et l'écarter laisse l'article avec son stock ERP en face de rien : ni
compté, ni manquant. C'est la source de l'observation « Scan missed » du bilan
de juin.

Ce que le filtre confond réellement, c'est autre chose : « personne n'est allé
voir » et « on est allé voir, il n'y avait rien ». Cette distinction-là ne pèse
sur aucune quantité ; elle dit seulement si une zone reste à compter.

#### d) La section est lue dans une cellule de texte libre

La colonne `source` contient `BDL`, `MOM_OK`, `MOM_WAITING`. Une faute de frappe
ou une variante d'écriture change silencieusement la règle de calcul appliquée à
la ligne : comptée telle quelle au lieu d'être éclatée, ou l'inverse.

### 2.2 Défauts de `BILAN INVENTAIRE.xlsx`

#### a) Des formules cassées en production

L'onglet `TOP ECARTS` — celui qui pilote l'analyse — contient des
**`#REF!` dans les formules elles-mêmes** :

```excel
=SUMIFS(#REF!,#REF!,"GENERIQUE",#REF!,A2)        → colonne PURGE 0906
=_xlfn.XLOOKUP(A2,#REF!,#REF!,,0,1)*M2           → colonne CRASH 1006
=VLOOKUP(A2,#REF!,9,FALSE)                       → colonne Unit price
```

L'onglet `RAPPORT` affiche `#REF!` dans la colonne **« Inventory level (before) — € »**,
c'est-à-dire la valeur du stock ERP : le chiffre d'entrée de tout le rapport.
Les colonnes concernées sont donc vides ou fausses, et les totaux qui en
dépendent aussi.

#### b) Un modèle réutilisé d'une campagne à l'autre

Les en-têtes portent des dates codées en dur d'une campagne précédente :

```
Ecart Stock Qté (le 15/03)          Ecart Valorisation € (du 17/03 au 21/03)
```

alors que la campagne analysée est celle du **13 juin**. Les colonnes
`Younes` et `Cathy` codent l'affectation des analyses dans le nom même de la
colonne : le classeur ne survit pas à un changement d'équipe.

#### c) Les identifiants métier sont concaténés

```excel
=VLOOKUP(E2&F2,'0.Zones'!D:E,2,0)   -- entrepôt & emplacement collés
```

Deux emplacements portant le même nom dans deux entrepôts différents ne sont
distingués que par la concaténation. Un espace de trop, une casse différente, et
la correspondance échoue silencieusement — `#N/A` traité comme une zone vide.

#### d) Les référentiels ne sont pas normalisés

Dans `STOCK AVANT INVENTAIRE`, le même emplacement apparaît en `STK B2SUD` et
`stk b2sud1` ; les références articles mélangent `mass-00040922` et
`MASS-00040922`. Chaque variante crée un article ou un emplacement fantôme.

> **Vérifié.** Sur les 478 articles reconstitués depuis les fichiers réels,
> la normalisation en majuscules en a fusionné **5 doublons de casse** — cinq
> articles qui existaient en double dans les analyses.

#### e) Volume ingérable

`Ajustements & Comptages` : 103 963 lignes, chacune portant 8 formules
`VLOOKUP` / `XLOOKUP` — soit plus de 800 000 recherches recalculées à chaque
frappe. Le fichier pèse 17,6 Mo.

### 2.3 Défauts de gouvernance

| Constat | Conséquence |
|---|---|
| Aucune trace de qui a modifié quoi | Un chiffre ne peut pas être défendu six mois plus tard |
| Aucun gel des données | Le stock ERP peut être modifié après le calcul des écarts |
| Aucun contrôle d'accès | N'importe qui peut écraser une formule |
| Aucune reproductibilité | Le même classeur rouvert donne un résultat différent si un référentiel externe a bougé |
| Le fichier *est* l'application | Sauvegarder, versionner et corriger sont la même opération |

### 2.4 Défauts du processus terrain

Relevés dans les *Improvement points* des deux bilans exécutifs :

- « Still some counting fails / Actual WIP status not recognized by counting teams »
- « Scan missed »
- « Finished goods return flows still fragile »
- « Chemical products and bolts difficult to estimate »
- « ATP counting feedbacks interpretation »

Le double comptage existe (comptage n°1 et n°2 par deux équipes) mais **l'arbitrage
n'est pas outillé** : la comparaison se fait de tête, et seules les références
présentes sur les deux feuilles sont comparées. Une référence comptée par une
seule équipe passe inaperçue.

### 2.5 Défaut de qualité de l'export ERP lui-même

L'export OData joint au cahier des charges contient **une ligne dont
l'emplacement est nul** (ligne 635, article `P-00324093`, 15 unités, journal
`NPEM-523609`). Sous Excel, une ligne sans emplacement ne se rattache à aucun
journal : la quantité est perdue ou rattachée au mauvais endroit selon la
manipulation.

La nouvelle solution la récupère — tous les autres lignes du journal
`NPEM-523609` portent sur `B06VRAC / GENERIQUE`, donc la déduction est
univoque — **et l'affiche comme une correction automatique à vérifier**.

---

## 3. Ce que la nouvelle solution change, point par point

| Faiblesse constatée | Traitement dans la solution |
|---|---|
| Jointure interne qui perd des quantités | `WIP_WITHOUT_BOM` bloque la consolidation, avec une résolution en un clic |
| Éclatement mono-niveau, structures fantômes | `BomIndex` s'arrête au premier article porteur de stock, traverse les fantômes |
| Cycle de nomenclature | `find_cycles()` détecté à l'import, `BomCycleError` à l'éclatement |
| Filtre `<> null and <> 0` qui écarte les lignes | Une case vide compte **zéro** — l'article reste dans le stock compté et son écart apparaît ; `has_entry` ne sert plus qu'à l'avancement d'une zone |
| Section en texte libre | `CountSection` typée, anciens libellés traduits à l'import uniquement |
| `#REF!` en production | Aucune formule : tout est recalculé depuis les snapshots gelés |
| Modèle réutilisé, dates codées en dur | Duplication de campagne qui copie les référentiels et **vide** les mesures |
| Identifiants concaténés | `LocationKey(warehouse_id, location_id)` — clé composite, jamais une chaîne |
| Référentiels non normalisés | `normalise_key()` appliqué à toutes les frontières d'entrée |
| Volume ingérable | Postgres indexé + agrégations SQL ; l'export Excel est une sortie, pas le moteur |
| Aucune traçabilité | Table d'audit append-only, `UPDATE`/`DELETE` neutralisés par des règles SQL |
| Aucun gel | Matrice de gel par phase, appliquée côté serveur avant chaque écriture |
| Arbitrage non outillé | Écran d'arbitrage valorisé en euros, couvrant les articles présents dans un seul comptage |
| Ligne d'export corrompue | Récupération univoque + avertissement explicite dans le rapport d'import |

---

## 4. Ce qu'il faut conserver de l'existant

L'analyse ne conclut pas que le dispositif actuel est mauvais dans son
intention. Plusieurs choix métier sont justes et ont été **repris tels quels** :

- **Le double comptage par deux équipes indépendantes** sur les zones GENERIQUE :
  c'est la bonne pratique WMS, et elle est ici outillée plutôt que supprimée.
- **La distinction bord de ligne / WIP non déclaré / WIP assemblé** : c'est une
  vraie règle métier, qui reflète l'état de la déclaration dans l'ERP.
- **Le référentiel de 14 causes standard** (`Standard assignable causes`) :
  repris intégralement comme référentiel de site.
- **La séparation snapshot / comptages / ajustements** : le raisonnement
  « stock ERP + écart + ajustements = stock après » est correct.
- **Le forçage au stock ERP** pour les emplacements inventoriés avant le
  snapshot : pratique légitime, désormais explicite et tracée plutôt qu'implicite.
- **L'analyse de contribution** (`FORT CONTRIB STK`, `CONTRIB ECARTS`) :
  l'intuition Pareto était juste ; elle est maintenant calculée et non saisie.

Ce qui change n'est pas la logique métier — c'est sa **fiabilité d'exécution**.
