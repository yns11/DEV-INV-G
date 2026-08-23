"""Transformer une entrée en lignes, quelle que soit son origine.

Un tableur déposé, un bloc collé depuis Excel, une lecture des tables Unity
Catalog : trois origines, un seul point d'entrée. C'est délibéré — une lecture
ERP ne doit pas être un second chemin, moins contrôlé, vers le référentiel.
Elle rejoint le pipeline exactement où une feuille de calcul le rejoint : même
validation, même essai à blanc, même grille éditable ensuite.

Rien n'est écrit ici. C'est ce qui sépare ce module des six importeurs : ceux-ci
décident quoi faire des lignes, celui-là se contente de les produire et de dire
lesquelles il refuse.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any, Literal

from ..errors import ValidationError
from ..ingest import (
    GridContract,
    ParseResult,
    get_contract,
    parse_clipboard,
    parse_rows,
    parse_tabular_bytes,
)
from ..ingest.erp import validate_period
from .context import ServiceContext
from .import_batches import (
    ImportBatches,
    ImportOutcome,
    _jsonable,
)

#: Les quatre origines d'une entrée. Le mode dit d'où viennent les lignes, et
#: seulement cela : ce qu'on en fait ensuite est identique pour les quatre.
InputMode = Literal["file", "paste", "rows", "erp"]

__all__ = ["ImportParser", "InputMode", "_base_outcome", "_require_period"]


class ImportParser:
    """La lecture d'une entrée, sans écriture."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx
        #: La provenance et l'idempotence, tenues à côté plutôt que dedans :
        #: elles accompagnent les six importeurs sans appartenir à aucun.
        self.batches = ImportBatches(ctx)

    def parse(
        self,
        contract_key: str,
        *,
        mode: InputMode,
        payload: bytes | None = None,
        filename: str = "",
        sheet: str | None = None,
        text: str | None = None,
        rows: Sequence[dict[str, Any]] | None = None,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
        snapshot_date: dt.date | None = None,
    ) -> tuple[GridContract, ParseResult]:
        """Parse input in any of the supported modes.

        ``erp`` reads the source tables in Unity Catalog and produces rows in the
        grid's own shape, so it joins the pipeline at exactly the same point as a
        spreadsheet: same validation, same dry run, same mappers, same audit,
        same editable grid afterwards. That is deliberate — an ERP load must not
        be a second, less-checked path into the referential.
        """
        contract = get_contract(contract_key)
        limit = self.ctx.settings.max_import_rows

        match mode:
            case "erp":
                result = parse_rows(
                    contract,
                    self._read_erp(
                        contract_key, limit=limit,
                        period_start=period_start, period_end=period_end,
                        snapshot_date=snapshot_date,
                    ),
                    max_rows=limit,
                )
            case "file":
                if payload is None:
                    raise ValidationError("Aucun fichier reçu.")
                if len(payload) > self.ctx.settings.max_upload_bytes:
                    raise ValidationError(
                        "Fichier trop volumineux "
                        f"({len(payload) / 1e6:.1f} Mo, maximum "
                        f"{self.ctx.settings.max_upload_bytes / 1e6:.0f} Mo)."
                    )
                result = parse_tabular_bytes(
                    contract, payload, filename=filename, sheet=sheet, max_rows=limit
                )
            case "paste":
                if not text:
                    raise ValidationError("Le presse-papiers est vide.")
                result = parse_clipboard(contract, text, max_rows=limit)
            case "rows":
                result = parse_rows(contract, rows or [], max_rows=limit)
            case _:
                raise ValidationError(f"Mode d'import inconnu : {mode!r}")

        return contract, result

    def _read_erp(
        self,
        contract_key: str,
        *,
        limit: int,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
        snapshot_date: dt.date | None = None,
    ) -> list[dict[str, Any]]:
        """Rows from the ERP tables, in the grid's shape.

        The two period arguments are only meaningful for the grids that read a
        *fact* table rather than a referential: a referential has a state, a fact
        table has a history, and reading the second without bounds would answer
        a question nobody asked.

        ``snapshot_date`` est de la troisième espèce : le stock n'est ni un état
        courant ni un historique à parcourir, mais une suite de photos dont on en
        charge **une**, nommée.
        """
        from ..ingest.erp import ErpReader

        reader = ErpReader()
        match contract_key:
            case "items":
                return reader.fetch_items(limit=limit)
            case "boms":
                return reader.fetch_bom_links(limit=limit)
            case "book_stock":
                # Une photo, celle que l'écran a nommée. Sans date, la plus
                # récente — c'est le défaut, pas la seule possibilité.
                return reader.fetch_book_stock(
                    limit=limit, snapshot_date=snapshot_date
                )
            case "backflush":
                start, end = _require_period(period_start, period_end)
                return reader.fetch_backflush(
                    period_start=start, period_end=end, limit=limit
                )
            case _:
                raise ValidationError(
                    f"La grille « {contract_key} » n'a pas de source ERP. "
                    "Seuls les articles, les nomenclatures et l'écart backflush "
                    "en ont une."
                )

    def preview(
        self, contract_key: str, *, limit: int = 50, **kwargs: Any
    ) -> dict[str, Any]:
        """Dry-run an import: validate everything, persist nothing.

        The user always sees what will happen before it happens — the single
        biggest behavioural difference from pasting into a spreadsheet.

        The payload is built by :meth:`ImportOutcome.as_dict`, exactly like the
        commit path. Serialising a dry run through a second, similar-looking
        function is how the two shapes silently diverged once already: the
        preview omitted ``warnings``, and the client — which cannot tell the two
        responses apart — crashed on the missing key.
        """
        _, result = self.parse(contract_key, **kwargs)
        outcome = _base_outcome(contract_key, result)
        outcome.rows_accepted = len(result.rows)
        return {
            **outcome.as_dict(),
            "sample": [_jsonable(r) for r in result.rows[:limit]],
        }


def _require_period(
    start: dt.date | None, end: dt.date | None
) -> tuple[dt.date, dt.date]:
    """The two bounds, refused rather than guessed when they are missing.

    A default period would be the worst of both worlds here: the figure would
    look computed, the header would show bounds nobody chose, and the number
    would be wrong for every campaign whose period is not the default. The
    screen proposes a period; it is the user who fixes it.
    """
    if start is None or end is None:
        raise ValidationError(
            "L'écart backflush se lit sur une période : indiquez la borne de "
            "début et la borne de fin (des lundis ISO, fin exclue).",
            borneDebut=start.isoformat() if start else None,
            borneFin=end.isoformat() if end else None,
        )
    # Validated here rather than in the query builder, so a file or a paste is
    # held to the same rule as an ERP read. It was not, and a Wednesday typed
    # into the form was stored as a bound the source could never have produced.
    validate_period(start, end)
    return start, end


def _base_outcome(target: str, parsed: ParseResult) -> ImportOutcome:
    return ImportOutcome(
        target=target,
        rows_received=parsed.rows_received,
        rows_rejected=len(parsed.errors),
        errors=list(parsed.errors),
        missing_columns=list(parsed.missing_columns),
        unknown_columns=list(parsed.unknown_columns),
        duplicate_keys=list(parsed.duplicate_keys),
    )
