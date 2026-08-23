-- =============================================================================
-- 010 — D'où vient chaque quantité de la comparaison
-- -----------------------------------------------------------------------------
-- Les trois étapes chargées à la main — réceptions, expéditions, rebuts — se
-- lisent maintenant aussi dans l'ERP, chacune par la requête de son domaine
-- (bons de réception fournisseur, bons de livraison client, mouvements vers
-- l'emplacement rebut). Et une fois chargées, elles s'éditent à l'écran.
--
-- Trois provenances coexistent donc pour une même ligne, et elles ne se valent
-- pas devant un chiffre contesté :
--
--   ERP     lue dans le catalogue, rejouable par la même requête
--   FILE    chargée depuis un fichier ou un collage
--   MANUAL  saisie ou corrigée directement dans la grille
--
-- Sans cette colonne, une valeur corrigée à la main est indiscernable de celle
-- que l'ERP a donnée — et c'est précisément la question qui se pose en réunion
-- six mois plus tard. La colonne le dit, et l'écran l'affiche.
--
-- `MANUAL` par défaut plutôt que `FILE` : les lignes déjà en base ont été
-- chargées avant que la provenance existe, et affirmer d'où elles viennent
-- serait inventer une information. « Saisie » est la lecture qui promet le
-- moins.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE stock_flow_input
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'MANUAL';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'stock_flow_input_source_ck'
    ) THEN
        ALTER TABLE stock_flow_input
            ADD CONSTRAINT stock_flow_input_source_ck
            CHECK (source IN ('ERP', 'FILE', 'MANUAL'));
    END IF;
END $$;

-- L'instantané ERP s'édite lui aussi : la même question s'y pose, et la même
-- réponse doit être disponible.
ALTER TABLE stock_flow_erp
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'ERP';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'stock_flow_erp_source_ck'
    ) THEN
        ALTER TABLE stock_flow_erp
            ADD CONSTRAINT stock_flow_erp_source_ck
            CHECK (source IN ('ERP', 'FILE', 'MANUAL'));
    END IF;
END $$;

-- Quand chaque étape a été lue dans l'ERP. Portée par la série et non par la
-- ligne : c'est la lecture qui a une date, pas chacun de ses résultats.
ALTER TABLE stock_flow_run
    ADD COLUMN IF NOT EXISTS receipts_refreshed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS shipments_refreshed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS scrap_refreshed_at     TIMESTAMPTZ;
