"""Reading the article referential and its bill of materials from the ERP.

The silver tables in Unity Catalog are fed from Dynamics 365 F&O, and they are a
better starting point than a spreadsheet for the obvious reason: nobody has
retyped anything. Loading from them removes the export/re-import round trip that
produced most of the referential errors in the legacy process.

Two design choices matter here.

**The ERP is a source, not a second pipeline.** What this module returns is rows
shaped like the ``items`` and ``boms`` grid contracts — the same rows a
spreadsheet would have produced. Everything downstream is therefore unchanged:
the same validation, the same dry-run preview, the same mappers, the same audit
trail, and the same editable grid afterwards. An ERP load is one more input
mode, not a bypass.

**The translation is explicit and lives here.** The ERP has its own vocabulary —
``item_group_id`` for the functional group, a price that may be quoted per *n*
units, ``programme = 'Commun'`` for a shared article. Turning that into the
campaign's vocabulary is a decision, and decisions belong in readable code
rather than in a SQL string nobody re-reads.

Reads go through the SQL Statement Execution API of the workspace's warehouse:
no extra dependency, and the caller's own credentials govern what is readable.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Iterator, Sequence
from enum import StrEnum
from typing import Any

from ..config import get_settings
from ..domain.enums import FlowKind
from ..errors import UpstreamError, ValidationError

log = logging.getLogger(__name__)

__all__ = [
    "ErpReader", "ERP_ITEM_TYPES", "erp_available", "unavailable_reason",
    "reading_from_mirror", "mirror_state", "validate_period",
    "ITEM_COLUMNS", "BACKFLUSH_COLUMNS",
    "MIRROR_ITEMS_TABLE", "MIRROR_BOM_TABLE", "MIRROR_BACKFLUSH_TABLE",
    "MIRROR_MOVEMENTS_TABLE", "MOVEMENT_COLUMNS",
]

#: ERP functional group → the campaign's article type. Packaging never appears:
#: the silver table already excludes ``EMBLG``, as does the data dictionary.
ERP_ITEM_TYPES = {
    "COMPO": "COMPONENT",       # composant acheté ou fabriqué
    "PFINI": "FINISHED",        # produit fini
    "PSMFI": "SEMI_FINISHED",   # sous-ensemble
    "PVENDU": "FINISHED",       # référence de vente (P-00, fini ou semi-fini)
    "PPROTO": "COMPONENT",      # prototype : traité comme un composant, comme
                                # l'était l'appro prototype avant son propre groupe
    "APVPR": "COMPONENT",       # après-vente
}

#: Groups that are not physical stock. Left UNKNOWN rather than guessed: a
#: subcontracted operation valued as a component would distort the variance.
_NON_STOCK_GROUPS = {"SSTRA", "PRESTA"}

#: The ERP's marker for an article shared across programmes.
_COMMON_PROGRAMME = "COMMUN"

#: How long a read may block. The platform caps a request at 120 s, and a
#: referential read that has not answered in 50 s will not answer in 119.
_STATEMENT_TIMEOUT = "50s"

#: The article columns, in the order :func:`_item_row` unpacks them. Declared
#: once because two transports now read them — Unity Catalog and the local
#: mirror — and a column added to one query but not the other would shift every
#: field of the tuple by one, silently loading prices into unit codes.
ITEM_COLUMNS = (
    "item_id", "item_name", "item_description", "search_name", "name_alias",
    "categorie", "programme", "item_group_id", "item_group_label",
    "std_cost_price", "std_price_unit", "std_unit",
)

#: Same contract for a bill-of-materials link. The parent designation is joined
#: in from the article table, hence the aliases.
#:
#: ``statut`` decides whether the link is exploded. Every version of a recipe is
#: read — retired ones included — because an assembly whose only recipe is out
#: of force *has* a structure, and reporting it as having none produced a page
#: of alerts nobody could act on.
_BOM_SELECT = (
    "b.parent_itemid", "p.item_name AS parent_name", "b.child_itemid",
    "b.child_qty", "b.child_unitid", "b.statut",
)

#: Mirror tables, in the application's own Lakebase schema (migration 005).
MIRROR_ITEMS_TABLE = "erp_base_article"
MIRROR_BOM_TABLE = "erp_bom"
MIRROR_BACKFLUSH_TABLE = "erp_ecart_backflush"
#: Receipts, shipments and scrap, at article × day grain (migration 011). One
#: table for the three, told apart by `kind`.
MIRROR_MOVEMENTS_TABLE = "erp_mouvement_stock"

#: Columns the backflush mirror carries, in the order the sync job copies them.
#: Only what the application reads: the gold table holds a score of others that
#: would cost storage here for nothing.
BACKFLUSH_COLUMNS = (
    "semaine_debut", "parent_itemid", "child_itemid", "child_name", "child_unite",
    "qty_parent_produite", "conso_theorique", "conso_reelle", "ecart_brut",
    "loaded_at",
)

#: Columns the movements mirror carries, in the order the sync job copies them.
MOVEMENT_COLUMNS = ("kind", "item_id", "mouvement_date", "qty")

#: Past this age, the mirror is reported as stale. A referential is not a live
#: feed — a week-old copy is usually fine — but a campaign counted against a
#: month-old one has to say so on screen rather than in a log.
MIRROR_STALE_AFTER_DAYS = 7


class _OnWaitTimeout(StrEnum):
    """What the warehouse does when the wait expires.

    The SDK reads ``.value`` off this argument, so a plain string raises
    ``'str' object has no attribute 'value'`` at call time — which is exactly
    what happened in production. Mirrored here rather than imported from
    ``databricks.sdk.service.sql`` so that the read path stays testable without
    a workspace: the SDK only ever reads the value.
    """

    #: Give up rather than leave a statement running that nobody will collect.
    CANCEL = "CANCEL"


def reading_from_mirror() -> bool:
    """Whether the ERP is read from the local mirror rather than from the ERP."""
    return get_settings().erp_source == "mirror"


def erp_available() -> bool:
    """Whether an ERP read can even be attempted.

    Used by the screen to offer the option or explain its absence, rather than
    presenting a button that will always fail. The mirror does not need a
    warehouse: it is read over the application's own database connection.
    """
    settings = get_settings()
    if settings.erp_source == "mirror":
        return settings.lakebase_configured
    return bool(settings.warehouse_id)


def unavailable_reason() -> str | None:
    """Why the button cannot be offered, in the user's terms."""
    if erp_available():
        return None
    if reading_from_mirror():
        return "La base de l'application n'est pas accessible."
    return "Aucun entrepôt SQL n'est attaché à l'application."


def mirror_state() -> dict[str, Any]:
    """Row counts and synchronisation dates of the local mirror.

    Shown next to the button so nobody loads a referential without seeing how
    old it is. Never raises: a screen that cannot say « je ne sais pas » would
    fail to display at all when the mirror has not been created yet.
    """
    state: dict[str, Any] = {}
    for key, table in (
        ("items", MIRROR_ITEMS_TABLE),
        ("boms", MIRROR_BOM_TABLE),
        ("backflush", MIRROR_BACKFLUSH_TABLE),
        ("movements", MIRROR_MOVEMENTS_TABLE),
    ):
        try:
            from ..db.engine import get_database

            with get_database().cursor() as cur:
                cur.execute(
                    f"SELECT count(*) AS rows, max(synced_at) AS synced_at FROM {table}"
                )
                row = cur.fetchone() or {}
        except Exception as exc:  # pragma: no cover — depends on the database
            log.warning("État du miroir ERP (%s) illisible : %s", table, exc)
            state[key] = {"rows": None, "syncedAt": None, "stale": None}
            continue

        synced_at = row.get("synced_at")
        state[key] = {
            "rows": int(row.get("rows") or 0),
            "syncedAt": synced_at.isoformat() if synced_at else None,
            "stale": _is_stale(synced_at),
        }
    return state


def _is_stale(synced_at: Any) -> bool:
    if synced_at is None:
        return True
    now = dt.datetime.now(dt.UTC)
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=dt.UTC)
    return (now - synced_at).days >= MIRROR_STALE_AFTER_DAYS


class ErpReader:
    """Reads the ERP silver tables and yields grid-contract rows."""

    def __init__(
        self, client: Any | None = None, *, warehouse_id: str | None = None
    ) -> None:
        self._client = client
        self._settings = get_settings()
        self._warehouse_id = warehouse_id or self._settings.warehouse_id
        # A caller that hands in a workspace client is reading Unity Catalog by
        # construction — that is what the client is for.
        self._from_mirror = client is None and self._settings.erp_source == "mirror"

    # ------------------------------------------------------------------ items

    def fetch_items(self, *, limit: int) -> list[dict[str, Any]]:
        """The article referential, as ``items`` grid rows.

        Ordered by article number so a truncated read is a prefix rather than an
        arbitrary sample — a partial load whose contents depend on the query
        planner is impossible to reason about the next day.
        """
        if self._from_mirror:
            return [_item_row(r) for r in _mirror_rows(
                MIRROR_ITEMS_TABLE, ITEM_COLUMNS, order_by="item_id", limit=limit
            )]
        table = self._settings.erp_items_fqn
        rows = self._query(
            f"""
            SELECT {", ".join(ITEM_COLUMNS)}
            FROM {table}
            ORDER BY item_id
            LIMIT {int(limit)}
            """,
            source=table,
        )
        return [_item_row(r) for r in rows]

    # ------------------------------------------------------------------- boms

    def fetch_bom_links(self, *, limit: int) -> list[dict[str, Any]]:
        """The bill of materials, as ``boms`` grid rows.

        The parent's designation is joined in rather than left blank: the grid
        shows it, and a second round trip to fetch names the referential already
        holds would be wasted.

        Every version is returned, in force or not. Filtering here would make
        an assembly with a retired recipe indistinguishable from one the ERP has
        no recipe for at all — two situations calling for two different actions.
        The explosion applies the filter instead, where it belongs.
        """
        if self._from_mirror:
            return [_bom_row(r) for r in _mirror_rows(
                f"{MIRROR_BOM_TABLE} b "
                f"LEFT JOIN {MIRROR_ITEMS_TABLE} p ON b.parent_itemid = p.item_id",
                _BOM_SELECT,
                order_by="b.parent_itemid, b.child_itemid",
                limit=limit,
            )]
        bom, items = self._settings.erp_bom_fqn, self._settings.erp_items_fqn
        rows = self._query(
            f"""
            SELECT {", ".join(_BOM_SELECT)}
            FROM {bom} b
            LEFT JOIN {items} p ON b.parent_itemid = p.item_id
            ORDER BY b.parent_itemid, b.child_itemid
            LIMIT {int(limit)}
            """,
            source=bom,
        )
        return [_bom_row(r) for r in rows]

    # -------------------------------------------------------------- backflush

    def fetch_backflush(
        self, *, period_start: dt.date, period_end: dt.date, limit: int
    ) -> list[dict[str, Any]]:
        """The backflush variance per component, as ``backflush`` grid rows.

        This is the guide's reference query, and three of its choices are load
        bearing rather than stylistic:

        * **No filter on** ``type_ecart``. Dropping the lines labelled
          « Conforme » would remove thousands of small variances whose sum is
          not small.
        * **No filter on** ``statut_ligne``. « Hors nomenclature » and « Sans
          consommation » are the two cases where the system stock drifted most;
          they are signal, and excluding them removes exactly the evidence.
        * ``qty_parent_produite`` **is never summed here**. It is repeated on
          every component line of a parent, so its sum means nothing. The one
          place it is legitimately totalled — :meth:`fetch_stock_flow` — first
          collapses it to one value per parent and week.

        Bounds are ISO Mondays, start inclusive and end exclusive, because that
        is the grain of the fact table: a period cut mid-week would either count
        a whole week's production against three of its days or drop it entirely.
        """
        start, end = _assert_bounds(period_start, period_end)
        table = self._table()
        statement = f"""
            SELECT
                f.child_itemid                  AS item_number,
                MAX(f.child_name)               AS name,
                MAX(f.child_unite)              AS unit,
                SUM(f.ecart_brut)               AS net_qty,
                SUM(GREATEST(f.ecart_brut, 0))  AS under_consumed_qty,
                SUM(GREATEST(-f.ecart_brut, 0)) AS over_consumed_qty,
                SUM(f.conso_theorique)          AS theoretical_qty,
                SUM(f.conso_reelle)             AS actual_qty,
                COUNT(DISTINCT f.parent_itemid) AS parent_count,
                COUNT(DISTINCT f.semaine_debut) AS week_count,
                MAX(f.loaded_at)                AS source_loaded_at
            FROM {table} AS f
            WHERE f.semaine_debut >= DATE '{start}'
              AND f.semaine_debut <  DATE '{end}'
            GROUP BY f.child_itemid
            ORDER BY f.child_itemid
            LIMIT {int(limit)}
        """
        return [
            _backflush_row(row, start=period_start, end=period_end)
            for row in self._read(statement, source=table)
        ]

    def fetch_stock_flow(
        self, *, period_start: dt.date, period_end: dt.date, limit: int
    ) -> list[dict[str, Any]]:
        """Production and theoretical consumption per article, over a period.

        Two measures at two different grains, joined on the article: an article
        is produced *as a parent* and consumed *as a component*, and a
        sub-assembly is legitimately both.

        The production half collapses to one row per parent and week before
        summing. ``qty_parent_produite`` is repeated across every component line
        of the parent, so summing it raw multiplies the output by the size of the
        bill of materials — the single most expensive mistake available in this
        table. ``MAX`` rather than ``DISTINCT`` on purpose: if one week ever
        carried two different values for the same parent, ``DISTINCT`` would keep
        both and double-count, where ``MAX`` still yields one row per week.
        """
        start, end = _assert_bounds(period_start, period_end)
        table = self._table()
        window = (
            f"WHERE semaine_debut >= DATE '{start}' AND semaine_debut < DATE '{end}'"
        )
        statement = f"""
            WITH production AS (
                SELECT parent_itemid AS item_number, SUM(qty) AS produced_qty
                FROM (
                    SELECT parent_itemid, semaine_debut,
                           MAX(qty_parent_produite) AS qty
                    FROM {table}
                    {window}
                    GROUP BY parent_itemid, semaine_debut
                ) AS weekly
                GROUP BY parent_itemid
            ),
            consommation AS (
                SELECT child_itemid AS item_number,
                       SUM(conso_theorique) AS consumed_qty
                FROM {table}
                {window}
                GROUP BY child_itemid
            )
            SELECT
                COALESCE(p.item_number, c.item_number) AS item_number,
                COALESCE(p.produced_qty, 0)            AS produced_qty,
                COALESCE(c.consumed_qty, 0)            AS consumed_qty
            FROM production p
            FULL OUTER JOIN consommation c ON p.item_number = c.item_number
            ORDER BY 1
            LIMIT {int(limit)}
        """
        return [_stock_flow_row(row) for row in self._read(statement, source=table)]

    # ------------------------------------------------------------- mouvements

    def fetch_movements(
        self,
        kind: FlowKind,
        *,
        period_start: dt.date,
        period_end: dt.date,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Receipts, shipments or scrap per article, over the period.

        The three queries come from the ERP movement guide, and three of their
        choices carry the result:

        * **Receipts and shipments come from the packing-slip lines**, not from
          ``invent_trans``. Those lines are the ones tied to a purchase or sales
          order, so a quantity that looks wrong can be traced back to a document
          somebody can open.
        * **Scrap is identified by its bin**, ``QUAL VRAC`` / ``QUA REBUT``, not
          by the journal that moved it. Production NOK, quality holds and manual
          write-offs each use a different journal and all land in that bin;
          filtering on journals would quietly miss two of the three paths.
        * **Bounds are start-inclusive, end-exclusive**, matching the backflush
          half. Mixing an inclusive end here with an exclusive one there would
          count the closing Monday twice in the same comparison.

        Quantities come back **as the ERP signs them** — shipments positive,
        returns negative, scrap negative. The direction belongs to the step, so
        the caller takes the absolute value; the sign is reported separately for
        the rows that carry information in it.

        Reads the local mirror when the application is configured for it, like
        every other ERP read. That matters more here than elsewhere: these three
        tables live in a *different catalogue* from the referential, hence behind
        a second ``USE CATALOG`` grant, and an application already running on the
        mirror because the first grant was missing is unlikely to have the
        second.
        """
        start, end = _assert_bounds(period_start, period_end)
        table, statement = self._movement_query(kind, start=start, end=end, limit=limit)
        return [_movement_row(row) for row in self._read(statement, source=table)]

    def _movement_query(
        self, kind: FlowKind, *, start: str, end: str, limit: int
    ) -> tuple[str, str]:
        """The table read and the statement to run, for one kind of movement."""
        settings = self._settings
        if self._from_mirror:
            # Le miroir est déjà agrégé à la maille article × jour : il ne reste
            # qu'à retailler sur la période. Même forme de résultat que les trois
            # requêtes du catalogue, donc même traduction en aval.
            return MIRROR_MOVEMENTS_TABLE, f"""
                SELECT item_id AS item_number, SUM(qty) AS qty
                FROM {MIRROR_MOVEMENTS_TABLE}
                WHERE kind = {_literal(str(kind))}
                  AND mouvement_date >= DATE '{start}'
                  AND mouvement_date <  DATE '{end}'
                GROUP BY item_id
                ORDER BY 1
                LIMIT {int(limit)}
            """

        entity = _literal(settings.erp_legal_entity)
        alive = "(t.`IsDelete` = false OR t.`IsDelete` IS NULL)"

        if kind is FlowKind.SCRAP:
            table = settings.erp_movements_fqn
            dims = settings.erp_dimensions_fqn
            return table, f"""
                SELECT t.`itemid` AS item_number, SUM(t.`qty`) AS qty
                FROM {table} t
                INNER JOIN {dims} d ON t.`inventdimid` = d.`inventdimid`
                WHERE t.`datephysical` >= DATE '{start}'
                  AND t.`datephysical` <  DATE '{end}'
                  AND {alive}
                  AND t.`dataareaid` = {entity}
                  AND (d.`IsDelete` = false OR d.`IsDelete` IS NULL)
                  AND UPPER(d.`inventlocationid`) = {_literal(
                      settings.erp_scrap_warehouse.upper())}
                  AND UPPER(d.`wmslocationid`) = {_literal(
                      settings.erp_scrap_location.upper())}
                GROUP BY t.`itemid`
                ORDER BY 1
                LIMIT {int(limit)}
            """

        table = (
            settings.erp_receipts_fqn
            if kind is FlowKind.RECEIPT
            else settings.erp_shipments_fqn
        )
        return table, f"""
            SELECT t.`itemid` AS item_number, SUM(t.`qty`) AS qty
            FROM {table} t
            WHERE t.`deliverydate` >= DATE '{start}'
              AND t.`deliverydate` <  DATE '{end}'
              AND {alive}
              AND t.`dataareaid` = {entity}
            GROUP BY t.`itemid`
            ORDER BY 1
            LIMIT {int(limit)}
        """

    def movements_source(self, kind: FlowKind) -> str:
        """Which table a movement was read from, mirror or catalogue.

        Reported with every read: « zéro ligne » means a period without receipts
        in the catalogue and, in the mirror, usually a synchronisation job that
        has not run yet. The name is also what lets somebody re-run the same
        query by hand.
        """
        if self._from_mirror:
            return MIRROR_MOVEMENTS_TABLE
        settings = self._settings
        return {
            FlowKind.RECEIPT: settings.erp_receipts_fqn,
            FlowKind.SHIPMENT: settings.erp_shipments_fqn,
            FlowKind.SCRAP: settings.erp_movements_fqn,
        }[kind]

    def backflush_loaded_at(
        self, *, period_start: dt.date, period_end: dt.date
    ) -> dt.datetime | None:
        """Freshness of the fact table over the period, for the audit trail."""
        start, end = _assert_bounds(period_start, period_end)
        table = self._table()
        rows = self._read(
            f"SELECT MAX(loaded_at) AS loaded_at FROM {table} "
            f"WHERE semaine_debut >= DATE '{start}' AND semaine_debut < DATE '{end}'",
            source=table,
        )
        return _timestamp(rows[0][0]) if rows and rows[0] else None

    @property
    def backflush_source(self) -> str:
        """Which table the backflush is read from, mirror or catalogue.

        Reported with every read: « zéro ligne » is a different problem
        depending on whether it came from Unity Catalog or from a local mirror
        the synchronisation job has not filled yet, and the name is what lets
        somebody run the same query by hand.
        """
        return self._table()

    def _table(self) -> str:
        """Where the fact table is read from, mirror or catalogue."""
        return (
            MIRROR_BACKFLUSH_TABLE
            if self._from_mirror
            else self._settings.erp_backflush_fqn
        )

    def _read(self, statement: str, *, source: str) -> list[Sequence[Any]]:
        """One statement, against whichever transport this reader is bound to.

        The two dialects agree on everything these statements use — ``GREATEST``,
        ``FULL OUTER JOIN``, ``DATE 'yyyy-mm-dd'``, ordinal ``ORDER BY`` — so the
        SQL is written once. It is the aggregation that makes this affordable on
        both: whatever the grain of the source, what comes back is a few thousand
        rows.
        """
        if not self._from_mirror:
            return self._query(statement, source=source)
        return _mirror_statement(statement, source=source)

    # ---------------------------------------------------------------- transport

    def _query(self, statement: str, *, source: str) -> list[Sequence[Any]]:
        """Run one statement and return its rows, chunks included."""
        if not self._warehouse_id:
            raise ValidationError(
                "Aucun entrepôt SQL n'est attaché à l'application : la lecture "
                "ERP est indisponible. Chargez un fichier, ou attachez une "
                "ressource « sql-warehouse »."
            )

        client = self._client or _workspace_client()
        try:
            response = client.statement_execution.execute_statement(
                warehouse_id=self._warehouse_id,
                statement=statement,
                wait_timeout=_STATEMENT_TIMEOUT,
                on_wait_timeout=_OnWaitTimeout.CANCEL,
            )
        except Exception as exc:
            log.error("Lecture ERP impossible (%s) : %s", source, exc)
            raise UpstreamError(
                f"Lecture de « {source} » impossible : {exc}", cause=str(exc)
            ) from exc

        _assert_succeeded(response, source)
        return list(_rows_of(response, client))


def _mirror_rows(
    source: str,
    columns: Sequence[str],
    *,
    order_by: str,
    limit: int,
    where: str = "",
) -> list[Sequence[Any]]:
    """Rows of the local mirror, in the same shape a warehouse read returns.

    Returning tuples rather than the dictionaries psycopg hands back is what
    lets both transports share one translation: the mirror is a copy of the ERP,
    not a second vocabulary.
    """
    from ..db.engine import get_database

    names = [c.split(" AS ")[-1].split(".")[-1] for c in columns]
    try:
        with get_database().cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(columns)} FROM {source} {where} "
                f"ORDER BY {order_by} LIMIT %s",
                (int(limit),),
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.error("Lecture du miroir ERP impossible (%s) : %s", source, exc)
        raise UpstreamError(
            "Lecture du miroir ERP impossible. Le job de synchronisation "
            f"a-t-il déjà tourné ? ({exc})",
            cause=str(exc),
        ) from exc

    if not rows:
        raise ValidationError(
            "Le miroir ERP est vide : le job « Synchronisation du miroir ERP » "
            "n'a pas encore alimenté la copie locale. Lancez-le, ou chargez un "
            "fichier."
        )
    return [[row[name] for name in names] for row in rows]


def _mirror_statement(statement: str, *, source: str) -> list[Sequence[Any]]:
    """Run a read-only statement against the local mirror, returning tuples.

    Tuples rather than the dictionaries psycopg hands back, so the row
    translation below is shared by both transports — the mirror is a copy of the
    ERP, not a second vocabulary.
    """
    from ..db.engine import get_database

    try:
        with get_database().cursor() as cur:
            cur.execute(statement)
            rows = cur.fetchall()
    except Exception as exc:
        log.error("Lecture du miroir impossible (%s) : %s", source, exc)
        # La table est nommée : le miroir en porte plusieurs, alimentées par le
        # même job mais pas par la même cellule, et « le miroir est vide » sans
        # dire lequel envoie chercher au mauvais endroit.
        raise UpstreamError(
            f"Lecture du miroir « {source} » impossible. Le job "
            f"« Synchronisation du miroir ERP » a-t-il déjà tourné ? ({exc})",
            cause=str(exc),
        ) from exc
    # psycopg's dict rows preserve the SELECT order, which is the contract the
    # translators below rely on.
    return [tuple(row.values()) for row in rows]


def validate_period(start: dt.date, end: dt.date) -> None:
    """What makes a period readable against the fact table.

    Mondays are required rather than snapped silently: the grain is the ISO
    week, and a period quietly widened by four days would produce a figure whose
    header says one thing and whose value means another.

    Exported, and called on *every* input mode rather than only on the ERP read.
    The first version validated inside the query builder, so a file or a paste
    carrying a Wednesday sailed through and was stored under bounds the source
    could never have produced.
    """
    for label, value in (("de début", start), ("de fin", end)):
        if not isinstance(value, dt.date) or isinstance(value, dt.datetime):
            raise ValidationError(
                f"La borne {label} doit être une date (lundi ISO)."
            )
        if value.weekday() != 0:
            raise ValidationError(
                f"La borne {label} ({value:%d/%m/%Y}) n'est pas un lundi. "
                "L'écart backflush est calculé à la semaine ISO : une borne en "
                "milieu de semaine compterait une production entière contre "
                "quelques jours.",
                borne=value.isoformat(),
            )
    if end <= start:
        raise ValidationError(
            "La borne de fin doit être postérieure à la borne de début "
            f"({start:%d/%m/%Y} → {end:%d/%m/%Y}). La fin est exclue : pour une "
            "seule semaine, prenez le lundi suivant.",
            debut=start.isoformat(),
            fin=end.isoformat(),
        )


def _assert_bounds(start: dt.date, end: dt.date) -> tuple[str, str]:
    """The validated period, rendered as two literals safe to interpolate.

    The bounds reach SQL as text, so this is the gate against injection — hence
    the type check in :func:`validate_period` rather than a duck-typed
    ``str()``. A ``datetime.date`` cannot carry a quote, and nothing else gets
    through.
    """
    validate_period(start, end)
    return start.isoformat(), end.isoformat()


def _backflush_row(
    row: Sequence[Any], *, start: dt.date, end: dt.date
) -> dict[str, Any]:
    (item, name, unit, net, under, over, theoretical, actual,
     parents, weeks, loaded_at) = _pad(row, 11)
    return {
        "item_number": _text(item),
        "name": _text(name),
        "unit": _text(unit) or "PCE",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "net_qty": _number(net),
        "under_consumed_qty": _number(under),
        "over_consumed_qty": _number(over),
        "theoretical_qty": _number(theoretical),
        "actual_qty": _number(actual),
        "parent_count": int(_number(parents)),
        "week_count": int(_number(weeks)),
        "source_loaded_at": (
            stamp.isoformat() if (stamp := _timestamp(loaded_at)) else ""
        ),
    }


def _stock_flow_row(row: Sequence[Any]) -> dict[str, Any]:
    item, produced, consumed = _pad(row, 3)
    return {
        "item_number": _text(item),
        "produced_qty": _number(produced),
        "consumed_qty": _number(consumed),
    }


def _movement_row(row: Sequence[Any]) -> dict[str, Any]:
    item, qty = _pad(row, 2)
    return {"item_number": _text(item), "qty": _number(qty)}


def _literal(value: str) -> str:
    """One string, quoted for SQL, or refused.

    These values come from configuration rather than from a request, but they
    are interpolated into a statement all the same. A quote in an entity code
    would be a deployment mistake, not an attack — and it would still produce a
    broken query whose error message named the wrong thing.
    """
    text = str(value).strip()
    if "'" in text or "\\" in text or "\n" in text:
        raise ValidationError(
            f"Valeur de configuration ERP invalide : {value!r}.", value=text
        )
    return f"'{text}'"


def _timestamp(value: Any) -> dt.datetime | None:
    """A timestamp from either transport: psycopg gives a datetime, the API text."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        log.warning("Horodatage de source illisible : %r", value)
        return None


def _workspace_client() -> Any:
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:  # pragma: no cover — the SDK is a hard dependency
        raise UpstreamError("SDK Databricks indisponible.", cause=str(exc)) from exc
    return WorkspaceClient()


def _assert_succeeded(response: Any, source: str) -> None:
    """Turn a failed statement into the reason, not into an empty result."""
    status = getattr(response, "status", None)
    # ``state`` is an SDK enum whose ``str()`` is "StatementState.SUCCEEDED";
    # matching on the suffix reads both that and a plain string.
    state = str(getattr(status, "state", "") or "")
    if state.endswith("SUCCEEDED"):
        return
    if state.endswith("CANCELED"):
        raise ValidationError(
            f"La lecture de « {source} » a dépassé {_STATEMENT_TIMEOUT} et a été "
            "annulée. Réessayez, ou chargez un fichier si l'entrepôt est froid."
        )
    error = getattr(status, "error", None)
    message = getattr(error, "message", None) or state or "raison inconnue"
    if "TABLE_OR_VIEW_NOT_FOUND" in message or "not found" in message.lower():
        raise ValidationError(
            f"La table « {source} » est introuvable ou n'est pas lisible avec "
            "les droits de l'application. Vérifiez son nom et les autorisations "
            "Unity Catalog."
        )
    if _is_permission_refusal(message):
        raise ValidationError(_missing_grant_message(message, source))
    raise UpstreamError(f"Lecture de « {source} » refusée : {message}")


#: What Unity Catalog says when a privilege is missing, in either of its wordings.
_PERMISSION_MARKERS = ("INSUFFICIENT_PERMISSIONS", "PERMISSION_DENIED")

#: « User does not have USE CATALOG on Catalog 'emotors_data_champions' ».
_MISSING_PRIVILEGE = re.compile(
    r"does not have (?P<privilege>[A-Z][A-Z_ ]*[A-Z]) on "
    r"(?P<kind>Catalog|Schema|Table|View)\s*'(?P<name>[^']+)'",
    re.IGNORECASE,
)


def _is_permission_refusal(message: str) -> bool:
    return any(marker in message.upper() for marker in _PERMISSION_MARKERS)


def _missing_grant_message(message: str, source: str) -> str:
    """A refusal an administrator can act on, rather than a SQLSTATE.

    Unity Catalog names the privilege and the object it is missing on; repeating
    that back as the ``GRANT`` to run turns a dead end into a one-line fix. It
    matters more here than elsewhere because the grant is on the *application's*
    service principal, not on the person reading the screen — the usual reflex,
    "but I can query that table myself", is true and beside the point.
    """
    settings = get_settings()
    principal = settings.service_principal_id or "<service principal de l'App>"

    found = _MISSING_PRIVILEGE.search(message)
    if found:
        privilege = found.group("privilege").upper()
        kind = found.group("kind").upper()
        name = found.group("name")
        grant = f"GRANT {privilege} ON {kind} {name} TO `{principal}`;"
    else:
        catalog, schema = source.split(".")[0], ".".join(source.split(".")[:2])
        grant = (
            f"GRANT USE CATALOG ON CATALOG {catalog} TO `{principal}`; "
            f"GRANT USE SCHEMA ON SCHEMA {schema} TO `{principal}`; "
            f"GRANT SELECT ON TABLE {source} TO `{principal}`;"
        )

    return (
        f"L'application n'a pas les droits Unity Catalog pour lire "
        f"« {source} ». Ce sont les droits du service principal de "
        f"l'application qui comptent, pas les vôtres. À faire exécuter par un "
        f"administrateur du catalogue : {grant} — en attendant, chargez un "
        f"fichier."
    )


def _rows_of(response: Any, client: Any) -> Iterator[Sequence[Any]]:
    """Every row of a statement result, following the chunk chain.

    A referential is tens of thousands of rows and the API paginates above a few
    MB. Reading only the first chunk would silently truncate the referential,
    which is exactly the class of error this whole application exists to remove.
    """
    result = getattr(response, "result", None)
    statement_id = getattr(response, "statement_id", None)
    while result is not None:
        yield from (getattr(result, "data_array", None) or [])
        next_index = getattr(result, "next_chunk_index", None)
        if next_index is None or statement_id is None:
            return
        result = client.statement_execution.get_statement_result_chunk_n(
            statement_id=statement_id, chunk_index=next_index
        )


# --------------------------------------------------------------------------- #
# ERP vocabulary → campaign vocabulary
# --------------------------------------------------------------------------- #

def _item_row(row: Sequence[Any]) -> dict[str, Any]:
    (item_id, item_name, item_description, search_name, name_alias, categorie,
     programme, group_id, group_label, cost, price_unit, unit) = _pad(row, 12)

    group = _text(group_id).upper()
    return {
        "item_number": _text(item_id),
        # The ERP spreads the human-readable label over three columns filled to
        # varying degrees. Taking the first non-empty one beats shipping a blank
        # designation onto a counting sheet.
        "name": _text(item_name) or _text(name_alias) or _text(item_description),
        "search_name": _text(search_name),
        "item_group": _text(group_label) or group,
        "lifecycle_state": "",
        "item_type": (
            "UNKNOWN" if group in _NON_STOCK_GROUPS
            else ERP_ITEM_TYPES.get(group, "UNKNOWN")
        ),
        "category": _text(categorie),
        "program": _text(programme),
        "commonality": _commonality(programme),
        "unit": _text(unit) or "PCE",
        "std_price": _unit_price(cost, price_unit),
        # Nothing in the ERP expresses the campaign's exclusion scopes; they are
        # a decision made here, campaign by campaign, and stay editable.
        "exclusions": "",
    }


def _bom_row(row: Sequence[Any]) -> dict[str, Any]:
    parent, parent_name, child, qty, unit, statut = _pad(row, 6)
    return {
        "parent_item": _text(parent),
        "parent_name": _text(parent_name),
        "child_item": _text(child),
        "qty_per": _number(qty),
        "unit": _text(unit) or "PCE",
        # Passed through as the ERP spells it; the mapper decides what counts as
        # "in force", once, for every input mode.
        "statut": _text(statut) or "Actif",
    }


def _commonality(programme: Any) -> str:
    """``Commun`` in the ERP is the campaign's COMMON; a named programme is SPECIFIC."""
    value = _text(programme).upper()
    if not value:
        return "UNKNOWN"
    return "COMMON" if value == _COMMON_PROGRAMME else "SPECIFIC"


def _unit_price(cost: Any, price_unit: Any) -> float:
    """The cost of **one** unit.

    ``std_cost_price`` is quoted for ``std_price_unit`` units — usually one, but
    not always. Ignoring the divisor would value a whole campaign at a hundred
    times its worth on the articles priced per hundred, and the error would only
    surface at the variance stage.
    """
    price = _number(cost)
    per = _number(price_unit)
    if not price:
        return 0.0
    return price / per if per else price


def _pad(row: Sequence[Any], size: int) -> list[Any]:
    """Tolerate a shorter row than expected rather than raising an IndexError."""
    values = list(row)
    return values + [None] * (size - len(values))


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
