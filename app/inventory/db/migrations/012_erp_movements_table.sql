-- =============================================================================
-- 012 — Le miroir des mouvements prend la forme de sa source
-- -----------------------------------------------------------------------------
-- La plateforme publie désormais `silver_erp_ye.mouvements` : une ligne par
-- référence et par jour, une colonne par flux — réception, expédition,
-- production, consommation théorique, consommation réelle, rebut. Tout ce que
-- l'application faisait elle-même contre trois tables bronze est fait en amont :
-- l'entité juridique, l'exclusion des lignes supprimées, la reconnaissance du
-- rebut à son emplacement, et la déduplication de la production d'un parent sur
-- ses composants.
--
-- Deux conséquences, et la seconde est la plus utile.
--
-- **Le miroir devient une copie fidèle.** La table de la migration 011 était un
-- agrégat maison — trois natures empilées sous une colonne `kind`, une quantité
-- unique — parce que trois requêtes différentes y convergeaient. Il n'y a plus
-- qu'une source, donc plus de raison de la reformater : le miroir porte les
-- colonnes de l'ERP, comme les trois autres tables du miroir. Une copie qui
-- traduit est un second vocabulaire à tenir à jour, et il dérive.
--
-- **La table vit dans le schéma du référentiel.** Elle est donc derrière le
-- grant que l'application a déjà, là où les tables bronze demandaient un second
-- `USE CATALOG` sur un autre catalogue, accordé par un autre propriétaire.
--
-- La table de 011 est supprimée plutôt que migrée : c'est un miroir, entièrement
-- reconstruit à chaque synchronisation, et transposer un agrégat vers un autre
-- coûterait plus que de le relire.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

DROP TABLE IF EXISTS erp_mouvement_stock;

CREATE TABLE IF NOT EXISTS erp_mouvements (
    reference       TEXT NOT NULL,
    date_mouvement  DATE NOT NULL,

    -- NUMERIC et non DOUBLE PRECISION : ces quantités entrent dans un stock
    -- attendu, et un arrondi binaire s'y accumule sur une période entière.
    --
    -- Les signes sont ceux de l'ERP, la copie ne réinterprète rien : une
    -- expédition négative est un retour, la consommation et le rebut sortent du
    -- stock donc sont négatifs. C'est la lecture applicative qui prend la valeur
    -- absolue, puisque le sens appartient à l'étape.
    reception       NUMERIC(20, 6) NOT NULL DEFAULT 0,
    expedition      NUMERIC(20, 6) NOT NULL DEFAULT 0,
    production      NUMERIC(20, 6) NOT NULL DEFAULT 0,
    conso_theorique NUMERIC(20, 6) NOT NULL DEFAULT 0,
    -- Consommation réelle : la comparaison ne s'en sert pas — c'est le théorique
    -- qui entre dans le stock attendu — mais elle est copiée avec le reste, la
    -- source la publiant sur la même ligne. L'écart théorique/réel est une
    -- question qu'on posera sans avoir à retoucher au miroir.
    consommation    NUMERIC(20, 6) NOT NULL DEFAULT 0,
    rebut           NUMERIC(20, 6) NOT NULL DEFAULT 0,

    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (reference, date_mouvement)
);

-- La lecture applicative découpe toujours sur la période puis regroupe par
-- référence : c'est cet ordre-là que l'index sert.
CREATE INDEX IF NOT EXISTS erp_mouvements_window_idx
    ON erp_mouvements (date_mouvement, reference);

-- Même raison qu'en migration 006 : la table appartient au service principal de
-- l'application, qui vient de la créer, et le job de synchronisation tourne sous
-- une autre identité. Sans ce grant il ne pourrait pas y écrire, et seul le
-- propriétaire peut l'accorder.
GRANT SELECT, INSERT, DELETE, TRUNCATE ON erp_mouvements TO PUBLIC;
