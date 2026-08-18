-- =============================================================================
-- 009 — Réconciliation de flux entre deux campagnes
-- -----------------------------------------------------------------------------
-- Deux inventaires successifs encadrent une période. Entre les deux, le stock
-- d'un article n'a pas bougé au hasard : il a reçu, produit, expédié, consommé
-- et rebuté des quantités qu'on sait chiffrer. La question que cette table sert
-- à poser est donc celle-ci — en partant du stock *compté* du premier
-- inventaire et en appliquant les flux de la période, retombe-t-on sur le stock
-- *compté* du second ?
--
--   stock attendu = compté(campagne initiale)
--                 + réceptions          (chargées)
--                 + production parent   (lue dans le backflush, dédoublonnée)
--                 − expéditions         (chargées)
--                 − consommation théo.  (lue dans le backflush)
--                 − rebuts              (chargés, étape facultative)
--
-- Ce que l'écart entre attendu et compté mesure, c'est ce qu'aucun de ces flux
-- n'explique : ni la production, ni les mouvements saisis. C'est un contrôle de
-- cohérence sur la période, pas un inventaire de plus.
--
-- Trois tables plutôt qu'une
-- --------------------------
-- La *série* (`stock_flow_run`) porte le couple de campagnes et les bornes ;
-- elle est l'unité qu'on relance. Les *saisies* et l'*instantané ERP* pendent
-- d'elle par son identifiant plutôt que de la campagne : changer de campagne
-- initiale change la période, donc change les chiffres, et rattacher les lignes
-- à la campagne les laisserait survivre à ce changement — un stock attendu
-- calculé sur une période avec les réceptions d'une autre.
--
-- Les quantités ERP sont figées ici pour la même raison que l'écart backflush
-- (voir migration 008) : la table gold est reconstruite chaque nuit, et un
-- rapport qu'on ne peut pas rejouer à l'identique ne se défend pas en réunion.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS stock_flow_run (
    id                   UUID PRIMARY KEY,
    -- La campagne d'arrivée : celle dont on cherche à expliquer le stock compté.
    campaign_id          UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    -- La campagne de départ, forcément la plus ancienne par *date d'inventaire*.
    -- Deux campagnes créées dans un ordre et comptées dans l'autre existent ;
    -- c'est la date de comptage qui ordonne la période, pas la date de saisie.
    baseline_campaign_id UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,

    -- Bornes de la période, en lundis ISO — la maille du backflush. Début
    -- inclus, fin exclue.
    period_start         DATE NOT NULL,
    period_end           DATE NOT NULL,

    -- L'étape rebut est facultative : ce drapeau distingue « pas de rebut » de
    -- « rebut non renseigné », deux lectures très différentes du même zéro.
    scrap_loaded         BOOLEAN NOT NULL DEFAULT false,

    source_loaded_at     TIMESTAMPTZ,
    erp_refreshed_at     TIMESTAMPTZ,

    created_by           TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS stock_flow_run_pair_uq
    ON stock_flow_run (campaign_id, baseline_campaign_id);

-- Les quantités saisies par l'utilisateur, une ligne par article et par nature.
CREATE TABLE IF NOT EXISTS stock_flow_input (
    run_id      UUID NOT NULL REFERENCES stock_flow_run (id) ON DELETE CASCADE,
    item_number TEXT NOT NULL,
    kind        TEXT NOT NULL
        CHECK (kind IN ('RECEIPT', 'SHIPMENT', 'SCRAP')),
    -- Toujours positive : le sens est porté par `kind`, pas par le signe. Une
    -- expédition saisie en négatif serait ajoutée au lieu d'être retranchée, et
    -- rien à l'écran ne le montrerait.
    qty         NUMERIC(20, 6) NOT NULL DEFAULT 0,
    unit        TEXT NOT NULL DEFAULT 'PCE',

    PRIMARY KEY (run_id, item_number, kind)
);

-- L'instantané des deux mesures lues dans le backflush, figé avec la série.
CREATE TABLE IF NOT EXISTS stock_flow_erp (
    run_id           UUID NOT NULL REFERENCES stock_flow_run (id) ON DELETE CASCADE,
    item_number      TEXT NOT NULL,
    -- Production de l'article en tant que *parent*, dédoublonnée par semaine :
    -- la table de faits répète cette quantité sur chaque ligne composant, et la
    -- sommer telle quelle la multiplierait par le nombre de composants.
    produced_qty     NUMERIC(20, 6) NOT NULL DEFAULT 0,
    -- Consommation théorique de l'article en tant que *composant*.
    consumed_qty     NUMERIC(20, 6) NOT NULL DEFAULT 0,

    PRIMARY KEY (run_id, item_number)
);
