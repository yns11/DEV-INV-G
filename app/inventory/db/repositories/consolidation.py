"""La consolidation : ce qui a été compté, référence par référence.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ...domain.models import (
    ConsolidatedLine,
    WipBreakdown,
)
from ._base import _Base, _NullContext, new_id

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
