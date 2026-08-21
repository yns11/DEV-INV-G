-- =============================================================================
-- 013 — Le miroir du snapshot de stock
-- -----------------------------------------------------------------------------
-- Le stock ERP d'une campagne se chargeait par fichier ou par collage : un
-- export tiré de l'ERP à la main, puis re-importé ici. C'est exactement
-- l'aller-retour que la lecture directe a supprimé pour les articles et les
-- nomenclatures, et il produisait les mêmes erreurs — un export de la veille,
-- une colonne décalée, un filtre d'entrepôt oublié.
--
-- La plateforme publie désormais `silver_erp_ye.stock_snapshot` : une photo
-- quotidienne du stock physique du site, une ligne par article × entrepôt ×
-- emplacement, l'entité juridique et les lignes supprimées déjà filtrées.
--
-- **Une copie fidèle**, comme les quatre autres tables du miroir : les colonnes
-- gardent les noms de l'ERP, et la traduction en vocabulaire de campagne reste
-- dans `inventory.ingest.erp`, exécutée à l'import. Deux vocabulaires finissent
-- toujours par diverger ; il n'y en a qu'un.
--
-- **La date fait partie de la clé.** La source est partitionnée par jour et le
-- job en copie une tranche ; sans la date, deux exécutions le même jour
-- s'écraseraient l'une l'autre — ce qui est voulu — mais deux jours distincts
-- entreraient en conflit sur la même référence. L'application ne lit de toute
-- façon que la date la plus récente : une campagne compare son comptage à *un*
-- état du système à *un* instant, jamais à un stock additionné sur trois mois.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS erp_stock_snapshot (
    item_id         TEXT NOT NULL,
    entrepot        TEXT NOT NULL,
    -- L'emplacement peut être vide : tout le stock n'est pas rangé sous WMS.
    -- Vide plutôt que NULL, pour qu'il entre dans la clé primaire.
    emplacement     TEXT NOT NULL DEFAULT '',

    -- NUMERIC et non DOUBLE PRECISION : cette quantité est comparée à un
    -- comptage et sa différence est valorisée. Un arrondi binaire y produirait
    -- des écarts d'un centième d'unité sur des milliers de lignes, c'est-à-dire
    -- une liste d'exceptions que personne ne peut traiter.
    stock_physique  NUMERIC(20, 6) NOT NULL DEFAULT 0,
    unite           TEXT NOT NULL DEFAULT 'PCE',
    snapshot_date   DATE NOT NULL,

    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (snapshot_date, item_id, entrepot, emplacement)
);

-- La lecture applicative résout d'abord la date maximale, puis lit les lignes
-- de ce seul jour : c'est cet ordre-là que l'index sert.
CREATE INDEX IF NOT EXISTS erp_stock_snapshot_day_idx
    ON erp_stock_snapshot (snapshot_date DESC, item_id);

-- Même raison qu'en migration 006 : la table appartient au service principal de
-- l'application, qui vient de la créer, et le job de synchronisation tourne sous
-- une autre identité. Sans ce grant il ne pourrait pas y écrire, et seul le
-- propriétaire peut l'accorder.
GRANT SELECT, INSERT, DELETE, TRUNCATE ON erp_stock_snapshot TO PUBLIC;
