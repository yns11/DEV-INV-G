# Jeu de données de contrôle — le calcul posé

Ce document pose l'arithmétique du jeu de données de
[`fixtures/jeu-de-donnees/`](../fixtures/jeu-de-donnees/). Chaque quantité y est
rattachée au fichier et à la ligne d'où elle vient, pour qu'un désaccord entre
l'application et le calcul théorique se tranche avec une calculette plutôt
qu'avec un débogueur.

Trois pièces, et c'est le triangle qui vaut :

| Pièce | Ce qu'elle fait |
|---|---|
| `fixtures/jeu-de-donnees/*.csv` | La campagne, dans le format des imports réels |
| `fixtures/jeu-de-donnees/oracle.py` | Le résultat attendu, calculé **sans l'application** |
| `tests/test_jeu_de_donnees.py` | Charge les CSV dans l'application et confronte les deux |

---

## 1. Ce que la campagne contient

**Articles** (`01-articles.csv`) — le prix standard est la seule base de
valorisation de toute la campagne.

| Article | Type | Prix std | Exclusion |
|---|---|---:|---|
| P-100 Aimant | Composant | 10,00 € | |
| P-200 Tôle | Composant | 20,00 € | |
| P-300 Fil de cuivre | Composant | 5,00 € | |
| P-400 Roulement | Composant | 50,00 € | |
| P-500 Vis | Composant | **0,00 €** | ← prix nul : le coût de la ligne sert de secours |
| P-600 Carton | Emballage | 2,00 € | |
| P-700 Colle | Composant | 100,00 € | **ALL** — hors périmètre |
| P-800 Huile | Composant | 8,00 € | **GENERIC** — hors consolidation |
| SF-10 Stator nu | Semi-fini | 300,00 € | |
| PF-01 Moteur | Produit fini | 1 000,00 € | |

**Nomenclature** (`02-nomenclatures.csv`) : `SF-10 = 4 × P-100 + 2 × P-300`,
`PF-01 = 1 × SF-10 + 2 × P-400`.

**Seuils de matérialité** : 100 € **et** 2 %, sur tous les types. Une ligne est
une exception quand les deux portes cèdent.

---

## 2. Le stock ERP : deux origines, une ligne par emplacement

Les emplacements `ATP/SOL`, `ATP/SE2` et `B06/PAL01` sont **scellés** par leur
précomptage. Le snapshot du jour J porte pourtant des lignes sur eux, à 999 —
c'est délibéré : **elles doivent être ignorées.**

| Emplacement | Article | Stock ERP retenu | Origine |
|---|---|---:|---|
| ATP / SOL | P-100 | 100 | `NPEM-A` ligne 1, au 10/06 |
| ATP / SOL | P-200 | 50 | `NPEM-A` ligne 2, au 10/06 |
| ATP / SE2 | P-300 | 200 | `NPEM-A` ligne 3, au 10/06 |
| B06 / PAL01 | P-400 | 40 | `NPEM-B` ligne 1, au 11/06 |
| B06 / PAL01 | P-600 | 500 | `NPEM-B` ligne 2, au 11/06 |
| B06 / PAL02 | P-400 | 60 | snapshot du 13/06 |
| ATP / QUAI | P-200 | 30 | snapshot du 13/06 |
| B06 / FORCE | P-500 | 25 | snapshot du 13/06 |
| B06 / VIDE | P-600 | 12 | snapshot du 13/06 |
| B06VRAC / GENERIQUE | P-100 | 80 | snapshot du 13/06 |
| B06VRAC / GENERIQUE | P-300 | 150 | snapshot du 13/06 |
| B06VRAC / GENERIQUE | P-800 | 20 | snapshot du 13/06 |

**Écartés** : `INV/01` (emplacement désactivé) et `P-700` sur `B06/PAL02`
(article exclu `ALL`) — ni en quantités, ni en valeurs.

---

## 3. Le comptage

### Précomptages

| Emplacement | Article | Compté | Remarque |
|---|---|---:|---|
| ATP / SOL | P-100 | 100 | exact |
| ATP / SOL | P-200 | 45 | −5 |
| ATP / SE2 | P-300 | 210 | +10 |
| B06 / PAL01 | P-400 | 38 | −2 |
| B06 / PAL01 | P-600 | 500 | exact |

`NPEM-A` ligne 4 pose 3 `P-400` sur `B06/PAL02`, qu'il **ne couvre pas** : c'est
une ligne de passage. Elle reste dans le journal ERP comme trace et **ne compte
pas**.

### Jour J

| Emplacement | Article | Compté | Remarque |
|---|---|---:|---|
| B06 / PAL02 | P-400 | 58 | |
| ATP / QUAI | P-200 | 32 | porte l'étiquette `ET-002`, scellée sur `ATP/SOL` → alerte |
| ATP / NOUVEAU | P-100 | 6 | absent du stock ERP → `compté seul` |
| B06 / PAL02 | P-700 | 5 | article exclu → ignoré |
| B06 / FORCE | P-500 | **25** | forcé au stock ERP : écart nul par construction |

### GENERIQUE

Deux passages sur la zone `ZONE-1` :

| Article | Section | Passage 1 | Passage 2 | Retenu |
|---|---|---:|---:|---:|
| P-100 | Bord de ligne | 30 | 30 | **30** (accord) |
| P-300 | Bord de ligne | 100 | 110 | **105** (arbitrage décidé) |
| P-800 | Bord de ligne | 20 | 20 | 20 |
| PF-01 | Bord de ligne | 1 | 1 | 1 |
| SF-10 | WIP (à éclater) | 5 | 5 | **5** |

Puis la consolidation, dans l'ordre :

1. **PF-01 est écarté** — un produit fini n'entre que par la porte du WIP ; en
   bord de ligne il compterait une deuxième fois ce que ses composants comptent.
2. **SF-10 est éclaté** : 5 × (4 P-100 + 2 P-300) = **20 P-100 + 10 P-300**.
3. **P-800 est retiré** — exclu `GENERIC`, et retiré *après* l'éclatement.

```
P-100 = 30 (bord de ligne) + 20 (éclatement)  = 50
P-300 = 105 (arbitrage)    + 10 (éclatement)  = 115
```

> `P-800` sort de la consolidation mais **garde son stock ERP** : l'exclusion
> `GENERIC` ne retire l'article que du comptage GENERIQUE. Son écart est donc
> de −20, ce qui est exactement ce que l'exclusion veut dire — « on ne le compte
> pas ici ».

### Ajustement

`10-ajustements.csv` : +4 `P-400` sur `B06/PAL02`, posté le 14/06.
**Stock physique = compté + ajustements.**

---

## 4. Les écarts, article par article

| Article | Stock ERP | Compté | Ajusté | Physique | Écart | Prix | Écart € | Matériel ? |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| P-100 | 180 | 156 | 0 | 156 | **−24** | 10 | **−240,00** | oui |
| P-200 | 80 | 77 | 0 | 77 | **−3** | 20 | −60,00 | non — 60 € < 100 € |
| P-300 | 350 | 325 | 0 | 325 | **−25** | 5 | **−125,00** | oui |
| P-400 | 100 | 96 | +4 | 100 | 0 | 50 | 0,00 | non |
| P-500 | 25 | 25 | 0 | 25 | 0 | *4* | 0,00 | non |
| P-600 | 512 | 500 | 0 | 500 | **−12** | 2 | −24,00 | non — 24 € < 100 € |
| P-800 | 20 | 0 | 0 | 0 | **−20** | 8 | **−160,00** | oui |

Le détail des totaux :

```
P-100   ERP  100 (SOL) +  0 (NOUVEAU) + 80 (GENERIQUE)              = 180
        cpt  100 (SOL) +  6 (NOUVEAU) + 50 (GENERIQUE)              = 156
P-200   ERP   50 (SOL) + 30 (QUAI)                                  =  80
        cpt   45 (SOL) + 32 (QUAI)                                  =  77
P-300   ERP  200 (SE2) + 150 (GENERIQUE)                            = 350
        cpt  210 (SE2) + 115 (GENERIQUE)                            = 325
P-400   ERP   40 (PAL01) + 60 (PAL02)                               = 100
        cpt   38 (PAL01) + 58 (PAL02)   [+3 de passage : ignorés]   =  96
P-600   ERP  500 (PAL01) + 12 (VIDE)                                = 512
        cpt  500 (PAL01) +  0 (VIDE)                                = 500
```

*P-500 : le prix standard est nul, la ligne de stock porte 4,00 € — c'est le
secours. Le stock ERP de P-500 vaut donc 25 × 4 = 100 €.*

**`compté seul`** : 0 au grain article — `ATP/NOUVEAU` porte du P-100, présent
ailleurs au stock ERP. **`ERP seul`** : 1, `P-800`, jamais compté nulle part.

---

## 5. Les KPI

```
Stock ERP      = 180×10 + 80×20 + 350×5 + 100×50 + 25×4 + 512×2 + 20×8
               = 1 800 + 1 600 + 1 750 + 5 000 + 100 + 1 024 + 160
               = 11 434,00 €          pour 1 267 unités

Stock physique = 156×10 + 77×20 + 325×5 + 100×50 + 25×4 + 500×2 + 0
               = 1 560 + 1 540 + 1 625 + 5 000 + 100 + 1 000 + 0
               = 10 825,00 €          pour 1 183 unités

Écart net      = 10 825 − 11 434 = −609,00 €      pour −84 unités
Écart brut     = 240 + 60 + 125 + 0 + 0 + 24 + 160 = 609,00 €
```

| KPI | Valeur attendue | Formule |
|---|---:|---|
| Stock ERP | **11 434,00 €** / 1 267 u. | Σ prix std × quantité |
| Stock physique | **10 825,00 €** / 1 183 u. | compté + ajustements |
| Écart net | **−609,00 €** / −84 u. | physique − ERP, signé |
| Écart brut | **609,00 €** / 84 u. | Σ des valeurs absolues |
| Fiabilité nette (valeur) | **94,67 %** | 1 − \|−609\| / 11 434 |
| Fiabilité brute (valeur) | **94,67 %** | 1 − 609 / 11 434 |
| Fiabilité brute (quantité) | **93,37 %** | 1 − 84 / 1 267 |
| IRA | **28,57 %** (2 / 7) | lignes à écart nul ÷ lignes |
| Lignes | **7** | un article = une ligne |
| Lignes matérielles | **3** | P-100, P-300, P-800 |
| Compté seul | **0** | |
| ERP seul | **1** | P-800 |

*Ici l'écart net et l'écart brut coïncident : tous les écarts d'article vont
dans le même sens. C'est voulu — les deux indicateurs se distinguent quand des
écarts se compensent, et le jeu de données garde ce cas pour la lecture par
emplacement, où `ATP/QUAI` (+2) et `B06/PAL01` (−2) se compensent sur P-400.*

**IRA** : seuls `P-400` (écart nul après ajustement) et `P-500` (forcé au stock
ERP) sont exacts. Il n'y a pas de tolérance — une ligne fausse d'une unité est
une ligne fausse.

**Matérialité** — les deux portes doivent céder :

| Article | \|Δ€\| ≥ 100 € | \|Δqté\| / ERP ≥ 2 % | Matériel |
|---|---|---|---|
| P-100 | 240 ✔ | 24/180 = 13,3 % ✔ | **oui** |
| P-200 | 60 ✘ | 3/80 = 3,8 % ✔ | non |
| P-300 | 125 ✔ | 25/350 = 7,1 % ✔ | **oui** |
| P-600 | 24 ✘ | 12/512 = 2,3 % ✔ | non |
| P-800 | 160 ✔ | 20/20 = 100 % ✔ | **oui** |

---

## 6. Ce que le contrôle vérifie en plus des chiffres

`tests/test_jeu_de_donnees.py` vérifie aussi le **processus**, parce qu'un total
juste sur un processus faux ne prouve rien :

* les trois emplacements précomptés sont bien scellés ;
* le chargement du stock ERP général **n'a pas** remplacé leur référence — les
  lignes à 999 du snapshot n'ont pas gagné ;
* la consolidation GENERIQUE rend exactement `P-100 = 50` et `P-300 = 115` ;
* chaque article produit la ligne d'écart attendue, avec ses quantités **et** ses
  valeurs — un total peut être juste par compensation, une ligne non.

## 7. Faire évoluer le jeu de données

Modifier un CSV suffit : le contrôle relance `oracle.py` avant de comparer, si
bien qu'un `attendu.json` oublié ne peut pas faire passer une vérification sur
des chiffres périmés.

En revanche, **si les deux divergent, l'une des deux implémentations a tort** —
et il faut décider laquelle avant de toucher à quoi que ce soit. Aligner
l'oracle sur l'application parce que « c'est l'application qui tourne » retirerait
au jeu de données la seule chose qu'il apporte.
