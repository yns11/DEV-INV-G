-- =============================================================================
-- 011 — Miroir local des mouvements de stock
-- -----------------------------------------------------------------------------
-- Les réceptions, expéditions et rebuts se lisent maintenant dans l'ERP au lieu
-- d'être saisis à la main. Mais elles vivent dans `emotors_data_platform`,
-- c'est-à-dire dans un *autre catalogue* que le référentiel silver et que la
-- table de faits du backflush — donc derrière un second grant `USE CATALOG`,
-- accordé par un autre propriétaire.
--
-- Sans ce miroir, « Tout charger de l'ERP » serait mort partout où l'application
-- tourne en mode miroir : c'est-à-dire là où le premier grant manquait déjà, et
-- où le second a toutes les chances de manquer aussi. Le bouton s'afficherait,
-- et échouerait — exactement ce que le reste de l'application s'interdit.
--
-- Maille jour, et non période
-- ---------------------------
-- Les bornes se choisissent campagne par campagne : un job ne peut pas
-- pré-agréger sur une période qu'il ignore. La maille jour est celle des
-- requêtes du guide (« par référence et par date »), elle se retaille sur
-- n'importe quel intervalle, et elle réduit `invent_trans` — vingt millions de
-- lignes — à ce que l'application lit réellement. Copier la table brute serait
-- inutilisable ; copier un total de période serait faux dès la campagne
-- suivante.
--
-- Une table pour les trois natures, distinguées par `kind`. Elles sortent de
-- trois requêtes différentes mais ont la même forme, et trois tables auraient
-- fait diverger trois copies du même job.
--
-- Quantités **telles que l'ERP les signe** : un retour est une expédition
-- négative, un rebut sort du stock donc est négatif. Le sens appartient à
-- l'étape, et c'est la lecture applicative qui prend la valeur absolue — la
-- copie, elle, ne réinterprète rien.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS erp_mouvement_stock (
    -- RECEIPT / SHIPMENT / SCRAP — les mêmes valeurs que `FlowKind`.
    kind            TEXT NOT NULL
        CHECK (kind IN ('RECEIPT', 'SHIPMENT', 'SCRAP')),
    item_id         TEXT NOT NULL,
    mouvement_date  DATE NOT NULL,
    -- NUMERIC et non DOUBLE PRECISION : cette quantité entre dans un stock
    -- attendu, et un arrondi binaire s'y accumule sur une période entière.
    qty             NUMERIC(20, 6) NOT NULL DEFAULT 0,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (kind, item_id, mouvement_date)
);

-- La lecture applicative découpe toujours sur la période puis regroupe par
-- article : c'est cet ordre-là que l'index sert.
CREATE INDEX IF NOT EXISTS erp_mouvement_stock_window_idx
    ON erp_mouvement_stock (kind, mouvement_date, item_id);

-- Même raison qu'en migration 006 : la table appartient au service principal de
-- l'application, qui vient de la créer, et le job de synchronisation tourne sous
-- une autre identité. Sans ce grant il ne pourrait pas y écrire, et seul le
-- propriétaire peut l'accorder.
GRANT SELECT, INSERT, DELETE, TRUNCATE ON erp_mouvement_stock TO PUBLIC;
