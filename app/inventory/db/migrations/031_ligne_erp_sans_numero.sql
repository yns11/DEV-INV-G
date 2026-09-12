-- =============================================================================
-- 031 — Ce qui identifie une ligne ERP, ce sont les coordonnées de ce qu'elle
--       compte — jamais son numéro de ligne
-- -----------------------------------------------------------------------------
-- La migration 025 avait fait du couple « journal + numéro de ligne » la clé
-- d'une ligne de journal ERP. C'était le choix naturel : l'ERP numérote ses
-- lignes, et deux palettes du même article au même endroit sont deux lignes
-- légitimes que seul ce numéro distingue.
--
-- Quatre extractions réelles du même jour ont montré que ce numéro n'est pas
-- celui de l'ERP. Sur les journaux comptés **par étiquette**, l'export descend
-- « 1, -1, -2, … -79 », et un autre journal porte un « 13,5 » : la chaîne
-- d'extraction les invente pour départager des lignes que l'ERP numérote
-- pareil. Ils dépendent donc de l'**ordre des lignes** — une étiquette saisie
-- entre deux extractions décale tout ce qui suit, et la même palette physique
-- change de clé d'un quart d'heure à l'autre. Une clé qui bouge ne désigne
-- rien. Le jour de l'inventaire, avec une extraction toutes les quinze minutes
-- sur des journaux encore en cours de remplissage, ce n'est pas un risque mais
-- une certitude.
--
-- Comme clé d'unicité, ce numéro manquait des deux côtés à la fois. Trop
-- strict : « 13,5 » ne se lit pas en entier, et le lecteur refusait la ligne —
-- une quantité comptée perdue pour une colonne technique. Trop lâche : la
-- colonne est nullable, le contrôle de doublon exemptait les lignes qui ne la
-- portent pas, et cet index tenait deux NULL pour distincts. Un vrai doublon
-- passait donc sans un mot.
--
-- La clé devient ce que la ligne **désigne**, et qui ne dépend d'aucun ordre :
-- le journal, le site, l'entrepôt, l'emplacement, l'étiquette et l'article.
-- Vérifiée unique sur les quatre extractions — 614, 637, 1 061 et 1 075
-- lignes, aucun doublon — et stable de l'une à l'autre.
--
-- Une ligne sans étiquette n'est pas une exception à prévoir : un journal en
-- quantité (INVV) n'en porte pas, et la clé y devient « une ligne par article
-- et par emplacement », ce qui est exactement son grain. C'est pourquoi
-- l'index ne porte aucune clause `WHERE` : il n'y a rien à excepter.
--
-- Et la garantie est cette fois entière. Les six colonnes sont `NOT NULL` —
-- une étiquette absente vaut `''`, jamais NULL — là où l'ancienne clé reposait
-- sur une colonne nullable et laissait donc passer autant de doublons que
-- l'export omettait de numéros.
--
-- La colonne `erp_line_number` disparaît plutôt que de rester vide. Une
-- colonne que plus rien n'écrit finit par être lue comme si elle voulait dire
-- quelque chose, et celle-ci porterait des « -79 » qui ne désignent rien dans
-- l'ERP. Si le jour vient où l'extraction transmet le vrai `RecId`, il méritera
-- sa propre colonne et son propre nom.
--
-- **Rejouer la 025 seule n'est plus possible**, et c'est assumé : elle crée un
-- index sur une colonne que celle-ci supprime. La séquence complète, elle, se
-- rejoue sans difficulté — 025 crée la colonne et son index, 031 les retire —
-- et c'est la seule propriété dont l'application dépende. Même situation, même
-- choix qu'à la migration 029.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- L'ancien index part avant la colonne qu'il porte : l'inverse laisserait
-- Postgres refuser la suppression.
DROP INDEX IF EXISTS erp_journal_line_uq;

ALTER TABLE erp_journal_line
    DROP COLUMN IF EXISTS erp_line_number;

-- Le doublon devient impossible plutôt que détecté après coup par un contrôle
-- qu'on pourrait oublier de brancher. `erp_journal_id` porte déjà le journal et
-- la campagne — voir sa clé étrangère composite.
CREATE UNIQUE INDEX IF NOT EXISTS erp_journal_line_uq
    ON erp_journal_line (
        erp_journal_id, site_id, warehouse_id, location_id, label_id, item_number
    );
