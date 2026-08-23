-- =============================================================================
-- 020 — La trace d'audit résiste aussi à ce que les règles ne voient pas
-- -----------------------------------------------------------------------------
-- `audit_event` porte deux règles depuis la migration 001 :
--
--     CREATE RULE audit_event_no_update AS ON UPDATE TO audit_event DO INSTEAD NOTHING;
--     CREATE RULE audit_event_no_delete AS ON DELETE TO audit_event DO INSTEAD NOTHING;
--
-- Elles font ce qu'elles annoncent — un `UPDATE` et un `DELETE` directs ne
-- changent rien, vérifié contre PostgreSQL — mais une règle ne couvre pas tout
-- ce qui peut vider une table. Deux chemins restaient, et ils n'ont pas la même
-- gravité.
--
-- **TRUNCATE efface tout, sans un mot.** `TRUNCATE` n'est pas un `DELETE` : il
-- ne passe pas par la réécriture de requête, donc aucune règle ne s'y applique.
-- Mesuré sur une base réelle avant d'écrire cette migration : la table se vide,
-- la commande réussit, rien n'est journalisé. Un script de nettoyage
-- d'environnement, une remise à zéro pour reproduire un bug, un copier-coller
-- malheureux — et la piste d'audit de l'inventaire n'existe plus. C'est le seul
-- vrai trou, et c'est celui qu'un trigger `BEFORE TRUNCATE` ferme : les
-- triggers de niveau instruction, eux, voient `TRUNCATE`.
--
-- **La suppression en cascade d'une campagne échouait déjà, mais salement.**
-- `audit_event.campaign_id` était déclarée `ON DELETE CASCADE`. En pratique la
-- règle `DO INSTEAD NOTHING` empêche l'intégrité référentielle de faire son
-- travail, et PostgreSQL refuse la suppression avec :
--
--     ERROR: referential integrity query on "campaign" gave unexpected result
--     HINT:  This is most likely due to a rule having rewritten the query.
--
-- La trace était donc protégée — par accident, et avec un message qui ne dit
-- rien à qui le lit. `ON DELETE RESTRICT` énonce la même garantie franchement :
-- une campagne qui a une histoire ne se supprime pas physiquement. Ce n'est une
-- contrainte pour personne, l'application ne supprimant que logiquement
-- (`deleted_at`), et c'est exactement ce qu'on veut lire dans un message
-- d'erreur à trois heures du matin.
--
-- **Ce que cette migration ne prétend pas faire.** Le propriétaire du schéma
-- peut toujours `DROP TABLE`, désactiver un trigger ou supprimer une règle :
-- aucune protection intra-base ne tient devant qui possède l'objet. La réponse
-- à cette menace-là n'est pas ici, elle est dans l'archive Delta — une copie
-- hors de cette base, écrite à la clôture, que la campagne emporte avec elle.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- --- TRUNCATE : le seul chemin qui vidait réellement la table ---------------

CREATE OR REPLACE FUNCTION audit_event_no_truncate() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'La trace d''audit ne se vide pas : elle est la seule chose qu''un '
        'contrôle puisse opposer six mois plus tard. Pour repartir d''une base '
        'propre, recréez le schéma.'
        USING ERRCODE = 'raise_exception';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_event_no_truncate ON audit_event;
CREATE TRIGGER audit_event_no_truncate
    BEFORE TRUNCATE ON audit_event
    FOR EACH STATEMENT EXECUTE FUNCTION audit_event_no_truncate();

-- --- La cascade : même refus, dit lisiblement ------------------------------

ALTER TABLE audit_event
    DROP CONSTRAINT IF EXISTS audit_event_campaign_id_fkey,
    ADD  CONSTRAINT audit_event_campaign_id_fkey
         FOREIGN KEY (campaign_id) REFERENCES campaign (id) ON DELETE RESTRICT;

COMMENT ON CONSTRAINT audit_event_campaign_id_fkey ON audit_event IS
    'RESTRICT et non CASCADE : une campagne qui a une histoire ne se supprime '
    'pas physiquement. L''application ne supprime que logiquement.';
