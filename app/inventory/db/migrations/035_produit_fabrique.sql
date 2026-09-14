-- =============================================================================
-- 035 — Le produit fabriqué auquel une référence se rattache
-- -----------------------------------------------------------------------------
-- Une troisième découpe, et elle ne recouvre aucune des deux autres. Le
-- **périmètre** répartit des emplacements entre gestionnaires, le
-- **portefeuille** répartit des articles entre personnes ; celle-ci rattache un
-- article à ce que l'usine en fait.
--
-- Ce qu'elle fait voir et que rien d'autre ne montre : deux références du même
-- produit fabriqué dont les écarts se compensent à peu près ne sont pas deux
-- anomalies indépendantes. C'est la signature d'une inversion au comptage — un
-- plus ici, un moins là, sur deux pièces qui se ressemblent et qui voisinent sur
-- le même assemblage. Ni la catégorie, ni le programme, ni l'emplacement ne
-- rapprochent ces deux lignes.
--
-- « Programme » existe déjà et répond à une autre question : pour quel marché la
-- pièce est produite, pas de quel assemblage elle fait partie.
--
-- Une table à part, et pour la même raison que les portefeuilles
-- --------------------------------------------------------------
-- Sa place naturelle est une colonne du référentiel articles, et c'est là
-- qu'elle ira. Mais `item` **gèle à l'entrée en comptage** : sur les campagnes
-- déjà gelées — celles précisément qu'on analyse aujourd'hui — une colonne du
-- référentiel serait arrivée trop tard pour servir. La table à part se charge
-- quand on veut, y compris en pleine analyse.
--
-- Une référence, un produit
-- -------------------------
-- La clé est (campagne, article) : c'est ce que la nomenclature décrit, et
-- recharger une référence la déplace plutôt que de l'ajouter à un second
-- produit. Là où le portefeuille a dû s'élargir — plusieurs personnes suivent
-- légitimement la même référence — ce rattachement-ci n'a pas de raison de le
-- faire, et une clé large ferait compter deux fois le même écart dans deux
-- produits.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS item_product (
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number  TEXT        NOT NULL,
    -- Normalisé comme toute clé métier : « M3 GEN2 » et « m3  gen2 » désignent
    -- le même produit, et deux graphies en feraient deux valeurs à cocher dans
    -- une liste de filtre qui n'en attend qu'une.
    product      TEXT        NOT NULL,
    updated_by   TEXT        NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, item_number)
);

-- « Toutes les références de ce produit », posée par le filtre de la vue Écarts
-- et par le contexte envoyé au modèle. Sans cet index elle balaie la table.
CREATE INDEX IF NOT EXISTS item_product_product_idx
    ON item_product (campaign_id, product);
