-- =============================================================================
-- 004 — Quantités négatives, autorisées zone par zone
-- -----------------------------------------------------------------------------
-- Une quantité comptée négative n'a pas de sens physique : on ne trouve pas
-- moins vingt vis dans un bac. C'est donc une faute de frappe dans l'immense
-- majorité des cas, et la refuser à la saisie est ce qui évite qu'un « -150 »
-- tapé au lieu de « 150 » ne traverse toute la chaîne jusqu'à l'écart.
--
-- Il reste des feuilles où elle est légitime : une feuille de correction, un
-- retour à retrancher d'un comptage déjà posté, un en-cours consommé qu'on
-- déduit. Le drapeau est donc porté par la zone — les deux passages d'une même
-- aire doivent obéir à la même règle, sans quoi l'arbitrage compare des choses
-- qui n'ont pas les mêmes bornes.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE zone ADD COLUMN IF NOT EXISTS allow_negative BOOLEAN NOT NULL DEFAULT false;
