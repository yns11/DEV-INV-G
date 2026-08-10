-- =============================================================================
-- Campagnes Inventaire — Lakebase (PostgreSQL) operational schema
-- -----------------------------------------------------------------------------
-- This schema holds everything that is *written* during a campaign. Immutable
-- snapshots and analytics live in Delta / Unity Catalog (see sql/00_unity_catalog.sql);
-- the two are reconciled by jobs/publish_campaign_to_delta.py.
--
-- Conventions
--   * business keys are never concatenated into a fragile string: every
--     dimension has its own column plus a technical surrogate key (uuid);
--   * quantities are NUMERIC(20,6), money NUMERIC(20,2) — never float;
--   * deletions are logical (`deleted_at`), never physical, so the audit trail
--     always resolves;
--   * every mutable table carries `row_version` for optimistic concurrency and
--     `updated_at`/`updated_by` for the audit trail.
--
-- Idempotent: safe to re-run. Applied by inventory.db.migrations.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS inventory;
SET search_path TO inventory, public;

-- --------------------------------------------------------------------------
-- Enumerated domains
-- --------------------------------------------------------------------------
-- CHECK constraints rather than PG ENUM types: adding a value to an ENUM
-- requires ALTER TYPE (not transactional before PG12 and awkward to roll back),
-- while a CHECK is a plain, reversible DDL statement.

-- --------------------------------------------------------------------------
-- Campaign
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaign (
    id                      UUID PRIMARY KEY,
    code                    TEXT        NOT NULL,
    label                   TEXT        NOT NULL DEFAULT '',
    count_date              DATE        NOT NULL,
    status                  TEXT        NOT NULL DEFAULT 'PREPARATION'
        CHECK (status IN ('PREPARATION', 'COUNTING', 'ANALYSIS', 'CLOSED')),
    config                  JSONB       NOT NULL DEFAULT '{}'::jsonb,

    referentials_frozen_at  TIMESTAMPTZ,
    book_stock_frozen_at    TIMESTAMPTZ,
    counting_frozen_at      TIMESTAMPTZ,
    closed_at               TIMESTAMPTZ,

    cloned_from_code        TEXT,
    engine_version          TEXT        NOT NULL DEFAULT '1.0.0',

    created_by              TEXT        NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by              TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version             BIGINT      NOT NULL DEFAULT 1,
    deleted_at              TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS campaign_code_uq
    ON campaign (code) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS campaign_status_idx ON campaign (status, count_date DESC);

-- Materiality thresholds, one row per (campaign, item type).
CREATE TABLE IF NOT EXISTS threshold (
    campaign_id     UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_type       TEXT        NOT NULL
        CHECK (item_type IN ('COMPONENT','SEMI_FINISHED','FINISHED','PACKAGING','UNKNOWN')),
    value_abs_eur   NUMERIC(20,2) NOT NULL DEFAULT 1000,
    qty_relative    NUMERIC(10,6),
    qty_abs_floor   NUMERIC(20,2) NOT NULL DEFAULT 0,
    ira_tolerance   NUMERIC(10,6) NOT NULL DEFAULT 0,
    updated_by      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, item_type)
);

-- --------------------------------------------------------------------------
-- Referentials — snapshotted per campaign (the "immutable dossier")
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item (
    campaign_id     UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number     TEXT        NOT NULL,
    name            TEXT        NOT NULL DEFAULT '',
    search_name     TEXT        NOT NULL DEFAULT '',
    item_group      TEXT        NOT NULL DEFAULT '',
    lifecycle_state TEXT        NOT NULL DEFAULT '',
    item_type       TEXT        NOT NULL DEFAULT 'UNKNOWN'
        CHECK (item_type IN ('COMPONENT','SEMI_FINISHED','FINISHED','PACKAGING','UNKNOWN')),
    category        TEXT        NOT NULL DEFAULT '',
    program         TEXT        NOT NULL DEFAULT '',
    commonality     TEXT        NOT NULL DEFAULT 'UNKNOWN'
        CHECK (commonality IN ('SPECIFIC','COMMON','UNKNOWN')),
    unit            TEXT        NOT NULL DEFAULT 'PCE',
    std_price       NUMERIC(20,2) NOT NULL DEFAULT 0,
    -- Exclusion facets: any subset of {'GENERIC','BOM','ALL'}.
    exclusions      TEXT[]      NOT NULL DEFAULT '{}',
    source          TEXT        NOT NULL DEFAULT 'FILE_IMPORT',
    updated_by      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version     BIGINT      NOT NULL DEFAULT 1,
    deleted_at      TIMESTAMPTZ,
    PRIMARY KEY (campaign_id, item_number)
);
CREATE INDEX IF NOT EXISTS item_type_idx     ON item (campaign_id, item_type);
CREATE INDEX IF NOT EXISTS item_program_idx  ON item (campaign_id, program);
CREATE INDEX IF NOT EXISTS item_category_idx ON item (campaign_id, category);
-- Trigram-free prefix search on the two columns users actually type into.
CREATE INDEX IF NOT EXISTS item_name_idx     ON item (campaign_id, lower(name) text_pattern_ops);

CREATE TABLE IF NOT EXISTS bom_link (
    id           UUID PRIMARY KEY,
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    parent_item  TEXT        NOT NULL,
    child_item   TEXT        NOT NULL,
    qty_per      NUMERIC(20,6) NOT NULL CHECK (qty_per > 0),
    unit         TEXT        NOT NULL DEFAULT 'PCE',
    level        INTEGER     NOT NULL DEFAULT 1,
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at   TIMESTAMPTZ,
    CHECK (parent_item <> child_item)
);
CREATE UNIQUE INDEX IF NOT EXISTS bom_link_uq
    ON bom_link (campaign_id, parent_item, child_item) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS bom_link_parent_idx ON bom_link (campaign_id, parent_item);
CREATE INDEX IF NOT EXISTS bom_link_child_idx  ON bom_link (campaign_id, child_item);

CREATE TABLE IF NOT EXISTS warehouse (
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    warehouse_id TEXT        NOT NULL,
    label        TEXT        NOT NULL DEFAULT '',
    type         TEXT        NOT NULL DEFAULT 'UNKNOWN'
        CHECK (type IN ('LABEL','BULK','UNKNOWN')),
    status       TEXT        NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','DISABLED')),
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (campaign_id, warehouse_id)
);

-- The (warehouse, location) pair is the key: two warehouses may reuse a
-- location name, so neither column alone identifies anything.
CREATE TABLE IF NOT EXISTS location (
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    warehouse_id TEXT        NOT NULL,
    location_id  TEXT        NOT NULL,
    zone         TEXT        NOT NULL DEFAULT '',
    type         TEXT        NOT NULL DEFAULT 'UNKNOWN'
        CHECK (type IN ('LABEL','BULK','UNKNOWN')),
    status       TEXT        NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','DISABLED')),
    source       TEXT        NOT NULL DEFAULT 'SYSTEM',
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version  BIGINT      NOT NULL DEFAULT 1,
    PRIMARY KEY (campaign_id, warehouse_id, location_id)
);
CREATE INDEX IF NOT EXISTS location_status_idx ON location (campaign_id, status);

-- --------------------------------------------------------------------------
-- Book stock snapshot (stock ERP), frozen once per campaign
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS book_stock (
    id           UUID PRIMARY KEY,
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number  TEXT        NOT NULL,
    warehouse_id TEXT        NOT NULL,
    location_id  TEXT        NOT NULL,
    qty          NUMERIC(20,6) NOT NULL DEFAULT 0,
    unit         TEXT        NOT NULL DEFAULT 'PCE',
    unit_cost    NUMERIC(20,2) NOT NULL DEFAULT 0,
    imported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    import_batch UUID
);
CREATE UNIQUE INDEX IF NOT EXISTS book_stock_uq
    ON book_stock (campaign_id, item_number, warehouse_id, location_id);
CREATE INDEX IF NOT EXISTS book_stock_loc_idx
    ON book_stock (campaign_id, warehouse_id, location_id);
CREATE INDEX IF NOT EXISTS book_stock_item_idx ON book_stock (campaign_id, item_number);

-- --------------------------------------------------------------------------
-- Counting journals — exactly one per active (warehouse, location)
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS count_journal (
    id             UUID PRIMARY KEY,
    campaign_id    UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    warehouse_id   TEXT        NOT NULL,
    location_id    TEXT        NOT NULL,
    kind           TEXT        NOT NULL DEFAULT 'INVV' CHECK (kind IN ('INVE','INVV')),
    status         TEXT        NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','IN_PROGRESS','POSTED','BOOK_ENFORCED')),
    journal_number TEXT        NOT NULL DEFAULT '',
    description    TEXT        NOT NULL DEFAULT '',
    posted_at      TIMESTAMPTZ,
    auto_created   BOOLEAN     NOT NULL DEFAULT false,
    updated_by     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version    BIGINT      NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS count_journal_uq
    ON count_journal (campaign_id, warehouse_id, location_id);
CREATE INDEX IF NOT EXISTS count_journal_status_idx ON count_journal (campaign_id, status);

CREATE TABLE IF NOT EXISTS count_journal_line (
    id           UUID PRIMARY KEY,
    journal_id   UUID        NOT NULL REFERENCES count_journal (id) ON DELETE CASCADE,
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number  TEXT        NOT NULL,
    -- Imported and manual values live side by side: reloading the ERP export
    -- refreshes qty_imported without ever destroying a human decision.
    qty_imported NUMERIC(20,6),
    qty_manual   NUMERIC(20,6),
    unit         TEXT        NOT NULL DEFAULT 'PCE',
    source       TEXT        NOT NULL DEFAULT 'ERP_IMPORT'
        CHECK (source IN ('ERP_IMPORT','FILE_IMPORT','MANUAL','SCAN_AI',
                          'CONSOLIDATION','ARBITRATION','SYSTEM')),
    comment      TEXT        NOT NULL DEFAULT '',
    updated_by   TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version  BIGINT      NOT NULL DEFAULT 1,
    deleted_at   TIMESTAMPTZ,
    CHECK (qty_imported IS NOT NULL OR qty_manual IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS cjl_journal_idx ON count_journal_line (journal_id)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS cjl_item_idx    ON count_journal_line (campaign_id, item_number)
    WHERE deleted_at IS NULL;

-- --------------------------------------------------------------------------
-- GENERIQUE — zones, printable sheets, lines, arbitration
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zone (
    id            UUID PRIMARY KEY,
    campaign_id   UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    code          TEXT        NOT NULL,
    label         TEXT        NOT NULL DEFAULT '',
    sector        TEXT        NOT NULL DEFAULT '',
    display_order INTEGER     NOT NULL DEFAULT 0,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS zone_uq
    ON zone (campaign_id, code) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS count_sheet (
    id                    UUID PRIMARY KEY,
    campaign_id           UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    zone_id               UUID        NOT NULL REFERENCES zone (id) ON DELETE CASCADE,
    pass_no               TEXT        NOT NULL CHECK (pass_no IN ('PASS_1','PASS_2')),
    status                TEXT        NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','COUNTING','ENCODING','DONE')),
    counter_name          TEXT        NOT NULL DEFAULT '',
    started_at            TIMESTAMPTZ,
    ended_at              TIMESTAMPTZ,
    evidence_path         TEXT,
    extraction_confidence REAL,
    updated_by            TEXT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version           BIGINT      NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS count_sheet_uq ON count_sheet (zone_id, pass_no);
CREATE INDEX IF NOT EXISTS count_sheet_status_idx ON count_sheet (campaign_id, status);

CREATE TABLE IF NOT EXISTS count_sheet_line (
    id            UUID PRIMARY KEY,
    sheet_id      UUID        NOT NULL REFERENCES count_sheet (id) ON DELETE CASCADE,
    campaign_id   UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number   TEXT        NOT NULL,
    section       TEXT        NOT NULL DEFAULT 'LINE_SIDE'
        CHECK (section IN ('LINE_SIDE','WIP','WIP_OK')),
    qty_imported  NUMERIC(20,6),
    qty_manual    NUMERIC(20,6),
    unit          TEXT        NOT NULL DEFAULT 'PCE',
    source        TEXT        NOT NULL DEFAULT 'MANUAL'
        CHECK (source IN ('ERP_IMPORT','FILE_IMPORT','MANUAL','SCAN_AI',
                          'CONSOLIDATION','ARBITRATION','SYSTEM')),
    confidence    REAL,
    comment       TEXT        NOT NULL DEFAULT '',
    display_order INTEGER     NOT NULL DEFAULT 0,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version   BIGINT      NOT NULL DEFAULT 1,
    deleted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS csl_sheet_idx ON count_sheet_line (sheet_id, display_order)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS csl_item_idx  ON count_sheet_line (campaign_id, item_number)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS arbitration (
    id             UUID PRIMARY KEY,
    campaign_id    UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    zone_id        UUID        NOT NULL REFERENCES zone (id) ON DELETE CASCADE,
    item_number    TEXT        NOT NULL,
    section        TEXT        NOT NULL CHECK (section IN ('LINE_SIDE','WIP','WIP_OK')),
    qty_pass_1     NUMERIC(20,6),
    qty_pass_2     NUMERIC(20,6),
    qty_arbitrated NUMERIC(20,6),
    decided_by     TEXT,
    decided_at     TIMESTAMPTZ,
    comment        TEXT        NOT NULL DEFAULT '',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS arbitration_uq
    ON arbitration (zone_id, item_number, section);

-- Consolidation output: what the GENERIQUE INVV journal will contain, plus the
-- WIP breakdown that explains every exploded quantity.
CREATE TABLE IF NOT EXISTS consolidation_run (
    id             UUID PRIMARY KEY,
    campaign_id    UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_by         TEXT        NOT NULL,
    engine_version TEXT        NOT NULL DEFAULT '1.0.0',
    zones_included TEXT[]      NOT NULL DEFAULT '{}',
    zones_skipped  TEXT[]      NOT NULL DEFAULT '{}',
    findings       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    is_current     BOOLEAN     NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS consolidation_run_campaign_idx
    ON consolidation_run (campaign_id, run_at DESC);

CREATE TABLE IF NOT EXISTS consolidation_line (
    run_id            UUID        NOT NULL REFERENCES consolidation_run (id) ON DELETE CASCADE,
    item_number       TEXT        NOT NULL,
    qty               NUMERIC(20,6) NOT NULL,
    unit              TEXT        NOT NULL DEFAULT 'PCE',
    qty_line_side     NUMERIC(20,6) NOT NULL DEFAULT 0,
    qty_wip_ok        NUMERIC(20,6) NOT NULL DEFAULT 0,
    qty_wip_exploded  NUMERIC(20,6) NOT NULL DEFAULT 0,
    zone_codes        TEXT[]      NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, item_number)
);

CREATE TABLE IF NOT EXISTS wip_breakdown (
    run_id          UUID        NOT NULL REFERENCES consolidation_run (id) ON DELETE CASCADE,
    zone_code       TEXT        NOT NULL DEFAULT '',
    parent_item     TEXT        NOT NULL,
    parent_qty      NUMERIC(20,6) NOT NULL,
    child_item      TEXT        NOT NULL,
    qty_per_parent  NUMERIC(20,6) NOT NULL,
    child_qty       NUMERIC(20,6) NOT NULL,
    depth           INTEGER     NOT NULL DEFAULT 1,
    PRIMARY KEY (run_id, zone_code, parent_item, child_item)
);
CREATE INDEX IF NOT EXISTS wip_breakdown_child_idx ON wip_breakdown (run_id, child_item);

-- --------------------------------------------------------------------------
-- Adjustments & analysis
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adjustment_line (
    id             UUID PRIMARY KEY,
    campaign_id    UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number    TEXT        NOT NULL,
    warehouse_id   TEXT        NOT NULL DEFAULT '',
    location_id    TEXT        NOT NULL DEFAULT '',
    kind           TEXT        NOT NULL DEFAULT 'ADJUSTMENT'
        CHECK (kind IN ('COUNT','ADJUSTMENT','RECOUNT','OTHER')),
    qty            NUMERIC(20,6) NOT NULL DEFAULT 0,
    unit           TEXT        NOT NULL DEFAULT 'PCE',
    value          NUMERIC(20,2) NOT NULL DEFAULT 0,
    journal_number TEXT        NOT NULL DEFAULT '',
    physical_date  DATE,
    reason_code    TEXT        NOT NULL DEFAULT '',
    comment        TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL DEFAULT 'FILE_IMPORT',
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version    BIGINT      NOT NULL DEFAULT 1,
    deleted_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS adj_campaign_item_idx
    ON adjustment_line (campaign_id, item_number) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS adj_date_idx
    ON adjustment_line (campaign_id, physical_date) WHERE deleted_at IS NULL;

-- Site-wide referential of standard root causes (not campaign-scoped).
CREATE TABLE IF NOT EXISTS assignable_cause (
    code          TEXT PRIMARY KEY,
    label         TEXT        NOT NULL,
    family        TEXT        NOT NULL DEFAULT '',
    description   TEXT        NOT NULL DEFAULT '',
    display_order INTEGER     NOT NULL DEFAULT 0,
    active        BOOLEAN     NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS variance_analysis (
    id                 UUID PRIMARY KEY,
    campaign_id        UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    item_number        TEXT        NOT NULL,
    cause_code         TEXT        REFERENCES assignable_cause (code),
    comment            TEXT        NOT NULL DEFAULT '',
    analyst            TEXT,
    accepted           BOOLEAN     NOT NULL DEFAULT false,
    -- The AI proposal is stored beside, never instead of, the human decision.
    ai_suggested_cause TEXT,
    ai_confidence      REAL,
    ai_rationale       TEXT        NOT NULL DEFAULT '',
    updated_by         TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_version        BIGINT      NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS variance_analysis_uq
    ON variance_analysis (campaign_id, item_number);

-- --------------------------------------------------------------------------
-- Import batches — provenance of every bulk load
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS import_batch (
    id             UUID PRIMARY KEY,
    campaign_id    UUID        REFERENCES campaign (id) ON DELETE CASCADE,
    target         TEXT        NOT NULL,     -- items | boms | book_stock | journals | ...
    filename       TEXT        NOT NULL DEFAULT '',
    -- SHA-256 of the payload: re-uploading the same file is detected instead of
    -- silently duplicating rows.
    content_hash   TEXT        NOT NULL DEFAULT '',
    storage_path   TEXT,                     -- UC volume path of the archived file
    rows_received  INTEGER     NOT NULL DEFAULT 0,
    rows_accepted  INTEGER     NOT NULL DEFAULT 0,
    rows_rejected  INTEGER     NOT NULL DEFAULT 0,
    report         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    imported_by    TEXT        NOT NULL,
    imported_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS import_batch_campaign_idx
    ON import_batch (campaign_id, imported_at DESC);

-- --------------------------------------------------------------------------
-- Audit trail — append-only
-- --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_event (
    id          UUID PRIMARY KEY,
    campaign_id UUID        REFERENCES campaign (id) ON DELETE CASCADE,
    at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor       TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    entity_type TEXT        NOT NULL,
    entity_id   TEXT        NOT NULL DEFAULT '',
    summary     TEXT        NOT NULL DEFAULT '',
    before      JSONB,
    after       JSONB,
    request_id  TEXT
);
CREATE INDEX IF NOT EXISTS audit_campaign_idx ON audit_event (campaign_id, at DESC);
CREATE INDEX IF NOT EXISTS audit_entity_idx   ON audit_event (entity_type, entity_id, at DESC);
CREATE INDEX IF NOT EXISTS audit_actor_idx    ON audit_event (actor, at DESC);

-- Defence in depth: even a bug in the service layer cannot rewrite history.
CREATE OR REPLACE RULE audit_event_no_update AS
    ON UPDATE TO audit_event DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_event_no_delete AS
    ON DELETE TO audit_event DO INSTEAD NOTHING;

-- --------------------------------------------------------------------------
-- Derived reporting views
-- --------------------------------------------------------------------------

-- Effective counted quantity per (item, warehouse, location).
-- Journals in BOOK_ENFORCED status are handled by the service layer (their
-- counted quantity is the book quantity by definition) and are excluded here.
CREATE OR REPLACE VIEW v_counted_qty AS
SELECT
    j.campaign_id,
    l.item_number,
    j.warehouse_id,
    j.location_id,
    SUM(COALESCE(l.qty_manual, l.qty_imported, 0)) AS qty,
    MAX(l.unit)                                    AS unit
FROM count_journal_line l
JOIN count_journal j ON j.id = l.journal_id
WHERE l.deleted_at IS NULL
  AND j.status IN ('POSTED', 'IN_PROGRESS')
GROUP BY j.campaign_id, l.item_number, j.warehouse_id, j.location_id;

-- Counting progress, the headline indicator of the counting phase.
CREATE OR REPLACE VIEW v_journal_progress AS
SELECT
    c.id                                                            AS campaign_id,
    COUNT(j.id)                                                     AS total_journals,
    COUNT(j.id) FILTER (WHERE j.status IN ('POSTED','BOOK_ENFORCED')) AS complete_journals,
    COUNT(j.id) FILTER (WHERE j.status = 'IN_PROGRESS')             AS running_journals,
    COUNT(j.id) FILTER (WHERE j.status = 'PENDING')                 AS pending_journals
FROM campaign c
LEFT JOIN count_journal j ON j.campaign_id = c.id
GROUP BY c.id;

-- --------------------------------------------------------------------------
-- Seed: standard assignable causes (site referential, from BILAN INVENTAIRE)
-- --------------------------------------------------------------------------
INSERT INTO assignable_cause (code, label, family, display_order) VALUES
    ('1',  'Écarts réception',                 'Goods incoming',                        1),
    ('2',  'Écarts retours fournisseurs',      'Goods incoming',                        2),
    ('3',  'Écarts expéditions',               'Shipping',                              3),
    ('4',  'Écarts retours clients',           'Shipping',                              4),
    ('5',  'Écarts de comptage (scan)',        'Counting mistakes',                     5),
    ('6',  'Écarts de comptage (manuel)',      'Counting mistakes',                     6),
    ('7',  'Rebuts non déclarés',              'Production / quality status mistakes',  7),
    ('8',  'Encodage mauvais niveau BOM',      'Production / quality status mistakes',  8),
    ('9',  'Écart démarrage process',          'Industrialisation mistake',             9),
    ('10', 'Écart nomenclature',               'Industrialisation mistake',            10),
    ('11', 'Écart consommation (backflush)',   'ERP consumption mechanism failure',    11),
    ('12', 'Erreur inventaires précédents',    'Historical',                           12),
    ('13', 'Écart nul ou mineur',              'Non significant',                      13),
    ('99', 'Autre / à qualifier',              'Non significant',                      99)
ON CONFLICT (code) DO NOTHING;
