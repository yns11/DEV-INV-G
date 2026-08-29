"""Les journaux ERP, leur périmètre déclaré et leurs lignes brutes.

Voir :mod:`inventory.db.repositories` pour les trois règles que tous les dépôts
appliquent.

Un journal ERP n'est pas un emplacement. Il tient à un entrepôt et couvre
plusieurs emplacements — sur l'export du 13 juin 2026, 48 journaux sur 73 en
couvrent plus d'un, jusqu'à 54 pour l'un d'eux. Ses lignes sont conservées au
grain où l'ERP les produit, une par étiquette, quand
:class:`~inventory.domain.models.CountJournalLine` garde celui sur lequel tout
le reste de l'application est écrit : emplacement plus article.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Sequence
from typing import Any

import psycopg

from ...domain.enums import DriftResolution, JournalKind, LabelResolution
from ...domain.models import (
    EarlyCountDrift,
    ErpJournal,
    ErpJournalLine,
    LabelDecision,
    LocationKey,
)
from ._base import _Base, _NullContext, new_id

__all__ = [
    "LabelDecisionRepository",
    "EarlyCountDriftRepository",
    "ErpJournalRepository",
]


class ErpJournalRepository(_Base):
    """Les journaux ERP d'une campagne."""

    _COLUMNS = (
        "id, campaign_id, journal_number, kind, description, site_id, "
        "erp_posted, erp_posted_at, line_count, first_imported_at, "
        "last_imported_at, scope_declared_at, scope_declared_by, "
        "counted_on, sealed_at, sealed_by"
    )

    _LINE_COLUMNS = (
        "id, erp_journal_id, campaign_id, erp_line_number, site_id, warehouse_id, "
        "location_id, label_id, serial_number, item_number, qty_on_hand, "
        "qty_counted, unit, inventory_status_id"
    )

    # ------------------------------------------------------------------ lecture

    def list(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[ErpJournal]:
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM erp_journal "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY journal_number",
            (campaign_id,),
            conn=conn,
        )
        scopes = self.scopes(campaign_id, conn=conn)
        return [self._journal(row, scopes.get(str(row["id"]), [])) for row in rows]

    def get_by_number(
        self, campaign_id: str, number: str, *,
        conn: psycopg.Connection | None = None,
    ) -> ErpJournal | None:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM erp_journal "
            "WHERE campaign_id = %s AND journal_number = %s AND deleted_at IS NULL",
            (campaign_id, number),
            conn=conn,
        )
        if row is None:
            return None
        scopes = self.scopes(campaign_id, conn=conn)
        return self._journal(row, scopes.get(str(row["id"]), []))

    def scopes(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> dict[str, list[LocationKey]]:
        rows = self._fetch_all(
            "SELECT erp_journal_id, warehouse_id, location_id FROM erp_journal_scope "
            "WHERE campaign_id = %s ORDER BY warehouse_id, location_id",
            (campaign_id,),
            conn=conn,
        )
        out: dict[str, list[LocationKey]] = {}
        for row in rows:
            out.setdefault(str(row["erp_journal_id"]), []).append(
                LocationKey(
                    warehouse_id=row["warehouse_id"], location_id=row["location_id"]
                )
            )
        return out

    def lines(
        self, campaign_id: str, erp_journal_id: str, *,
        conn: psycopg.Connection | None = None,
    ) -> list[ErpJournalLine]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM erp_journal_line "
            "WHERE campaign_id = %s AND erp_journal_id = %s "
            "ORDER BY erp_line_number NULLS LAST, item_number",
            (campaign_id, erp_journal_id),
            conn=conn,
        )
        return [self._line(row) for row in rows]

    # ------------------------------------------------------------------ écriture

    def upsert_journal(
        self,
        campaign_id: str,
        *,
        journal_number: str,
        kind: JournalKind,
        description: str = "",
        site_id: str = "",
        erp_posted: bool = False,
        erp_posted_at: dt.datetime | None = None,
        line_count: int = 0,
        counted_on: dt.date | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Enregistrer ou rafraîchir l'en-tête d'un journal ERP, et rendre son id.

        Le périmètre déclaré et le scellement ne sont **jamais** touchés ici :
        un réimport rafraîchit les faits que l'ERP annonce — postage, nombre de
        lignes, date de comptage — pas la décision qu'un humain a prise.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "INSERT INTO erp_journal (id, campaign_id, journal_number, kind, "
                "description, site_id, erp_posted, erp_posted_at, line_count, "
                "counted_on) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (campaign_id, journal_number) "
                "WHERE deleted_at IS NULL DO UPDATE SET "
                "kind = EXCLUDED.kind, description = EXCLUDED.description, "
                "site_id = EXCLUDED.site_id, erp_posted = EXCLUDED.erp_posted, "
                "erp_posted_at = EXCLUDED.erp_posted_at, "
                "line_count = EXCLUDED.line_count, "
                # `COALESCE` dans cet ordre : un export qui omet la colonne ne
                # doit pas effacer la date qu'un export précédent portait.
                "counted_on = COALESCE(EXCLUDED.counted_on, erp_journal.counted_on), "
                "last_imported_at = now() "
                "RETURNING id",
                (new_id(), campaign_id, journal_number, str(kind), description,
                 site_id, erp_posted, erp_posted_at, line_count, counted_on),
            )
            return str(cur.fetchone()["id"])

    def replace_lines(
        self,
        campaign_id: str,
        erp_journal_id: str,
        lines: Sequence[ErpJournalLine],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Remplacer les lignes **de ce journal**, et d'aucun autre.

        C'est la traduction en une phrase de la règle « l'application
        n'additionne jamais plusieurs photographies, sauf pour les comptages
        avancés » : chaque import remplace ce qu'il apporte, et laisse
        intact ce qu'il n'apporte pas. Les journaux d'un lot avancé, absents de
        la fenêtre de dates du jour J, survivent donc d'eux-mêmes — sans
        exception à écrire, ce qui est la meilleure façon de ne pas l'oublier.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_line "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO erp_journal_line (id, erp_journal_id, campaign_id, "
                "erp_line_number, site_id, warehouse_id, location_id, label_id, "
                "serial_number, item_number, qty_on_hand, qty_counted, unit, "
                "inventory_status_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (line.id or new_id(), erp_journal_id, campaign_id,
                     line.erp_line_number, line.site_id, line.warehouse_id,
                     line.location_id, line.label_id, line.serial_number,
                     line.item_number, line.qty_on_hand, line.qty_counted,
                     line.unit, line.inventory_status_id)
                    for line in lines
                ],
            )
            return len(lines)

    def set_scope(
        self,
        campaign_id: str,
        erp_journal_id: str,
        keys: Sequence[LocationKey],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Déclarer les emplacements que ce journal couvre, **et les sceller**.

        Les deux gestes n'en font qu'un. Dire quels emplacements ce journal
        couvre, c'est dire lesquels sont comptés et ne bougeront plus : il n'y
        avait aucune décision entre les deux, seulement des clics.

        Un emplacement n'appartient au périmètre que d'un seul journal — c'est
        un index unique de la migration 025, pas une vérification faite ici :
        une déclaration qui empiéterait sur un autre journal est refusée par la
        base, même si le calcul qui a produit la proposition se trompait.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_scope "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            if keys:
                cur.executemany(
                    "INSERT INTO erp_journal_scope "
                    "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                    "VALUES (%s,%s,%s,%s)",
                    [
                        (erp_journal_id, campaign_id, k.warehouse_id, k.location_id)
                        for k in keys
                    ],
                )
            cur.execute(
                "UPDATE erp_journal SET scope_declared_at = now(), "
                "scope_declared_by = %s, sealed_at = now(), sealed_by = %s "
                "WHERE campaign_id = %s AND id = %s",
                (actor, actor, campaign_id, erp_journal_id),
            )
            return len(keys)

    def unseal(
        self,
        campaign_id: str,
        erp_journal_id: str,
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Retirer le périmètre et le scellement du journal.

        Le périmètre part avec le scellement, et c'est voulu : sans périmètre,
        le journal n'a plus d'emplacement à couvrir, donc plus rien à sceller.
        Redéclarer est le geste qui rescelle.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_scope "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            cur.execute(
                "UPDATE erp_journal SET scope_declared_at = NULL, "
                "scope_declared_by = '', sealed_at = NULL, sealed_by = '' "
                "WHERE campaign_id = %s AND id = %s",
                (campaign_id, erp_journal_id),
            )
            return cur.rowcount

    def touch_import(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> None:
        """Horodater le dernier import de journaux réussi sur la campagne."""
        self._execute(
            "UPDATE campaign SET journals_imported_at = now() WHERE id = %s",
            (campaign_id,),
            conn=conn,
        )

    # ------------------------------------------------------------- propositions

    def candidate_locations(
        self,
        campaign_id: str,
        erp_journal_id: str,
        *,
        buffer_key: LocationKey,
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Les emplacements que ce journal *pourrait* couvrir.

        Ceux de ses lignes, moins le tampon, moins ceux déjà alloués à un autre
        journal. Classés par nombre de lignes décroissant : le périmètre réel
        arrive en tête, ce qui rend la sélection courte quand elle est évidente.

        L'application propose, l'utilisateur tranche. Les emplacements des lignes
        ne suffisent pas à dire le périmètre — certaines ne sont là que pour
        matérialiser un déplacement, 1 932 lignes sur 58 345 dans l'export
        analysé — et deviner à leur place produirait des références fausses sur
        des emplacements que le journal ne couvre pas.
        """
        return self._fetch_all(
            """
            SELECT l.warehouse_id,
                   l.location_id,
                   count(*)                             AS line_count,
                   count(DISTINCT l.item_number)        AS item_count,
                   sum(l.qty_on_hand)                   AS qty_on_hand,
                   sum(l.qty_counted)                   AS qty_counted
            FROM erp_journal_line l
            WHERE l.campaign_id = %(cid)s
              AND l.erp_journal_id = %(jid)s
              AND l.location_id <> ''
              AND NOT (l.warehouse_id = %(bwh)s AND l.location_id = %(bloc)s)
              AND NOT EXISTS (
                    SELECT 1 FROM erp_journal_scope s
                    WHERE s.campaign_id = l.campaign_id
                      AND s.warehouse_id = l.warehouse_id
                      AND s.location_id = l.location_id
                      AND s.erp_journal_id <> l.erp_journal_id
              )
            GROUP BY l.warehouse_id, l.location_id
            ORDER BY line_count DESC, l.warehouse_id, l.location_id
            """,
            {
                "cid": campaign_id, "jid": erp_journal_id,
                "bwh": buffer_key.warehouse_id, "bloc": buffer_key.location_id,
            },
            conn=conn,
        )

    # ------------------------------------------------------------- agrégations

    def aggregate_in_scope(
        self,
        campaign_id: str,
        *,
        excluded_labels: Collection[tuple[str, str]] = (),
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Référence et comptage par (entrepôt, emplacement, article), dans le périmètre.

        C'est ici que le journal devient autonome : ``qty_on_hand`` agrège la
        colonne « Stock ERP » des lignes, c'est-à-dire le stock d'avant comptage,
        et un lot avancé n'a donc besoin d'aucun chargement séparé.

        La jointure sur le périmètre déclaré n'est pas décorative : sans elle,
        une ligne de passage créerait une référence sur un emplacement que le
        journal ne couvre pas.

        ``excluded_labels`` porte les étiquettes qu'une décision a fait sortir de
        leur emplacement scellé — la pièce est ailleurs, quelqu'un est allé
        voir. Les exclure ici plutôt qu'après coup est ce qui fait que la
        référence, le comptage et l'écart racontent la même histoire.
        """
        return self._fetch_all(
            """
            SELECT l.warehouse_id,
                   l.location_id,
                   l.item_number,
                   max(l.unit)                   AS unit,
                   max(j.journal_number)         AS journal_number,
                   sum(l.qty_on_hand)            AS qty_on_hand,
                   sum(l.qty_counted)            AS qty_counted,
                   count(*)                      AS label_count,
                   bool_and(j.erp_posted)        AS erp_posted
            FROM erp_journal_line l
            JOIN erp_journal j
              ON j.id = l.erp_journal_id AND j.campaign_id = l.campaign_id
            JOIN erp_journal_scope s
              ON s.erp_journal_id = l.erp_journal_id
             AND s.campaign_id = l.campaign_id
             AND s.warehouse_id = l.warehouse_id
             AND s.location_id = l.location_id
            WHERE l.campaign_id = %(cid)s AND j.deleted_at IS NULL
              AND (l.label_id, l.item_number) <> ALL (
                    SELECT * FROM unnest(%(labels)s::text[], %(items)s::text[])
              )
            GROUP BY l.warehouse_id, l.location_id, l.item_number
            ORDER BY l.warehouse_id, l.location_id, l.item_number
            """,
            {
                "cid": campaign_id,
                "labels": [label for label, _ in excluded_labels],
                "items": [item for _, item in excluded_labels],
            },
            conn=conn,
        )

    def labels_counted_elsewhere(
        self,
        campaign_id: str,
        sealed: Sequence[LocationKey],
        *,
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Les étiquettes d'un emplacement scellé retrouvées dans un autre journal.

        Le seul contrôle du dispositif qui descende au grain de l'étiquette, et
        celui qui rattrape ce que la dérive ne voit pas : une pièce sortie d'un
        emplacement scellé sans aucune transaction ERP laisse une dérive nulle,
        mais si elle est re-scannée ailleurs, son étiquette apparaît dans un
        second journal.

        Proportionné : sur l'export du 13 juin, 433 étiquettes sur 39 558
        figurent dans plus d'un journal — de l'ordre du pour cent, une liste
        qu'on peut réellement traiter.
        """
        if not sealed:
            return []
        return self._fetch_all(
            """
            WITH scelle AS (
                SELECT l.label_id, l.warehouse_id, l.location_id,
                       l.item_number, l.erp_journal_id
                FROM erp_journal_line l
                WHERE l.campaign_id = %(cid)s
                  AND l.label_id <> ''
                  AND l.qty_counted <> 0
                  AND (l.warehouse_id, l.location_id) IN (
                        SELECT * FROM unnest(%(wh)s::text[], %(loc)s::text[])
                  )
            )
            SELECT s.label_id,
                   s.item_number,
                   s.warehouse_id            AS sealed_warehouse_id,
                   s.location_id             AS sealed_location_id,
                   o.warehouse_id            AS other_warehouse_id,
                   o.location_id             AS other_location_id,
                   j.journal_number          AS other_journal_number,
                   o.qty_counted             AS other_qty_counted
            FROM scelle s
            JOIN erp_journal_line o
              ON o.campaign_id = %(cid)s
             AND o.label_id = s.label_id
             AND o.erp_journal_id <> s.erp_journal_id
             AND o.qty_counted <> 0
            JOIN erp_journal j
              ON j.id = o.erp_journal_id AND j.campaign_id = o.campaign_id
            ORDER BY s.label_id, o.warehouse_id, o.location_id
            """,
            {
                "cid": campaign_id,
                "wh": [k.warehouse_id for k in sealed],
                "loc": [k.location_id for k in sealed],
            },
            conn=conn,
        )

    # ------------------------------------------------------------------ mapping

    @staticmethod
    def _journal(row: dict[str, Any], scope: list[LocationKey]) -> ErpJournal:
        return ErpJournal(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            journal_number=row["journal_number"],
            kind=JournalKind(row["kind"]),
            description=row["description"],
            site_id=row["site_id"],
            erp_posted=row["erp_posted"],
            erp_posted_at=row["erp_posted_at"],
            line_count=int(row["line_count"] or 0),
            first_imported_at=row["first_imported_at"],
            last_imported_at=row["last_imported_at"],
            scope=scope,
            scope_declared_at=row["scope_declared_at"],
            scope_declared_by=row["scope_declared_by"] or "",
            counted_on=row["counted_on"],
            sealed_at=row["sealed_at"],
            sealed_by=row["sealed_by"] or "",
        )

    @staticmethod
    def _line(row: dict[str, Any]) -> ErpJournalLine:
        return ErpJournalLine(
            id=str(row["id"]),
            erp_journal_id=str(row["erp_journal_id"]),
            campaign_id=str(row["campaign_id"]),
            erp_line_number=row["erp_line_number"],
            site_id=row["site_id"],
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            label_id=row["label_id"],
            serial_number=row["serial_number"],
            item_number=row["item_number"],
            qty_on_hand=row["qty_on_hand"],
            qty_counted=row["qty_counted"],
            unit=row["unit"],
            inventory_status_id=row["inventory_status_id"],
        )


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
