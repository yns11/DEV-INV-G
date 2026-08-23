-- =============================================================================
-- 007 — Statut des versions de nomenclature
-- -----------------------------------------------------------------------------
-- L'ERP conserve toutes les versions d'une nomenclature, en vigueur ou non.
-- Jusqu'ici seules les versions actives arrivaient jusqu'ici, et les produits
-- finis dont la recette avait été retirée apparaissaient comme n'ayant aucune
-- nomenclature — une page d'alertes sur lesquelles personne ne pouvait agir,
-- puisque la structure existait bel et bien.
--
-- La campagne charge désormais toutes les versions, et le drapeau décide de ce
-- qui est éclaté. Les deux usages sont distincts et le resteront :
--
--   éclatement d'un WIP        → uniquement les liens actifs. Ajouter une
--                                quantité retirée à une quantité en vigueur
--                                ferait apparaître des composants que
--                                l'assemblage ne contient plus.
--   « a-t-il une nomenclature ? » → toutes versions confondues. Une recette
--                                retirée est une décision déjà prise, pas un
--                                trou dans le référentiel.
--
-- Défaut à `true` : une campagne chargée avant ce changement ne contenait que
-- des versions actives, et c'est exactement ce que cette valeur signifie.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE bom_link ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT true;

-- Le miroir ERP reçoit le statut tel que l'ERP l'écrit ; la traduction en
-- booléen se fait à l'import, au même endroit que pour un fichier.
ALTER TABLE erp_bom ADD COLUMN IF NOT EXISTS statut TEXT;

-- La colonne `approved` du miroir n'existe plus dans la table silver ; elle est
-- laissée en place plutôt que supprimée, pour qu'un miroir déjà synchronisé
-- reste lisible pendant le déploiement.
