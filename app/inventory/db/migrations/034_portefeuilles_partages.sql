-- =============================================================================
-- 034 — Une référence peut être suivie par plusieurs personnes
-- -----------------------------------------------------------------------------
-- La 033 posait un propriétaire par référence : l'identité était une colonne, et
-- la clé primaire `(campagne, article)`. C'était le choix prudent — « à qui est
-- cette référence » avait une réponse et une seule — et il ne correspond pas à
-- l'organisation réelle : une même référence est suivie par plusieurs personnes,
-- un acheteur et un contrôleur de gestion, deux acheteurs sur deux programmes.
--
-- L'identité entre donc dans la clé. Rien d'autre ne change : ni les colonnes,
-- ni l'index, ni le sens de `actor`.
--
-- Aucune donnée ne se perd et aucune ne se déplace
-- ------------------------------------------------
-- Les lignes déjà posées sont uniques sur `(campagne, article)`, donc a fortiori
-- sur `(campagne, article, identité)` : l'élargissement d'une clé primaire ne
-- rejette jamais ce qui tenait sous l'ancienne. Chaque attribution existante
-- devient simplement l'une des personnes possibles de sa référence.
--
-- Ce qui change en revanche, c'est l'écriture : charger `P-100 → boris@` ne
-- remplace plus silencieusement Anne par la grâce d'un `ON CONFLICT DO UPDATE`.
-- Le dépôt pose désormais la liste des propriétaires **des références citées**,
-- et c'est le fichier qui fait foi pour elles — voir `PortfolioRepository.upsert`.
--
-- Idempotent : rejouable sans effet de bord. La clé est lue avant d'être
-- touchée, si bien qu'un second passage ne fait rien plutôt que de détruire et
-- reconstruire un index pour rien.
-- =============================================================================

SET search_path TO inventory, public;

DO $$
DECLARE
    colonnes text;
BEGIN
    IF to_regclass('inventory.item_portfolio') IS NULL THEN
        RETURN;
    END IF;

    SELECT string_agg(a.attname, ',' ORDER BY k.ord)
      INTO colonnes
      FROM pg_constraint c
      JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
      JOIN pg_attribute a
        ON a.attrelid = c.conrelid AND a.attnum = k.attnum
     WHERE c.conrelid = 'inventory.item_portfolio'::regclass
       AND c.contype = 'p';

    IF colonnes IS DISTINCT FROM 'campaign_id,item_number,actor' THEN
        ALTER TABLE item_portfolio DROP CONSTRAINT IF EXISTS item_portfolio_pkey;
        ALTER TABLE item_portfolio
            ADD CONSTRAINT item_portfolio_pkey
            PRIMARY KEY (campaign_id, item_number, actor);
    END IF;
END $$;

-- « Quelles sont les miennes ? », posée à chaque affichage filtré. Inchangée par
-- la 034, et rappelée ici parce que c'est elle qui porte le filtre : la clé
-- primaire commence par la campagne et l'article, et ne sait pas répondre.
CREATE INDEX IF NOT EXISTS item_portfolio_actor_idx
    ON item_portfolio (campaign_id, actor);
