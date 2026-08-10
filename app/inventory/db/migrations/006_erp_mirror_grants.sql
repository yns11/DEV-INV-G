-- =============================================================================
-- 006 — Droits d'écriture sur le miroir ERP
-- -----------------------------------------------------------------------------
-- Le miroir met deux identités face à face, et c'est voulu : l'application ne
-- peut pas lire l'ERP, le job de synchronisation le peut. Il tourne donc sous
-- une autre identité que celle de l'App — c'est la raison d'être du dispositif.
--
-- Conséquence côté Postgres : les tables du miroir appartiennent au service
-- principal de l'application, qui les a créées en migration 005, et le job s'y
-- voit refuser l'écriture. Personne ne peut accorder ce droit à sa place : dans
-- PostgreSQL, seul le propriétaire d'une table le fait, et le seul endroit où
-- l'application parle en tant que propriétaire est ici.
--
-- Portée du grant. PUBLIC, dans une base Lakebase dédiée à cette application,
-- désigne les identités qui ont déjà le droit de s'y connecter — un droit
-- accordé une par une. Il porte sur les deux tables du miroir et sur elles
-- seules : les tables de campagne, l'audit et les comptages ne sont pas
-- concernés, PostgreSQL n'accordant aucun privilège de table à PUBLIC par
-- défaut. Ce qui est ainsi exposé est une copie du référentiel articles, que
-- l'identité de synchronisation lit de toute façon dans l'ERP.
--
-- Pour restreindre à une identité nommée, une fois qu'elle est connue :
--     REVOKE ALL ON erp_base_article, erp_bom FROM PUBLIC;
--     GRANT SELECT, INSERT, DELETE, TRUNCATE
--        ON erp_base_article, erp_bom TO "prenom.nom@societe.com";
-- (à passer avec le rôle propriétaire, donc depuis une migration suivante).
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- Résoudre les noms dans le schéma. Ne donne aucun accès aux tables qu'il
-- contient : chaque table garde ses propres privilèges.
GRANT USAGE ON SCHEMA inventory TO PUBLIC;

-- TRUNCATE est nommé explicitement : il ne découle ni de DELETE ni de ALL sur
-- une table dont on n'est pas propriétaire, et la synchronisation en dépend.
GRANT SELECT, INSERT, DELETE, TRUNCATE ON erp_base_article TO PUBLIC;
GRANT SELECT, INSERT, DELETE, TRUNCATE ON erp_bom TO PUBLIC;
