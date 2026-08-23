"""L'analyse des écarts, leurs causes et les ajustements qui en sortent.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import psycopg

from ...domain.enums import (
    DataSource,
)
from ...domain.models import (
    AdjustmentLine,
    AssignableCause,
    VarianceAnalysis,
)
from ...errors import NotFoundError
from ._base import _Base, new_id

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
