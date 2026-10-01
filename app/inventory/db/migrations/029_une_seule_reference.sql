-- =============================================================================
-- 029 — Une seule référence, et les comptages avancés ne calculent plus rien
-- -----------------------------------------------------------------------------
-- Le dispositif des comptages avancés portait sa propre référence — le stock
-- ERP d'avant comptage, `ERP@T0` — contre laquelle l'écart d'un emplacement
-- scellé était mesuré. Cette référence n'existe plus.
--
-- La raison est dans l'ordre des faits : un journal de précomptage est **posté
-- dans l'ERP** avant que la photo du jour J ne soit prise, et cette photo l'a
-- donc déjà intégré. La correction de l'inventaire n'est pas perdue, elle est
-- enregistrée plus tôt — dans l'ERP, avant la campagne. La mesurer une seconde
-- fois contre un état antérieur revenait à compter deux fois la même
-- correction, et c'est ce qui rendait le dispositif illisible : deux axes qui
-- se ressemblaient, six gestes dont quatre ne changeaient aucun chiffre.
--
-- **La référence est désormais unique** : le stock ERP du jour J, gelé, pour
-- tout article et tout emplacement.
--
-- Ce qui subsiste des comptages avancés est un affichage, et rien d'autre : la
-- dérive `ERP@J − compté@T0` et les étiquettes d'un emplacement scellé
-- retrouvées ailleurs. Elles se regardent, elles n'appellent aucune décision et
-- ne bloquent rien. Les colonnes et la table qui portaient ces décisions s'en
-- vont donc, plutôt que de rester en place à ne jamais se remplir : une colonne
-- qui ne peut plus prendre de valeur est un piège pour le prochain lecteur.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- --------------------------------------------------------------------------
-- 1. Le stock ERP ne vient plus d'un journal de précomptage
-- --------------------------------------------------------------------------
-- Toutes les lignes viennent du chargement général. L'index partiel disparaît
-- avec la colonne qu'il portait.
DROP INDEX IF EXISTS book_stock_early_idx;
ALTER TABLE book_stock DROP COLUMN IF EXISTS erp_journal_id;

-- --------------------------------------------------------------------------
-- 2. La dérive : deux termes, une soustraction, aucune décision
-- --------------------------------------------------------------------------
-- `qty_erp_t0` était la référence supprimée. `is_material` et les cinq colonnes
-- d'issue servaient un arbitrage qui n'a plus lieu d'être : une dérive
-- n'entraîne aucune action requise et aucun constat bloquant. Et « physique »
-- disait « compté + ajusté » : l'ajustement des précomptages n'existe plus, et
-- le nom mentait sur ce que la colonne contient.
--
-- **Reposée, et non retouchée colonne par colonne.** La table est de la donnée
-- **dérivée** : chaque chargement général la recalcule entièrement. La reposer
-- ne perd donc rien qu'un import ne reproduise, et c'est ce qui autorise le
-- geste franc plutôt qu'une suite d'`ALTER` dont chacun devrait survivre au
-- fichier qui l'a précédé.
--
-- **Ce que cela coûte, et qu'il faut savoir.** La 025 crée un index partiel
-- `early_count_drift_open_idx … WHERE is_material AND resolution IS NULL`, et
-- ces deux colonnes s'en vont. Postgres analyse le prédicat d'un `CREATE INDEX
-- IF NOT EXISTS` **avant** de regarder si le nom est déjà pris : rejouer la 025
-- seule sur un schéma déjà à jour échoue donc, et reposer un index du même nom
-- n'y change rien. Aucune écriture d'ici ne le rendrait possible sans garder en
-- base deux colonnes que plus rien ne remplit.
--
-- Ce qui reste vrai, et qui est ce qu'une reprise exécute réellement : la
-- séquence complète sur les tables qui manquent. La 025 les repose telles
-- qu'elle les connaît, la 029 les ramène à leur forme actuelle. C'est ce que
-- `test_early_count_schema.py` épingle.
--
-- L'index reparaît ici sous son nom, avec la définition qui a désormais du
-- sens : les dérives non nulles, c'est-à-dire les seules que la vue montre.
DROP INDEX IF EXISTS early_count_drift_open_idx;
DROP INDEX IF EXISTS early_count_drift_uq;
DROP TABLE IF EXISTS early_count_drift;

CREATE TABLE early_count_drift (
    id             UUID PRIMARY KEY,
    campaign_id    UUID          NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    erp_journal_id UUID,
    warehouse_id   TEXT          NOT NULL,
    location_id    TEXT          NOT NULL,
    item_number    TEXT          NOT NULL,
    -- Ce que le précomptage a compté. Compté, et rien d'autre.
    qty_counted_t0 NUMERIC(20,6) NOT NULL DEFAULT 0,
    -- La référence unique de la campagne : le stock ERP du jour J, gelé.
    qty_erp_j      NUMERIC(20,6) NOT NULL DEFAULT 0,
    drift_qty      NUMERIC(20,6) NOT NULL DEFAULT 0,
    drift_value    NUMERIC(20,2) NOT NULL DEFAULT 0,
    computed_at    TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS early_count_drift_uq
    ON early_count_drift (campaign_id, warehouse_id, location_id, item_number);
CREATE INDEX IF NOT EXISTS early_count_drift_open_idx
    ON early_count_drift (campaign_id) WHERE drift_qty <> 0;

-- --------------------------------------------------------------------------
-- 3. L'étiquette ne se tranche plus
-- --------------------------------------------------------------------------
-- Une étiquette d'un emplacement scellé retrouvée dans un autre journal est
-- une information sur ce qui a bougé entre le précomptage et le jour J. Elle
-- n'exclut plus rien d'aucune agrégation, donc elle n'a plus de décision à
-- porter — et la table qui les gardait s'en va avec elles.
DROP INDEX IF EXISTS early_count_label_decision_recount_idx;
DROP INDEX IF EXISTS early_count_label_decision_uq;
DROP TABLE IF EXISTS early_count_label_decision;
