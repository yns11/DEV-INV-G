"""Les décisions d'étiquette et les dérives des emplacements précomptés.

Voir :mod:`inventory.db.repositories` pour les trois règles que tous les dépôts
appliquent.

Séparé de :mod:`.erp_journal` — qui garde le journal, son périmètre et ses
lignes brutes — parce que ce sont deux agrégats : le journal est ce que l'ERP
produit, ces deux tables sont ce que la campagne en décide. Elles ne partagent
que l'écran qui les affiche.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.enums import DriftResolution, LabelResolution
from ...domain.models import EarlyCountDrift, LabelDecision
from ._base import _Base, _NullContext

__all__ = ["LabelDecisionRepository", "EarlyCountDriftRepository"]


class LabelDecisionRepository(_Base):
    """Les issues données aux étiquettes scellées recomptées ailleurs.

    Une table minuscule et une règle simple : la décision survit aux réimports.
    Le notebook est rejoué toutes les quelques minutes le jour J, et repartir de
    zéro effacerait des décisions prises entre deux imports — un exploitant
    tranche à neuf heures et retrouve la question vierge à neuf heures cinq,
    sans que rien ne le lui dise.
    """

    _COLUMNS = (
        "id, campaign_id, label_id, item_number, decision, "
        "sealed_warehouse_id, sealed_location_id, other_warehouse_id, "
        "other_location_id, comment, decided_at, decided_by"
    )

    def list(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[LabelDecision]:
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM early_count_label_decision "
            "WHERE campaign_id = %s ORDER BY label_id, item_number",
            (campaign_id,),
            conn=conn,
        )
        return [self._decision(row) for row in rows]

    def decide(
        self,
        decision: LabelDecision,
        *,
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Poser ou remplacer l'issue d'une étiquette.

        Remplacer, et non refuser : se raviser sur une étiquette est un geste
        légitime — on est allé voir, et ce qu'on a vu n'est pas ce qu'on croyait.
        """
        self._execute(
            "INSERT INTO early_count_label_decision "
            "(id, campaign_id, label_id, item_number, decision, "
            " sealed_warehouse_id, sealed_location_id, other_warehouse_id, "
            " other_location_id, comment, decided_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (campaign_id, label_id, item_number) DO UPDATE SET "
            "decision = EXCLUDED.decision, comment = EXCLUDED.comment, "
            "decided_at = now(), decided_by = EXCLUDED.decided_by",
            (
                decision.id, decision.campaign_id, decision.label_id,
                decision.item_number, str(decision.decision),
                decision.sealed_warehouse_id, decision.sealed_location_id,
                decision.other_warehouse_id, decision.other_location_id,
                decision.comment, decision.decided_by,
            ),
            conn=conn,
        )
        return decision.id

    def clear(
        self,
        campaign_id: str,
        label_id: str,
        item_number: str,
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Retirer l'issue : la question redevient ouverte."""
        return self._execute(
            "DELETE FROM early_count_label_decision "
            "WHERE campaign_id = %s AND label_id = %s AND item_number = %s",
            (campaign_id, label_id, item_number),
            conn=conn,
        )

    @staticmethod
    def _decision(row: dict[str, Any]) -> LabelDecision:
        return LabelDecision(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            label_id=row["label_id"],
            item_number=row["item_number"],
            decision=LabelResolution(row["decision"]),
            sealed_warehouse_id=row["sealed_warehouse_id"],
            sealed_location_id=row["sealed_location_id"],
            other_warehouse_id=row["other_warehouse_id"],
            other_location_id=row["other_location_id"],
            comment=row["comment"] or "",
            decided_at=row["decided_at"],
            decided_by=row["decided_by"] or "",
        )


class EarlyCountDriftRepository(_Base):
    """Les dérives d'une campagne, et l'issue qu'un humain leur donne."""

    _COLUMNS = (
        "id, campaign_id, erp_journal_id, warehouse_id, location_id, item_number, "
        "qty_erp_t0, qty_physical_t0, qty_erp_j, drift_value, is_material, "
        "resolution, cause_code, comment, resolved_at, resolved_by"
    )

    def list(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[EarlyCountDrift]:
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM early_count_drift WHERE campaign_id = %s "
            "ORDER BY warehouse_id, location_id, item_number",
            (campaign_id,),
            conn=conn,
        )
        return [self._drift(row) for row in rows]

    def replace(
        self,
        campaign_id: str,
        drifts: Sequence[EarlyCountDrift],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Recalculer les dérives **en conservant les issues déjà données**.

        Le notebook est rejoué très régulièrement le jour J, et chaque import
        relance ce calcul. Repartir de zéro effacerait les décisions prises
        entre deux imports — un exploitant tranche une dérive à neuf heures et
        la retrouve vierge à neuf heures cinq, sans que rien ne le dise.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "SELECT warehouse_id, location_id, item_number, resolution, "
                "cause_code, comment, resolved_at, resolved_by "
                "FROM early_count_drift "
                "WHERE campaign_id = %s AND resolution IS NOT NULL",
                (campaign_id,),
            )
            decided = {
                (r["warehouse_id"], r["location_id"], r["item_number"]): r
                for r in cur.fetchall()
            }
            cur.execute(
                "DELETE FROM early_count_drift WHERE campaign_id = %s", (campaign_id,)
            )
            if not drifts:
                return 0
            cur.executemany(
                "INSERT INTO early_count_drift (id, campaign_id, erp_journal_id, "
                "warehouse_id, location_id, item_number, qty_erp_t0, "
                "qty_physical_t0, qty_erp_j, drift_qty, drift_value, is_material, "
                "resolution, cause_code, comment, resolved_at, resolved_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        drift.id, campaign_id, drift.erp_journal_id, drift.warehouse_id,
                        drift.location_id, drift.item_number, drift.qty_erp_t0,
                        drift.qty_physical_t0, drift.qty_erp_j, drift.drift_qty,
                        drift.drift_value, drift.is_material,
                        *self._carried(decided, drift),
                    )
                    for drift in drifts
                ],
            )
            return len(drifts)

    @staticmethod
    def _carried(decided: dict, drift: EarlyCountDrift) -> tuple:
        previous = decided.get(
            (drift.warehouse_id, drift.location_id, drift.item_number)
        )
        if previous is None:
            return (None, "", "", None, None)
        return (
            previous["resolution"], previous["cause_code"] or "",
            previous["comment"] or "", previous["resolved_at"],
            previous["resolved_by"],
        )

    def resolve(
        self,
        campaign_id: str,
        drift_ids: Sequence[str],
        resolution: DriftResolution,
        *,
        cause_code: str,
        comment: str,
        actor: str,
        resolved_at: Any,
        conn: psycopg.Connection | None = None,
    ) -> int:
        return self._execute(
            "UPDATE early_count_drift SET resolution = %s, cause_code = %s, "
            "comment = %s, resolved_at = %s, resolved_by = %s "
            "WHERE campaign_id = %s AND id = ANY(%s::uuid[])",
            (str(resolution), cause_code, comment, resolved_at, actor,
             campaign_id, list(drift_ids)),
            conn=conn,
        )

    @staticmethod
    def _drift(row: dict[str, Any]) -> EarlyCountDrift:
        return EarlyCountDrift(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            erp_journal_id=(
                str(row["erp_journal_id"]) if row["erp_journal_id"] else None
            ),
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            item_number=row["item_number"],
            qty_erp_t0=row["qty_erp_t0"],
            qty_physical_t0=row["qty_physical_t0"],
            qty_erp_j=row["qty_erp_j"],
            drift_value=row["drift_value"],
            is_material=row["is_material"],
            resolution=(
                DriftResolution(row["resolution"]) if row["resolution"] else None
            ),
            cause_code=row["cause_code"] or "",
            comment=row["comment"] or "",
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"] or "",
        )
