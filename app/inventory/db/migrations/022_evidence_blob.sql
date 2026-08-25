-- =============================================================================
-- 022 — Les pièces justificatives peuvent tenir dans la base
-- -----------------------------------------------------------------------------
-- Un scan de feuille manuscrite doit être archivé avant que les quantités qu'il
-- porte soient écrites : le papier repart à l'atelier, et une quantité que rien
-- ne rattache au document lu est un comptage invérifiable. L'archive était le
-- volume Unity Catalog, et c'est le bon endroit — un volume se parcourt depuis
-- l'espace de travail, sans requête SQL et sans l'application.
--
-- Encore faut-il pouvoir y écrire. Unity Catalog traverse la hiérarchie : il
-- faut `USE CATALOG` sur le catalogue, `USE SCHEMA` sur le schéma, `WRITE
-- VOLUME` sur le volume, et les deux derniers ne servent à rien sans le
-- premier. Or `GRANT USE CATALOG` demande `MANAGE` sur le catalogue, c'est-à-dire
-- son propriétaire. Sur un catalogue partagé, ce propriétaire peut être
-- injoignable — et un inventaire garde sa date.
--
-- Cette table est l'autre archive. L'application possède son schéma et y écrit
-- déjà tout le reste : aucun administrateur n'est nécessaire. C'est le même
-- renversement que la migration 005 pour le référentiel ERP, appliqué aux
-- pièces plutôt qu'aux lignes.
--
-- **Ce qu'on y perd, et c'est réel.** Un volume se parcourt dans l'interface de
-- l'espace de travail ; cette table ne se lit qu'à travers l'application ou en
-- SQL. Le chemin garde donc la même forme — campagne, nature, horodatage,
-- empreinte, nom — pour qu'un `SELECT path FROM evidence_blob` reste lisible par
-- un humain, et pour qu'une pièce puisse être ressortie vers le volume le jour
-- où le grant arrive.
--
-- **Le chemin est la clé.** Il porte l'empreinte du contenu : deux dépôts du même
-- fichier convergent vers le même chemin, deux fichiers différents ne peuvent
-- pas s'y retrouver. Un conflit est donc un re-dépôt à l'identique, pas une
-- collision — `ON CONFLICT DO NOTHING` est le comportement voulu, et non un
-- écrasement toléré.
--
-- **Pas de `campaign_id`, pas de clé étrangère.** Le dépôt reçoit le *code* de
-- la campagne, pas son identifiant technique, et il a lieu avant que la ligne
-- qui référencera la pièce existe — une contrainte l'obligerait à s'insérer
-- dans une transaction qui n'est pas encore ouverte. La colonne sert à
-- retrouver les pièces d'une campagne, pas à garantir une intégrité que le
-- chemin porte déjà.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS evidence_blob (
    path          TEXT PRIMARY KEY,
    campaign_code TEXT        NOT NULL,
    kind          TEXT        NOT NULL,
    filename      TEXT        NOT NULL,
    mime          TEXT        NOT NULL,
    sha256        TEXT        NOT NULL,
    size_bytes    BIGINT      NOT NULL,
    content       BYTEA       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE evidence_blob IS
    'Pièces justificatives archivées dans la base, quand le volume Unity '
    'Catalog n''est pas ouvrable à l''application (INV_EVIDENCE_STORE=lakebase).';

COMMENT ON COLUMN evidence_blob.path IS
    'Chemin logique « lakebase:/<campagne>/<nature>/<horodatage>-<empreinte>-'
    '<nom> », écrit tel quel dans import_batch.storage_path et '
    'count_sheet.evidence_path.';

COMMENT ON COLUMN evidence_blob.sha256 IS
    'Empreinte du contenu déposé. Répond à la question d''un contrôle : le '
    'fichier relu est-il celui que le modèle a lu ?';

-- Retrouver les pièces d'une campagne, dans l'ordre où on les cherche : les
-- plus récentes d'abord, par nature. Sans lui, la recherche parcourt la table
-- entière — et une table de pièces se compte en gigaoctets, pas en lignes.
CREATE INDEX IF NOT EXISTS ix_evidence_blob_campaign
    ON evidence_blob (campaign_code, kind, created_at DESC);
