-- =============================================================================
-- 025 — Le journal ERP entre dans le modèle, et les comptages avancés avec lui
-- -----------------------------------------------------------------------------
-- Trois constats tirés de l'export réel du 13 juin 2026 (58 345 lignes,
-- 73 journaux) et de la note métier sur les journaux de comptage.
--
-- **Un journal ERP n'est pas un emplacement.** Il tient à un entrepôt mais
-- couvre plusieurs emplacements — 48 journaux sur 73 en couvrent plus d'un,
-- jusqu'à 54 pour l'un d'eux. Et les emplacements de ses lignes ne suffisent pas
-- à dire lesquels il couvre : 1 932 lignes portent un autre entrepôt que celui
-- du journal, uniquement pour matérialiser un déplacement. Le périmètre se
-- **déclare** donc, il ne se devine pas.
--
-- **Le journal porte sa propre référence.** La colonne `OnHandQuantity` donne le
-- stock ERP *avant* comptage, `CountedQuantity` le physique relevé. Un comptage
-- avancé n'a donc besoin d'aucun chargement de stock séparé : sa référence
-- s'agrège depuis ses propres lignes.
--
-- **L'étiquette et le numéro de série existent.** `SILlabelID` et
-- `ItemSerialNumber` descendent sous le grain de l'application, qui reste
-- « emplacement + article » pour tout calcul. Ils servent la traçabilité et un
-- seul contrôle : signaler qu'une étiquette d'un emplacement scellé se retrouve
-- comptée dans un autre journal.
--
-- Ce que cette migration ne fait **pas**
-- --------------------------------------
-- Elle ne transforme pas `count_journal` en journal ERP. `count_journal` reste
-- un par (campagne, entrepôt, emplacement) : c'est l'unité de comptage, de
-- progression et de gel de l'application, et tout le produit en dépend — la
-- clé du journal, le forçage au stock ERP, les quantités comptées, les écrans.
-- Le journal ERP devient un objet **à côté**, avec son périmètre et ses lignes
-- brutes ; l'application continue d'agréger vers l'emplacement.
--
-- Deux tables, deux grains, et c'est délibéré : `erp_journal_line` conserve la
-- ligne telle que l'ERP la produit — une par étiquette — quand
-- `count_journal_line` garde le grain « emplacement + article » sur lequel tout
-- le reste est écrit.
--
-- Idempotent : rejouable sans effet de bord.
-- =============================================================================

SET search_path TO inventory, public;

-- --------------------------------------------------------------------------
-- Le journal ERP et son périmètre déclaré
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS erp_journal (
    id                UUID PRIMARY KEY,
    campaign_id       UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    journal_number    TEXT        NOT NULL,
    kind              TEXT        NOT NULL DEFAULT 'INVV'
        CHECK (kind IN ('INVE','INVV')),
    description       TEXT        NOT NULL DEFAULT '',
    site_id           TEXT        NOT NULL DEFAULT '',
    -- Le postage tel que l'ERP le déclare (en-tête `IsPosted`), distinct du
    -- statut de workflow d'un `count_journal`, qu'un humain peut faire avancer.
    -- C'est cette colonne-ci que le scellement d'un lot avancé exige, et le
    -- réalignement de l'ERP sur le comptage est alors acquis par construction.
    erp_posted        BOOLEAN     NOT NULL DEFAULT false,
    erp_posted_at     TIMESTAMPTZ,
    line_count        INTEGER     NOT NULL DEFAULT 0,
    first_imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_imported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Tant que le périmètre n'est pas déclaré, rien n'est calculable : ni la
    -- référence d'un emplacement, ni ce qui est une ligne de passage.
    scope_declared_at TIMESTAMPTZ,
    scope_declared_by TEXT,
    deleted_at        TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS erp_journal_uq
    ON erp_journal (campaign_id, journal_number) WHERE deleted_at IS NULL;
-- Ajoutée seulement si absente, et non pas retirée puis reposée.
--
-- Le `DROP … ADD` de la migration 018 ne se rejoue pas ici : les clés étrangères
-- composites créées plus bas s'appuient sur cet index, et Postgres refuse de le
-- retirer tant qu'elles existent. La migration 018 ne rencontrait pas le cas —
-- ses dépendants n'étaient pas dans le même fichier — mais celle-ci si, et
-- « rejouable sans effet de bord » n'est pas une formule de politesse.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'erp_journal_id_campaign_key'
          AND conrelid = 'erp_journal'::regclass
    ) THEN
        ALTER TABLE erp_journal
            ADD CONSTRAINT erp_journal_id_campaign_key UNIQUE (id, campaign_id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS erp_journal_scope (
    erp_journal_id UUID NOT NULL,
    campaign_id    UUID NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    warehouse_id   TEXT NOT NULL,
    location_id    TEXT NOT NULL,
    PRIMARY KEY (erp_journal_id, warehouse_id, location_id),
    FOREIGN KEY (erp_journal_id, campaign_id)
        REFERENCES erp_journal (id, campaign_id) ON DELETE CASCADE
);
-- Un emplacement n'appartient au périmètre que d'un seul journal. C'est ce qui
-- rend la proposition « hors emplacements déjà alloués » vraie par construction
-- plutôt que par la vigilance du code qui la calcule.
CREATE UNIQUE INDEX IF NOT EXISTS erp_journal_scope_location_uq
    ON erp_journal_scope (campaign_id, warehouse_id, location_id);

-- --------------------------------------------------------------------------
-- La ligne ERP brute, au grain de l'étiquette
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS erp_journal_line (
    id                  UUID PRIMARY KEY,
    erp_journal_id      UUID          NOT NULL,
    campaign_id         UUID          NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    erp_line_number     INTEGER,
    site_id             TEXT          NOT NULL DEFAULT '',
    warehouse_id        TEXT          NOT NULL,
    location_id         TEXT          NOT NULL DEFAULT '',
    -- Étiquette logistique et numéro de série, en TEXT et jamais autrement :
    -- « 001609231 » perd trois caractères au premier passage par un entier, et
    -- une étiquette tronquée ne se rattache plus à rien.
    label_id            TEXT          NOT NULL DEFAULT '',
    serial_number       TEXT          NOT NULL DEFAULT '',
    item_number         TEXT          NOT NULL,
    -- « Stock ERP » : la référence, avant comptage. « Qté Comptée » : le relevé.
    qty_on_hand         NUMERIC(20,6) NOT NULL DEFAULT 0,
    qty_counted         NUMERIC(20,6) NOT NULL DEFAULT 0,
    unit                TEXT          NOT NULL DEFAULT 'PCE',
    inventory_status_id TEXT          NOT NULL DEFAULT '',
    imported_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
    FOREIGN KEY (erp_journal_id, campaign_id)
        REFERENCES erp_journal (id, campaign_id) ON DELETE CASCADE
);
-- Le doublon « Journal ERP + Numéro de ligne » devient impossible plutôt que
-- détecté après coup par un contrôle qu'on pourrait oublier de brancher.
--
-- Un export peut légitimement omettre le numéro de ligne, et refuser ces
-- lignes-là perdrait des quantités comptées pour une colonne technique absente.
-- L'index les laisse passer sans clause particulière : dans un index unique,
-- Postgres tient deux NULL pour **distincts** (`NULLS DISTINCT`, le défaut).
-- C'est cette propriété-là qui est en jeu — pas une clause `WHERE` — et c'est
-- elle que `test_a_missing_line_number_is_not_a_duplicate` épingle.
CREATE UNIQUE INDEX IF NOT EXISTS erp_journal_line_uq
    ON erp_journal_line (erp_journal_id, erp_line_number);
CREATE INDEX IF NOT EXISTS erp_journal_line_loc_idx
    ON erp_journal_line (campaign_id, warehouse_id, location_id, item_number);
-- L'index du contrôle étiquette : « cette étiquette est-elle comptée ailleurs ? »
CREATE INDEX IF NOT EXISTS erp_journal_line_label_idx
    ON erp_journal_line (campaign_id, label_id) WHERE label_id <> '';

-- --------------------------------------------------------------------------
-- Le comptage avancé : le lot, et le scellement porté par le journal
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS early_count_batch (
    id          UUID PRIMARY KEY,
    campaign_id UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    code        TEXT        NOT NULL,
    label       TEXT        NOT NULL DEFAULT '',
    counted_on  DATE,
    opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    opened_by   TEXT        NOT NULL DEFAULT '',
    closed_at   TIMESTAMPTZ,
    closed_by   TEXT,
    sealed_at   TIMESTAMPTZ,
    sealed_by   TEXT,
    deleted_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS early_count_batch_uq
    ON early_count_batch (campaign_id, code) WHERE deleted_at IS NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'early_count_batch_id_campaign_key'
          AND conrelid = 'early_count_batch'::regclass
    ) THEN
        ALTER TABLE early_count_batch
            ADD CONSTRAINT early_count_batch_id_campaign_key UNIQUE (id, campaign_id);
    END IF;
END $$;

-- Le premier gel **par objet** du produit. La matrice de mutabilité reste
-- consultée en premier et garde le dernier mot pour interdire ; le scellement
-- s'y ajoute et ne peut que restreindre davantage, jamais rouvrir.
ALTER TABLE count_journal
    ADD COLUMN IF NOT EXISTS early_batch_id UUID,
    ADD COLUMN IF NOT EXISTS sealed_at      TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sealed_by      TEXT;
CREATE INDEX IF NOT EXISTS count_journal_early_idx
    ON count_journal (campaign_id, early_batch_id) WHERE early_batch_id IS NOT NULL;

-- La référence d'une ligne comptée, agrégée depuis les lignes ERP du périmètre.
-- NULL n'est pas 0 : NULL dit « aucune référence ERP connue pour cette ligne »
-- — une saisie manuelle, une ligne née d'un scan — quand 0 dit « l'ERP annonce
-- zéro ». Les confondre ferait d'un article inconnu de l'ERP un écart franc.
ALTER TABLE count_journal_line
    ADD COLUMN IF NOT EXISTS qty_on_hand        NUMERIC(20,6),
    ADD COLUMN IF NOT EXISTS erp_journal_number TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS label_count        INTEGER NOT NULL DEFAULT 0;

-- --------------------------------------------------------------------------
-- La dérive d'un emplacement scellé
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS early_count_drift (
    id              UUID PRIMARY KEY,
    campaign_id     UUID          NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    batch_id        UUID,
    warehouse_id    TEXT          NOT NULL,
    location_id     TEXT          NOT NULL,
    item_number     TEXT          NOT NULL,
    qty_erp_t0      NUMERIC(20,6) NOT NULL DEFAULT 0,
    qty_physical_t0 NUMERIC(20,6) NOT NULL DEFAULT 0,
    qty_erp_j       NUMERIC(20,6) NOT NULL DEFAULT 0,
    drift_qty       NUMERIC(20,6) NOT NULL DEFAULT 0,
    drift_value     NUMERIC(20,2) NOT NULL DEFAULT 0,
    is_material     BOOLEAN       NOT NULL DEFAULT false,
    -- Deux issues, pas quatre. « Rejouer le postage » n'existe pas : on ne
    -- scelle qu'un journal posté dans l'ERP. « Ajuster » n'existe pas non plus :
    -- un mouvement réel se saisit par le mécanisme d'ajustement, qui a déjà son
    -- sens et sa table.
    resolution      TEXT CHECK (resolution IN ('KEEP_EARLY','RECOUNT')),
    cause_code      TEXT          NOT NULL DEFAULT '',
    comment         TEXT          NOT NULL DEFAULT '',
    resolved_at     TIMESTAMPTZ,
    resolved_by     TEXT,
    computed_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS early_count_drift_uq
    ON early_count_drift (campaign_id, warehouse_id, location_id, item_number);
CREATE INDEX IF NOT EXISTS early_count_drift_open_idx
    ON early_count_drift (campaign_id) WHERE is_material AND resolution IS NULL;

-- --------------------------------------------------------------------------
-- La référence porte sa date
-- --------------------------------------------------------------------------
-- Le total « stock ERP » d'une campagne qui précompte est composite : la plupart
-- des lignes à la date du jour J, les lignes scellées à leur date de
-- précomptage. Sans cette colonne, un rapprochement avec un état ERP tiré à une
-- date unique trouverait une différence que rien n'expliquerait.
ALTER TABLE book_stock
    ADD COLUMN IF NOT EXISTS reference_date DATE,
    ADD COLUMN IF NOT EXISTS early_batch_id UUID;

-- Le jalon qui sépare les deux sous-phases du comptage, et l'heure du dernier
-- import de journaux réussi — celle que l'écran doit afficher le jour J.
ALTER TABLE campaign
    ADD COLUMN IF NOT EXISTS general_count_opened_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS journals_imported_at    TIMESTAMPTZ;
