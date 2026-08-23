-- =============================================================================
-- 014 — Un propriétaire aux campagnes qui n'en ont pas
-- -----------------------------------------------------------------------------
-- Depuis que l'écriture suppose d'être propriétaire ou gestionnaire déclaré,
-- `created_by` n'est plus un champ d'information : c'est ce qui décide qui peut
-- modifier la campagne. Or les campagnes créées avant cette règle ont pu être
-- écrites sans identité — un `created_by` vide.
--
-- Vide ne fait de personne le propriétaire, et c'est délibéré : sans quoi la
-- première personne à ouvrir l'écran s'en emparerait. Mais la conséquence, sur
-- ces campagnes-là, est qu'**elles ne sont modifiables par personne** — pas même
-- par celui qui les a créées. Elles se consultent, s'exportent, et rien d'autre.
--
-- On leur affecte donc un propriétaire nommé. Ce n'est pas une valeur par
-- défaut à retenir pour la suite : les campagnes créées depuis portent l'identité
-- de leur auteur, et cette migration ne les touche pas.
--
-- Idempotent : rejouable sans effet de bord — la deuxième exécution ne trouve
-- plus aucune ligne à corriger.
-- =============================================================================

SET search_path TO inventory, public;

UPDATE campaign
   SET created_by = 'younes.elhachi1@emotors.com',
       updated_by = 'migration:014'
 WHERE coalesce(btrim(created_by), '') = '';
