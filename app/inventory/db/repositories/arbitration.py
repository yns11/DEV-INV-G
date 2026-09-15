"""L'arbitrage entre les deux comptages d'une zone.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.

Ce qui le sépare des feuilles
-----------------------------
Un arbitrage ne porte pas sur une feuille mais sur **la zone** : il compare ce
que deux équipes ont trouvé, article par article et section par section, et la
quantité qu'il retient ne s'écrit sur aucune des deux feuilles. Il naît des
comptages, il ne s'y range pas.

Il vivait pourtant dans le dépôt des feuilles, et la couche au-dessus disait
déjà le contraire : l'arbitrage a son propre service depuis longtemps. Ce module
met le dépôt d'accord avec lui.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import psycopg

from ...domain.enums import CountSection
from ...domain.models import ArbitrationLine
from ...errors import NotFoundError
from ._base import _Base

__all__ = ["ArbitrationRepository"]


class ArbitrationRepository(_Base):
    """Lit, propose et tranche les arbitrages d'une campagne."""

    def list_arbitrations(
        self,
        campaign_id: str,
        *,
        zone_id: str | None = None,
        conn: psycopg.Connection | None = None,
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
            conn=conn,
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
            # **Ce que le domaine écrit fait foi, y compris quand il écrit
            # NULL.** Ces trois colonnes ont porté un ``COALESCE(EXCLUDED.…,
            # arbitration.…)`` — une prudence qui gardait la décision existante
            # quand la ligne n'en apportait pas. Elle défaisait la seule règle
            # qui compte ici : un arbitrage dont l'un des deux comptages a bougé
            # perd sa signature. ``build_arbitration_lines`` posait bien
            # ``decided_by = None`` ; ``COALESCE(NULL, l'ancien)`` rendait
            # l'ancien, et la décision périmée survivait — visible nulle part,
            # puisque le domaine, lui, était juste, et que les contrôles le
            # vérifiaient sur le domaine.
            #
            # La fonction du domaine recopie déjà la décision antérieure quand
            # elle reste valable : l'affectation directe la préserve donc dans
            # ce cas, et l'efface dans l'autre. C'est exactement ce qui est
            # voulu, et il n'y a qu'un seul endroit qui en décide.
            "ON CONFLICT (zone_id, item_number, section) DO UPDATE SET "
            "qty_pass_1 = EXCLUDED.qty_pass_1, qty_pass_2 = EXCLUDED.qty_pass_2, "
            "qty_arbitrated = EXCLUDED.qty_arbitrated, "
            "decided_by = EXCLUDED.decided_by, "
            "decided_at = EXCLUDED.decided_at, "
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
