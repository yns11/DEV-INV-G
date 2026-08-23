-- =============================================================================
-- 005 — Miroir local des tables ERP
-- -----------------------------------------------------------------------------
-- La lecture directe des tables silver d'Unity Catalog suppose que le service
-- principal de l'application ait USE CATALOG sur le catalogue de l'ERP. Ce
-- privilège ne s'accorde que par un propriétaire du catalogue, et il arrive
-- qu'aucun ne soit joignable dans le délai d'une campagne : l'inventaire, lui,
-- a une date.
--
-- Ces deux tables sont une copie locale, alimentée par un job planifié qui
-- tourne, lui, avec une identité qui a le droit de lire l'ERP. L'application y
-- lit ce qu'elle aurait lu dans le catalogue.
--
-- Deux principes.
--
-- **Copie brute.** Les colonnes portent les noms de l'ERP, pas ceux de
-- l'application : la traduction (groupe fonctionnel → type d'article, prix
-- ramené à l'unité, « Commun » → COMMON) reste au même endroit qu'avant, dans
-- ``inventory.ingest.erp``. Un miroir qui traduirait de son côté ferait deux
-- vocabulaires à maintenir, qui divergeraient.
--
-- **Fraîcheur portée par la donnée.** ``synced_at`` est écrit à chaque
-- synchronisation. Une campagne qui charge un référentiel de trois semaines
-- doit pouvoir le dire — c'est précisément la classe d'erreur que cette
-- application existe pour supprimer, et un miroir muet la réintroduirait.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS erp_base_article (
    item_id          TEXT PRIMARY KEY,
    item_name        TEXT,
    item_description TEXT,
    search_name      TEXT,
    name_alias       TEXT,
    categorie        TEXT,
    programme        TEXT,
    item_group_id    TEXT,
    item_group_label TEXT,
    -- NUMERIC et non DOUBLE PRECISION : un prix standard sert à valoriser un
    -- écart, et un arrondi binaire s'y voit à l'euro près sur un stock entier.
    std_cost_price   NUMERIC(18, 6),
    std_price_unit   NUMERIC(18, 6),
    std_unit         TEXT,
    synced_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Aucune clé primaire sur le lien : un parent peut légitimement lister deux
-- fois le même composant (deux positions de nomenclature). Contraindre l'unicité
-- ferait échouer la synchronisation sur une nomenclature parfaitement valide.
CREATE TABLE IF NOT EXISTS erp_bom (
    parent_itemid TEXT NOT NULL,
    child_itemid  TEXT NOT NULL,
    child_qty     NUMERIC(18, 6),
    child_unitid  TEXT,
    approved      SMALLINT,
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS erp_bom_parent_idx ON erp_bom (parent_itemid);
