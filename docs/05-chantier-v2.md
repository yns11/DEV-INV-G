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
| `item_number` | oui | article, référence, ref, item |
| `section` | non | section, statut, emplacement |
| `unit` | non | unité, ue, uom |

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

- passer à 1 **archive** la feuille de passage 2 (statut `ARCHIVED`), ne la
  supprime pas : sa trace d'audit doit survivre à la décision ;
- **refuser** si la feuille de passage 2 porte déjà une quantité saisie —
  ramener une zone à un seul comptage après coup effacerait un comptage réel ;
  le message doit nommer les zones concernées ;
- repasser à 2 réactive la feuille archivée si elle existe, en recrée une
  sinon.

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

## B. Gestionnaires et portefeuilles

### B.1 Schéma (migration `003_managers.sql`)

```sql
CREATE TABLE manager (
    id           UUID PRIMARY KEY,
    campaign_id  UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    updated_by   TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX manager_unique ON manager (campaign_id, lower(email));

CREATE TABLE portfolio (
    id           UUID PRIMARY KEY,
    campaign_id  UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    manager_id   UUID NOT NULL REFERENCES manager (id) ON DELETE CASCADE,
    scope_type   TEXT NOT NULL CHECK (scope_type IN
                     ('JOURNAL', 'ZONE', 'WAREHOUSE', 'LOCATION')),
    scope_key    TEXT NOT NULL,
    updated_by   TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX portfolio_unique
    ON portfolio (campaign_id, manager_id, scope_type, scope_key);
CREATE INDEX portfolio_lookup ON portfolio (campaign_id, scope_type, scope_key);
```

**Pourquoi une clé métier (`scope_key`) et non une clé étrangère.** Un
portefeuille se définit avant que les journaux n'existent : on sait qui suit
l'entrepôt B06 bien avant le premier import ERP. Stocker le numéro de journal
comme texte laisse le portefeuille survivre à un ré-import qui régénère les
identifiants techniques. C'est un choix assumé : l'intégrité référentielle est
sacrifiée à une propriété plus utile ici, la stabilité de l'affectation.

**Pourquoi quatre granularités.** Affecter par entrepôt ou par zone couvre le
gros du parc en quelques lignes ; affecter par journal ou emplacement traite
les exceptions. Un même objet peut être couvert par plusieurs règles : elles
s'additionnent, elles ne se contredisent pas.

**Contrat d'import `portfolios`** : `[gestionnaire, périmètre, clé]`, avec le
même détecteur tolérant que le reste — `périmètre` accepte « entrepôt »,
« zone », « journal », « emplacement ». Le gestionnaire est créé s'il n'existe
pas ; son courriel est la clé, car c'est ce que la plateforme injecte.

### B.2 Mode focus

**Résolution.** Un journal est dans le périmètre du gestionnaire si une règle
correspond à son numéro, à son entrepôt, ou à l'emplacement de ses lignes. Une
feuille l'est si une règle correspond au code de sa zone. À écrire comme une
fonction pure dans `app/inventory/domain/` — c'est de la logique métier
testable sans base.

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
sans données. Les référentiels Gestionnaires et Portefeuilles s'administrent
sous Préparation → Référentiels, avec les mêmes grilles que le reste.

---

## Séquence proposée

| # | Lot | Dépend de | Livrable vérifiable |
|---|---|---|---|
| 1 | Contrat + importeur `count_sheets`, matrice de gel | — | Import d'un fichier `[feuille, article, section]` → zones et lignes créées, articles inconnus rejetés |
| 2 | `zone.passes` + endpoint de masse + consolidation | 1 | Zone à 1 comptage : pas d'arbitrage, refus si le passage 2 est déjà saisi |
| 3 | `free_entry` + contrôles | 1 | Zone sans articles créée en préparation, non signalée comme défaut |
| 4 | Tables + CRUD + import `portfolios` | — | Gestionnaires et portefeuilles administrables et importables |
| 5 | Résolution du périmètre + `?focus=` + interrupteur | 4 | Deux gestionnaires voient deux périmètres disjoints, actions identiques |

Les lots 1–3 et 4–5 sont indépendants : ils peuvent avancer en parallèle.

## Ce que chaque lot doit prouver avant d'être dit fini

La méthode qui a servi jusqu'ici, et qui a trouvé tous les défauts de cette
campagne de mise au point : un test qui échoue **avant** le correctif, une
vérification contre un vrai PostgreSQL, et un passage dans un vrai navigateur
pour tout ce qui touche l'interface. Un `200` dans le journal ne prouve rien —
la page blanche de l'import en était la démonstration.
