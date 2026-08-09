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
from ..errors import UpstreamError, ValidationError

log = logging.getLogger(__name__)

__all__ = [
    "ErpReader", "ERP_ITEM_TYPES", "erp_available", "unavailable_reason",
    "reading_from_mirror", "mirror_state", "ITEM_COLUMNS",
    "MIRROR_ITEMS_TABLE", "MIRROR_BOM_TABLE",
]

#: ERP functional group → the campaign's article type. Packaging never appears:
#: the silver table already excludes ``EMBLG``, as does the data dictionary.
ERP_ITEM_TYPES = {
    "COMPO": "COMPONENT",       # composant acheté ou fabriqué
    "PFINI": "FINISHED",        # produit fini
    "PSMFI": "SEMI_FINISHED",   # sous-ensemble
    "APVPR": "COMPONENT",       # appro prototype — un composant, plus tôt
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
_BOM_SELECT = (
    "b.parent_itemid", "p.item_name AS parent_name", "b.child_itemid",
    "b.child_qty", "b.child_unitid",
)

#: Mirror tables, in the application's own Lakebase schema (migration 005).
MIRROR_ITEMS_TABLE = "erp_base_article"
MIRROR_BOM_TABLE = "erp_bom"

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
    for key, table in (("items", MIRROR_ITEMS_TABLE), ("boms", MIRROR_BOM_TABLE)):
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

    def fetch_bom_links(
        self, *, limit: int, approved_only: bool = False
    ) -> list[dict[str, Any]]:
        """The bill of materials, as ``boms`` grid rows.

        The parent's designation is joined in rather than left blank: the grid
        shows it, and a second round trip to fetch names the referential already
        holds would be wasted.

        :param approved_only: keep only rows flagged approved in the ERP. Off by
            default — the silver table already restricts itself to active
            recipes, and silently dropping every row whose flag is null would
            look like an empty bill of materials rather than a filter.
        """
        where = "WHERE b.approved = 1" if approved_only else ""
        if self._from_mirror:
            return [_bom_row(r) for r in _mirror_rows(
                f"{MIRROR_BOM_TABLE} b "
                f"LEFT JOIN {MIRROR_ITEMS_TABLE} p ON b.parent_itemid = p.item_id",
                _BOM_SELECT,
                order_by="b.parent_itemid, b.child_itemid",
                limit=limit,
                where=where,
            )]
        bom, items = self._settings.erp_bom_fqn, self._settings.erp_items_fqn
        rows = self._query(
            f"""
            SELECT {", ".join(_BOM_SELECT)}
            FROM {bom} b
            LEFT JOIN {items} p ON b.parent_itemid = p.item_id
            {where}
            ORDER BY b.parent_itemid, b.child_itemid
            LIMIT {int(limit)}
            """,
            source=bom,
        )
        return [_bom_row(r) for r in rows]

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
    parent, parent_name, child, qty, unit = _pad(row, 5)
    return {
        "parent_item": _text(parent),
        "parent_name": _text(parent_name),
        "child_item": _text(child),
        "qty_per": _number(qty),
        "unit": _text(unit) or "PCE",
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
