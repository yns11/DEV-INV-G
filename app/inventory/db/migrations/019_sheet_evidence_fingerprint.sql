-- =============================================================================
-- 019 — Une feuille scannée dit de quel fichier elle vient
-- -----------------------------------------------------------------------------
-- `count_sheet.evidence_path` désigne l'image que le modèle a lue. Un chemin
-- n'est pourtant pas une preuve : un volume Unity Catalog se parcourt et se
-- modifie depuis l'espace de travail, et le fichier relu six mois plus tard
-- n'est pas nécessairement celui qui a produit les quantités enregistrées.
-- Jusqu'ici, rien ne permettait de faire la différence — il fallait croire le
-- chemin sur parole.
--
-- L'empreinte, elle, tranche : le sha256 relu est celui de l'extraction, ou il
-- ne l'est pas. La taille et le type complètent la description au moment du
-- dépôt, ce qui permet de dire « ce PDF de 4,2 Mo » sans avoir à télécharger
-- quoi que ce soit — utile quand la question est posée à un écran, pas à une
-- requête.
--
-- `import_batch` porte déjà `content_hash` : les chargements de fichiers
-- avaient cette réponse depuis le début, les feuilles scannées ne l'avaient
-- pas. Cette migration met les deux au même niveau.
--
-- Les colonnes sont nullables : les feuilles scannées avant cette migration
-- gardent leur chemin sans empreinte, et l'écran distingue « pièce vérifiable »
-- de « pièce antérieure à la traçabilité ». Les remplir après coup demanderait
-- de relire chaque fichier pour en calculer l'empreinte, c'est-à-dire d'affirmer
-- que le fichier présent aujourd'hui est bien l'original — précisément ce que
-- ces colonnes existent pour ne plus avoir à supposer.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE count_sheet
    ADD COLUMN IF NOT EXISTS evidence_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS evidence_bytes  BIGINT,
    ADD COLUMN IF NOT EXISTS evidence_mime   TEXT;

COMMENT ON COLUMN count_sheet.evidence_sha256 IS
    'sha256 du fichier lu par le modèle, au moment du dépôt. NULL avant 019.';
COMMENT ON COLUMN count_sheet.evidence_bytes IS
    'Taille en octets du fichier déposé.';
COMMENT ON COLUMN count_sheet.evidence_mime IS
    'Type MIME déduit du nom du fichier déposé.';
