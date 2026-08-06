-- =============================================================================
-- 002 — Nombre de comptages et saisie libre, par zone
-- -----------------------------------------------------------------------------
-- Le nombre de passages était une propriété de la campagne (`config.generic_passes`),
-- ce qui obligeait à compter deux fois *toutes* les zones ou aucune. Il devient
-- une propriété de la zone : une zone de métrologie à trois références n'a pas
-- besoin du même dispositif qu'un bord de ligne.
--
-- `free_entry` distingue une feuille volontairement vide — le compteur écrit ce
-- qu'il trouve — d'une feuille dont on a oublié de préparer la liste d'articles.
-- Sans ce drapeau, les deux se ressemblent exactement, et les contrôles de
-- préparation signalaient les premières comme un défaut.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- Défaut à 2 : le double comptage est la règle, le comptage unique l'exception
-- qu'on assume explicitement, zone par zone.
ALTER TABLE zone ADD COLUMN IF NOT EXISTS passes     SMALLINT NOT NULL DEFAULT 2;
ALTER TABLE zone ADD COLUMN IF NOT EXISTS free_entry BOOLEAN  NOT NULL DEFAULT false;

-- `ADD CONSTRAINT` n'accepte pas `IF NOT EXISTS` : on retire puis on recrée,
-- ce qui rend l'ensemble rejouable sans erreur.
ALTER TABLE zone DROP CONSTRAINT IF EXISTS zone_passes_check;
ALTER TABLE zone ADD  CONSTRAINT zone_passes_check CHECK (passes IN (1, 2));
