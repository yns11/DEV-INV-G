-- =============================================================================
-- 016 — Le scan d'une feuille seule devient un travail suivi, lui aussi
-- -----------------------------------------------------------------------------
-- La pile de cent feuilles était passée en travail suivi (migration 015) ; le
-- scan d'**une** feuille, lui, était resté dans la requête HTTP du chargement.
-- Il est plus court, mais pas court : rendu des pages, un appel au modèle de
-- vision, écriture des lignes — de dix secondes à plus d'une minute selon la
-- longueur de la liste pré-imprimée et l'état de l'endpoint. Pendant tout ce
-- temps, le bouton disait « Lecture en cours… » et rien d'autre ne bougeait :
-- impossible de distinguer un travail qui avance d'un appel qui a calé.
--
-- Le même travail, la même table, le même écran de suivi. `sheet_id` est ce qui
-- les sépare : renseigné, le travail lit cette feuille-là ; nul, c'est une pile
-- multi-feuilles. Une colonne plutôt qu'une seconde table — les deux chemins ont
-- le même cycle de vie, le même avancement et le même rapport, et les séparer
-- aurait dupliqué le suivi, le nettoyage au démarrage et l'écran.
--
-- ON DELETE CASCADE : une feuille supprimée emporte le suivi de son scan. La
-- pièce justificative, elle, reste au volume — c'est elle qui justifie les
-- quantités, pas la ligne d'avancement.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE scan_job
    ADD COLUMN IF NOT EXISTS sheet_id UUID
        REFERENCES count_sheet (id) ON DELETE CASCADE;

COMMENT ON COLUMN scan_job.sheet_id IS
    'Feuille visée par un scan unitaire. NULL pour une pile multi-feuilles.';

-- L'écran d'une feuille cherche « le dernier scan de CETTE feuille » à
-- l'ouverture, pour retrouver un travail encore en cours après un rafraîchissement.
CREATE INDEX IF NOT EXISTS scan_job_sheet_idx
    ON scan_job (sheet_id, created_at DESC) WHERE sheet_id IS NOT NULL;
