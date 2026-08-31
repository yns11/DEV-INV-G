# Jeu de données de contrôle

Une campagne complète, assez petite pour être vérifiée à la main, assez large
pour toucher chaque règle du produit. À côté des fichiers, `oracle.py` calcule
le résultat attendu **sans l'application** ; `tests/test_jeu_de_donnees.py`
charge le tout dans l'application et confronte les deux.

> Deux implémentations indépendantes qui tombent sur les mêmes chiffres se
> confirment l'une l'autre. Une seule ne confirme rien.

---

## 1. Vérifier le calcul théorique

```bash
python fixtures/jeu-de-donnees/oracle.py
```

Écrit `attendu.json` et affiche l'essentiel :

```
Stock ERP            1267.000000 unités       11434.00 €
Stock physique       1183.000000 unités       10825.00 €
Écart net             -84.000000 unités        -609.00 €
Écart brut             84.000000 unités         609.00 €
IRA           0.2857142857142857142857142857   (2/7)
Lignes matérielles : 3
```

Le détail article par article est dans
[`docs/09-jeu-de-donnees-de-controle.md`](../../docs/09-jeu-de-donnees-de-controle.md),
avec l'arithmétique posée : chaque quantité y est rattachée au fichier et à la
ligne d'où elle vient.

`oracle.py` n'importe rien de `inventory`. Il ré-implémente les règles à partir
de ce que la documentation en dit — c'est ce qui en fait un contrôle et non un
écho.

## 2. Confronter l'application au calcul théorique

**Automatiquement** — le contrôle déroule tout le processus et compare 23
grandeurs :

```bash
make db-start                       # ou un PostgreSQL joignable
pytest tests/test_jeu_de_donnees.py -v
```

Un échec nomme la grandeur qui diverge et les deux valeurs.

**À la main, dans l'interface** — pour voir les mêmes chiffres à l'écran :

| # | Écran | Fichier | Remarque |
|---|---|---|---|
| 1 | Nouvelle campagne | — | Code `INV-TEST-01`, date de comptage **13/06/2026** |
| 2 | Référentiels & seuils → Articles | `01-articles.csv` | |
| 3 | Référentiels & seuils → Nomenclatures | `02-nomenclatures.csv` | |
| 4 | Référentiels & seuils → Emplacements | `03-emplacements.csv` | |
| 5 | Référentiels & seuils → Seuils | — | **100 €** et **2 %** sur tous les types |
| 6 | GENERIQUE → Zones | `07-zones-generique.csv` | crée les deux feuilles |
| 7 | GENERIQUE → Feuilles | `08-feuilles-generique.csv` | |
| 8 | *Passer en comptage* | — | gèle les référentiels |
| 9 | Comptages avancés → Import | `04-journaux-precomptage.csv` | |
| 10 | Comptages avancés → Journaux ERP | — | **NPEM-A** → `ATP/SOL` + `ATP/SE2` · **NPEM-B** → `B06/PAL01`, *Déclarer et sceller* |
| 11 | Référentiels & seuils → Stock ERP | `05-stock-erp-jour-j.csv` | |
| 12 | Comptage → Import ERP | `06-journaux-jour-j.csv` | |
| 13 | Comptage → Journaux | — | `B06/FORCE` → *Forcer au stock ERP* |
| 14 | GENERIQUE → saisie | `09-comptages-generique.csv` | les deux passages |
| 15 | GENERIQUE → Arbitrages | `09b-arbitrages-generique.csv` | P-300 : retenir **105** |
| 16 | *Passer en analyse* | — | |
| 17 | Analyse → Ajustements | `10-ajustements.csv` | |
| 18 | Analyse → Backflush | `11-backflush.csv` | période **18/05/2026 → 15/06/2026** |

Le carrousel doit alors afficher exactement les chiffres ci-dessus.

---

## Ce que le jeu de données couvre

| Cas | Où il se joue |
|---|---|
| Précomptage scellé, référence à sa date | `NPEM-A`, `NPEM-B` |
| Un journal couvrant **deux** emplacements | `NPEM-A` → `ATP/SOL` + `ATP/SE2` |
| **Ligne de passage** : un journal touche un emplacement qu'il ne couvre pas | `NPEM-A` ligne 4 → `B06/PAL02` |
| Le snapshot du jour J **ne reprend pas** un emplacement scellé | lignes à 999 dans `05-…` |
| Étiquette scellée recomptée ailleurs | `ET-002` : `ATP/SOL` → `ATP/QUAI` |
| Journal **vrac** : l'étiquette générique n'est pas une identité | `NPEM-B`, étiquette `VRAC` |
| Emplacement **désactivé** (tampon) hors périmètre | `INV/01` |
| Article **exclu `ALL`** : aucune ligne d'écart | `P-700` |
| Article **exclu `GENERIC`** : hors consolidation, écart ailleurs conservé | `P-800` |
| Article **sans prix standard** : le coût de la ligne en secours | `P-500` |
| Emplacement **inventorié ailleurs** : écart nul par construction | `B06/FORCE` |
| Compté sans être au stock ERP (`counted_only`) | `ATP/NOUVEAU` |
| Au stock ERP sans être compté (`book_only`) | `B06/VIDE` |
| GENERIQUE : deux passages, **désaccord**, arbitrage décidé | `P-300` : 100 vs 110 → 105 |
| GENERIQUE : **éclatement WIP** par nomenclature | `SF-10` × 5 → 20 `P-100` + 10 `P-300` |
| GENERIQUE : **produit fini hors WIP** écarté | `PF-01` en bord de ligne |
| Ajustement posté après comptage | `P-400` +4 sur `B06/PAL02` |
| Écart **backflush** mesuré | `P-300` : +8 |
| Matérialité : les deux portes doivent céder | `P-200` (−60 €) n'est pas matériel |

## Les fichiers

| Fichier | Contrat d'import | Contenu |
|---|---|---|
| `01-articles.csv` | `items` | 10 articles, 4 types, 2 exclusions, 1 prix nul |
| `02-nomenclatures.csv` | `boms` | 4 liens, 2 niveaux |
| `03-emplacements.csv` | `locations` | 10 emplacements, 1 désactivé |
| `04-journaux-precomptage.csv` | `count_journal_lines` | 2 journaux avancés, 6 lignes |
| `05-stock-erp-jour-j.csv` | `book_stock` | 14 lignes, dont 5 sur des emplacements scellés |
| `06-journaux-jour-j.csv` | `count_journal_lines` | 1 journal, 4 lignes |
| `07-zones-generique.csv` | `zones` | 1 zone, 2 passages |
| `08-feuilles-generique.csv` | `count_sheets` | 5 lignes pré-imprimées |
| `09-comptages-generique.csv` | *saisie écran* | les deux passages |
| `09b-arbitrages-generique.csv` | *saisie écran* | 1 arbitrage décidé |
| `10-ajustements.csv` | `adjustments` | 1 mouvement |
| `11-backflush.csv` | `backflush` | 1 article mesuré |

Séparateur `;`, encodage UTF-8, décimale française acceptée.
