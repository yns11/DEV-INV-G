-- =============================================================================
-- 015 — Le scan multi-feuilles devient un travail suivi
-- -----------------------------------------------------------------------------
-- Une pile de cent feuilles fait deux cents pages. Le traitement — rendu,
-- routage, cent lectures, écritures — tenait jusqu'ici dans la requête HTTP du
-- chargement : le navigateur attendait, la passerelle coupait avant la fin, et
-- ce qui avait été lu était perdu avec la connexion.
--
-- Le chargement rend donc maintenant un identifiant de travail, tout de suite,
-- et le traitement continue derrière. Cette table est ce que l'écran interroge
-- pour savoir où il en est.
--
-- **Ce qu'elle porte, et pourquoi.** L'avancement (`step`, `sheets_done`) fait
-- la différence entre « ça travaille » et « c'est bloqué » — sans lui, une
-- attente de six minutes est indistinguable d'une panne. Le `report` garde le
-- résultat complet, chronomètres compris : après coup, « c'était lent » ne dit
-- pas si le temps est parti dans le rendu, dans la file d'attente de l'endpoint
-- ou dans la génération, et ces trois causes appellent trois corrections
-- différentes.
--
-- **Ce qu'elle ne porte pas : le PDF.** Le fichier reste en mémoire du
-- conteneur qui l'a reçu, et la pièce justificative part au volume comme avant.
-- Le stocker ici doublerait jusqu'à soixante mégaoctets par pile dans une base
-- transactionnelle, pour un gain qui n'existe que si le conteneur redémarre au
-- milieu — cas où le travail est de toute façon marqué en échec et le
-- chargement se refait.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

CREATE TABLE IF NOT EXISTS scan_job (
    id              UUID PRIMARY KEY,
    campaign_id     UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,

    filename        TEXT        NOT NULL DEFAULT '',
    content_type    TEXT        NOT NULL DEFAULT '',
    -- QUEUED · RUNNING · SUCCEEDED · FAILED. Texte plutôt qu'ENUM : un statut
    -- ajouté ne doit pas demander une migration de type sur une table vivante.
    status          TEXT        NOT NULL DEFAULT 'QUEUED',
    -- L'étape en cours, en clair, telle que l'écran l'affiche.
    step            TEXT        NOT NULL DEFAULT '',

    total_pages     INTEGER     NOT NULL DEFAULT 0,
    pages_routed    INTEGER     NOT NULL DEFAULT 0,
    sheets_total    INTEGER     NOT NULL DEFAULT 0,
    sheets_done     INTEGER     NOT NULL DEFAULT 0,

    -- Le rapport final, dans la forme que rendait déjà l'appel synchrone : les
    -- feuilles lues, préservées, en échec, les pages non attribuées, et les
    -- chronomètres. Une seule forme pour les deux chemins, sinon l'écran en
    -- apprend deux.
    report          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error           TEXT        NOT NULL DEFAULT '',

    -- `overwrite_reviewed` est rejoué par le worker : c'est une décision de
    -- l'utilisateur au moment du dépôt, pas un réglage du serveur.
    overwrite_reviewed BOOLEAN  NOT NULL DEFAULT FALSE,

    created_by      TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

-- L'écran liste les travaux d'une campagne, le plus récent d'abord.
CREATE INDEX IF NOT EXISTS scan_job_campaign_idx
    ON scan_job (campaign_id, created_at DESC);

-- Au démarrage, l'application cherche les travaux restés en cours : ils
-- appartiennent à un conteneur qui n'existe plus.
CREATE INDEX IF NOT EXISTS scan_job_running_idx
    ON scan_job (status) WHERE status IN ('QUEUED', 'RUNNING');
