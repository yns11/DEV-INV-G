"""Generic import pipeline shared by every grid.

One code path handles file uploads, clipboard pastes and typed rows, so the
validation rules, the duplicate detection and the audit trail cannot diverge
between them.

Every import produces an :class:`ImportOutcome` that the UI renders as a
before/after summary: how many rows arrived, how many were accepted, exactly
which ones were rejected and why. Nothing is ever loaded blind.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ..db import new_id
from ..domain.enums import (
    AuditAction,
    DataSource,
    JournalKind,
    JournalStatus,
    LocationStatus,
    LocationType,
)
from ..domain.models import (
    Campaign,
    CountJournalLine,
    LocationKey,
    Warehouse,
)
from ..errors import ConflictError, ValidationError
from ..ingest import (
    GridContract,
    ParseResult,
    RowError,
    get_contract,
    map_adjustments,
    map_bom_links,
    map_book_stock,
    map_items,
    map_journal_lines,
    map_locations,
    parse_clipboard,
    parse_rows,
    parse_tabular_bytes,
)
from .context import ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["ImportOutcome", "ImportService", "InputMode"]

InputMode = Literal["file", "paste", "rows"]


@dataclass(slots=True)
class ImportOutcome:
    """Result of one import, shaped for direct display."""

    target: str
    rows_received: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    errors: list[RowError] = field(default_factory=list)
    warnings: list[RowError] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    batch_id: str | None = None
    #: Free-form facts the specific import wants to surface (journals created,
    #: locations discovered, …).
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.rows_rejected == 0 and not self.missing_columns

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "rowsReceived": self.rows_received,
            "rowsAccepted": self.rows_accepted,
            "rowsRejected": self.rows_rejected,
            "ok": self.ok,
            "errors": [e.as_dict() for e in self.errors[:200]],
            "warnings": [w.as_dict() for w in self.warnings[:200]],
            "truncatedErrors": max(0, len(self.errors) - 200),
            "missingColumns": self.missing_columns,
            "unknownColumns": self.unknown_columns,
            "duplicateKeys": self.duplicate_keys[:50],
            "batchId": self.batch_id,
            "details": self.details,
        }


class ImportService:
    """Parses, validates and persists bulk data for every grid."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ---------------------------------------------------------------- parsing

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
    ) -> tuple[GridContract, ParseResult]:
        """Parse input in any of the three supported modes."""
        contract = get_contract(contract_key)
        limit = self.ctx.settings.max_import_rows

        match mode:
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

    # -------------------------------------------------------------- importers

    def import_items(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "items")
        _, parsed = self.parse("items", **kwargs)
        outcome = _base_outcome("items", parsed)
        if not parsed.rows:
            return outcome

        source = _source_of(kwargs.get("mode", "file"))
        items, errors = map_items(campaign.id, parsed.rows, source=source)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(items)
        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_items(items, actor=ctx.actor, conn=conn)
            outcome.batch_id = self._record_batch(
                campaign.id, "items", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="item",
                summary=f"Import de {len(items)} article(s)",
                after={"accepted": len(items), "rejected": outcome.rows_rejected},
                conn=conn,
            )
        return outcome

    def import_boms(
        self, campaign: Campaign, *, replace: bool = False, **kwargs: Any
    ) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "boms")
        _, parsed = self.parse("boms", **kwargs)
        outcome = _base_outcome("boms", parsed)
        if not parsed.rows:
            return outcome

        links, errors = map_bom_links(campaign.id, parsed.rows)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(links)
        with ctx.db.transaction() as conn:
            if replace:
                removed = ctx.referentials.clear_bom(campaign.id, actor=ctx.actor)
                outcome.details["replacedLinks"] = removed
            ctx.referentials.upsert_bom_links(links, actor=ctx.actor, conn=conn)
            outcome.batch_id = self._record_batch(
                campaign.id, "boms", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="bom_link",
                summary=f"Import de {len(links)} lien(s) de nomenclature",
                after={"accepted": len(links), "replaced": replace},
                conn=conn,
            )

        # A cycle makes every explosion undefined, so it is reported immediately
        # rather than at consolidation time on the day of the inventory.
        from ..domain.bom import BomIndex

        cycles = BomIndex(ctx.referentials.list_bom_links(campaign.id)).find_cycles()
        if cycles:
            outcome.details["bomCycles"] = [" → ".join(c) for c in cycles[:10]]
            outcome.warnings.append(
                RowError(0, "parent_item", None,
                         f"{len(cycles)} cycle(s) détecté(s) dans la nomenclature. "
                         "L'éclatement du WIP sera bloqué tant qu'ils subsistent.")
            )
        return outcome

    def import_adjustments(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "adjustments")
        _, parsed = self.parse("adjustments", **kwargs)
        outcome = _base_outcome("adjustments", parsed)
        if not parsed.rows:
            return outcome

        lines, errors = map_adjustments(
            campaign.id, parsed.rows,
            source=_source_of(kwargs.get("mode", "file")), id_factory=new_id,
        )
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(lines)
        with ctx.db.transaction() as conn:
            ctx.adjustments.upsert(lines, actor=ctx.actor, conn=conn)
            outcome.batch_id = self._record_batch(
                campaign.id, "adjustments", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="adjustment_line",
                summary=f"Import de {len(lines)} mouvement(s) de stock",
                after={"accepted": len(lines)},
                conn=conn,
            )
        return outcome

    def import_locations(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "locations")
        _, parsed = self.parse("locations", **kwargs)
        outcome = _base_outcome("locations", parsed)
        if not parsed.rows:
            return outcome

        locations, errors = map_locations(campaign.id, parsed.rows)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        warehouses = {
            l.warehouse_id: Warehouse(
                campaign_id=campaign.id, warehouse_id=l.warehouse_id, type=l.type
            )
            for l in locations
        }
        outcome.rows_accepted = len(locations)
        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_warehouses(
                warehouses.values(), actor=ctx.actor, conn=conn
            )
            ctx.referentials.upsert_locations(locations, actor=ctx.actor, conn=conn)
            outcome.batch_id = self._record_batch(
                campaign.id, "locations", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="location",
                summary=f"Import de {len(locations)} emplacement(s)",
                conn=conn,
            )
        return outcome

    # ------------------------------------------------------------ book stock

    def import_book_stock(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        """Load the ERP snapshot and derive the location referential from it.

        Three things happen in one transaction, because they only make sense
        together:

        1. the snapshot replaces any previous one (a photograph is not merged);
        2. every warehouse/location pair it contains is added to the referential,
           **preserving** the ACTIVE/DISABLED decisions already made;
        3. one PENDING counting journal is created per active location.
        """
        ctx = self.ctx
        ctx.guard(campaign, "book_stock")
        if campaign.book_stock_frozen_at is not None:
            raise ConflictError(
                "Le stock livre est gelé pour cette campagne. Créez une nouvelle "
                "campagne si un nouveau snapshot est nécessaire.",
                frozenAt=campaign.book_stock_frozen_at.isoformat(),
            )

        _, parsed = self.parse("book_stock", **kwargs)
        outcome = _base_outcome("book_stock", parsed)
        if not parsed.rows:
            return outcome

        items = ctx.referentials.items_by_number(campaign.id)
        lines, errors = map_book_stock(campaign.id, parsed.rows, items=items)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)
        if not lines:
            return outcome

        existing = ctx.referentials.locations_by_key(campaign.id)
        generic_key = campaign.config.generic_key
        discovered: dict[LocationKey, Any] = {}
        warehouses: dict[str, Warehouse] = {}

        from ..domain.models import Location

        for line in lines:
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in existing or key in discovered:
                continue
            # A warehouse whose name contains VRAC, or the GENERIQUE location
            # itself, is a bulk location: it is counted by manual entry (INVV)
            # rather than by scanning labels (INVE).
            is_bulk = "VRAC" in key.warehouse_id or key == generic_key
            discovered[key] = Location(
                campaign_id=campaign.id,
                warehouse_id=key.warehouse_id,
                location_id=key.location_id,
                type=LocationType.BULK if is_bulk else LocationType.LABEL,
                status=LocationStatus.ACTIVE,
                source=DataSource.SYSTEM,
            )
            warehouses.setdefault(
                key.warehouse_id,
                Warehouse(
                    campaign_id=campaign.id,
                    warehouse_id=key.warehouse_id,
                    type=LocationType.BULK if is_bulk else LocationType.LABEL,
                ),
            )

        batch_id = new_id()
        with ctx.db.transaction() as conn:
            ctx.book_stock.replace(campaign.id, lines, batch_id=batch_id, conn=conn)
            if warehouses:
                ctx.referentials.upsert_warehouses(
                    warehouses.values(), actor=ctx.actor, conn=conn
                )
            if discovered:
                ctx.referentials.upsert_locations(
                    discovered.values(), actor=ctx.actor, conn=conn
                )
            active_keys = [
                key
                for key, location in {
                    **existing, **discovered
                }.items()
                if location.status is LocationStatus.ACTIVE
            ]
            created = ctx.journals.ensure_journals(
                campaign.id,
                active_keys,
                kinds={
                    k: (JournalKind.INVV
                        if ({**existing, **discovered})[k].type is LocationType.BULK
                        else JournalKind.INVE)
                    for k in active_keys
                },
                actor=ctx.actor,
                conn=conn,
            )
            ctx.imports.create(
                campaign_id=campaign.id,
                target="book_stock",
                filename=kwargs.get("filename", ""),
                content_hash=_hash_of(kwargs),
                storage_path=None,
                rows_received=outcome.rows_received,
                rows_accepted=len(lines),
                rows_rejected=outcome.rows_rejected,
                report=outcome.as_dict(),
                imported_by=ctx.actor,
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="book_stock",
                summary=(
                    f"Stock livre chargé : {len(lines)} lignes, "
                    f"{len(discovered)} nouvel(s) emplacement(s), "
                    f"{created} journal(aux) créé(s)."
                ),
                after={
                    "lines": len(lines),
                    "newLocations": len(discovered),
                    "journalsCreated": created,
                },
                conn=conn,
            )

        outcome.batch_id = batch_id
        outcome.rows_accepted = len(lines)
        outcome.details = {
            "newLocations": len(discovered),
            "totalLocations": len(existing) + len(discovered),
            "journalsCreated": created,
            "warehouses": sorted(
                {l.warehouse_id for l in lines}
            ),
        }
        return outcome

    def freeze_book_stock(self, campaign: Campaign) -> Campaign:
        """Lock the snapshot. From here on, variances are reproducible."""
        ctx = self.ctx
        ctx.guard(campaign, "book_stock")
        if ctx.book_stock.count(campaign.id) == 0:
            raise ValidationError(
                "Impossible de geler un stock livre vide : chargez d'abord le "
                "snapshot ERP."
            )
        with ctx.db.transaction() as conn:
            ctx.campaigns.update_status(
                campaign.id,
                campaign.status,
                actor=ctx.actor,
                timestamps={"book_stock_frozen_at": utcnow()},
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.FREEZE,
                entity_type="book_stock",
                summary="Stock livre gelé",
                conn=conn,
            )
        return ctx.campaigns.get(campaign.id)

    # -------------------------------------------------------- count journals

    def import_journal_lines(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        """Load or refresh the ERP counting-journal lines.

        Behaviour required by the specification:

        * reloading replaces the imported values but never a manual correction;
        * a journal present in the file but absent from the referential is
          created — unless its location is disabled, in which case the lines are
          rejected with an explicit message rather than silently dropped;
        * a journal whose lines are all flagged posted becomes ``POSTED``.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        _, parsed = self.parse("count_journal_lines", **kwargs)
        outcome = _base_outcome("count_journal_lines", parsed)
        if not parsed.rows:
            return outcome

        imported, errors, warnings = map_journal_lines(parsed.rows)
        outcome.errors.extend(errors)
        outcome.warnings.extend(warnings)
        outcome.rows_rejected += len(errors)
        if not imported:
            return outcome

        locations = ctx.referentials.locations_by_key(campaign.id)
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}

        keys_in_file = {
            LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
            for l in imported
        }
        disabled = {
            k for k in keys_in_file
            if k in locations and locations[k].status is LocationStatus.DISABLED
        }
        to_create = [
            k for k in keys_in_file
            if k not in journals and k not in disabled
        ]

        if disabled:
            for line_no, line in enumerate(imported, start=2):
                key = LocationKey(
                    warehouse_id=line.warehouse_id, location_id=line.location_id
                )
                if key in disabled:
                    outcome.warnings.append(
                        RowError(
                            line_no, "location_id", str(key),
                            f"L'emplacement {key} est désactivé : la ligne est "
                            "ignorée. Réactivez-le pour l'inclure.",
                        )
                    )

        with ctx.db.transaction() as conn:
            if to_create:
                kinds = {}
                for key in to_create:
                    matching = next(
                        (l for l in imported
                         if l.warehouse_id == key.warehouse_id
                         and l.location_id == key.location_id),
                        None,
                    )
                    kinds[key] = matching.kind if matching else JournalKind.INVV
                ctx.journals.ensure_journals(
                    campaign.id, to_create, kinds=kinds,
                    auto_created=True, actor=ctx.actor, conn=conn,
                )
            # Read back through the *same* connection: the journals just created
            # are not visible to another pooled connection until this
            # transaction commits, and their lines would be dropped.
            journals = {j.key: j for j in ctx.journals.list(campaign.id, conn=conn)}

            lines: list[CountJournalLine] = []
            posted_flags: dict[str, list[bool]] = {}
            journal_numbers: dict[str, str] = {}
            for line in imported:
                key = LocationKey(
                    warehouse_id=line.warehouse_id, location_id=line.location_id
                )
                if key in disabled:
                    continue
                journal = journals.get(key)
                if journal is None:  # pragma: no cover - defensive
                    continue
                lines.append(
                    CountJournalLine(
                        id=new_id(),
                        journal_id=journal.id,
                        campaign_id=campaign.id,
                        item_number=line.item_number,
                        qty_imported=line.qty,
                        unit=line.unit,
                        source=DataSource.ERP_IMPORT,
                        updated_by=ctx.actor,
                    )
                )
                posted_flags.setdefault(journal.id, []).append(line.is_posted)
                if line.journal_number:
                    journal_numbers[journal.id] = line.journal_number

            touched = sorted(posted_flags)
            ctx.journals.replace_imported_lines(
                campaign.id, touched, lines, conn=conn
            )

            fully_posted = [jid for jid, flags in posted_flags.items() if all(flags)]
            partially = [
                jid for jid, flags in posted_flags.items()
                if any(flags) and not all(flags)
            ]
            in_progress = [
                jid for jid, flags in posted_flags.items() if not any(flags)
            ]
            if fully_posted:
                ctx.journals.set_status(
                    fully_posted, JournalStatus.POSTED,
                    actor=ctx.actor, posted_at=utcnow(), conn=conn,
                )
            for group in (partially, in_progress):
                running = [
                    jid for jid in group
                    if journals and _journal_by_id(journals, jid) is not None
                    and _journal_by_id(journals, jid).status is not JournalStatus.POSTED
                ]
                if running:
                    ctx.journals.set_status(
                        running, JournalStatus.IN_PROGRESS, actor=ctx.actor, conn=conn
                    )

            outcome.rows_accepted = len(lines)
            outcome.batch_id = self._record_batch(
                campaign.id, "count_journal_lines", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="count_journal",
                summary=(
                    f"Import de {len(lines)} ligne(s) de comptage sur "
                    f"{len(touched)} journal(aux) ; {len(to_create)} journal(aux) "
                    f"créé(s) automatiquement ; {len(fully_posted)} posté(s)."
                ),
                after={
                    "lines": len(lines),
                    "journalsTouched": len(touched),
                    "journalsCreated": len(to_create),
                    "journalsPosted": len(fully_posted),
                    "disabledLocationsSkipped": sorted(str(k) for k in disabled),
                },
                conn=conn,
            )

        outcome.details = {
            "journalsTouched": len(touched),
            "journalsCreated": len(to_create),
            "journalsPosted": len(fully_posted),
            "journalsInProgress": len(partially) + len(in_progress),
            "disabledLocationsSkipped": sorted(str(k) for k in disabled),
        }
        return outcome

    # --------------------------------------------------------------- helpers

    def _record_batch(
        self,
        campaign_id: str,
        target: str,
        outcome: ImportOutcome,
        *,
        conn: Any = None,
        **kwargs: Any,
    ) -> str:
        """Persist the provenance of one import.

        Call this **after** ``outcome.rows_accepted`` is set: the batch row is
        the permanent record of what a file actually loaded, and a zero there
        would make the import history useless.
        """
        return self.ctx.imports.create(
            campaign_id=campaign_id,
            target=target,
            filename=kwargs.get("filename", ""),
            content_hash=_hash_of(kwargs),
            storage_path=None,
            rows_received=outcome.rows_received,
            rows_accepted=outcome.rows_accepted,
            rows_rejected=outcome.rows_rejected,
            report=outcome.as_dict(),
            imported_by=self.ctx.actor,
            conn=conn,
        )

    def check_duplicate(
        self, campaign_id: str, target: str, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Warn when the exact same payload was already imported."""
        digest = _hash_of(kwargs)
        if not digest:
            return None
        return self.ctx.imports.find_duplicate(campaign_id, target, digest)


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


def _source_of(mode: str) -> DataSource:
    return DataSource.MANUAL if mode in ("paste", "rows") else DataSource.FILE_IMPORT


def _hash_of(kwargs: dict[str, Any]) -> str:
    payload = kwargs.get("payload")
    if isinstance(payload, bytes):
        return hashlib.sha256(payload).hexdigest()
    text = kwargs.get("text")
    if isinstance(text, str):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ""


def _journal_by_id(journals: dict[LocationKey, Any], journal_id: str) -> Any:
    for journal in journals.values():
        if journal.id == journal_id:
            return journal
    return None


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    import datetime as _dt
    from decimal import Decimal

    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, Decimal):
            out[key] = float(value)
        elif isinstance(value, (_dt.date, _dt.datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out
