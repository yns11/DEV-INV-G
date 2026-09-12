-- 026 — Le journal ERP *est* le précomptage. Le lot disparaît.
--
-- La 025 posait un objet « lot » entre le journal et le scellement. Le métier a
-- tranché : un précomptage couvre exactement un journal ERP, qui couvre un ou
-- plusieurs emplacements. Le lot n'apportait donc rien qu'un regroupement dont
-- personne n'avait besoin, plus deux champs — la date du comptage et le
-- scellement — qui appartiennent au journal.
--
-- Trois conséquences, et cette migration les porte toutes :
--
-- 1. **Déclarer le périmètre vaut scellement.** Le journal porte donc
--    `sealed_at` / `sealed_by`, et `counted_on` — la date de comptage, lue dans
--    la colonne « Date de comptage » de ses propres lignes plutôt que retapée.
-- 2. **Ce qui pointait vers un lot pointe vers le journal.** La référence d'un
--    emplacement scellé et la dérive du jour J nomment le journal qui les a
--    posées.
-- 3. **Les étiquettes se traitent.** Une étiquette d'un emplacement scellé
--    recomptée ailleurs reçoit une issue, et cette issue survit aux réimports —
--    comme celle d'une dérive.
--
-- Les données existantes sont reportées avant toute suppression : une
-- installation qui a déjà scellé des lots garde ses références et ses dérives,
-- rattachées au journal qui portait le périmètre.

-- --------------------------------------------------------------------------
-- 1. Le journal porte la date et le scellement
-- --------------------------------------------------------------------------
ALTER TABLE erp_journal
    -- La date du relevé physique, pas celle de l'import ni celle du postage.
    -- C'est elle qui date la référence, donc l'inventaire d'un emplacement
    -- précompté. L'ERP la donne sur chaque ligne ; l'application la lisait déjà
    -- au contrat et la jetait, puis la redemandait à l'utilisateur.
    ADD COLUMN IF NOT EXISTS counted_on DATE,
    ADD COLUMN IF NOT EXISTS sealed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sealed_by  TEXT;

-- --------------------------------------------------------------------------
-- 2. Ce qui nommait un lot nomme le journal
-- --------------------------------------------------------------------------
ALTER TABLE book_stock
    ADD COLUMN IF NOT EXISTS erp_journal_id UUID;
ALTER TABLE early_count_drift
    ADD COLUMN IF NOT EXISTS erp_journal_id UUID;

-- Report : le journal d'un emplacement est celui dont le périmètre le contient.
UPDATE book_stock bs
SET erp_journal_id = s.erp_journal_id
FROM erp_journal_scope s
WHERE bs.erp_journal_id IS NULL
  AND bs.early_batch_id IS NOT NULL
  AND s.campaign_id = bs.campaign_id
  AND s.warehouse_id = bs.warehouse_id
  AND s.location_id = bs.location_id;

UPDATE early_count_drift d
SET erp_journal_id = s.erp_journal_id
FROM erp_journal_scope s
WHERE d.erp_journal_id IS NULL
  AND s.campaign_id = d.campaign_id
  AND s.warehouse_id = d.warehouse_id
  AND s.location_id = d.location_id;

-- Report du scellement et de sa date, depuis le lot vers le journal qui portait
-- ses emplacements. `MAX` parce qu'un lot pouvait couvrir plusieurs journaux :
-- chacun hérite du scellement du lot auquel ses emplacements appartenaient.
UPDATE erp_journal j
SET sealed_at  = COALESCE(j.sealed_at, agg.sealed_at),
    sealed_by  = COALESCE(j.sealed_by, agg.sealed_by),
    counted_on = COALESCE(j.counted_on, agg.counted_on)
FROM (
    SELECT s.erp_journal_id,
           MAX(b.sealed_at)  AS sealed_at,
           MIN(b.sealed_by)  AS sealed_by,
           MAX(b.counted_on) AS counted_on
    FROM erp_journal_scope s
    JOIN count_journal c
      ON c.campaign_id = s.campaign_id
     AND c.warehouse_id = s.warehouse_id
     AND c.location_id = s.location_id
    JOIN early_count_batch b ON b.id = c.early_batch_id
    WHERE b.deleted_at IS NULL
    GROUP BY s.erp_journal_id
) agg
WHERE agg.erp_journal_id = j.id;

CREATE INDEX IF NOT EXISTS book_stock_early_idx
    ON book_stock (campaign_id, erp_journal_id) WHERE erp_journal_id IS NOT NULL;

-- --------------------------------------------------------------------------
-- 3. L'issue d'une étiquette scellée recomptée ailleurs
-- --------------------------------------------------------------------------
-- Une étiquette rattachée à un emplacement scellé qui réapparaît dans un autre
-- journal — précomptage voisin ou comptage du jour J — pose une question à
-- laquelle seul un humain répond : où est la pièce ?
--
-- Trois réponses, et chacune a un effet mesurable :
--
-- * `KEEP_NEW`   — elle est au nouvel emplacement. L'étiquette sort de
--                  l'agrégation de l'emplacement scellé.
-- * `KEEP_SEALED`— elle est restée où elle était. L'étiquette sort de
--                  l'agrégation du nouvel emplacement.
-- * `RECOUNT`    — on ne tranche pas sur pièce. L'ancien emplacement rejoint la
--                  liste « à desceller et rescanner », et rien n'est exclu.
--
-- L'issue survit aux réimports, exactement comme celle d'une dérive : le
-- notebook est rejoué toutes les quelques minutes le jour J, et repartir de
-- zéro effacerait des décisions prises entre deux imports.
CREATE TABLE IF NOT EXISTS early_count_label_decision (
    id           UUID PRIMARY KEY,
    campaign_id  UUID        NOT NULL REFERENCES campaign (id) ON DELETE CASCADE,
    label_id     TEXT        NOT NULL,
    item_number  TEXT        NOT NULL,
    decision     TEXT        NOT NULL
        CHECK (decision IN ('KEEP_NEW','KEEP_SEALED','RECOUNT')),
    -- L'emplacement scellé d'origine, et celui où l'étiquette a reparu. Gardés
    -- tels quels : ils datent de la décision, et un réimport qui déplacerait
    -- encore l'étiquette ne doit pas réécrire ce qu'un humain a constaté.
    sealed_warehouse_id TEXT NOT NULL DEFAULT '',
    sealed_location_id  TEXT NOT NULL DEFAULT '',
    other_warehouse_id  TEXT NOT NULL DEFAULT '',
    other_location_id   TEXT NOT NULL DEFAULT '',
    comment      TEXT        NOT NULL DEFAULT '',
    decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by   TEXT        NOT NULL DEFAULT ''
);
-- Une étiquette porte un article et un seul : la décision se prend une fois.
CREATE UNIQUE INDEX IF NOT EXISTS early_count_label_decision_uq
    ON early_count_label_decision (campaign_id, label_id, item_number);
CREATE INDEX IF NOT EXISTS early_count_label_decision_recount_idx
    ON early_count_label_decision (campaign_id) WHERE decision = 'RECOUNT';

-- --------------------------------------------------------------------------
-- 4. Le lot s'en va
-- --------------------------------------------------------------------------
-- Après le report, et pas avant : une suppression qui précède la copie ne se
-- rattrape pas.
DROP INDEX IF EXISTS count_journal_early_idx;
ALTER TABLE count_journal   DROP COLUMN IF EXISTS early_batch_id;
ALTER TABLE book_stock      DROP COLUMN IF EXISTS early_batch_id;
ALTER TABLE early_count_drift DROP COLUMN IF EXISTS batch_id;
DROP TABLE IF EXISTS early_count_batch;
