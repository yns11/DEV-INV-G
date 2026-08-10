-- =============================================================================
-- 003 — Gestionnaires et périmètres
-- -----------------------------------------------------------------------------
-- Un inventaire est piloté à plusieurs : chacun suit ses entrepôts et ses zones.
-- Sans périmètre, tout le monde voit tout, et sur un site à quarante zones la
-- liste devient illisible le jour J.
--
-- Trois objets, et seulement trois :
--
--   manager            le gestionnaire, son libellé et l'identité qui le
--                      désigne (l'e-mail transmis par la plateforme) ;
--   warehouse_manager  l'affectation d'un entrepôt — donc de ses journaux —
--                      à un gestionnaire ;
--   zone.manager_code  l'affectation d'une zone GENERIQUE.
--
-- `warehouse_manager.warehouse_id` accepte la valeur réservée « AUTRES » :
-- elle affecte d'un coup tous les entrepôts sans affectation explicite, ce qui
-- évite d'avoir à réaffecter à la main chaque entrepôt découvert par un nouvel
-- import de stock ERP.
--
-- Aucune contrainte de clé étrangère vers `warehouse` : le référentiel des
-- entrepôts naît du stock ERP, chargé *après* la préparation, et l'on doit
-- pouvoir répartir les entrepôts connus du site avant qu'il n'existe.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS manager (
    campaign_id   UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    code          TEXT        NOT NULL,
    label         TEXT        NOT NULL DEFAULT '',
    -- Identité transmise par le proxy d'authentification (e-mail, minuscules).
    -- Vide tant que personne n'occupe le poste : le mode focus répond alors
    -- « aucun objet ne vous est affecté » plutôt qu'une liste vide ambiguë.
    actor         TEXT        NOT NULL DEFAULT '',
    active        BOOLEAN     NOT NULL DEFAULT true,
    display_order INTEGER     NOT NULL DEFAULT 0,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, code)
);

-- Le mode focus résout « qui suis-je ? » à chaque requête filtrée : l'index
-- rend cette résolution constante quel que soit le nombre de campagnes.
CREATE INDEX IF NOT EXISTS manager_actor_idx
    ON manager (campaign_id, actor) WHERE actor <> '';

CREATE TABLE IF NOT EXISTS warehouse_manager (
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    warehouse_id TEXT        NOT NULL,
    manager_code TEXT        NOT NULL,
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, warehouse_id)
);
CREATE INDEX IF NOT EXISTS warehouse_manager_code_idx
    ON warehouse_manager (campaign_id, manager_code);

ALTER TABLE zone ADD COLUMN IF NOT EXISTS manager_code TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS zone_manager_idx
    ON zone (campaign_id, manager_code) WHERE deleted_at IS NULL;
