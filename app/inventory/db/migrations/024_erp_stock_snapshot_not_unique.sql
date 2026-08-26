-- =============================================================================
-- 024 — Le miroir du stock cesse d'exiger une unicité que la source n'a pas
-- -----------------------------------------------------------------------------
-- La migration 013 a posé `PRIMARY KEY (snapshot_date, item_id, entrepot,
-- emplacement)` sur `erp_stock_snapshot`, en s'appuyant sur ce que la source
-- annonce : « une ligne par article × entrepôt × emplacement ». La source ne
-- tient pas cette promesse, et c'est **normal** :
--
--     duplicate key value violates unique constraint "erp_stock_snapshot_pkey"
--     DETAIL: Key (snapshot_date, item_id, entrepot, emplacement)
--             = (2026-08-21, mass-00037799, QUAL VRAC, PRISON QO) already exists.
--
-- L'application le sait depuis toujours et le documente à l'endroit qui compte,
-- `map_book_stock` : « Duplicate (item, warehouse, location) triples are
-- **summed**, not overwritten: the ERP export legitimately splits one location's
-- stock across several rows when batch or status dimensions differ, and dropping
-- all but the last would understate the book. » Le stock d'un emplacement est
-- réparti sur plusieurs lignes dès qu'une dimension que le miroir ne copie pas
-- — lot, statut qualité — les distingue. L'emplacement du refus, « PRISON QO »
-- dans l'entrepôt « QUAL VRAC », est précisément une zone de quarantaine.
--
-- **Pourquoi ne pas dédupliquer.** `swap` sait le faire (`DISTINCT ON`), et ce
-- serait la correction d'une ligne. Ce serait aussi un défaut : garder une ligne
-- sur deux **sous-évalue le stock ERP**, ce que la phrase ci-dessus nomme comme
-- l'erreur à ne pas commettre — et l'écart d'inventaire qui en sortirait serait
-- faux sans que rien ne le signale. Pire, le miroir cesserait de dire la même
-- chose que la lecture directe d'Unity Catalog, qui rend les deux lignes : le
-- même inventaire donnerait deux résultats selon `INV_ERP_SOURCE`.
--
-- **Le miroir est une copie fidèle.** C'est la règle des cinq tables, énoncée en
-- 013 elle-même. Une contrainte qui contredit la source n'est pas une garantie,
-- c'est un pari sur elle — et il vient d'être perdu.
--
-- **Ce que la clé apportait, et qui n'est pas perdu.** Rien n'écrit ici par
-- `ON CONFLICT` : la substitution est un `TRUNCATE` suivi d'un `INSERT ...
-- SELECT`, dans une transaction. La clé ne servait donc pas à arbitrer une
-- écriture concurrente. Restait l'index, que le chemin de lecture emploie
-- vraiment — d'abord la date maximale, puis les lignes de ce jour : il est
-- recréé à l'identique, en non unique.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE erp_stock_snapshot DROP CONSTRAINT IF EXISTS erp_stock_snapshot_pkey;

-- Le même index que la clé fournissait, sans l'unicité. La colonne de date vient
-- en tête et décroissante parce que c'est l'ordre de la lecture : `stock_dates`
-- énumère les jours publiés, `fetch_book_stock` lit ensuite celui qu'on désigne.
CREATE INDEX IF NOT EXISTS erp_stock_snapshot_key_idx
    ON erp_stock_snapshot (snapshot_date DESC, item_id, entrepot, emplacement);
