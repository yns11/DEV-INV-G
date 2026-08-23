"""Le référentiel d'une campagne : articles, nomenclatures, entrepôts, emplacements, zones et gestionnaires.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import psycopg

from ...domain.enums import (
    DataSource,
    ItemType,
    LocationStatus,
)
from ...domain.models import (
    BomLink,
    Item,
    Location,
    LocationKey,
    Manager,
    Warehouse,
    in_perimeter,
)
from ._base import _Base, new_id

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
