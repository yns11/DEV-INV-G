"""Repositories — the only place that knows SQL.

Each repository maps one aggregate between Lakebase rows and domain models.
Services above call them; nothing below them exists. Three rules are enforced
consistently across all of them:

* **Bulk over loops.** Imports use ``COPY`` or multi-row ``INSERT … ON CONFLICT``
  so loading a 100 000-line ERP export is one round trip, not 100 000.
* **Logical deletes.** ``deleted_at`` is set; rows never disappear, so the audit
  trail always resolves and a mistaken deletion is one UPDATE away from undone.
* **Optimistic concurrency.** Mutating writes check ``row_version``; two people
  editing the same journal line get a 409, not a silent last-write-wins.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, ClassVar

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..domain.enums import (
    AuditAction,
    CampaignStatus,
    CountSection,
    DataSource,
    FlowKind,
    ItemType,
    JournalKind,
    JournalStatus,
    LocationStatus,
    SheetPass,
)
from ..domain.models import (
    AdjustmentLine,
    ArbitrationLine,
    AssignableCause,
    AuditEvent,
    BackflushLine,
    BomLink,
    BookStockLine,
    Campaign,
    CampaignConfig,
    ConsolidatedLine,
    CountJournal,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
    Item,
    Location,
    LocationKey,
    Manager,
    StockFlowErp,
    StockFlowInput,
    StockFlowRun,
    Thresholds,
    VarianceAnalysis,
    Warehouse,
    WipBreakdown,
    Zone,
    in_perimeter,
)
from ..errors import ConflictError, NotFoundError
from .engine import Database

__all__ = [
    "new_id",
    "CampaignRepository",
    "ReferentialRepository",
    "BookStockRepository",
    "JournalRepository",
    "SheetRepository",
    "ConsolidationRepository",
    "AdjustmentRepository",
    "AnalysisRepository",
    "BackflushRepository",
    "StockFlowRepository",
    "AuditRepository",
    "ImportBatchRepository",
    "ScanJobRepository",
    "OperationsRepository",
]


def new_id() -> str:
    """A fresh technical identifier (UUID4 as text)."""
    return str(uuid.uuid4())


class _Base:
    """Shared plumbing: connection access and row → model helpers."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- low-level helpers ---------------------------------------------------

    def _fetch_all(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        if conn is not None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                return list(cur.fetchall())
        with self.db.cursor() as cur:
            cur.execute(query, params)
            return list(cur.fetchall())

    def _fetch_one(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> dict[str, Any] | None:
        rows = self._fetch_all(query, params, conn=conn)
        return rows[0] if rows else None

    def _execute(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.rowcount
        with self.db.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    def _execute_many(
        self, query: str, rows: Sequence[Sequence[Any]],
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        if not rows:
            return 0
        if conn is not None:
            with conn.cursor() as cur:
                cur.executemany(query, rows)
                return cur.rowcount
        with self.db.cursor() as cur:
            cur.executemany(query, rows)
            return cur.rowcount


# --------------------------------------------------------------------------- #
# Campaign
# --------------------------------------------------------------------------- #

class CampaignRepository(_Base):
    """Campaigns, their configuration and their materiality thresholds."""

    _COLUMNS = (
        "id, code, label, count_date, status, config, referentials_frozen_at, "
        "book_stock_frozen_at, counting_frozen_at, closed_at, published_at, "
        "cloned_from_code, "
        "engine_version, created_by, created_at, updated_at, row_version"
    )

    def list(
        self, *, include_closed: bool = True, limit: int = 100, offset: int = 0
    ) -> list[Campaign]:
        """Les campagnes, sans le total.

        ``page`` rend une paire parce qu'un écran qui pagine a besoin des deux.
        Un service qui cherche « la campagne précédente » n'a que faire du
        total, et itérer la paire lui donne silencieusement deux éléments dont
        le premier est une liste — ``other.id`` lève alors sur un ``list``, en
        production, sur trois écrans à la fois. Le nom dit désormais ce qu'on
        reçoit.
        """
        campaigns, _ = self.page(
            include_closed=include_closed, limit=limit, offset=offset
        )
        return campaigns

    def page(
        self, *, include_closed: bool = True, limit: int = 100, offset: int = 0
    ) -> tuple[list[Campaign], int]:
        """Une page de campagnes, et combien il y en a en tout.

        Le total n'est pas décoratif : la liste était bornée à cent sans le
        dire, si bien qu'après quelques années d'inventaires trimestriels les
        plus anciennes cessaient simplement d'apparaître. Aucun message, aucun
        bouton — elles n'existaient plus pour qui regardait l'écran, alors
        qu'elles étaient toujours en base.

        Pagination par décalage plutôt que par curseur : cette liste s'allonge
        de quelques lignes par an et se trie sur une date de comptage stable.
        La dérive qu'un curseur évite — une ligne insérée entre deux pages —
        suppose des écritures concurrentes fréquentes, ce qui n'est pas le cas
        ici, et coûterait un encodage de curseur que personne ne lirait.
        """
        clause = "" if include_closed else "AND status <> 'CLOSED'"
        total = self._fetch_one(
            f"SELECT count(*) AS n FROM campaign WHERE deleted_at IS NULL {clause}"
        )
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM campaign "
            f"WHERE deleted_at IS NULL {clause} "
            "ORDER BY count_date DESC, created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return [self._to_model(r) for r in rows], int(total["n"]) if total else 0

    def get(self, campaign_id: str) -> Campaign:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM campaign WHERE id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        if row is None:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)
        campaign = self._to_model(row)
        campaign.thresholds = self.list_thresholds(campaign_id)
        return campaign

    def get_by_code(self, code: str) -> Campaign | None:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM campaign "
            "WHERE code = %s AND deleted_at IS NULL",
            (code.upper(),),
        )
        if row is None:
            return None
        campaign = self._to_model(row)
        campaign.thresholds = self.list_thresholds(campaign.id)
        return campaign

    def create(self, campaign: Campaign, *, conn: psycopg.Connection | None = None) -> Campaign:
        self._execute(
            "INSERT INTO campaign (id, code, label, count_date, status, config, "
            "cloned_from_code, engine_version, created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                campaign.id, campaign.code, campaign.label, campaign.count_date,
                str(campaign.status), Jsonb(campaign.config.model_dump(mode="json")),
                campaign.cloned_from_code, campaign.engine_version,
                campaign.created_by, campaign.created_at, campaign.created_at,
            ),
            conn=conn,
        )
        self.replace_thresholds(campaign.id, campaign.thresholds, conn=conn)
        return campaign

    def update_status(
        self,
        campaign_id: str,
        status: CampaignStatus,
        *,
        actor: str,
        timestamps: dict[str, dt.datetime] | None = None,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Move a campaign to *status* and stamp the matching freeze timestamp."""
        sets = ["status = %s", "updated_by = %s", "updated_at = now()",
                "row_version = row_version + 1"]
        params: list[Any] = [str(status), actor]
        for column, value in (timestamps or {}).items():
            if column not in {
                "referentials_frozen_at", "book_stock_frozen_at",
                "counting_frozen_at", "closed_at", "published_at",
            }:
                raise ValueError(f"unexpected freeze column {column!r}")
            sets.append(f"{column} = %s")
            params.append(value)
        params.append(campaign_id)
        n = self._execute(
            f"UPDATE campaign SET {', '.join(sets)} WHERE id = %s AND deleted_at IS NULL",
            params,
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)

    def soft_delete(
        self, campaign_id: str, *, actor: str, conn: psycopg.Connection | None = None
    ) -> None:
        """Retire a campaign without destroying it.

        Everything the campaign carries — counts, journals, audit entries — stays
        on disk, because a campaign that was closed on figures somebody signed
        cannot be made to have never existed. The row simply stops being returned
        by :meth:`list` and :meth:`get`, which frees its code for reuse.
        """
        n = self._execute(
            "UPDATE campaign SET deleted_at = now(), updated_by = %s, "
            "updated_at = now(), row_version = row_version + 1 "
            "WHERE id = %s AND deleted_at IS NULL",
            (actor, campaign_id),
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)

    def set_cloned_from(
        self, campaign_id: str, source_code: str, *, conn: psycopg.Connection | None = None
    ) -> None:
        """Record which campaign this one was duplicated from."""
        self._execute(
            "UPDATE campaign SET cloned_from_code = %s, updated_at = now() "
            "WHERE id = %s",
            (source_code, campaign_id),
            conn=conn,
        )

    # -- thresholds ----------------------------------------------------------

    def list_thresholds(self, campaign_id: str) -> list[Thresholds]:
        rows = self._fetch_all(
            "SELECT item_type, value_abs_eur, qty_relative "
            "FROM threshold WHERE campaign_id = %s ORDER BY item_type",
            (campaign_id,),
        )
        return [
            Thresholds(
                item_type=ItemType(r["item_type"]),
                value_abs_eur=r["value_abs_eur"],
                qty_relative=r["qty_relative"],
            )
            for r in rows
        ]

    def replace_thresholds(
        self,
        campaign_id: str,
        thresholds: Sequence[Thresholds],
        *,
        actor: str = "system",
        conn: psycopg.Connection | None = None,
    ) -> None:
        self._execute_many(
            # `qty_abs_floor` and `ira_tolerance` are no longer written: they
            # were two dials nobody turned, and the columns keep their defaults
            # rather than being dropped, so an older campaign stays readable.
            "INSERT INTO threshold (campaign_id, item_type, value_abs_eur, "
            "qty_relative, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (campaign_id, item_type) DO UPDATE SET "
            "value_abs_eur = EXCLUDED.value_abs_eur, "
            "qty_relative = EXCLUDED.qty_relative, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            [
                (campaign_id, str(t.item_type), t.value_abs_eur, t.qty_relative, actor)
                for t in thresholds
            ],
            conn=conn,
        )

    # -- mapping -------------------------------------------------------------

    @staticmethod
    def _to_model(row: dict[str, Any]) -> Campaign:
        config = row.get("config") or {}
        if isinstance(config, str):
            config = json.loads(config)
        return Campaign(
            id=str(row["id"]),
            code=row["code"],
            label=row["label"],
            count_date=row["count_date"],
            status=CampaignStatus(row["status"]),
            config=CampaignConfig(**config) if config else CampaignConfig(),
            referentials_frozen_at=row["referentials_frozen_at"],
            book_stock_frozen_at=row["book_stock_frozen_at"],
            counting_frozen_at=row["counting_frozen_at"],
            closed_at=row["closed_at"],
            published_at=row["published_at"],
            cloned_from_code=row["cloned_from_code"],
            engine_version=row["engine_version"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# --------------------------------------------------------------------------- #
# Referentials
# --------------------------------------------------------------------------- #

class ReferentialRepository(_Base):
    """Articles, bills of materials, warehouses and locations."""

    # -- items ---------------------------------------------------------------

    _ITEM_COLUMNS = (
        "campaign_id, item_number, name, search_name, item_group, lifecycle_state, "
        "item_type, category, program, commonality, unit, std_price, exclusions, source"
    )

    def list_items(
        self, campaign_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[Item]:
        query = (
            f"SELECT {self._ITEM_COLUMNS} FROM item "
            "WHERE campaign_id = %s AND deleted_at IS NULL ORDER BY item_number"
        )
        params: list[Any] = [campaign_id]
        if limit is not None:
            query += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return [self._item(r) for r in self._fetch_all(query, params)]

    def items_by_number(self, campaign_id: str) -> dict[str, Item]:
        """The whole article referential as a lookup map.

        Loading ~2 000 rows once beats issuing 2 000 point queries during a
        consolidation; the referential is frozen during counting so the map
        cannot go stale mid-run.
        """
        return {i.item_number: i for i in self.list_items(campaign_id)}

    def items_in_scope(self, campaign_id: str) -> dict[str, Item]:
        """:func:`~inventory.domain.models.in_perimeter` applied to the campaign."""
        return in_perimeter(self.items_by_number(campaign_id))

    def count_items(self, campaign_id: str) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM item WHERE campaign_id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        return int(row["n"]) if row else 0

    def upsert_items(
        self,
        items: Iterable[Item],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        rows = [
            (
                i.campaign_id, i.item_number, i.name, i.search_name, i.item_group,
                i.lifecycle_state, str(i.item_type), i.category, i.program,
                str(i.commonality), i.unit, i.std_price,
                sorted(str(e) for e in i.exclusions), str(i.source), actor,
            )
            for i in items
        ]
        return self._execute_many(
            "INSERT INTO item (campaign_id, item_number, name, search_name, "
            "item_group, lifecycle_state, item_type, category, program, commonality, "
            "unit, std_price, exclusions, source, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, item_number) DO UPDATE SET "
            "name = EXCLUDED.name, search_name = EXCLUDED.search_name, "
            "item_group = EXCLUDED.item_group, "
            "lifecycle_state = EXCLUDED.lifecycle_state, "
            "item_type = EXCLUDED.item_type, category = EXCLUDED.category, "
            "program = EXCLUDED.program, commonality = EXCLUDED.commonality, "
            "unit = EXCLUDED.unit, std_price = EXCLUDED.std_price, "
            "exclusions = EXCLUDED.exclusions, source = EXCLUDED.source, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "row_version = item.row_version + 1, deleted_at = NULL",
            rows,
            conn=conn,
        )

    def get_item(self, campaign_id: str, item_number: str) -> Item | None:
        """One article, by its business key.

        A targeted read rather than filtering the whole referential in Python:
        editing a single line should not load fifteen hundred rows.
        """
        row = self._fetch_one(
            "SELECT * FROM item WHERE campaign_id = %s AND item_number = %s "
            "AND deleted_at IS NULL",
            (campaign_id, item_number),
        )
        return self._item(row) if row else None

    def delete_item(self, campaign_id: str, item_number: str, *, actor: str) -> None:
        self._execute(
            "UPDATE item SET deleted_at = now(), updated_by = %s WHERE campaign_id = %s "
            "AND item_number = %s AND deleted_at IS NULL",
            (actor, campaign_id, item_number),
        )

    # -- BOM -----------------------------------------------------------------

    def list_bom_links(self, campaign_id: str) -> list[BomLink]:
        rows = self._fetch_all(
            "SELECT id, campaign_id, parent_item, child_item, qty_per, unit, "
            "level, active FROM bom_link WHERE campaign_id = %s "
            "AND deleted_at IS NULL "
            "ORDER BY parent_item, child_item",
            (campaign_id,),
        )
        return [
            BomLink(
                campaign_id=str(r["campaign_id"]),
                parent_item=r["parent_item"],
                child_item=r["child_item"],
                qty_per=r["qty_per"],
                unit=r["unit"],
                level=r["level"],
                active=r["active"],
            )
            for r in rows
        ]

    def upsert_bom_links(
        self,
        links: Iterable[BomLink],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        rows = [
            (new_id(), l.campaign_id, l.parent_item, l.child_item, l.qty_per,
             l.unit, l.level, l.active, actor)
            for l in links
        ]
        return self._execute_many(
            "INSERT INTO bom_link (id, campaign_id, parent_item, child_item, "
            "qty_per, unit, level, active, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, parent_item, child_item) "
            "WHERE deleted_at IS NULL DO UPDATE SET "
            "qty_per = EXCLUDED.qty_per, unit = EXCLUDED.unit, "
            "level = EXCLUDED.level, active = EXCLUDED.active, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            rows,
            conn=conn,
        )

    def get_bom_link(
        self, campaign_id: str, parent_item: str, child_item: str
    ) -> BomLink | None:
        row = self._fetch_one(
            "SELECT campaign_id, parent_item, child_item, qty_per, unit, level, "
            "active FROM bom_link WHERE campaign_id = %s AND parent_item = %s "
            "AND child_item = %s AND deleted_at IS NULL",
            (campaign_id, parent_item, child_item),
        )
        if row is None:
            return None
        return BomLink(
            campaign_id=str(row["campaign_id"]),
            parent_item=row["parent_item"],
            child_item=row["child_item"],
            qty_per=row["qty_per"],
            unit=row["unit"],
            level=row["level"],
            active=row["active"],
        )

    def delete_bom_link(
        self, campaign_id: str, parent_item: str, child_item: str, *, actor: str
    ) -> int:
        """Logical deletion of one edge, so the history keeps what was there."""
        return self._execute(
            "UPDATE bom_link SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND parent_item = %s AND child_item = %s "
            "AND deleted_at IS NULL",
            (actor, campaign_id, parent_item, child_item),
        )

    def clear_bom(
        self, campaign_id: str, *, actor: str, conn: psycopg.Connection | None = None
    ) -> int:
        """Retire every bill-of-materials link of a campaign.

        Takes the caller's connection. Without it the deletion commits on its
        own while the replacement rows are still inside an open transaction: a
        failure there rolls the insert back and leaves the campaign with no
        nomenclature at all, which is worse than the state it started from.
        """
        return self._execute(
            "UPDATE bom_link SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND deleted_at IS NULL",
            (actor, campaign_id),
            conn=conn,
        )

    # -- warehouses & locations ---------------------------------------------

    def list_warehouses(self, campaign_id: str) -> list[Warehouse]:
        rows = self._fetch_all(
            "SELECT campaign_id, warehouse_id, label, type, status FROM warehouse "
            "WHERE campaign_id = %s ORDER BY warehouse_id",
            (campaign_id,),
        )
        return [
            Warehouse(
                campaign_id=str(r["campaign_id"]), warehouse_id=r["warehouse_id"],
                label=r["label"], type=r["type"], status=r["status"],
            )
            for r in rows
        ]

    def list_locations(self, campaign_id: str) -> list[Location]:
        rows = self._fetch_all(
            "SELECT campaign_id, warehouse_id, location_id, zone, type, status, source "
            "FROM location WHERE campaign_id = %s ORDER BY warehouse_id, location_id",
            (campaign_id,),
        )
        return [
            Location(
                campaign_id=str(r["campaign_id"]), warehouse_id=r["warehouse_id"],
                location_id=r["location_id"], zone=r["zone"], type=r["type"],
                status=r["status"], source=r["source"],
            )
            for r in rows
        ]

    def locations_by_key(self, campaign_id: str) -> dict[LocationKey, Location]:
        return {l.key: l for l in self.list_locations(campaign_id)}

    def upsert_warehouses(
        self, warehouses: Iterable[Warehouse], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO warehouse (campaign_id, warehouse_id, label, type, status, "
            "updated_by, updated_at) VALUES (%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, warehouse_id) DO UPDATE SET "
            "label = EXCLUDED.label, type = EXCLUDED.type, status = EXCLUDED.status, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            [(w.campaign_id, w.warehouse_id, w.label, str(w.type), str(w.status), actor)
             for w in warehouses],
            conn=conn,
        )

    def upsert_locations(
        self, locations: Iterable[Location], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO location (campaign_id, warehouse_id, location_id, zone, "
            "type, status, source, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, warehouse_id, location_id) DO UPDATE SET "
            "zone = EXCLUDED.zone, type = EXCLUDED.type, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "row_version = location.row_version + 1",
            [(l.campaign_id, l.warehouse_id, l.location_id, l.zone, str(l.type),
              str(l.status), str(l.source), actor) for l in locations],
            conn=conn,
        )

    def set_location_status(
        self,
        campaign_id: str,
        keys: Sequence[LocationKey],
        status: LocationStatus,
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Activate or disable a batch of locations in one statement."""
        if not keys:
            return 0
        return self._execute(
            "UPDATE location SET status = %s, updated_by = %s, updated_at = now(), "
            "row_version = row_version + 1 "
            "WHERE campaign_id = %s AND (warehouse_id, location_id) IN "
            "(SELECT * FROM unnest(%s::text[], %s::text[]))",
            (
                str(status), actor, campaign_id,
                [k.warehouse_id for k in keys], [k.location_id for k in keys],
            ),
            conn=conn,
        )

    # -- managers & perimeters -----------------------------------------------

    def list_managers(self, campaign_id: str) -> list[Manager]:
        rows = self._fetch_all(
            "SELECT campaign_id, code, label, actor, active, display_order "
            "FROM manager WHERE campaign_id = %s ORDER BY display_order, code",
            (campaign_id,),
        )
        return [
            Manager(
                campaign_id=str(r["campaign_id"]), code=r["code"], label=r["label"],
                actor=r["actor"], active=r["active"],
                display_order=r["display_order"],
            )
            for r in rows
        ]

    def upsert_managers(
        self, managers: Iterable[Manager], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO manager (campaign_id, code, label, actor, active, "
            "display_order, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, code) DO UPDATE SET "
            "label = EXCLUDED.label, actor = EXCLUDED.actor, "
            "active = EXCLUDED.active, display_order = EXCLUDED.display_order, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            [
                (m.campaign_id, m.code, m.label, m.actor, m.active,
                 m.display_order, actor)
                for m in managers
            ],
            conn=conn,
        )

    def warehouse_assignments(self, campaign_id: str) -> dict[str, str]:
        """``{warehouse_id: manager_code}``, including the reserved ``AUTRES`` key."""
        rows = self._fetch_all(
            "SELECT warehouse_id, manager_code FROM warehouse_manager "
            "WHERE campaign_id = %s",
            (campaign_id,),
        )
        return {r["warehouse_id"]: r["manager_code"] for r in rows}

    def set_warehouse_assignments(
        self,
        campaign_id: str,
        assignments: Mapping[str, str],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Assign warehouses to managers; an empty code clears the assignment.

        Clearing has to delete the row rather than store an empty string: the
        ``AUTRES`` fallback answers "no explicit assignment", and a row holding
        ``''`` would silently shadow it.
        """
        cleared = [w for w, code in assignments.items() if not code]
        assigned = [(campaign_id, w, code, actor)
                    for w, code in assignments.items() if code]
        written = 0
        if cleared:
            written += self._execute(
                "DELETE FROM warehouse_manager WHERE campaign_id = %s "
                "AND warehouse_id = ANY(%s::text[])",
                (campaign_id, cleared),
                conn=conn,
            )
        if assigned:
            written += self._execute_many(
                "INSERT INTO warehouse_manager (campaign_id, warehouse_id, "
                "manager_code, updated_by, updated_at) VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (campaign_id, warehouse_id) DO UPDATE SET "
                "manager_code = EXCLUDED.manager_code, "
                "updated_by = EXCLUDED.updated_by, updated_at = now()",
                assigned,
                conn=conn,
            )
        return written

    @staticmethod
    def _item(row: dict[str, Any]) -> Item:
        return Item(
            campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"],
            name=row["name"],
            search_name=row["search_name"],
            item_group=row["item_group"],
            lifecycle_state=row["lifecycle_state"],
            item_type=ItemType(row["item_type"]),
            category=row["category"],
            program=row["program"],
            commonality=row["commonality"],
            unit=row["unit"],
            std_price=row["std_price"],
            exclusions=set(row["exclusions"] or []),
            source=DataSource(row["source"]),
        )


# --------------------------------------------------------------------------- #
# Book stock
# --------------------------------------------------------------------------- #

class BookStockRepository(_Base):
    """The frozen ERP snapshot."""

    def list(self, campaign_id: str) -> list[BookStockLine]:
        rows = self._fetch_all(
            "SELECT campaign_id, item_number, warehouse_id, location_id, qty, unit, "
            "unit_cost FROM book_stock WHERE campaign_id = %s",
            (campaign_id,),
        )
        return [
            BookStockLine(
                campaign_id=str(r["campaign_id"]), item_number=r["item_number"],
                warehouse_id=r["warehouse_id"], location_id=r["location_id"],
                qty=r["qty"], unit=r["unit"], unit_cost=r["unit_cost"],
            )
            for r in rows
        ]

    def count(self, campaign_id: str) -> int:
        row = self._fetch_one(
            "SELECT COUNT(*) AS n FROM book_stock WHERE campaign_id = %s",
            (campaign_id,),
        )
        return int(row["n"]) if row else 0

    def replace(
        self,
        campaign_id: str,
        lines: Sequence[BookStockLine],
        *,
        batch_id: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Replace the whole snapshot atomically.

        The book stock is a *photograph*: a partial merge would produce a
        picture that never existed. Loading it therefore truncates and rewrites
        inside one transaction, using ``COPY`` for throughput.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM book_stock WHERE campaign_id = %s",
                        (campaign_id,))
            if not lines:
                return 0
            with cur.copy(
                "COPY book_stock (id, campaign_id, item_number, warehouse_id, "
                "location_id, qty, unit, unit_cost, import_batch) FROM STDIN"
            ) as copy:
                for line in lines:
                    copy.write_row((
                        new_id(), campaign_id, line.item_number,
                        line.warehouse_id, line.location_id, line.qty,
                        line.unit, line.unit_cost, batch_id,
                    ))
        return len(lines)

class _NullContext:
    """Adapt an existing connection to the ``with`` protocol."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection:
        return self._conn

    def __exit__(self, *exc: Any) -> bool:
        return False


# --------------------------------------------------------------------------- #
# Counting journals
# --------------------------------------------------------------------------- #

class JournalRepository(_Base):
    """Counting journals and their lines."""

    _COLUMNS = (
        "id, campaign_id, warehouse_id, location_id, kind, status, journal_number, "
        "description, posted_at, auto_created, updated_at, row_version"
    )

    def list(
        self,
        campaign_id: str,
        *,
        status: JournalStatus | None = None,
        warehouse_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> list[CountJournal]:
        """List journals.

        :param conn: pass the *current* connection when reading back rows that
            were inserted earlier in the same open transaction. Reading through
            a second pooled connection would not see them yet, and the lines
            keyed on those journals would be silently dropped.
        """
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if status is not None:
            clauses.append("status = %s")
            params.append(str(status))
        if warehouse_id:
            clauses.append("warehouse_id = %s")
            params.append(warehouse_id)
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM count_journal WHERE {' AND '.join(clauses)} "
            "ORDER BY warehouse_id, location_id",
            params,
            conn=conn,
        )
        return [self._journal(r) for r in rows]

    def get(
        self, journal_id: str, *, conn: psycopg.Connection | None = None
    ) -> CountJournal:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM count_journal WHERE id = %s",
            (journal_id,),
            conn=conn,
        )
        if row is None:
            raise NotFoundError("Journal introuvable.", journalId=journal_id)
        return self._journal(row)

    def progress(self, campaign_id: str) -> dict[str, int]:
        row = self._fetch_one(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE status IN ('POSTED','BOOK_ENFORCED')) AS complete, "
            "COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS running, "
            "COUNT(*) FILTER (WHERE status = 'PENDING') AS pending "
            "FROM count_journal WHERE campaign_id = %s",
            (campaign_id,),
        )
        return {k: int(v) for k, v in (row or {}).items()}

    def ensure_journals(
        self,
        campaign_id: str,
        keys: Sequence[LocationKey],
        *,
        kinds: dict[LocationKey, JournalKind] | None = None,
        auto_created: bool = False,
        actor: str = "system",
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Create one PENDING journal per key that does not already have one."""
        if not keys:
            return 0
        kinds = kinds or {}
        rows = [
            (
                new_id(), campaign_id, k.warehouse_id, k.location_id,
                str(kinds.get(k, JournalKind.INVV)), auto_created, actor,
            )
            for k in keys
        ]
        return self._execute_many(
            "INSERT INTO count_journal (id, campaign_id, warehouse_id, location_id, "
            "kind, auto_created, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, warehouse_id, location_id) DO NOTHING",
            rows,
            conn=conn,
        )

    def set_status(
        self,
        campaign_id: str,
        journal_ids: Sequence[str],
        status: JournalStatus,
        *,
        actor: str,
        posted_at: dt.datetime | None = None,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Poste un lot de journaux — **de cette campagne**.

        Le filtre sur la campagne n'est pas une ceinture de plus : la
        permission est vérifiée sur la campagne de l'URL, tandis que les
        identifiants viennent du corps de la requête. Sans ce filtre, un
        gestionnaire habilité sur A postait un journal de B en connaissant son
        UUID, et la garde d'écriture n'y voyait rien.
        """
        if not journal_ids:
            return 0
        return self._execute(
            "UPDATE count_journal SET status = %s, posted_at = COALESCE(%s, posted_at), "
            "updated_by = %s, updated_at = now(), row_version = row_version + 1 "
            "WHERE campaign_id = %s AND id = ANY(%s::uuid[])",
            (str(status), posted_at, actor, campaign_id, list(journal_ids)),
            conn=conn,
        )

    def untouched_journal_keys(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> set[tuple[str, str]]:
        """Which of these locations have a journal nobody has used yet.

        "Used" is deliberately generous: a journal that carries a single line,
        or that somebody has merely opened, is work — and work is not something
        a reload of the ERP snapshot gets to throw away. Only a journal still
        ``PENDING`` and still empty is a leftover.
        """
        if not keys:
            return set()
        rows = self._fetch_all(
            """
            SELECT j.warehouse_id, j.location_id
            FROM count_journal j
            WHERE j.campaign_id = %s
              AND (j.warehouse_id, j.location_id)
                  IN (SELECT * FROM unnest(%s::text[], %s::text[]))
              AND j.status = 'PENDING'
              AND NOT EXISTS (
                    SELECT 1 FROM count_journal_line l
                    WHERE l.journal_id = j.id AND l.deleted_at IS NULL
              )
            """,
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )
        return {(str(r["warehouse_id"]), str(r["location_id"])) for r in rows}

    def journal_keys(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> set[tuple[str, str]]:
        """Which of these locations have a journal at all."""
        if not keys:
            return set()
        rows = self._fetch_all(
            "SELECT warehouse_id, location_id FROM count_journal "
            "WHERE campaign_id = %s AND (warehouse_id, location_id) "
            "IN (SELECT * FROM unnest(%s::text[], %s::text[]))",
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )
        return {(str(r["warehouse_id"]), str(r["location_id"])) for r in rows}

    def delete_journals_for_locations(
        self, campaign_id: str, keys: Sequence[LocationKey],
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        """Remove journals of locations that were just disabled."""
        if not keys:
            return 0
        return self._execute(
            "DELETE FROM count_journal WHERE campaign_id = %s "
            "AND (warehouse_id, location_id) IN "
            "(SELECT * FROM unnest(%s::text[], %s::text[]))",
            (campaign_id, [k.warehouse_id for k in keys],
             [k.location_id for k in keys]),
            conn=conn,
        )

    # -- lines ---------------------------------------------------------------

    _LINE_COLUMNS = (
        "id, journal_id, campaign_id, item_number, qty_imported, qty_manual, unit, "
        "source, comment, updated_by, updated_at, row_version"
    )

    def list_lines(self, journal_id: str) -> list[CountJournalLine]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM count_journal_line "
            "WHERE journal_id = %s AND deleted_at IS NULL ORDER BY item_number",
            (journal_id,),
        )
        return [self._line(r) for r in rows]

    def lines_by_journal(self, campaign_id: str) -> dict[str, list[CountJournalLine]]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM count_journal_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY journal_id, item_number",
            (campaign_id,),
        )
        out: dict[str, list[CountJournalLine]] = {}
        for r in rows:
            out.setdefault(str(r["journal_id"]), []).append(self._line(r))
        return out

    def replace_imported_lines(
        self,
        campaign_id: str,
        journal_ids: Sequence[str],
        lines: Sequence[CountJournalLine],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Refresh the imported values of the given journals.

        Reloading the ERP export replaces ``qty_imported`` but **preserves**
        ``qty_manual``: the whole point of keeping the two columns apart is that
        a re-import never silently discards a human correction.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            if journal_ids:
                # Drop imported lines that vanished from the new export, but
                # keep any line a user has touched.
                cur.execute(
                    "UPDATE count_journal_line SET deleted_at = now() "
                    "WHERE journal_id = ANY(%s::uuid[]) AND qty_manual IS NULL "
                    "AND deleted_at IS NULL",
                    (list(journal_ids),),
                )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO count_journal_line (id, journal_id, campaign_id, "
                "item_number, qty_imported, unit, source, updated_by, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (id) DO NOTHING",
                [
                    (l.id, l.journal_id, campaign_id, l.item_number, l.qty_imported,
                     l.unit, str(l.source), l.updated_by or "import")
                    for l in lines
                ],
            )
        return len(lines)

    def upsert_line(
        self, line: CountJournalLine, *, actor: str,
        expected_version: int | None = None,
        conn: psycopg.Connection | None = None,
    ) -> CountJournalLine:
        """Insert or update one line, honouring optimistic concurrency."""
        if expected_version is not None:
            n = self._execute(
                "UPDATE count_journal_line SET qty_manual = %s, unit = %s, "
                "comment = %s, source = %s, updated_by = %s, updated_at = now(), "
                "row_version = row_version + 1 "
                "WHERE campaign_id = %s AND id = %s AND row_version = %s "
                "AND deleted_at IS NULL",
                (line.qty_manual, line.unit, line.comment, str(DataSource.MANUAL),
                 actor, line.campaign_id, line.id, expected_version),
                conn=conn,
            )
            if n == 0:
                raise ConflictError(
                    "La ligne a été modifiée par quelqu'un d'autre. Rechargez-la.",
                    lineId=line.id,
                )
            return line

        self._execute(
            "INSERT INTO count_journal_line (id, journal_id, campaign_id, item_number, "
            "qty_imported, qty_manual, unit, source, comment, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (id) DO UPDATE SET qty_manual = EXCLUDED.qty_manual, "
            "unit = EXCLUDED.unit, comment = EXCLUDED.comment, "
            "source = EXCLUDED.source, updated_by = EXCLUDED.updated_by, "
            "updated_at = now(), row_version = count_journal_line.row_version + 1, "
            "deleted_at = NULL",
            (line.id, line.journal_id, line.campaign_id, line.item_number,
             line.qty_imported, line.qty_manual, line.unit, str(line.source),
             line.comment, actor),
            conn=conn,
        )
        return line

    def delete_line(
        self, campaign_id: str, line_id: str, *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        n = self._execute(
            "UPDATE count_journal_line SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND id = %s AND deleted_at IS NULL",
            (actor, campaign_id, line_id),
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Ligne de journal introuvable.", lineId=line_id)

    def replace_lines_for_journal(
        self,
        journal_id: str,
        campaign_id: str,
        lines: Sequence[CountJournalLine],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Overwrite a journal's content — used to post the GENERIQUE consolidation."""
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM count_journal_line WHERE journal_id = %s",
                        (journal_id,))
            if lines:
                cur.executemany(
                    "INSERT INTO count_journal_line (id, journal_id, campaign_id, "
                    "item_number, qty_imported, qty_manual, unit, source, "
                    "updated_by, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
                    [
                        (l.id, journal_id, campaign_id, l.item_number, l.qty_imported,
                         l.qty_manual, l.unit, str(l.source), actor)
                        for l in lines
                    ],
                )
        return len(lines)

    def listed_item_numbers(self, campaign_id: str) -> set[str]:
        """Articles present on a counting journal, whatever its status.

        Same reading as on the sheets: the line exists because somebody expects
        that article at that location, which is what makes it "stocké".
        """
        rows = self._fetch_all(
            "SELECT DISTINCT item_number FROM count_journal_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        return {str(r["item_number"]) for r in rows}

    def counted_quantities(self, campaign_id: str) -> list[dict[str, Any]]:
        """Effective counted quantity per (item, warehouse, location).

        Book-enforced journals are resolved against the snapshot so that a
        location inventoried before the freeze contributes a null variance
        instead of a phantom stock-out.
        """
        return self._fetch_all(
            """
            SELECT item_number, warehouse_id, location_id, SUM(qty) AS qty
            FROM (
                SELECT l.item_number, j.warehouse_id, j.location_id,
                       COALESCE(l.qty_manual, l.qty_imported, 0) AS qty
                FROM count_journal_line l
                JOIN count_journal j ON j.id = l.journal_id
                WHERE l.deleted_at IS NULL
                  AND j.campaign_id = %(cid)s
                  AND j.status IN ('POSTED', 'IN_PROGRESS')
                UNION ALL
                SELECT b.item_number, b.warehouse_id, b.location_id, b.qty
                FROM book_stock b
                JOIN count_journal j
                  ON j.campaign_id = b.campaign_id
                 AND j.warehouse_id = b.warehouse_id
                 AND j.location_id = b.location_id
                WHERE b.campaign_id = %(cid)s AND j.status = 'BOOK_ENFORCED'
            ) AS unified
            GROUP BY item_number, warehouse_id, location_id
            """,
            {"cid": campaign_id},
        )

    @staticmethod
    def _journal(row: dict[str, Any]) -> CountJournal:
        return CountJournal(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            kind=JournalKind(row["kind"]),
            status=JournalStatus(row["status"]),
            journal_number=row["journal_number"],
            description=row["description"],
            posted_at=row["posted_at"],
            auto_created=row["auto_created"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _line(row: dict[str, Any]) -> CountJournalLine:
        return CountJournalLine(
            id=str(row["id"]),
            journal_id=str(row["journal_id"]),
            campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"],
            qty_imported=row["qty_imported"],
            qty_manual=row["qty_manual"],
            unit=row["unit"],
            source=DataSource(row["source"]),
            comment=row["comment"],
            updated_by=row["updated_by"],
            updated_at=row["updated_at"],
        )


# --------------------------------------------------------------------------- #
# GENERIQUE zones & sheets
# --------------------------------------------------------------------------- #

class SheetRepository(_Base):
    """Zones, counting sheets, their lines and arbitration decisions."""

    _ZONE_COLUMNS = (
        "id, campaign_id, code, label, sector, display_order, passes, free_entry, "
        "manager_code, allow_negative, closed_at, closed_by"
    )

    def list_zones(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[Zone]:
        rows = self._fetch_all(
            f"SELECT {self._ZONE_COLUMNS} FROM zone "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY display_order, code",
            (campaign_id,),
            conn=conn,
        )
        return [self._zone(r) for r in rows]

    def create_zone(
        self, zone: Zone, *, actor: str, conn: psycopg.Connection | None = None
    ) -> Zone:
        self._execute(
            "INSERT INTO zone (id, campaign_id, code, label, sector, display_order, "
            "passes, free_entry, manager_code, allow_negative, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())",
            (zone.id, zone.campaign_id, zone.code, zone.label, zone.sector,
             zone.display_order, zone.passes, zone.free_entry, zone.manager_code,
             zone.allow_negative, actor),
            conn=conn,
        )
        return zone

    def set_zone_closed(
        self,
        campaign_id: str,
        zone_id: str,
        *,
        closed: bool,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Déclare une zone terminée, ou la rouvre.

        La seule écriture d'état du parcours de comptage : les deux autres
        statuts d'une zone se déduisent de ses quantités.
        """
        return self._execute(
            "UPDATE zone SET closed_at = %s, closed_by = %s, updated_by = %s, "
            "updated_at = now() "
            "WHERE campaign_id = %s AND id = %s AND deleted_at IS NULL",
            (dt.datetime.now(dt.UTC) if closed else None,
             actor if closed else "",
             actor, campaign_id, zone_id),
            conn=conn,
        )

    def delete_zone(
        self,
        campaign_id: str,
        zone_id: str,
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        self._execute(
            "UPDATE zone SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND id = %s",
            (actor, campaign_id, zone_id),
            conn=conn,
        )

    def update_zones(
        self,
        campaign_id: str,
        zone_ids: Sequence[str],
        *,
        actor: str,
        passes: int | None = None,
        free_entry: bool | None = None,
        manager_code: str | None = None,
        allow_negative: bool | None = None,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Set one attribute on a batch of zones — the shape the UI works in.

        Assigning a manager or switching a whole sector to a single count is a
        selection-wide action; issuing one statement per zone would turn a
        forty-zone campaign into forty round trips.
        """
        if not zone_ids:
            return 0
        sets = ["updated_by = %s", "updated_at = now()"]
        params: list[Any] = [actor]
        for column, value in (
            ("passes", passes),
            ("free_entry", free_entry),
            ("manager_code", manager_code),
            ("allow_negative", allow_negative),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        if len(sets) == 2:
            return 0
        params += [campaign_id, list(zone_ids)]
        return self._execute(
            f"UPDATE zone SET {', '.join(sets)} WHERE campaign_id = %s "
            "AND id = ANY(%s::uuid[]) AND deleted_at IS NULL",
            params,
            conn=conn,
        )

    def list_sheets(
        self,
        campaign_id: str,
        *,
        zone_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> list[CountSheet]:
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if zone_id:
            clauses.append("zone_id = %s")
            params.append(zone_id)
        rows = self._fetch_all(
            "SELECT id, campaign_id, zone_id, pass_no, counter_name, "
            "started_at, ended_at, evidence_path, evidence_sha256, evidence_bytes, "
            "evidence_mime, extraction_confidence, updated_at "
            f"FROM count_sheet WHERE {' AND '.join(clauses)} ORDER BY zone_id, pass_no",
            params,
            conn=conn,
        )
        return [self._sheet(r) for r in rows]

    def zones_with_counted_pass(
        self, campaign_id: str, zone_ids: Sequence[str], pass_no: SheetPass
    ) -> list[str]:
        """Zone ids whose sheet for *pass_no* already carries a typed quantity.

        Dropping a pass would delete its sheet; doing that once somebody has
        counted on it would erase a real count. This is the query that lets the
        refusal name the zones concerned instead of failing abstractly.
        """
        if not zone_ids:
            return []
        rows = self._fetch_all(
            "SELECT DISTINCT s.zone_id FROM count_sheet s "
            "JOIN count_sheet_line l ON l.sheet_id = s.id AND l.deleted_at IS NULL "
            "WHERE s.campaign_id = %s AND s.pass_no = %s "
            "AND s.zone_id = ANY(%s::uuid[]) "
            "AND (l.qty_manual IS NOT NULL OR l.qty_imported IS NOT NULL)",
            (campaign_id, str(pass_no), list(zone_ids)),
        )
        return [str(r["zone_id"]) for r in rows]

    def delete_sheets_for_pass(
        self,
        campaign_id: str,
        zone_ids: Sequence[str],
        pass_no: SheetPass,
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Remove a pass's sheets. ``ON DELETE CASCADE`` takes their lines with them."""
        if not zone_ids:
            return 0
        return self._execute(
            "DELETE FROM count_sheet WHERE campaign_id = %s AND pass_no = %s "
            "AND zone_id = ANY(%s::uuid[])",
            (campaign_id, str(pass_no), list(zone_ids)),
            conn=conn,
        )

    def delete_sheets(
        self,
        campaign_id: str,
        sheet_ids: Sequence[str],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Supprime des feuilles nommément. ``ON DELETE CASCADE`` emporte leurs lignes.

        Filtré sur la campagne autant que sur les identifiants : ceux-ci
        viennent d'une requête, et rien d'autre n'empêcherait de supprimer la
        feuille d'une campagne à laquelle on n'a pas affaire.
        """
        if not sheet_ids:
            return 0
        return self._execute(
            "DELETE FROM count_sheet WHERE campaign_id = %s AND id = ANY(%s::uuid[])",
            (campaign_id, list(sheet_ids)),
            conn=conn,
        )

    def get_sheet(self, sheet_id: str) -> CountSheet:
        row = self._fetch_one(
            "SELECT id, campaign_id, zone_id, pass_no, counter_name, "
            "started_at, ended_at, evidence_path, evidence_sha256, evidence_bytes, "
            "evidence_mime, extraction_confidence, updated_at "
            "FROM count_sheet WHERE id = %s",
            (sheet_id,),
        )
        if row is None:
            raise NotFoundError("Feuille de comptage introuvable.", sheetId=sheet_id)
        return self._sheet(row)

    def ensure_sheets(
        self,
        campaign_id: str,
        zone_id: str,
        passes: Sequence[SheetPass],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO count_sheet (id, campaign_id, zone_id, pass_no, updated_by, "
            "updated_at) VALUES (%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (zone_id, pass_no) DO NOTHING",
            [(new_id(), campaign_id, zone_id, str(p), actor) for p in passes],
            conn=conn,
        )

    def update_sheet(
        self,
        campaign_id: str,
        sheet_id: str,
        *,
        counter_name: str | None = None,
        started_at: dt.datetime | None = None,
        ended_at: dt.datetime | None = None,
        evidence_path: str | None = None,
        evidence_sha256: str | None = None,
        evidence_bytes: int | None = None,
        evidence_mime: str | None = None,
        extraction_confidence: float | None = None,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        sets = ["updated_by = %s", "updated_at = now()", "row_version = row_version + 1"]
        params: list[Any] = [actor]
        for column, value in (
            ("counter_name", counter_name),
            ("started_at", started_at),
            ("ended_at", ended_at),
            ("evidence_path", evidence_path),
            ("evidence_sha256", evidence_sha256),
            ("evidence_bytes", evidence_bytes),
            ("evidence_mime", evidence_mime),
            ("extraction_confidence", extraction_confidence),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        params += [campaign_id, sheet_id]
        n = self._execute(
            f"UPDATE count_sheet SET {', '.join(sets)} "
            "WHERE campaign_id = %s AND id = %s",
            params,
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Feuille de comptage introuvable.", sheetId=sheet_id)

    # -- sheet lines ---------------------------------------------------------

    _SHEET_LINE_COLUMNS = (
        "id, sheet_id, campaign_id, item_number, section, qty_imported, qty_manual, "
        "unit, source, confidence, comment, display_order, row_version"
    )

    def list_sheet_lines(
        self, sheet_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[CountSheetLine]:
        rows = self._fetch_all(
            f"SELECT {self._SHEET_LINE_COLUMNS} FROM count_sheet_line "
            "WHERE sheet_id = %s AND deleted_at IS NULL ORDER BY display_order, id",
            (sheet_id,),
            conn=conn,
        )
        return [self._sheet_line(r) for r in rows]

    def listed_item_numbers(self, campaign_id: str) -> set[str]:
        """Articles written on a GENERIQUE counting sheet, quantity or not.

        A line without a quantity counts: a pre-printed sheet is the statement
        that the article is expected to be found in that zone, which is exactly
        what the "stocké / compté" filter is asked to keep. Waiting for a
        quantity would make the filter useless during preparation, when it is
        most needed.
        """
        rows = self._fetch_all(
            "SELECT DISTINCT item_number FROM count_sheet_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        return {str(r["item_number"]) for r in rows}

    def count_counted_lines(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> int:
        """How many GENERIQUE sheet lines carry a quantity.

        The GENERIQUE journal holds no line of its own — its counting lives in
        the sheets — so "has anybody worked here?" cannot be answered by looking
        at journal lines alone. Asked as a count rather than a list: the caller
        only needs to know whether the answer is zero.
        """
        rows = self._fetch_all(
            "SELECT count(*) AS n FROM count_sheet_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "AND (qty_manual IS NOT NULL OR qty_imported IS NOT NULL)",
            (campaign_id,),
            conn=conn,
        )
        return int(rows[0]["n"]) if rows else 0

    def last_line_change(self, campaign_id: str) -> Any:
        """Quand une ligne de feuille a bougé pour la dernière fois.

        Comparée à la date de la consolidation enregistrée, elle répond à
        « les quantités consolidées sont-elles encore celles des feuilles ? ».
        Les lignes supprimées comptent : une suppression change le total autant
        qu'une correction.
        """
        row = self._fetch_one(
            "SELECT max(updated_at) AS at FROM count_sheet_line "
            "WHERE campaign_id = %s",
            (campaign_id,),
        )
        return (row or {}).get("at")

    def lines_by_sheet(self, campaign_id: str) -> dict[str, list[CountSheetLine]]:
        rows = self._fetch_all(
            f"SELECT {self._SHEET_LINE_COLUMNS} FROM count_sheet_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY sheet_id, display_order",
            (campaign_id,),
        )
        out: dict[str, list[CountSheetLine]] = {}
        for r in rows:
            out.setdefault(str(r["sheet_id"]), []).append(self._sheet_line(r))
        return out

    def upsert_sheet_lines(
        self, lines: Sequence[CountSheetLine], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO count_sheet_line (id, sheet_id, campaign_id, item_number, "
            "section, qty_imported, qty_manual, unit, source, confidence, comment, "
            "display_order, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (id) DO UPDATE SET item_number = EXCLUDED.item_number, "
            "section = EXCLUDED.section, qty_imported = EXCLUDED.qty_imported, "
            "qty_manual = EXCLUDED.qty_manual, unit = EXCLUDED.unit, "
            "source = EXCLUDED.source, confidence = EXCLUDED.confidence, "
            "comment = EXCLUDED.comment, display_order = EXCLUDED.display_order, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "row_version = count_sheet_line.row_version + 1, deleted_at = NULL",
            [
                (l.id, l.sheet_id, l.campaign_id, l.item_number, str(l.section),
                 l.qty_imported, l.qty_manual, l.unit, str(l.source), l.confidence,
                 l.comment, l.display_order, actor)
                for l in lines
            ],
            conn=conn,
        )

    def delete_sheet_line(
        self, campaign_id: str, line_id: str, *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        self._execute(
            "UPDATE count_sheet_line SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND id = %s",
            (actor, campaign_id, line_id),
            conn=conn,
        )

    def bump_sheet(
        self,
        campaign_id: str,
        sheet_id: str,
        *,
        expected_version: int,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Prend la feuille pour soi, ou refuse parce qu'elle a bougé.

        L'enregistrement d'une feuille **remplace** ses lignes. Deux personnes
        qui l'ouvrent au même moment pendant l'encodage — ce qui arrive tous les
        jours d'inventaire, une qui saisit, l'autre qui vérifie — écrivaient
        chacune l'ensemble qu'elle avait sous les yeux, et la seconde à cliquer
        gagnait. Rien ne le disait : les quantités de la première disparaissaient
        sans message, sans conflit, sans trace.

        L'``UPDATE`` conditionné sur ``row_version`` est ce qui transforme cette
        course en refus. Il est atomique par construction : PostgreSQL sérialise
        deux mises à jour de la même ligne, donc exactement une des deux voit la
        version attendue.

        Il doit être exécuté **dans la transaction qui écrit** — d'où ``conn``.
        Le prendre à part laisserait une fenêtre entre la prise et le
        remplacement, c'est-à-dire exactement la course qu'il ferme.
        """
        touched = self._execute(
            "UPDATE count_sheet SET row_version = row_version + 1, "
            "updated_by = %s, updated_at = now() "
            "WHERE campaign_id = %s AND id = %s AND row_version = %s",
            (actor, campaign_id, sheet_id, expected_version),
            conn=conn,
        )
        if touched == 0:
            raise ConflictError(
                "Cette feuille a été modifiée par quelqu'un d'autre pendant que "
                "vous la remplissiez. Rechargez-la : enregistrer maintenant "
                "effacerait ce que l'autre personne vient d'y saisir.",
                sheetId=sheet_id,
                expectedVersion=expected_version,
            )

    def replace_sheet_lines(
        self, sheet_id: str, lines: Sequence[CountSheetLine], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Make the sheet's content exactly *lines* — grid save, AI extraction.

        Deletion is logical (``deleted_at``), so an id survives its row: it can
        never be re-inserted, only revived. Wiping the sheet and re-inserting
        therefore violated the primary key as soon as the payload carried the
        ids it had just been served — which is what a grid always sends back.

        The two steps below are the correct reading of "replace": retire the
        lines that are *no longer* there, and upsert the ones that are. Ids stay
        stable across saves, which is what the grid and optimistic concurrency
        both rely on, and a line that leaves the sheet keeps its audit trail.
        """
        # `sheet_id` is authoritative: the AI extractor builds lines without
        # knowing which sheet they will land on.
        owned = [
            l if l.sheet_id == sheet_id else l.model_copy(update={"sheet_id": sheet_id})
            for l in lines
        ]
        kept = [str(l.id) for l in owned]
        owns = conn is None
        outer = self.db.transaction() if owns else _NullContext(conn)
        with outer as connection, connection.cursor() as cur:
            cur.execute(
                "UPDATE count_sheet_line SET deleted_at = now(), updated_by = %s "
                "WHERE sheet_id = %s AND deleted_at IS NULL "
                # ::uuid[] — the ids arrive as text and the column is uuid.
                "AND NOT (id = ANY(%s::uuid[]))",
                (actor, sheet_id, kept),
            )
            if owned:
                self.upsert_sheet_lines(owned, actor=actor, conn=connection)
        return len(owned)

    # -- arbitration ---------------------------------------------------------

    def list_arbitrations(
        self, campaign_id: str, *, zone_id: str | None = None
    ) -> list[ArbitrationLine]:
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if zone_id:
            clauses.append("zone_id = %s")
            params.append(zone_id)
        rows = self._fetch_all(
            "SELECT id, campaign_id, zone_id, item_number, section, qty_pass_1, "
            "qty_pass_2, qty_arbitrated, decided_by, decided_at, comment "
            f"FROM arbitration WHERE {' AND '.join(clauses)} ORDER BY item_number",
            params,
        )
        return [
            ArbitrationLine(
                id=str(r["id"]), campaign_id=str(r["campaign_id"]),
                zone_id=str(r["zone_id"]), item_number=r["item_number"],
                section=CountSection(r["section"]), qty_pass_1=r["qty_pass_1"],
                qty_pass_2=r["qty_pass_2"], qty_arbitrated=r["qty_arbitrated"],
                decided_by=r["decided_by"], decided_at=r["decided_at"],
                comment=r["comment"],
            )
            for r in rows
        ]

    def upsert_arbitrations(
        self, lines: Sequence[ArbitrationLine], *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO arbitration (id, campaign_id, zone_id, item_number, section, "
            "qty_pass_1, qty_pass_2, qty_arbitrated, decided_by, decided_at, comment, "
            "updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (zone_id, item_number, section) DO UPDATE SET "
            "qty_pass_1 = EXCLUDED.qty_pass_1, qty_pass_2 = EXCLUDED.qty_pass_2, "
            "qty_arbitrated = COALESCE(EXCLUDED.qty_arbitrated, "
            "arbitration.qty_arbitrated), "
            "decided_by = COALESCE(EXCLUDED.decided_by, arbitration.decided_by), "
            "decided_at = COALESCE(EXCLUDED.decided_at, arbitration.decided_at), "
            "comment = EXCLUDED.comment, updated_at = now()",
            [
                (l.id, l.campaign_id, l.zone_id, l.item_number, str(l.section),
                 l.qty_pass_1, l.qty_pass_2, l.qty_arbitrated, l.decided_by,
                 l.decided_at, l.comment)
                for l in lines
            ],
            conn=conn,
        )

    def delete_arbitrations(
        self, campaign_id: str, zone_ids: Sequence[str],
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        """Drop a zone's pass-1/pass-2 comparison.

        Called when a zone drops to a single count: the comparison no longer has
        two sides, and leaving the rows behind would keep the zone showing
        "arbitrages en attente" for a decision that cannot be made.
        """
        if not zone_ids:
            return 0
        return self._execute(
            "DELETE FROM arbitration WHERE campaign_id = %s "
            "AND zone_id = ANY(%s::uuid[])",
            (campaign_id, list(zone_ids)),
            conn=conn,
        )

    def propose_arbitrations(
        self,
        campaign_id: str,
        proposals: Mapping[str, Decimal],
        *,
        comment: str = "",
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Pre-fill quantities without deciding anything.

        ``decided_at`` is deliberately left NULL — and cleared if a previous
        proposal set it, which it never does. The value lands in the field the
        user is about to look at; confirming it is still a separate gesture, and
        the consolidation ignores it until then.
        """
        if not proposals:
            return 0
        return self._execute_many(
            "UPDATE arbitration SET qty_arbitrated = %s, comment = %s, "
            "decided_by = NULL, decided_at = NULL, updated_at = now() "
            "WHERE id = %s AND campaign_id = %s",
            [(qty, comment, arbitration_id, campaign_id)
             for arbitration_id, qty in proposals.items()],
            conn=conn,
        )

    def decide_arbitration(
        self, arbitration_id: str, qty: Decimal, *, actor: str, comment: str = ""
    ) -> None:
        n = self._execute(
            "UPDATE arbitration SET qty_arbitrated = %s, decided_by = %s, "
            "decided_at = now(), comment = %s, updated_at = now() WHERE id = %s",
            (qty, actor, comment, arbitration_id),
        )
        if n == 0:
            raise NotFoundError("Arbitrage introuvable.", arbitrationId=arbitration_id)

    @staticmethod
    def _zone(row: dict[str, Any]) -> Zone:
        return Zone(
            id=str(row["id"]), campaign_id=str(row["campaign_id"]), code=row["code"],
            label=row["label"], sector=row["sector"],
            display_order=row["display_order"], passes=row["passes"],
            free_entry=row["free_entry"], manager_code=row["manager_code"],
            allow_negative=row["allow_negative"],
            closed_at=row["closed_at"], closed_by=row["closed_by"] or "",
        )

    @staticmethod
    def _sheet(row: dict[str, Any]) -> CountSheet:
        return CountSheet(
            id=str(row["id"]), campaign_id=str(row["campaign_id"]),
            zone_id=str(row["zone_id"]), pass_no=SheetPass(row["pass_no"]),
            counter_name=row["counter_name"],
            started_at=row["started_at"], ended_at=row["ended_at"],
            evidence_path=row["evidence_path"],
            evidence_sha256=row["evidence_sha256"],
            evidence_bytes=row["evidence_bytes"],
            evidence_mime=row["evidence_mime"],
            extraction_confidence=row["extraction_confidence"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _sheet_line(row: dict[str, Any]) -> CountSheetLine:
        return CountSheetLine(
            id=str(row["id"]), sheet_id=str(row["sheet_id"]),
            campaign_id=str(row["campaign_id"]), item_number=row["item_number"],
            section=CountSection(row["section"]), qty_imported=row["qty_imported"],
            qty_manual=row["qty_manual"], unit=row["unit"],
            source=DataSource(row["source"]), confidence=row["confidence"],
            comment=row["comment"], display_order=row["display_order"],
        )


# --------------------------------------------------------------------------- #
# Consolidation
# --------------------------------------------------------------------------- #

class ConsolidationRepository(_Base):
    """Persisted output of the GENERIQUE consolidation engine."""

    def save_run(
        self,
        *,
        campaign_id: str,
        run_by: str,
        engine_version: str,
        zones_included: Sequence[str],
        zones_skipped: Sequence[str],
        findings: Sequence[dict[str, Any]],
        lines: Sequence[ConsolidatedLine],
        breakdown: Sequence[WipBreakdown],
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Persist a run and make it the current one, atomically.

        Accepte une transaction déjà ouverte : enregistrer le calcul et poster
        le journal qu'il produit sont un seul acte, et un calcul « courant »
        dont le journal n'a jamais été écrit ferait passer pour consolidée une
        campagne qui ne l'est pas.
        """
        run_id = new_id()
        owns = conn is None
        outer = self.db.transaction() if owns else _NullContext(conn)
        with outer as connection, connection.cursor() as cur:
            cur.execute(
                "UPDATE consolidation_run SET is_current = false "
                "WHERE campaign_id = %s AND is_current",
                (campaign_id,),
            )
            cur.execute(
                "INSERT INTO consolidation_run (id, campaign_id, run_by, "
                "engine_version, zones_included, zones_skipped, findings, is_current) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s, true)",
                (run_id, campaign_id, run_by, engine_version, list(zones_included),
                 list(zones_skipped), Jsonb(list(findings))),
            )
            if lines:
                cur.executemany(
                    "INSERT INTO consolidation_line (run_id, item_number, qty, unit, "
                    "qty_line_side, qty_wip_ok, qty_wip_exploded, zone_codes) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (run_id, l.item_number, l.qty, l.unit, l.qty_line_side,
                         l.qty_wip_ok, l.qty_wip_exploded, list(l.zone_codes))
                        for l in lines
                    ],
                )
            if breakdown:
                # The primary key collapses repeated (zone, parent, child) rows,
                # so aggregate before writing rather than losing quantity.
                merged: dict[tuple[str, str, str], WipBreakdown] = {}
                for b in breakdown:
                    key = (b.zone_code, b.parent_item, b.child_item)
                    existing = merged.get(key)
                    if existing is None:
                        merged[key] = b.model_copy()
                    else:
                        existing.parent_qty += b.parent_qty
                        existing.child_qty += b.child_qty
                cur.executemany(
                    "INSERT INTO wip_breakdown (run_id, zone_code, parent_item, "
                    "parent_qty, child_item, qty_per_parent, child_qty, depth) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    [
                        (run_id, b.zone_code, b.parent_item, b.parent_qty,
                         b.child_item, b.qty_per_parent, b.child_qty, b.depth)
                        for b in merged.values()
                    ],
                )
        return run_id

    def current_run(self, campaign_id: str) -> dict[str, Any] | None:
        return self._fetch_one(
            "SELECT id, run_at, run_by, engine_version, zones_included, "
            "zones_skipped, findings FROM consolidation_run "
            "WHERE campaign_id = %s AND is_current",
            (campaign_id,),
        )

    def current_lines(self, campaign_id: str) -> list[ConsolidatedLine]:
        rows = self._fetch_all(
            "SELECT l.item_number, l.qty, l.unit, l.qty_line_side, l.qty_wip_ok, "
            "l.qty_wip_exploded, l.zone_codes FROM consolidation_line l "
            "JOIN consolidation_run r ON r.id = l.run_id "
            "WHERE r.campaign_id = %s AND r.is_current ORDER BY l.item_number",
            (campaign_id,),
        )
        return [
            ConsolidatedLine(
                campaign_id=campaign_id, item_number=r["item_number"], qty=r["qty"],
                unit=r["unit"], qty_line_side=r["qty_line_side"],
                qty_wip_ok=r["qty_wip_ok"], qty_wip_exploded=r["qty_wip_exploded"],
                zone_codes=list(r["zone_codes"] or []),
            )
            for r in rows
        ]

    def wip_breakdown(
        self, campaign_id: str, *, child_item: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["r.campaign_id = %s", "r.is_current"]
        params: list[Any] = [campaign_id]
        if child_item:
            clauses.append("b.child_item = %s")
            params.append(child_item)
        return self._fetch_all(
            "SELECT b.zone_code, b.parent_item, b.parent_qty, b.child_item, "
            "b.qty_per_parent, b.child_qty, b.depth FROM wip_breakdown b "
            f"JOIN consolidation_run r ON r.id = b.run_id WHERE {' AND '.join(clauses)} "
            "ORDER BY b.child_qty DESC",
            params,
        )


# --------------------------------------------------------------------------- #
# Adjustments & analysis
# --------------------------------------------------------------------------- #

class AdjustmentRepository(_Base):
    """Stock movements recorded during the analysis phase."""

    _COLUMNS = (
        "id, campaign_id, item_number, warehouse_id, location_id, kind, qty, unit, "
        "value, journal_number, physical_date, reason_code, comment, source, created_at"
    )

    def list(self, campaign_id: str, *, limit: int | None = None) -> list[AdjustmentLine]:
        query = (
            f"SELECT {self._COLUMNS} FROM adjustment_line "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY physical_date DESC NULLS LAST, item_number"
        )
        params: list[Any] = [campaign_id]
        if limit:
            query += " LIMIT %s"
            params.append(limit)
        return [self._model(r) for r in self._fetch_all(query, params)]

    def upsert(
        self, lines: Iterable[AdjustmentLine], *, actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute_many(
            "INSERT INTO adjustment_line (id, campaign_id, item_number, warehouse_id, "
            "location_id, kind, qty, unit, value, journal_number, physical_date, "
            "reason_code, comment, source, created_by, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (id) DO UPDATE SET qty = EXCLUDED.qty, "
            "value = EXCLUDED.value, unit = EXCLUDED.unit, "
            "reason_code = EXCLUDED.reason_code, comment = EXCLUDED.comment, "
            "physical_date = EXCLUDED.physical_date, updated_by = EXCLUDED.updated_by, "
            "updated_at = now(), row_version = adjustment_line.row_version + 1, "
            "deleted_at = NULL",
            [
                (l.id, l.campaign_id, l.item_number, l.warehouse_id, l.location_id,
                 str(l.kind), l.qty, l.unit, l.value, l.journal_number,
                 l.physical_date, l.reason_code, l.comment, str(l.source), actor, actor)
                for l in lines
            ],
            conn=conn,
        )

    def delete(self, campaign_id: str, line_id: str, *, actor: str) -> None:
        n = self._execute(
            "UPDATE adjustment_line SET deleted_at = now(), updated_by = %s "
            "WHERE campaign_id = %s AND id = %s AND deleted_at IS NULL",
            (actor, campaign_id, line_id),
        )
        if n == 0:
            raise NotFoundError("Ligne d'ajustement introuvable.", lineId=line_id)

    @staticmethod
    def _model(row: dict[str, Any]) -> AdjustmentLine:
        return AdjustmentLine(
            id=str(row["id"]), campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"], warehouse_id=row["warehouse_id"],
            location_id=row["location_id"], kind=row["kind"], qty=row["qty"],
            unit=row["unit"], value=row["value"], journal_number=row["journal_number"],
            physical_date=row["physical_date"], reason_code=row["reason_code"],
            comment=row["comment"], source=DataSource(row["source"]),
            created_at=row["created_at"],
        )


class AnalysisRepository(_Base):
    """Assignable causes and per-article variance analysis."""

    def list_causes(self, *, active_only: bool = True) -> list[AssignableCause]:
        clause = "WHERE active" if active_only else ""
        rows = self._fetch_all(
            "SELECT code, label, family, description, display_order, active "
            f"FROM assignable_cause {clause} ORDER BY display_order, code"
        )
        return [AssignableCause(**r) for r in rows]

    def list_analyses(self, campaign_id: str) -> list[VarianceAnalysis]:
        rows = self._fetch_all(
            "SELECT id, campaign_id, item_number, cause_code, comment, analyst, "
            "accepted, ai_suggested_cause, ai_confidence, ai_rationale, updated_at "
            "FROM variance_analysis WHERE campaign_id = %s ORDER BY item_number",
            (campaign_id,),
        )
        return [
            VarianceAnalysis(
                id=str(r["id"]), campaign_id=str(r["campaign_id"]),
                item_number=r["item_number"], cause_code=r["cause_code"],
                comment=r["comment"], analyst=r["analyst"], accepted=r["accepted"],
                ai_suggested_cause=r["ai_suggested_cause"],
                ai_confidence=r["ai_confidence"], ai_rationale=r["ai_rationale"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def upsert_analysis(self, analysis: VarianceAnalysis, *, actor: str) -> None:
        self._execute(
            "INSERT INTO variance_analysis (id, campaign_id, item_number, cause_code, "
            "comment, analyst, accepted, ai_suggested_cause, ai_confidence, "
            "ai_rationale, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (campaign_id, item_number) DO UPDATE SET "
            "cause_code = EXCLUDED.cause_code, comment = EXCLUDED.comment, "
            "analyst = EXCLUDED.analyst, accepted = EXCLUDED.accepted, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "row_version = variance_analysis.row_version + 1",
            (analysis.id, analysis.campaign_id, analysis.item_number,
             analysis.cause_code, analysis.comment, analysis.analyst,
             analysis.accepted, analysis.ai_suggested_cause, analysis.ai_confidence,
             analysis.ai_rationale, actor),
        )

    def save_ai_suggestions(
        self, campaign_id: str, suggestions: Sequence[tuple[str, str, float, str]]
    ) -> int:
        """Store AI proposals without ever touching the human decision columns."""
        return self._execute_many(
            "INSERT INTO variance_analysis (id, campaign_id, item_number, "
            "ai_suggested_cause, ai_confidence, ai_rationale, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'ai', now()) "
            "ON CONFLICT (campaign_id, item_number) DO UPDATE SET "
            "ai_suggested_cause = EXCLUDED.ai_suggested_cause, "
            "ai_confidence = EXCLUDED.ai_confidence, "
            "ai_rationale = EXCLUDED.ai_rationale, updated_at = now()",
            [
                (new_id(), campaign_id, item, cause, confidence, rationale)
                for item, cause, confidence, rationale in suggestions
            ],
        )


# --------------------------------------------------------------------------- #
# Backflush
# --------------------------------------------------------------------------- #

_BACKFLUSH_COLUMNS = (
    "campaign_id, item_number, period_start, period_end, unit, net_qty, "
    "under_consumed_qty, over_consumed_qty, theoretical_qty, actual_qty, "
    "parent_count, week_count, source_loaded_at, refreshed_at"
)


class BackflushRepository(_Base):
    """The backflush variance, frozen per campaign and article."""

    def list(self, campaign_id: str) -> list[BackflushLine]:
        rows = self._fetch_all(
            f"SELECT {_BACKFLUSH_COLUMNS} FROM campaign_backflush "
            "WHERE campaign_id = %s ORDER BY item_number",
            (campaign_id,),
        )
        return [self._line(r) for r in rows]

    def by_item(self, campaign_id: str) -> dict[str, BackflushLine]:
        return {line.item_number: line for line in self.list(campaign_id)}

    def count(self, campaign_id: str) -> int:
        row = self._fetch_one(
            "SELECT count(*) AS n FROM campaign_backflush WHERE campaign_id = %s",
            (campaign_id,),
        )
        return int(row["n"]) if row else 0

    def period(self, campaign_id: str) -> dict[str, Any] | None:
        """The bounds and freshness of the frozen read, or ``None`` if never run.

        Read from the rows themselves rather than from a header table: the bounds
        are stored *with* every value precisely so that a figure cannot end up
        described by a period it was not computed on.
        """
        return self._fetch_one(
            "SELECT min(period_start) AS period_start, max(period_end) AS period_end, "
            "max(source_loaded_at) AS source_loaded_at, "
            "max(refreshed_at) AS refreshed_at, count(*) AS items "
            "FROM campaign_backflush WHERE campaign_id = %s",
            (campaign_id,),
        )

    def replace(
        self,
        campaign_id: str,
        lines: Sequence[BackflushLine],
        *,
        batch_id: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Rewrite the whole frozen read, atomically.

        Delete-then-insert rather than upsert-then-prune. Both are idempotent,
        but only this one guarantees no row survives from a previous period: an
        article that had a variance last time and none now must disappear, not
        keep its old figure under new bounds.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM campaign_backflush WHERE campaign_id = %s", (campaign_id,)
            )
            if not lines:
                return 0
            cur.executemany(
                f"INSERT INTO campaign_backflush ({_BACKFLUSH_COLUMNS}, import_batch) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)",
                [
                    (
                        campaign_id, line.item_number, line.period_start,
                        line.period_end, line.unit, line.net_qty,
                        line.under_consumed_qty, line.over_consumed_qty,
                        line.theoretical_qty, line.actual_qty, line.parent_count,
                        line.week_count, line.source_loaded_at, batch_id,
                    )
                    for line in lines
                ],
            )
        return len(lines)

    @staticmethod
    def _line(row: Mapping[str, Any]) -> BackflushLine:
        return BackflushLine(
            campaign_id=str(row["campaign_id"]),
            item_number=row["item_number"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            unit=row["unit"],
            net_qty=row["net_qty"],
            under_consumed_qty=row["under_consumed_qty"],
            over_consumed_qty=row["over_consumed_qty"],
            theoretical_qty=row["theoretical_qty"],
            actual_qty=row["actual_qty"],
            parent_count=row["parent_count"],
            week_count=row["week_count"],
            source_loaded_at=row["source_loaded_at"],
            refreshed_at=row["refreshed_at"],
        )


# --------------------------------------------------------------------------- #
# Stock-flow reconciliation
# --------------------------------------------------------------------------- #

class StockFlowRepository(_Base):
    """Runs comparing two campaigns, their loaded quantities and ERP snapshot."""

    _RUN_COLUMNS = (
        "id, campaign_id, baseline_campaign_id, period_start, period_end, "
        "scrap_loaded, source_loaded_at, erp_refreshed_at, "
        "receipts_refreshed_at, shipments_refreshed_at, scrap_refreshed_at, "
        "created_by, created_at, updated_at"
    )

    #: Which run column records the ERP read of each loaded step.
    _REFRESH_COLUMN: ClassVar[dict[FlowKind, str]] = {
        FlowKind.RECEIPT: "receipts_refreshed_at",
        FlowKind.SHIPMENT: "shipments_refreshed_at",
        FlowKind.SCRAP: "scrap_refreshed_at",
    }

    def list_runs(self, campaign_id: str) -> list[StockFlowRun]:
        rows = self._fetch_all(
            f"SELECT {self._RUN_COLUMNS} FROM stock_flow_run "
            "WHERE campaign_id = %s ORDER BY created_at DESC",
            (campaign_id,),
        )
        return [self._run(r) for r in rows]

    def get_run(self, run_id: str) -> StockFlowRun | None:
        row = self._fetch_one(
            f"SELECT {self._RUN_COLUMNS} FROM stock_flow_run WHERE id = %s", (run_id,)
        )
        return self._run(row) if row else None

    def upsert_run(self, run: StockFlowRun, *, actor: str) -> StockFlowRun:
        """Create the run, or update the one that already pairs the two campaigns.

        Keyed on the pair rather than on the run id: choosing the same baseline
        twice is a user re-opening their comparison, not starting a second one,
        and a second row would silently split the loaded quantities across two
        runs of which the screen would only ever show one.
        """
        row = self._fetch_one(
            "INSERT INTO stock_flow_run (id, campaign_id, baseline_campaign_id, "
            "period_start, period_end, scrap_loaded, source_loaded_at, "
            "erp_refreshed_at, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (campaign_id, baseline_campaign_id) DO UPDATE SET "
            "period_start = EXCLUDED.period_start, "
            "period_end = EXCLUDED.period_end, "
            "scrap_loaded = stock_flow_run.scrap_loaded OR EXCLUDED.scrap_loaded, "
            "updated_by = EXCLUDED.updated_by, updated_at = now() "
            f"RETURNING {self._RUN_COLUMNS}",
            (
                run.id, run.campaign_id, run.baseline_campaign_id,
                run.period_start, run.period_end, run.scrap_loaded,
                run.source_loaded_at, run.erp_refreshed_at, actor, actor,
            ),
        )
        assert row is not None  # RETURNING on an upsert always yields a row
        return self._run(row)

    def delete_run(self, run_id: str) -> None:
        self._execute("DELETE FROM stock_flow_run WHERE id = %s", (run_id,))

    def mark_erp_refreshed(
        self,
        run_id: str,
        *,
        at: dt.datetime,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Stamp when production and theoretical consumption were last read.

        A targeted UPDATE rather than :meth:`upsert_run`, whose ``DO UPDATE``
        clause never carried ``erp_refreshed_at``: on a run that already existed
        — which is every run by the time it is refreshed — the date was silently
        dropped and the screen kept saying the figures had never been read.
        """
        self._execute(
            "UPDATE stock_flow_run SET erp_refreshed_at = %s, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (at, actor, run_id),
            conn=conn,
        )

    def mark_scrap_loaded(
        self, run_id: str, *, actor: str, conn: psycopg.Connection | None = None
    ) -> None:
        """Record that the scrap step has been provided.

        Takes the caller's connection. Without it, a call made inside a
        transaction that has already touched this run borrows a *second*
        connection and waits on the row lock the first one holds — which it will
        never release, since it is blocked on this very call. The pool times out
        fifteen seconds later and reports a connection failure, naming the
        symptom rather than the deadlock.
        """
        self._execute(
            "UPDATE stock_flow_run SET scrap_loaded = true, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (actor, run_id),
            conn=conn,
        )

    # -- loaded quantities ---------------------------------------------------

    def list_inputs(self, run_id: str) -> list[StockFlowInput]:
        rows = self._fetch_all(
            "SELECT run_id, item_number, kind, qty, unit, source "
            "FROM stock_flow_input "
            "WHERE run_id = %s ORDER BY kind, item_number",
            (run_id,),
        )
        return [
            StockFlowInput(
                run_id=str(r["run_id"]), item_number=r["item_number"],
                kind=r["kind"], qty=r["qty"], unit=r["unit"],
                source=r["source"],
            )
            for r in rows
        ]

    def mark_refreshed(
        self,
        run_id: str,
        kind: FlowKind,
        *,
        at: dt.datetime,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Stamp when one step was last read from the ERP."""
        column = self._REFRESH_COLUMN[kind]
        self._execute(
            f"UPDATE stock_flow_run SET {column} = %s, updated_by = %s, "
            "updated_at = now() WHERE id = %s",
            (at, actor, run_id),
            conn=conn,
        )

    def replace_inputs(
        self,
        run_id: str,
        kind: FlowKind,
        lines: Sequence[StockFlowInput],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Replace one kind of loaded quantity, leaving the other two alone.

        Scoped to the kind because the three loads are three separate steps: a
        user correcting their shipments must not lose the receipts they loaded
        ten minutes earlier.
        """
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM stock_flow_input WHERE run_id = %s AND kind = %s",
                (run_id, str(kind)),
            )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO stock_flow_input "
                "(run_id, item_number, kind, qty, unit, source) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (run_id, item_number, kind) DO UPDATE SET "
                "qty = stock_flow_input.qty + EXCLUDED.qty, unit = EXCLUDED.unit, "
                "source = EXCLUDED.source",
                [
                    (
                        run_id, line.item_number, str(kind), line.qty, line.unit,
                        str(line.source),
                    )
                    for line in lines
                ],
            )
        return len(lines)

    # -- frozen ERP snapshot -------------------------------------------------

    def list_erp(self, run_id: str) -> list[StockFlowErp]:
        rows = self._fetch_all(
            "SELECT run_id, item_number, produced_qty, consumed_qty, source "
            "FROM stock_flow_erp WHERE run_id = %s ORDER BY item_number",
            (run_id,),
        )
        return [
            StockFlowErp(
                run_id=str(r["run_id"]), item_number=r["item_number"],
                produced_qty=r["produced_qty"], consumed_qty=r["consumed_qty"],
                source=r["source"],
            )
            for r in rows
        ]

    def replace_erp(
        self,
        run_id: str,
        lines: Sequence[StockFlowErp],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute("DELETE FROM stock_flow_erp WHERE run_id = %s", (run_id,))
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO stock_flow_erp (run_id, item_number, produced_qty, "
                "consumed_qty, source) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (run_id, item_number) DO UPDATE SET "
                "produced_qty = EXCLUDED.produced_qty, "
                "consumed_qty = EXCLUDED.consumed_qty, source = EXCLUDED.source",
                [
                    (
                        run_id, line.item_number, line.produced_qty,
                        line.consumed_qty, str(line.source),
                    )
                    for line in lines
                ],
            )
        return len(lines)

    @staticmethod
    def _run(row: Mapping[str, Any]) -> StockFlowRun:
        return StockFlowRun(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            baseline_campaign_id=str(row["baseline_campaign_id"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
            scrap_loaded=row["scrap_loaded"],
            source_loaded_at=row["source_loaded_at"],
            erp_refreshed_at=row["erp_refreshed_at"],
            receipts_refreshed_at=row["receipts_refreshed_at"],
            shipments_refreshed_at=row["shipments_refreshed_at"],
            scrap_refreshed_at=row["scrap_refreshed_at"],
            created_by=row["created_by"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# --------------------------------------------------------------------------- #
# Audit & imports
# --------------------------------------------------------------------------- #

class AuditRepository(_Base):
    """Append-only audit trail. Database rules make UPDATE/DELETE no-ops."""

    def record(
        self,
        *,
        campaign_id: str | None,
        actor: str,
        action: AuditAction | str,
        entity_type: str,
        entity_id: str = "",
        summary: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        event_id = new_id()
        self._execute(
            "INSERT INTO audit_event (id, campaign_id, actor, action, entity_type, "
            "entity_id, summary, before, after, request_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event_id, campaign_id, actor, str(action), entity_type, entity_id,
             summary, Jsonb(before) if before else None,
             Jsonb(after) if after else None, request_id),
            conn=conn,
        )
        return event_id

    def list(
        self,
        campaign_id: str,
        *,
        entity_type: str | None = None,
        actor: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AuditEvent]:
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if entity_type:
            clauses.append("entity_type = %s")
            params.append(entity_type)
        if actor:
            clauses.append("actor = %s")
            params.append(actor)
        params += [limit, offset]
        rows = self._fetch_all(
            "SELECT id, campaign_id, at, actor, action, entity_type, entity_id, "
            f"summary, before, after, request_id FROM audit_event "
            f"WHERE {' AND '.join(clauses)} ORDER BY at DESC LIMIT %s OFFSET %s",
            params,
        )
        return [
            AuditEvent(
                id=str(r["id"]),
                campaign_id=str(r["campaign_id"]) if r["campaign_id"] else None,
                at=r["at"], actor=r["actor"], action=r["action"],
                entity_type=r["entity_type"], entity_id=r["entity_id"],
                summary=r["summary"], before=r["before"], after=r["after"],
                request_id=r["request_id"],
            )
            for r in rows
        ]


class ImportBatchRepository(_Base):
    """Provenance of every bulk load."""

    def create(
        self,
        *,
        campaign_id: str | None,
        target: str,
        filename: str,
        content_hash: str,
        storage_path: str | None,
        rows_received: int,
        rows_accepted: int,
        rows_rejected: int,
        report: dict[str, Any],
        imported_by: str,
        batch_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Enregistre la provenance d'un chargement.

        ``batch_id`` peut être imposé par l'appelant. Les imports qui **marquent
        les lignes chargées** avec un identifiant de lot — le stock ERP, l'écart
        backflush — le tirent avant d'écrire, puis passent le même ici. Sans
        cela, deux identifiants coexistaient : celui gravé dans les lignes et
        celui de la ligne d'historique, chacun désignant le même chargement sans
        qu'aucune requête ne puisse aller de l'un à l'autre. « D'où vient cette
        quantité » n'avait alors pas de réponse.
        """
        batch_id = batch_id or new_id()
        self._execute(
            "INSERT INTO import_batch (id, campaign_id, target, filename, "
            "content_hash, storage_path, rows_received, rows_accepted, rows_rejected, "
            "report, imported_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (batch_id, campaign_id, target, filename, content_hash, storage_path,
             rows_received, rows_accepted, rows_rejected, Jsonb(report), imported_by),
            conn=conn,
        )
        return batch_id

    def find_duplicate(
        self, campaign_id: str, target: str, content_hash: str
    ) -> dict[str, Any] | None:
        """Detect a byte-identical re-upload before it duplicates rows."""
        return self._fetch_one(
            "SELECT id, filename, imported_by, imported_at, rows_accepted "
            "FROM import_batch WHERE campaign_id = %s AND target = %s "
            "AND content_hash = %s ORDER BY imported_at DESC LIMIT 1",
            (campaign_id, target, content_hash),
        )

    def evidence_of(self, campaign_id: str, batch_id: str) -> dict[str, Any] | None:
        """Le fichier archivé d'un lot, s'il en a un.

        Filtré sur la campagne autant que sur le lot : l'identifiant vient de
        l'URL, et rien d'autre n'empêcherait de télécharger la pièce d'une
        campagne à laquelle on n'a pas affaire.
        """
        return self._fetch_one(
            "SELECT filename, storage_path FROM import_batch "
            "WHERE id = %s AND campaign_id = %s AND storage_path IS NOT NULL",
            (batch_id, campaign_id),
        )

    def list(self, campaign_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._fetch_all(
            "SELECT id, target, filename, storage_path, rows_received, rows_accepted, "
            "rows_rejected, report, imported_by, imported_at FROM import_batch "
            "WHERE campaign_id = %s ORDER BY imported_at DESC LIMIT %s",
            (campaign_id, limit),
        )

    def latest_per_target(self, campaign_id: str) -> list[dict[str, Any]]:
        """Le dernier chargement de chaque grille, et ce qu'il a refusé.

        La question posée à la clôture n'est pas « un chargement a-t-il déjà
        échoué » — dix rechargements successifs sont le déroulement normal d'une
        préparation — mais « l'état actuel de cette grille vient-il d'un
        chargement amputé ». Seul le dernier compte : celui d'avant a été
        remplacé, ses rejets avec.
        """
        return self._fetch_all(
            "SELECT DISTINCT ON (target) target, rows_rejected, rows_accepted, "
            "filename, imported_at "
            "FROM import_batch WHERE campaign_id = %s "
            "ORDER BY target, imported_at DESC",
            (campaign_id,),
        )


# --------------------------------------------------------------------------- #
# Scan jobs
# --------------------------------------------------------------------------- #

class ScanJobRepository(_Base):
    """Le suivi d'un scan multi-feuilles (migration 015).

    La lecture d'une pile de cent feuilles dure des minutes. Cette table est ce
    que l'écran interroge pendant ce temps : sans elle, une attente longue est
    indistinguable d'une panne, et la requête HTTP du chargement finissait par
    être coupée par la passerelle en emportant le travail déjà fait.
    """

    _COLUMNS = (
        "id, campaign_id, sheet_id, filename, content_type, status, step, "
        "total_pages, pages_routed, sheets_total, sheets_done, report, error, "
        "overwrite_reviewed, created_by, created_at, started_at, finished_at"
    )

    def create(
        self,
        *,
        campaign_id: str,
        filename: str,
        content_type: str,
        overwrite_reviewed: bool,
        actor: str,
        sheet_id: str | None = None,
    ) -> str:
        """``sheet_id`` renseigné = scan d'une feuille ; nul = pile complète."""
        job_id = new_id()
        self._execute(
            "INSERT INTO scan_job (id, campaign_id, sheet_id, filename, "
            "content_type, overwrite_reviewed, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (job_id, campaign_id, sheet_id, filename, content_type,
             overwrite_reviewed, actor),
        )
        return job_id

    def latest_for_sheet(self, sheet_id: str, campaign_id: str) -> dict[str, Any] | None:
        """Le dernier scan déposé sur cette feuille, terminé ou non.

        C'est ce qui permet à l'écran de retrouver une lecture en cours après un
        rafraîchissement : sans lui, recharger la page pendant un scan donne une
        feuille d'apparence inerte, et l'utilisateur relance une lecture qui
        tourne déjà.
        """
        return self._fetch_one(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE sheet_id = %s "
            "AND campaign_id = %s ORDER BY created_at DESC LIMIT 1",
            (sheet_id, campaign_id),
        )

    def get(self, job_id: str, campaign_id: str) -> dict[str, Any] | None:
        """Filtré sur la campagne autant que sur le travail : l'identifiant vient
        de l'URL, et rien d'autre n'empêcherait de lire le scan du voisin."""
        return self._fetch_one(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE id = %s AND campaign_id = %s",
            (job_id, campaign_id),
        )

    def list(self, campaign_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        return self._fetch_all(
            f"SELECT {self._COLUMNS} FROM scan_job WHERE campaign_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (campaign_id, limit),
        )

    def start(self, job_id: str, *, total_pages: int = 0) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'RUNNING', started_at = now(), "
            "step = %s, total_pages = %s WHERE id = %s",
            ("Préparation des pages", total_pages, job_id),
        )

    def progress(
        self,
        job_id: str,
        *,
        step: str,
        total_pages: int | None = None,
        pages_routed: int | None = None,
        sheets_total: int | None = None,
        sheets_done: int | None = None,
    ) -> None:
        """Avance le compteur, sans jamais le faire reculer.

        Chaque champ est optionnel : une étape qui ne connaît pas encore le
        nombre de feuilles ne doit pas le remettre à zéro en le passant.
        """
        sets = ["step = %s"]
        params: list[Any] = [step]
        for column, value in (
            ("total_pages", total_pages),
            ("pages_routed", pages_routed),
            ("sheets_total", sheets_total),
            ("sheets_done", sheets_done),
        ):
            if value is not None:
                sets.append(f"{column} = %s")
                params.append(value)
        params.append(job_id)
        self._execute(f"UPDATE scan_job SET {', '.join(sets)} WHERE id = %s", params)

    def finish(self, job_id: str, *, report: dict[str, Any]) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'SUCCEEDED', step = 'Terminé', "
            "report = %s, finished_at = now(), "
            "sheets_done = %s WHERE id = %s",
            (Jsonb(report), len(report.get("sheetsProcessed") or []), job_id),
        )

    def fail(self, job_id: str, *, error: str) -> None:
        self._execute(
            "UPDATE scan_job SET status = 'FAILED', step = 'Échec', error = %s, "
            "finished_at = now() WHERE id = %s",
            (error[:2000], job_id),
        )

    def abandon_orphans(self, *, reason: str) -> int:
        """Marque en échec les travaux d'un conteneur qui n'existe plus.

        Le PDF vit en mémoire du processus qui l'a reçu : un travail encore
        « en cours » au démarrage appartient à une instance disparue et
        n'avancera jamais. Le laisser dans cet état, c'est une barre de
        progression qui tourne pour toujours.
        """
        return self._execute(
            "UPDATE scan_job SET status = 'FAILED', step = 'Échec', error = %s, "
            "finished_at = now() WHERE status IN ('QUEUED', 'RUNNING')",
            (reason,),
        )


# --------------------------------------------------------------------------- #
# Exploitation
# --------------------------------------------------------------------------- #

class OperationsRepository(_Base):
    """Ce qu'un exploitant demande à la base quand quelque chose ne va pas.

    Ces requêtes ne servent aucun écran métier : elles répondent aux trois
    questions qu'on pose un jour d'inventaire — « le miroir ERP est-il à
    jour ? », « les chargements passent-ils ? », « les scans avancent-ils ? » —
    et qui n'avaient jusqu'ici de réponse qu'en ouvrant un client SQL.

    Elles sont **globales**, sans campagne : c'est l'installation qu'on
    diagnostique, pas un inventaire en particulier. Une lecture par campagne
    existe déjà là où elle a du sens (``ImportBatchRepository.latest_per_target``,
    lue par la clôture).
    """

    #: Les tables du miroir ERP, et ce que chacune alimente. Toutes portent un
    #: ``synced_at`` écrit par le job de synchronisation ; c'est ce qui permet
    #: de poser une seule requête plutôt qu'une par table.
    MIRRORS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("erp_base_article", "Référentiel articles"),
        ("erp_bom", "Nomenclatures"),
        ("erp_mouvements", "Mouvements de stock"),
        ("erp_stock_snapshot", "Snapshot de stock"),
        ("erp_ecart_backflush", "Écart backflush"),
    )

    def erp_freshness(self) -> list[dict[str, Any]]:
        """Pour chaque table du miroir : son volume et sa dernière synchro.

        Une table vide et une table jamais synchronisée se ressemblent depuis
        l'application — les deux donnent « le miroir ERP est vide » — mais pas
        du tout depuis l'exploitation : la première dit que le job a tourné sur
        une source vide, la seconde qu'il n'a pas tourné.
        """
        union = " UNION ALL ".join(
            f"SELECT '{table}' AS table_name, count(*) AS rows, "
            f"max(synced_at) AS synced_at FROM {table}"
            for table, _ in self.MIRRORS
        )
        labels = dict(self.MIRRORS)
        rows = {r["table_name"]: r for r in self._fetch_all(union)}
        return [
            {
                "table": table,
                "label": labels[table],
                "rows": int(rows.get(table, {}).get("rows") or 0),
                "syncedAt": rows.get(table, {}).get("synced_at"),
            }
            for table, _ in self.MIRRORS
        ]

    def import_volumes(self, *, hours: int = 24) -> dict[str, Any]:
        """Les chargements récents, et ce qu'ils ont refusé.

        ``rejected`` est la ligne qu'on regarde : un import qui rejette est
        désormais refusé en bloc lorsqu'il remplace, mais un fichier qui rejette
        systématiquement quelques lignes reste un contrat mal accordé, et rien
        ne le signalait tant que personne n'ouvrait le rapport.
        """
        row = self._fetch_one(
            "SELECT count(*) AS batches, "
            "coalesce(sum(rows_accepted), 0) AS accepted, "
            "coalesce(sum(rows_rejected), 0) AS rejected, "
            "count(*) FILTER (WHERE rows_rejected > 0) AS with_rejects, "
            "max(imported_at) AS last_at "
            "FROM import_batch WHERE imported_at > now() - make_interval(hours => %s)",
            (hours,),
        ) or {}
        return {
            "hours": hours,
            "batches": int(row.get("batches") or 0),
            "rowsAccepted": int(row.get("accepted") or 0),
            "rowsRejected": int(row.get("rejected") or 0),
            "batchesWithRejects": int(row.get("with_rejects") or 0),
            "lastAt": row.get("last_at"),
        }

    def scan_jobs(self, *, hours: int = 24) -> dict[str, Any]:
        """L'état des lectures de feuilles scannées, par statut.

        Un scan qui reste « en cours » est le symptôme d'un conteneur recyclé
        en pleine lecture : le PDF vivait dans sa mémoire. Le démarrage marque
        ces travaux en échec, mais leur nombre dit à quelle fréquence cela
        arrive.
        """
        rows = self._fetch_all(
            "SELECT status, count(*) AS n FROM scan_job "
            "WHERE created_at > now() - make_interval(hours => %s) "
            "GROUP BY status",
            (hours,),
        )
        by_status = {str(r["status"]): int(r["n"]) for r in rows}
        return {
            "hours": hours,
            "byStatus": by_status,
            "running": by_status.get("RUNNING", 0) + by_status.get("QUEUED", 0),
            "failed": by_status.get("FAILED", 0),
        }
