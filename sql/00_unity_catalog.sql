-- =============================================================================
-- Unity Catalog assets — Campagnes Inventaire
-- -----------------------------------------------------------------------------
-- Run once per environment, on a SQL warehouse, as a user with CREATE SCHEMA on
-- the target catalog.
--
--   make uc WAREHOUSE_ID=<ID> PROFILE=<profil>
--   (or paste into the SQL editor)
--
-- `databricks sql query --file` n'existe pas : la CLI répond « unknown command
-- "sql" ». `make uc` passe par scripts/apply_unity_catalog.py, qui découpe ce
-- fichier et l'exécute instruction par instruction sur le warehouse.
--
-- Rejouable sans risque : tout est en CREATE ... IF NOT EXISTS ou CREATE OR
-- REPLACE VIEW. Les tables déjà présentes et leurs données ne sont pas touchées.
--
-- The catalog itself is NOT created here: this project only has rights to add
-- schemas, tables and volumes inside the existing `emotors_data_champions`.
-- Replace the catalog name below if yours differs.
--
-- Division of responsibilities
--   Lakebase (PostgreSQL)  everything written *during* a campaign — low latency,
--                          transactional, row-level edits, optimistic locking.
--   Delta / Unity Catalog  everything the campaign *leaves behind* — immutable
--                          snapshots, evidence, cross-campaign analytics, and
--                          the governed surface other teams can query.
--
-- The publish job (jobs/publish_campaign_to_delta.py) copies one into the other.
-- =============================================================================

USE CATALOG emotors_data_champions;

CREATE SCHEMA IF NOT EXISTS inventory
    COMMENT 'Campagnes d''inventaire physique : snapshots, comptages, écarts, preuves.';

USE SCHEMA inventory;

-- --------------------------------------------------------------------------
-- Evidence volume: scans of counting sheets, imported files, generated reports
-- --------------------------------------------------------------------------
CREATE VOLUME IF NOT EXISTS inventory_evidence
    COMMENT 'Preuves documentaires : scans de feuilles, fichiers importés, rapports générés.';
-- Suggested layout inside the volume — the app writes here and stores the path
-- next to the row it justifies:
--   /Volumes/<catalog>/inventory/inventory_evidence/<campaign_code>/scans/<sheet_id>.pdf
--   /Volumes/<catalog>/inventory/inventory_evidence/<campaign_code>/imports/<batch_id>-<filename>
--   /Volumes/<catalog>/inventory/inventory_evidence/<campaign_code>/reports/<name>.xlsx

-- --------------------------------------------------------------------------
-- Published campaign dossier (one row set per campaign, immutable once closed)
-- --------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campaign (
    campaign_id            STRING    NOT NULL COMMENT 'Identifiant technique (UUID)',
    code                   STRING    NOT NULL COMMENT 'Code métier, ex. INV-2026-06',
    label                  STRING            COMMENT 'Libellé de la campagne',
    count_date             DATE      NOT NULL COMMENT 'Jour J de l''inventaire physique',
    status                 STRING    NOT NULL COMMENT 'PREPARATION | COUNTING | ANALYSIS | CLOSED',
    referentials_frozen_at TIMESTAMP         COMMENT 'Gel des référentiels articles et BOM',
    book_stock_frozen_at   TIMESTAMP         COMMENT 'Gel du snapshot ERP',
    counting_frozen_at     TIMESTAMP         COMMENT 'Clôture de la phase de comptage',
    closed_at              TIMESTAMP         COMMENT 'Clôture définitive',
    cloned_from_code       STRING            COMMENT 'Campagne dont les référentiels ont été repris',
    engine_version         STRING    NOT NULL COMMENT 'Version du moteur de calcul ayant produit les données dérivées',
    created_by             STRING    NOT NULL,
    created_at             TIMESTAMP NOT NULL,
    published_at           TIMESTAMP NOT NULL COMMENT 'Horodatage de publication vers Delta'
)
USING DELTA
COMMENT 'Une ligne par campagne : son cycle de vie et ses horodatages de gel.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);

-- La publication elle-même : une ligne par campagne archivée.
--
-- Écrite **en dernier** par le job, et par rien d'autre. Delta n'offre pas de
-- transaction couvrant plusieurs tables : une panne au milieu de la publication
-- laisse quelques tables à la nouvelle version et les autres à l'ancienne.
-- Cette table est ce qui empêche un tel dossier de se faire passer pour
-- complet — une campagne est publiée si, et seulement si, elle y figure.
--
-- La reprise est propre : chaque table est réécrite par `replaceWhere` sur le
-- même `campaign_id`, donc rejouer le job écrase l'exécution interrompue.
CREATE TABLE IF NOT EXISTS publication (
    campaign_id    STRING    NOT NULL COMMENT 'Identifiant technique de la campagne publiée',
    campaign_code  STRING    NOT NULL COMMENT 'Code métier, pour la lecture humaine',
    published_at   TIMESTAMP NOT NULL COMMENT 'Fin de la publication, pas son début',
    engine_version STRING             COMMENT 'Version du moteur ayant produit les données dérivées',
    table_count    INT       NOT NULL COMMENT 'Nombre de tables écrites',
    row_total      BIGINT    NOT NULL COMMENT 'Total des lignes, toutes tables confondues',
    row_counts     MAP<STRING, BIGINT> COMMENT 'Décompte table par table : « l''archive est-elle fidèle » sans relire les neuf tables'
)
USING DELTA
COMMENT 'Manifeste de publication. Une campagne absente d''ici n''est pas archivée, quelles que soient les lignes présentes ailleurs.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS book_stock_snapshot (
    campaign_id  STRING        NOT NULL,
    campaign_code STRING       NOT NULL,
    count_date   DATE          NOT NULL,
    item_number  STRING        NOT NULL,
    warehouse_id STRING        NOT NULL,
    location_id  STRING,
    qty          DECIMAL(20,6) NOT NULL,
    unit         STRING,
    unit_cost    DECIMAL(20,2) NOT NULL COMMENT 'Coût figé au moment du snapshot',
    value        DECIMAL(20,2) NOT NULL COMMENT 'qty × unit_cost, matérialisé pour l''analyse',
    reference_date DATE COMMENT 'Date de la référence. Le jour J pour la plupart des lignes, la date du précomptage pour un emplacement scellé : une campagne qui précompte a une référence composite.',
    early_batch_id STRING COMMENT 'Le lot de comptage avancé dont vient cette référence, s''il y en a un',
    published_at TIMESTAMP     NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Photographie immuable du stock ERP au moment du gel. Ne jamais mettre à jour.'
TBLPROPERTIES (delta.autoOptimize.optimizeWrite = true);

CREATE TABLE IF NOT EXISTS item_snapshot (
    campaign_id     STRING NOT NULL,
    campaign_code   STRING NOT NULL,
    item_number     STRING NOT NULL,
    name            STRING,
    item_type       STRING COMMENT 'COMPONENT | SEMI_FINISHED | FINISHED | PACKAGING | UNKNOWN',
    category        STRING COMMENT 'MEL, STATOR, ROTOR, ONDULEUR…',
    program         STRING COMMENT 'M2BEV, M3, M4, M3GEN2, M2ERAD… vide = commun',
    commonality     STRING,
    unit            STRING,
    std_price       DECIMAL(20,2),
    exclusions      ARRAY<STRING> COMMENT 'Sous-ensemble de {GENERIC, BOM, ALL}',
    published_at    TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Référentiel articles tel qu''il était pour cette campagne.';

CREATE TABLE IF NOT EXISTS bom_snapshot (
    campaign_id   STRING NOT NULL,
    campaign_code STRING NOT NULL,
    parent_item   STRING NOT NULL,
    child_item    STRING NOT NULL,
    qty_per       DECIMAL(20,6) NOT NULL,
    unit          STRING,
    published_at  TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Nomenclatures effectives de la campagne — ce qui a servi à éclater le WIP.';

CREATE TABLE IF NOT EXISTS count_result (
    campaign_id   STRING NOT NULL,
    campaign_code STRING NOT NULL,
    count_date    DATE   NOT NULL,
    item_number   STRING NOT NULL,
    warehouse_id  STRING NOT NULL,
    location_id   STRING,
    journal_kind  STRING COMMENT 'INVE (scan) | INVV (vrac)',
    journal_status STRING COMMENT 'POSTED | BOOK_ENFORCED | …',
    journal_number STRING,
    qty_imported  DECIMAL(20,6) COMMENT 'Valeur telle qu''importée de l''ERP',
    qty_manual    DECIMAL(20,6) COMMENT 'Correction humaine, NULL si aucune',
    qty           DECIMAL(20,6) NOT NULL COMMENT 'Quantité retenue',
    unit          STRING,
    source        STRING COMMENT 'ERP_IMPORT | MANUAL | SCAN_AI | CONSOLIDATION | SYSTEM',
    qty_on_hand   DECIMAL(20,6) COMMENT 'Le stock ERP d''avant comptage, porté par le journal lui-même. NULL = aucune référence ERP connue pour cette ligne, ce qui n''est pas zéro.',
    erp_journal_number STRING COMMENT 'Le journal ERP d''où vient la ligne',
    label_count   INT COMMENT 'Nombre de lignes ERP — donc d''étiquettes — agrégées ici',
    sealed_at     TIMESTAMP COMMENT 'Renseigné pour un emplacement précompté et scellé',
    early_batch_id STRING,
    published_at  TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Comptages retenus, avec la valeur importée et la correction humaine côte à côte.';

-- --------------------------------------------------------------------------
-- Comptages avancés
-- --------------------------------------------------------------------------
-- Compter certains emplacements avant le jour J, sans éclater le dossier entre
-- plusieurs campagnes. Ces trois tables sont ce qui rend le raisonnement
-- rejouable : sans elles, l'archive ne dirait ni contre quoi un emplacement
-- précompté a été compté, ni ce qu'on a décidé de l'écart constaté le jour J.

CREATE TABLE IF NOT EXISTS early_count_batch (
    campaign_id   STRING NOT NULL,
    campaign_code STRING NOT NULL,
    batch_id      STRING NOT NULL,
    code          STRING NOT NULL,
    label         STRING,
    counted_on    DATE COMMENT 'La date du comptage physique du lot',
    opened_at     TIMESTAMP,
    opened_by     STRING,
    closed_at     TIMESTAMP,
    closed_by     STRING,
    sealed_at     TIMESTAMP COMMENT 'Le scellement : à partir de là, le comptage ne bouge plus sans descellement tracé',
    sealed_by     STRING,
    published_at  TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Les lots de comptage avancé d''une campagne.';

CREATE TABLE IF NOT EXISTS early_count_drift (
    campaign_id     STRING NOT NULL,
    campaign_code   STRING NOT NULL,
    batch_id        STRING,
    warehouse_id    STRING NOT NULL,
    location_id     STRING NOT NULL,
    item_number     STRING NOT NULL,
    qty_erp_t0      DECIMAL(20,6) COMMENT 'La référence : stock ERP d''avant le précomptage',
    qty_physical_t0 DECIMAL(20,6) COMMENT 'Compté + ajusté à T0',
    qty_erp_j       DECIMAL(20,6) COMMENT 'Stock ERP du snapshot général, gelé le jour J',
    drift_qty       DECIMAL(20,6) COMMENT 'ERP@J − physique@T0. Attendue nulle : l''emplacement était balisé, et poster son journal a réaligné l''ERP sur le physique compté.',
    drift_value     DECIMAL(20,2),
    is_material     BOOLEAN COMMENT 'Aux seuils de la campagne, pas à un réglage à part',
    resolution      STRING COMMENT 'KEEP_EARLY (le comptage avancé fait foi) | RECOUNT (l''emplacement rejoint le comptage général)',
    cause_code      STRING COMMENT 'Obligatoire pour KEEP_EARLY : cette issue laisse la campagne et l''ERP en désaccord',
    comment         STRING,
    resolved_at     TIMESTAMP,
    resolved_by     STRING,
    published_at    TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'L''écart entre ce que l''ERP dit d''un emplacement scellé le jour J et le physique qui y a été posté.';

CREATE TABLE IF NOT EXISTS erp_journal_scope (
    campaign_id    STRING NOT NULL,
    campaign_code  STRING NOT NULL,
    journal_number STRING NOT NULL,
    kind           STRING COMMENT 'INVE (étiquettes) | INVV (vrac)',
    erp_posted     BOOLEAN COMMENT 'Le postage tel que l''en-tête ERP le déclare',
    warehouse_id   STRING NOT NULL,
    location_id    STRING NOT NULL,
    published_at   TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Les emplacements qu''un journal ERP couvre réellement, tels qu''un humain les a déclarés — ils ne se déduisent pas de ses lignes.';

CREATE TABLE IF NOT EXISTS wip_breakdown (
    campaign_id    STRING NOT NULL,
    campaign_code  STRING NOT NULL,
    zone_code      STRING,
    parent_item    STRING NOT NULL,
    parent_qty     DECIMAL(20,6) NOT NULL,
    child_item     STRING NOT NULL,
    qty_per_parent DECIMAL(20,6) NOT NULL,
    child_qty      DECIMAL(20,6) NOT NULL,
    published_at   TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Traçabilité de l''éclatement du WIP : quel assemblage a produit quelle quantité de quel composant.';

CREATE TABLE IF NOT EXISTS adjustment (
    campaign_id    STRING NOT NULL,
    campaign_code  STRING NOT NULL,
    item_number    STRING NOT NULL,
    warehouse_id   STRING,
    location_id    STRING,
    kind           STRING COMMENT 'COUNT | ADJUSTMENT | RECOUNT | OTHER',
    qty            DECIMAL(20,6) NOT NULL COMMENT 'Signée : négatif = diminution',
    unit           STRING,
    value          DECIMAL(20,2) NOT NULL COMMENT 'Signée',
    journal_number STRING,
    physical_date  DATE,
    reason_code    STRING,
    published_at   TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Mouvements de stock postés après comptage.';

CREATE TABLE IF NOT EXISTS variance_analysis (
    campaign_id        STRING NOT NULL,
    campaign_code      STRING NOT NULL,
    item_number        STRING NOT NULL,
    cause_code         STRING COMMENT 'Décision humaine',
    comment            STRING,
    analyst            STRING,
    accepted           BOOLEAN,
    ai_suggested_cause STRING COMMENT 'Proposition IA, jamais confondue avec la décision',
    ai_confidence      FLOAT,
    ai_rationale       STRING,
    published_at       TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Analyse des écarts : la cause retenue par un humain et, à côté, ce que l''IA avait proposé.';

CREATE TABLE IF NOT EXISTS audit_event (
    campaign_id   STRING,
    campaign_code STRING,
    event_id      STRING NOT NULL,
    at            TIMESTAMP NOT NULL,
    actor         STRING NOT NULL,
    action        STRING NOT NULL,
    entity_type   STRING NOT NULL,
    entity_id     STRING,
    summary       STRING,
    published_at  TIMESTAMP NOT NULL
)
USING DELTA
PARTITIONED BY (campaign_id)
COMMENT 'Journal d''audit archivé. Append-only côté Lakebase, append-only ici.';

-- --------------------------------------------------------------------------
-- Analytical views — the governed surface for dashboards and cross-campaign work
-- --------------------------------------------------------------------------

-- Reconciled variance, at article granularity (the financial view: a transfer
-- between two bins is not a variance).
CREATE OR REPLACE VIEW v_variance AS
-- Les regroupements et les jointures portent sur `campaign_id`, jamais sur le
-- code : un code métier se réutilise après une suppression logique, et deux
-- campagnes homonymes verraient leurs stocks et leurs comptages additionnés
-- dans la même ligne d'écart.
WITH book AS (
    SELECT campaign_id, MAX(campaign_code) AS campaign_code, count_date, item_number,
           SUM(qty) AS book_qty,
           SUM(value) AS book_value,
           MAX(unit_cost) AS unit_cost,
           MAX(unit) AS unit
    FROM book_stock_snapshot
    GROUP BY campaign_id, count_date, item_number
),
counted AS (
    SELECT campaign_id, item_number, SUM(qty) AS counted_qty
    FROM count_result
    GROUP BY campaign_id, item_number
),
adjusted AS (
    SELECT campaign_id, item_number, SUM(qty) AS adjusted_qty
    FROM adjustment
    GROUP BY campaign_id, item_number
)
SELECT
    COALESCE(b.campaign_id, c.campaign_id)                  AS campaign_id,
    b.campaign_code,
    b.count_date,
    COALESCE(b.item_number, c.item_number)                  AS item_number,
    i.name,
    i.item_type,
    i.category,
    i.program,
    COALESCE(b.unit, i.unit)                                AS unit,
    COALESCE(b.unit_cost, i.std_price, 0)                   AS unit_cost,
    COALESCE(b.book_qty, 0)                                 AS book_qty,
    COALESCE(b.book_value, 0)                               AS book_value,
    COALESCE(c.counted_qty, 0)                              AS counted_qty,
    COALESCE(c.counted_qty, 0) - COALESCE(b.book_qty, 0)    AS variance_qty,
    (COALESCE(c.counted_qty, 0) - COALESCE(b.book_qty, 0))
        * COALESCE(b.unit_cost, i.std_price, 0)             AS variance_value,
    COALESCE(a.adjusted_qty, 0)                             AS adjusted_qty,
    (COALESCE(c.counted_qty, 0) - COALESCE(b.book_qty, 0) - COALESCE(a.adjusted_qty, 0))
        * COALESCE(b.unit_cost, i.std_price, 0)             AS residual_value,
    b.item_number IS NULL                                   AS counted_only,
    c.item_number IS NULL                                   AS book_only
FROM book b
FULL OUTER JOIN counted c
  ON b.campaign_id = c.campaign_id AND b.item_number = c.item_number
LEFT JOIN adjusted a
  ON COALESCE(b.campaign_id, c.campaign_id) = a.campaign_id
 AND COALESCE(b.item_number, c.item_number) = a.item_number
LEFT JOIN item_snapshot i
  ON COALESCE(b.campaign_id, c.campaign_id) = i.campaign_id
 AND COALESCE(b.item_number, c.item_number) = i.item_number;

COMMENT ON VIEW v_variance IS
    'Écarts réconciliés par article. Recalculable à l''identique depuis les snapshots.';

-- Campaign-level KPIs. The three reliability measures answer three different
-- questions and are deliberately kept apart — see docs/02-data-model.md.
CREATE OR REPLACE VIEW v_campaign_kpi AS
SELECT
    campaign_id,
    MAX(campaign_code)                                     AS campaign_code,
    MAX(count_date)                                        AS count_date,
    SUM(book_qty)                                          AS book_qty,
    SUM(book_value)                                        AS book_value,
    SUM(counted_qty)                                       AS counted_qty,
    SUM(variance_qty)                                      AS net_variance_qty,
    SUM(variance_value)                                    AS net_variance_value,
    SUM(ABS(variance_qty))                                 AS gross_variance_qty,
    SUM(ABS(variance_value))                               AS gross_variance_value,
    SUM(residual_value)                                    AS residual_value,
    -- Optimistic: surpluses offset shortages.
    1 - ABS(SUM(variance_value)) / NULLIF(ABS(SUM(book_value)), 0)
                                                           AS net_reliability_value,
    -- Honest: every error counts, in both directions. Steer on this one.
    1 - SUM(ABS(variance_value)) / NULLIF(ABS(SUM(book_value)), 0)
                                                           AS gross_reliability_value,
    COUNT(*)                                               AS line_count,
    COUNT_IF(variance_qty = 0)                             AS exact_line_count,
    COUNT_IF(variance_qty = 0) / NULLIF(COUNT(*), 0)       AS ira,
    COUNT_IF(book_only)                                    AS book_only_count,
    COUNT_IF(counted_only)                                 AS counted_only_count
FROM v_variance
GROUP BY campaign_id;

COMMENT ON VIEW v_campaign_kpi IS
    'Indicateurs de campagne. Trois mesures de fiabilité distinctes : nette (compensée), brute (absolue) et IRA (exactitude des enregistrements).';

-- Cross-campaign recurrence: an article whose variance keeps the same sign
-- across campaigns has a structural leak, not an accident.
CREATE OR REPLACE VIEW v_variance_recurrence AS
SELECT
    item_number,
    COUNT(DISTINCT campaign_id)                          AS campaigns,
    SUM(variance_value)                                    AS cumulative_variance_value,
    SUM(ABS(variance_value))                               AS cumulative_abs_variance_value,
    COUNT_IF(variance_value < 0)                           AS shortage_campaigns,
    COUNT_IF(variance_value > 0)                           AS surplus_campaigns,
    CASE
        WHEN COUNT_IF(variance_value < 0) = COUNT(DISTINCT campaign_id)
             AND COUNT(DISTINCT campaign_id) > 1 THEN 'manquant récurrent'
        WHEN COUNT_IF(variance_value > 0) = COUNT(DISTINCT campaign_id)
             AND COUNT(DISTINCT campaign_id) > 1 THEN 'excédent récurrent'
        WHEN COUNT(DISTINCT campaign_id) > 1 THEN 'alternant'
        ELSE 'ponctuel'
    END                                                    AS pattern
FROM v_variance
WHERE variance_value <> 0
GROUP BY item_number;

COMMENT ON VIEW v_variance_recurrence IS
    'Récurrence des écarts par article, toutes campagnes confondues : distingue une fuite structurelle d''un accident.';

-- Where the WIP explosion sends value — the question the legacy tool could not
-- answer at all.
CREATE OR REPLACE VIEW v_wip_contribution AS
SELECT
    w.campaign_id,
    MAX(w.campaign_code)                                   AS campaign_code,
    w.child_item                                           AS item_number,
    i.name,
    SUM(w.child_qty)                                       AS wip_qty,
    SUM(w.child_qty) * MAX(COALESCE(i.std_price, 0))       AS wip_value,
    COUNT(DISTINCT w.parent_item)                          AS parent_count,
    COLLECT_SET(w.zone_code)                               AS zones
FROM wip_breakdown w
LEFT JOIN item_snapshot i
  ON w.campaign_id = i.campaign_id AND w.child_item = i.item_number
GROUP BY w.campaign_id, w.child_item, i.name;

COMMENT ON VIEW v_wip_contribution IS
    'Quantité et valeur créditées à chaque composant par l''éclatement du WIP, avec les assemblages et zones d''origine.';
