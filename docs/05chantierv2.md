# Chantier V2 — feuilles préparées et portefeuilles de gestionnaires

Conception arrêtée, prête à implémenter. Chaque décision non triviale porte sa
justification : le but est qu'aucune ne soit à re-débattre au moment d'écrire
le code.

---

## A. Feuilles de comptage générées en préparation

### A.1 Import `[feuille, article, section]`

**Nouveau contrat de grille `count_sheets`** (`app/inventory/ingest/contracts.py`) :

| Champ | Requis | Alias acceptés |
|---|---|---|
| `sheet_code` | oui | feuille, zone, code feuille, sheet | 
| `item_number` | oui | article, référence, ref, item, Numéro d'article |
| `section` | non (bord de ligne par défaut) | section, statut, source |
| `unit` | non (PCE par défaut) | unité, unit, unité de comptage |

Clé naturelle : `(sheet_code, item_number, section)`. Un même article peut
légitimement figurer deux fois sur une feuille dans deux sections différentes
(bord de ligne *et* WIP) ; c'est le trio qui doit être unique, pas l'article.

**Importeur `ImportService.import_count_sheets`** :

1. Regrouper les lignes par `sheet_code`.
2. Pour chaque code inconnu, créer la zone — `ZoneService` crée déjà ses
   feuilles de passage. Un code connu est complété, jamais recréé.
3. Vérifier chaque article contre le référentiel figé. Un article absent est
   une **erreur de ligne**, pas un article créé à la volée : le référentiel
   est la vérité de la campagne, et un import de feuilles ne doit pas pouvoir
   l'étendre par effet de bord.
4. Normaliser la section avec `legacy_section_alias()` — le même vocabulaire
   que le collage côté client (`frontend/src/lib/pasteSheetLines.ts`), pour
   qu'un fichier accepté d'un côté le soit de l'autre.
5. Créer les lignes **sur chaque feuille de passage de la zone**, quantités
   vides. Les deux compteurs recensent les mêmes articles ; ne pré-remplir que
   le passage 1 rendrait le passage 2 aveugle et fausserait l'arbitrage.

**Câblage** : `_TARGETS["count_sheets"] = "import_count_sheets"` dans
`app/inventory/api/routers/data.py`.

**Matrice de gel** (`app/inventory/domain/workflow.py`) : `count_sheets`
devient mutable en `PREPARATION` *et* en `COUNTING`. La préparation est
précisément la phase où l'on décide quoi compter.

> ⚠️ Le contrat `count_sheet_lines` existe déjà mais n'a jamais eu
> d'importeur : c'est lui qu'il faut renommer/remplacer, pas un huitième
> contrat à ajouter à côté.

### A.2 Nombre de comptages par zone

**Migration `002_zone_passes.sql`** :

```sql
ALTER TABLE zone ADD COLUMN passes SMALLINT NOT NULL DEFAULT 2
    CHECK (passes IN (1, 2));
```

Défaut à 2 : le double comptage est la règle, le comptage unique l'exception
qu'on assume explicitement.

**Endpoint** `POST /campaigns/{id}/generic/zones/passes`
avec `{"zoneIds": [...], "passes": 1}` — action de masse sur une sélection,
comme demandé.

Règles :

- passer à 1 **supprime** la feuille de passage 2 ;
- **refuser** si la feuille de passage 2 porte déjà une quantité saisie —
  ramener une zone à un seul comptage après coup effacerait un comptage réel ;
  le message doit nommer les zones concernées ;
- repasser à 2 récrée la feuille de passage 2.

**Consolidation** (`app/inventory/domain/consolidation.py`) : une zone à un
seul passage ne peut pas produire d'écart d'arbitrage. `resolve_zone_quantities`
doit prendre le nombre de passages de la zone en entrée et, à 1, retenir le
passage unique sans émettre l'avertissement « un seul passage » qui n'a de sens
que lorsque deux étaient attendus.

### A.3 Feuilles de saisie libre

`POST /generic/zones` crée déjà une zone et ses feuilles vides : la
fonctionnalité existe, elle n'est simplement pas atteignable en préparation.
La lever suit de A.1 (matrice de gel).

Ajouter `zone.free_entry BOOLEAN NOT NULL DEFAULT false` dans la même
migration, positionné à `true` quand la zone est créée sans lignes. Deux
usages : l'interface l'affiche comme « saisie libre » plutôt que comme une
feuille vide anormale, et les contrôles cessent de signaler l'absence de
lignes pré-imprimées comme un défaut de préparation pour ces zones-là.

---

## B. Gestionnaires et périmètres

### B.2 Administration

Dans la phase Préparation, rajouter deux onglet :
- Un onglet "Affectation journaux" qui affiche les entrepôts suivants {B06, B06VRAC, QUAL, QUAL VRAC, AUTRES} que l'on peut affectr à l'un des gestionnaires {Gestionnaire 1, Genstionnaire 2, ..., jusqu'au Gestionnaire 5}.
- Un onglet "Affectation zones" qui permet de rattacher à chaque zone déjà créée (feuille de comptage) un des 5 gestionnaires (Getionnaire 1, à Gestionnaire 5)

### B.2 Mode focus

**Résolution.** Un journal est dans le périmètre du gestionnaire s'il concerne un entrepôt affecté à ce même fourisseur. Péreil pour les zones.

**Filtrage côté serveur**, jamais côté client :
`GET /counting/journals?focus=true` et `GET /generic/zones?focus=true`.
Filtrer dans le navigateur expédierait quand même les données de tout le monde
à chaque poste — inacceptable dès qu'un prestataire compte une zone.

**Le focus est un filtre, jamais une permission.** Les actions restent
identiques dans les deux modes, comme demandé. Un gestionnaire garde le droit
d'agir hors de son périmètre : le mode focus réduit le bruit, il ne cloisonne
pas. Ce point mérite d'être écrit dans l'interface, faute de quoi il sera lu
comme une habilitation.

**Interface** : un interrupteur « Mon périmètre » dans la barre supérieure,
persisté en `localStorage`, avec le décompte des objets concernés. Quand il
est actif et que le périmètre est vide, afficher explicitement « aucun objet
ne vous est affecté » plutôt qu'une liste vide indiscernable d'une campagne
sans données. Les référentiels Gestionnaires et périmètres s'administrent
sous Préparation → Référentiels, avec les mêmes grilles que le reste.

---

## Ce que chaque lot doit prouver avant d'être dit fini

La méthode qui a servi jusqu'ici, et qui a trouvé tous les défauts de cette
campagne de mise au point : un test qui échoue **avant** le correctif, une
vérification contre un vrai PostgreSQL, et un passage dans un vrai navigateur
pour tout ce qui touche l'interface. Un `200` dans le journal ne prouve rien —
la page blanche de l'import en était la démonstration.


## C. AUTRES AMELIORATIONS

### C.1 Feuilles de comptage

- Réduire un peu les marges sur les pages imprimées pour garner un peu plus d'espace.
- Mettre le nom, les dates et la signature sur une seule ligne (au lieu de 2)
- limiter à 32 le nombre de caractères affichés de la colonne désignation
- enlever le texte "Une case vide signifie « non compté » et sera traitée comme telle. Pour déclarer une absence de stock, écrire explicitement 0"
- augmenter la largeur des lignes des tableaux par 62%
- quand on ouvre le feuille de comptage n°2 (pour consultation ou édition), rajouter une colonne avec la quantité du comptage n°1. Cette colonne n'apparait pas dans les feuilles imprimables, naturellement.

### C.2 Alertes et Analyses
- Priorité à l'analyse de l'écart global par référence plutôt qu'à l'écart par couple référence / emplacement. En effet, s'il s'agit en grande partie d'un transfert entre deux emplacements, ça fait baisser l'IRA mais ce n'est pas grave en soi du moment qu'il ne s'agit pas d'une forte perte sèche.