-- =============================================================================
-- 033 — Le portefeuille : quelles références sont à qui
-- -----------------------------------------------------------------------------
-- Une campagne porte quatre à cinq cents références, et l'analyse des écarts se
-- répartit entre plusieurs personnes. Jusqu'ici chacune voyait tout : sur la
-- vue Écarts, sur le stock ERP, sur l'écart backflush, la première chose à
-- faire était de retrouver les siennes dans la liste de tout le monde.
--
-- L'application savait déjà répartir des **emplacements** — c'est « mon
-- périmètre », porté par les gestionnaires. Elle ne savait pas répartir des
-- **articles**, et ce n'est pas la même découpe : un acheteur suit ses
-- références partout où elles sont, quel que soit l'entrepôt qui les range.
--
-- Un propriétaire par référence
-- -----------------------------
-- La clé est (campagne, article), et l'identité est une colonne. Deux personnes
-- pourraient se partager une référence — il aurait suffi de mettre l'identité
-- dans la clé — et ce n'est pas ce qui est retenu : « à qui est cette
-- référence » doit avoir une réponse, sans quoi le tableau d'attribution se lit
-- à deux endroits et se corrige à trois. Le jour où le partage sera un besoin
-- réel, il méritera sa propre forme plutôt qu'une clé élargie par précaution.
--
-- L'identité est une **adresse e-mail**, celle que l'authentification transmet,
-- et c'est la même qui résout « mon périmètre » chez les gestionnaires. Elle
-- n'est pas une habilitation : un portefeuille filtre l'affichage, il n'interdit
-- rien. Chacun garde le droit d'agir sur toutes les références, comme il garde
-- le droit d'agir hors de son périmètre.
--
-- Elle n'est pas une clé étrangère vers `manager` non plus, et délibérément :
-- une référence peut être suivie par quelqu'un qui ne pilote aucun emplacement
-- — un acheteur, un contrôleur de gestion — et lier les deux notions obligerait
-- à inventer un poste de gestionnaire pour lui donner des articles.
--
-- Rattachée à la campagne, comme tout le reste : le personnel change d'un
-- trimestre à l'autre, et la duplication d'une campagne emporte ses
-- portefeuilles comme elle emporte ses gestionnaires.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS item_portfolio (
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number  TEXT        NOT NULL,
    -- L'adresse transmise par l'authentification, rangée en minuscules par
    -- l'application : un annuaire qui écrit « Prenom.Nom@ » un jour et
    -- « prenom.nom@ » le lendemain ne doit pas produire deux portefeuilles.
    actor        TEXT        NOT NULL,
    updated_by   TEXT        NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, item_number)
);

-- La question posée à chaque affichage filtré : « quelles sont les miennes ? ».
-- Sans cet index, elle balaie la table entière à chaque ouverture d'écran.
CREATE INDEX IF NOT EXISTS item_portfolio_actor_idx
    ON item_portfolio (campaign_id, actor);
