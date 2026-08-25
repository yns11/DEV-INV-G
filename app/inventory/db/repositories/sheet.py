"""Les zones GENERIQUE, leurs feuilles de comptage et les arbitrages entre deux passages.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import psycopg

from ...domain.enums import (
    CountSection,
    DataSource,
    SheetPass,
)
from ...domain.models import (
    ArbitrationLine,
    CountSheet,
    CountSheetLine,
    Zone,
)
from ...errors import ConflictError, NotFoundError
from ._base import _Base, _NullContext, new_id

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
        "unit, source, confidence, qty_formula, comment, display_order, row_version"
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
            "section, qty_imported, qty_manual, unit, source, confidence, "
            "qty_formula, comment, display_order, updated_by, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
            "ON CONFLICT (id) DO UPDATE SET item_number = EXCLUDED.item_number, "
            "section = EXCLUDED.section, qty_imported = EXCLUDED.qty_imported, "
            "qty_manual = EXCLUDED.qty_manual, unit = EXCLUDED.unit, "
            "source = EXCLUDED.source, confidence = EXCLUDED.confidence, "
            "qty_formula = EXCLUDED.qty_formula, "
            "comment = EXCLUDED.comment, display_order = EXCLUDED.display_order, "
            "updated_by = EXCLUDED.updated_by, updated_at = now(), "
            "row_version = count_sheet_line.row_version + 1, deleted_at = NULL",
            [
                (l.id, l.sheet_id, l.campaign_id, l.item_number, str(l.section),
                 l.qty_imported, l.qty_manual, l.unit, str(l.source), l.confidence,
                 l.qty_formula, l.comment, l.display_order, actor)
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
            qty_formula=row.get("qty_formula") or "",
            comment=row["comment"], display_order=row["display_order"],
        )
