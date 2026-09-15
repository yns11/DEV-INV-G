-- =============================================================================
-- 032 — Une ligne importée ne vit plus à côté de la ligne qu'elle rafraîchit
-- -----------------------------------------------------------------------------
-- Le rechargement de l'export ERP supprimait les lignes sans valeur manuelle,
-- puis réinsérait **toutes** celles du fichier avec un identifiant neuf, sans
-- jamais retrouver celle qui portait déjà l'article. Une ligne corrigée à la
-- main — ou posée par la consolidation GENERIQUE, qui écrit elle aussi une
-- valeur manuelle — survivait donc au ménage, et l'import lui en ajoutait une
-- seconde à côté. Les deux vivaient, et le calcul des quantités comptées les
-- additionnait.
--
-- Constaté sur une campagne terrain : le journal GENERIQUE, rempli par la
-- consolidation puis posté dans l'ERP, est revenu par l'extraction suivante et
-- ses 245 articles se sont retrouvés comptés deux fois. Soit, sur cet
-- emplacement, 6 448 049 unités lues pour 3 224 025 comptées.
--
-- Le code ne crée plus ce doublon — le rapprochement se fait désormais sur
-- (journal, article), voir `JournalRepository.replace_imported_lines`. Restent
-- les lignes déjà écrites, que cette migration range.
--
-- Ce qu'elle fusionne, et rien d'autre
-- ------------------------------------
-- Uniquement le couple qui pose problème : **une ligne importée à côté d'au
-- moins une ligne portant une valeur manuelle**, pour le même journal et le
-- même article. Les deux décrivent la même mesure, et la manuelle prime.
--
-- Deux lignes manuelles pour un même article ne sont **pas** touchées. L'écran
-- permet d'en ajouter — deux relevés distincts au même endroit — et elles
-- s'additionnent légitimement. C'est aussi pourquoi aucun index unique n'est
-- posé ici : il interdirait un geste que le métier utilise.
--
-- Rien n'est perdu
-- ----------------
-- La valeur importée est d'abord **reportée** sur la ligne qui survit, qui
-- porte donc les deux — ce que le code corrigé aurait écrit dès l'origine. La
-- ligne redondante est ensuite supprimée *logiquement* : elle reste en base,
-- `deleted_at` renseigné, et se relit si besoin.
--
-- Quand plusieurs lignes manuelles coexistent, l'écho de l'ERP se pose sur une
-- seule d'entre elles pour que le total reste celui des saisies et non leur
-- double. Le choix se fait sur l'identifiant, et `replace_imported_lines`
-- applique exactement la même règle : si les deux divergeaient, une base
-- fusionnée puis rechargée finirait avec l'écho sur deux lignes. Trier par
-- `updated_at` aurait été plus parlant et faux — le rechargement met cette
-- colonne à jour, donc le choix se déplacerait à chaque import.
--
-- Idempotent : rejouable sans effet de bord. Après un premier passage il ne
-- reste plus de couple à fusionner, et les deux instructions ne voient rien.
-- =============================================================================

SET search_path TO inventory, public;

-- Les couples à fusionner : la ligne importée (celle sans valeur manuelle) et
-- la ligne d'accueil (la plus ancienne de celles qui en portent une).
CREATE TEMPORARY TABLE fusion_032 ON COMMIT DROP AS
WITH vivantes AS (
    SELECT id, journal_id, item_number, qty_manual, qty_imported, unit,
           qty_on_hand, erp_journal_number, label_count, updated_at
    FROM count_journal_line
    WHERE deleted_at IS NULL
),
importees AS (
    SELECT DISTINCT ON (journal_id, item_number)
           id, journal_id, item_number, qty_imported, unit,
           qty_on_hand, erp_journal_number, label_count
    FROM vivantes
    WHERE qty_manual IS NULL
    ORDER BY journal_id, item_number, id
),
accueils AS (
    SELECT DISTINCT ON (journal_id, item_number)
           id, journal_id, item_number
    FROM vivantes
    WHERE qty_manual IS NOT NULL
    ORDER BY journal_id, item_number, id
)
SELECT a.id            AS accueil_id,
       i.id            AS redondante_id,
       i.qty_imported,
       i.unit,
       i.qty_on_hand,
       i.erp_journal_number,
       i.label_count
FROM accueils a
JOIN importees i
  ON i.journal_id = a.journal_id
 AND i.item_number = a.item_number;

-- 1. Reporter ce que la ligne importée apportait, puis seulement ensuite
--    retirer celle-ci : l'inverse perdrait la valeur pendant l'opération.
UPDATE count_journal_line l
   SET qty_imported        = f.qty_imported,
       unit                = COALESCE(NULLIF(f.unit, ''), l.unit),
       qty_on_hand         = f.qty_on_hand,
       erp_journal_number  = f.erp_journal_number,
       label_count         = f.label_count,
       row_version         = l.row_version + 1,
       updated_at          = now()
  FROM fusion_032 f
 WHERE l.id = f.accueil_id;

-- 2. La redondante part logiquement. `updated_by` nomme la migration : une
--    ligne disparue sans auteur est une ligne dont personne ne retrouve la
--    raison six mois plus tard.
UPDATE count_journal_line l
   SET deleted_at  = now(),
       updated_by  = 'migration-032',
       updated_at  = now()
  FROM fusion_032 f
 WHERE l.id = f.redondante_id
   AND l.deleted_at IS NULL;
