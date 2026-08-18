-- =============================================================================
-- 008 — Écart backflush figé sur la campagne
-- -----------------------------------------------------------------------------
-- En production, la sortie de stock des composants n'est pas saisie ligne à
-- ligne : elle est déduite de la déclaration de production, selon la
-- nomenclature. L'écart backflush mesure exactement l'hypothèse que fait cette
-- déduction — théorique moins réel — et son signe dit dans quel sens le stock
-- système a dérivé. Un écart backflush positif sur un composant prédit un écart
-- d'inventaire négatif du même ordre : c'est ce qui permet de séparer, dans un
-- écart constaté, ce que la production explique de ce qui reste à expliquer.
--
-- Pourquoi figer plutôt que relire à chaque affichage
-- ---------------------------------------------------
-- La table gold `fact_ecart_backflush` est reconstruite intégralement chaque
-- nuit : une correction de nomenclature, un mouvement saisi en retard ou une
-- mise à jour de coût standard change l'écart d'une semaine *passée*. Relire à
-- la volée ferait qu'une même campagne consultée à quinze jours d'intervalle
-- donnerait deux chiffres, et qu'un écart validé par un contrôleur deviendrait
-- infalsifiable. La valeur est donc lue une fois et écrite ici.
--
-- Les bornes sont stockées *avec* la valeur, et non déduites de la campagne.
-- Sans elles le chiffre n'est plus interprétable : « 42 » ne veut rien dire si
-- l'on ne sait pas sur quelles semaines. Les deux horodatages répondent à deux
-- questions différentes — la fraîcheur de la source au moment de la lecture, et
-- l'instant de la lecture — et il faut les deux pour rejouer un écart.
--
-- Absence de ligne = écart nul. Un composant que la production n'a pas touché
-- sur la période n'a pas d'écart backflush, et lui en inventer un serait pire
-- que de ne rien dire.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS campaign_backflush (
    campaign_id         UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number         TEXT NOT NULL,

    -- Bornes effectivement appliquées, en lundis ISO. Début inclus, fin exclue.
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,

    unit                TEXT NOT NULL DEFAULT 'PCE',
    -- NUMERIC et non DOUBLE PRECISION : cet écart entre dans un calcul de
    -- valorisation, et un arrondi binaire s'y voit à l'euro près.
    net_qty             NUMERIC(20, 6) NOT NULL DEFAULT 0,
    -- Les deux composantes du net. Elles ne servent pas au recalcul — c'est le
    -- net qui y entre — mais elles disent de quoi il est fait : 40 de
    -- non-consommation contre 38 de surconsommation ne se lit pas comme 2.
    under_consumed_qty  NUMERIC(20, 6) NOT NULL DEFAULT 0,
    over_consumed_qty   NUMERIC(20, 6) NOT NULL DEFAULT 0,
    theoretical_qty     NUMERIC(20, 6),
    actual_qty          NUMERIC(20, 6),
    parent_count        INTEGER,
    week_count          INTEGER,

    source_loaded_at    TIMESTAMPTZ,
    refreshed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    import_batch        UUID,

    PRIMARY KEY (campaign_id, item_number)
);

CREATE INDEX IF NOT EXISTS campaign_backflush_campaign_idx
    ON campaign_backflush (campaign_id);


-- -----------------------------------------------------------------------------
-- Miroir local de la table de faits
-- -----------------------------------------------------------------------------
-- Même raison que pour les articles et les nomenclatures (migration 005) : lire
-- Unity Catalog depuis l'application suppose un grant que seul un propriétaire
-- de catalogue peut accorder. Sans ce miroir, le bouton « Lire depuis l'ERP »
-- de la vue Backflush serait mort partout où l'application tourne en mode
-- miroir — c'est-à-dire là où le grant manquait, donc exactement là où on en a
-- besoin.
--
-- À la maille semaine, comme la source : les bornes sont choisies campagne par
-- campagne, et un job ne peut pas pré-agréger sur une période qu'il ignore.
-- Seules les colonnes que l'application lit sont copiées ; la table gold en
-- porte une vingtaine d'autres qui ne serviraient à rien ici.
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS erp_ecart_backflush (
    semaine_debut     DATE NOT NULL,
    parent_itemid     TEXT NOT NULL,
    child_itemid      TEXT NOT NULL,
    child_name        TEXT,
    child_unite       TEXT,
    qty_parent_produite NUMERIC(20, 6),
    conso_theorique   NUMERIC(20, 6),
    conso_reelle      NUMERIC(20, 6),
    ecart_brut        NUMERIC(20, 6),
    loaded_at         TIMESTAMPTZ,
    synced_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS erp_ecart_backflush_semaine_idx
    ON erp_ecart_backflush (semaine_debut);
CREATE INDEX IF NOT EXISTS erp_ecart_backflush_child_idx
    ON erp_ecart_backflush (child_itemid);
