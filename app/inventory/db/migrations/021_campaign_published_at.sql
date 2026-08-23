-- =============================================================================
-- 021 — La campagne sait si son archive existe
-- -----------------------------------------------------------------------------
-- La clôture est irréversible, et l'archive Delta est ce qui reste quand la base
-- opérationnelle a évolué. Une campagne pouvait pourtant être clôturée sans la
-- moindre preuve que la publication ait eu lieu : rien, côté application, ne
-- savait répondre à la question.
--
-- Et rien ne pouvait le savoir. Le job de publication écrit dans Delta ;
-- l'application lit Lakebase. Les deux ne se parlaient pas. Interroger l'entrepôt
-- SQL à chaque affichage du panneau « ce qui manque pour avancer » ferait
-- dépendre la clôture d'un entrepôt éveillé, pour une réponse qui ne change
-- qu'une fois par campagne.
--
-- Cette colonne est l'écriture en retour du job : après avoir écrit le manifeste
-- Delta, il repasse par la connexion Lakebase qu'il a déjà ouverte et pose
-- l'horodatage ici. L'application lit alors une colonne locale.
--
-- **Ce n'est pas la machine à états CLOSING → PUBLISHING → CLOSED que l'audit
-- décrit.** Deux phases supplémentaires demanderaient à la matrice de gel, aux
-- écrans et au séquencement de les apprendre, pour une propriété que cette
-- colonne donne déjà : on ne clôture pas sans archive. Le jour où la publication
-- devra être reprise automatiquement, ces phases auront leur raison d'être.
--
-- Nullable : les campagnes antérieures n'ont pas d'horodatage de publication.
-- Celles déjà clôturées le restent — cette migration ne rouvre rien.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

ALTER TABLE campaign
    ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;

COMMENT ON COLUMN campaign.published_at IS
    'Fin de la dernière publication Delta réussie, posée par le job après son '
    'manifeste. NULL = jamais archivée.';
