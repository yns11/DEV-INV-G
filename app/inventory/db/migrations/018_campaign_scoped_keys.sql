-- =============================================================================
-- 018 — Un enfant ne peut pas appartenir à la campagne d'un autre
-- -----------------------------------------------------------------------------
-- La permission d'écriture est vérifiée sur la campagne de l'**URL**, tandis que
-- les identifiants des objets arrivent dans le **corps** de la requête. Plusieurs
-- écritures recherchaient ensuite l'objet par son seul UUID : un gestionnaire
-- habilité sur la campagne A pouvait donc modifier une ligne, une feuille, une
-- zone ou un journal de la campagne B en connaissant son identifiant, et la
-- garde n'y voyait rien puisqu'elle avait bien vu A.
--
-- Le correctif applicatif porte chaque écriture par sa campagne
-- (`WHERE campaign_id = ? AND id = ?`). Cette migration ajoute la garantie
-- structurelle qui reste vraie même si une requête future l'oublie : une ligne
-- de journal ne peut pointer que vers un journal **de sa propre campagne**, et
-- ainsi de suite. Une clé étrangère composite le dit à Postgres, qui n'oublie
-- jamais.
--
-- **Pourquoi une clé unique redondante.** Une FK composite exige un index unique
-- sur les colonnes visées. `(id, campaign_id)` est redondant avec la clé
-- primaire `(id)` — c'est le prix de la garantie, et il est faible : l'index
-- sert aussi les lectures « tout ce que porte cette campagne ».
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- --- Les cibles : chaque parent devient adressable par (id, campagne) --------

ALTER TABLE count_journal
    DROP CONSTRAINT IF EXISTS count_journal_id_campaign_key,
    ADD  CONSTRAINT count_journal_id_campaign_key UNIQUE (id, campaign_id);

ALTER TABLE zone
    DROP CONSTRAINT IF EXISTS zone_id_campaign_key,
    ADD  CONSTRAINT zone_id_campaign_key UNIQUE (id, campaign_id);

ALTER TABLE count_sheet
    DROP CONSTRAINT IF EXISTS count_sheet_id_campaign_key,
    ADD  CONSTRAINT count_sheet_id_campaign_key UNIQUE (id, campaign_id);

-- --- Les enfants : le lien porte la campagne --------------------------------
--
-- Les anciennes clés simples sont remplacées, pas doublées : garder les deux
-- laisserait croire que la garantie tient alors que seule la plus faible
-- s'appliquerait à une colonne nulle.

ALTER TABLE count_journal_line
    DROP CONSTRAINT IF EXISTS count_journal_line_journal_id_fkey,
    DROP CONSTRAINT IF EXISTS count_journal_line_journal_campaign_fkey,
    ADD  CONSTRAINT count_journal_line_journal_campaign_fkey
         FOREIGN KEY (journal_id, campaign_id)
         REFERENCES count_journal (id, campaign_id) ON DELETE CASCADE;

ALTER TABLE count_sheet
    DROP CONSTRAINT IF EXISTS count_sheet_zone_id_fkey,
    DROP CONSTRAINT IF EXISTS count_sheet_zone_campaign_fkey,
    ADD  CONSTRAINT count_sheet_zone_campaign_fkey
         FOREIGN KEY (zone_id, campaign_id)
         REFERENCES zone (id, campaign_id) ON DELETE CASCADE;

ALTER TABLE count_sheet_line
    DROP CONSTRAINT IF EXISTS count_sheet_line_sheet_id_fkey,
    DROP CONSTRAINT IF EXISTS count_sheet_line_sheet_campaign_fkey,
    ADD  CONSTRAINT count_sheet_line_sheet_campaign_fkey
         FOREIGN KEY (sheet_id, campaign_id)
         REFERENCES count_sheet (id, campaign_id) ON DELETE CASCADE;

ALTER TABLE arbitration
    DROP CONSTRAINT IF EXISTS arbitration_zone_id_fkey,
    DROP CONSTRAINT IF EXISTS arbitration_zone_campaign_fkey,
    ADD  CONSTRAINT arbitration_zone_campaign_fkey
         FOREIGN KEY (zone_id, campaign_id)
         REFERENCES zone (id, campaign_id) ON DELETE CASCADE;
