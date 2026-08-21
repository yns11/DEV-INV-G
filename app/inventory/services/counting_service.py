"""Counting-journal operations: progress, line edits, posting, book enforcement."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.controls import check_journals
from ..domain.enums import (
    AuditAction,
    DataSource,
    JournalStatus,
    LocationStatus,
)
from ..domain.models import Campaign, CountJournalLine, LocationKey
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ServiceContext, utcnow
from .manager_service import Perimeter

log = logging.getLogger(__name__)

__all__ = ["CountingService"]


class CountingService:
    """Everything that happens to a counting journal during the counting phase."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list_journals(
        self,
        campaign_id: str,
        *,
        status: JournalStatus | None = None,
        warehouse_id: str | None = None,
        perimeter: Perimeter | None = None,
    ) -> list[dict[str, Any]]:
        """Journals enriched with their line count and counted quantity.

        The enrichment is done with two queries, not one per journal: the
        counting screen shows every journal of the site at once and must stay
        responsive on the day of the inventory.

        :param perimeter: when given, only the journals whose warehouse belongs
            to that manager are returned. The filtering happens here, before the
            response is built: doing it in the browser would still send every
            journal of the site to every workstation.
        """
        ctx = self.ctx
        journals = ctx.journals.list(campaign_id, status=status, warehouse_id=warehouse_id)
        if perimeter is not None:
            journals = [
                j for j in journals if perimeter.covers_warehouse(j.warehouse_id)
            ]
        lines_by_journal = ctx.journals.lines_by_journal(campaign_id)
        locations = ctx.referentials.locations_by_key(campaign_id)

        out: list[dict[str, Any]] = []
        for journal in journals:
            lines = lines_by_journal.get(journal.id, [])
            location = locations.get(journal.key)
            out.append({
                **journal.model_dump(mode="json"),
                "lineCount": len(lines),
                "countedQty": float(sum((l.qty for l in lines), Decimal(0))),
                "overriddenLines": sum(1 for l in lines if l.is_overridden),
                "locationType": str(location.type) if location else "UNKNOWN",
                "locationStatus": str(location.status) if location else "ACTIVE",
                "zone": location.zone if location else "",
            })
        return out

    def get_journal(self, campaign_id: str, journal_id: str) -> dict[str, Any]:
        ctx = self.ctx
        journal = ctx.journals.get(journal_id)
        if journal.campaign_id != campaign_id:
            raise NotFoundError("Journal introuvable dans cette campagne.")
        lines = ctx.journals.list_lines(journal_id)
        book = {
            (b.item_number): b
            for b in ctx.book_stock.list(campaign_id)
            if b.warehouse_id == journal.warehouse_id
            and b.location_id == journal.location_id
        }
        enriched = []
        for line in lines:
            book_line = book.get(line.item_number)
            enriched.append({
                **line.model_dump(mode="json"),
                "qty": float(line.qty),
                "effectiveSource": str(line.effective_source),
                "isOverridden": line.is_overridden,
                "bookQty": float(book_line.qty) if book_line else 0.0,
                "varianceQty": float(line.qty - (book_line.qty if book_line else 0)),
            })
        # Book-stock articles that nobody counted are the ones that will be
        # written down to zero: they belong on this screen, not in a report
        # three weeks later.
        counted_items = {l.item_number for l in lines}
        missing = [
            {
                "itemNumber": b.item_number,
                "bookQty": float(b.qty),
                "unit": b.unit,
                "value": float(b.value),
            }
            for item_number, b in book.items()
            if item_number not in counted_items and b.qty != 0
        ]
        return {
            "journal": journal.model_dump(mode="json"),
            "lines": enriched,
            "notCounted": sorted(missing, key=lambda m: -abs(m["value"])),
        }

    def progress(self, campaign_id: str) -> dict[str, Any]:
        return self.ctx.journals.progress(campaign_id)

    def controls(self, campaign: Campaign) -> list[dict[str, Any]]:
        """Run the journal control suite for the counting dashboard."""
        ctx = self.ctx
        findings = check_journals(
            journals=ctx.journals.list(campaign.id),
            lines_by_journal=ctx.journals.lines_by_journal(campaign.id),
            items=ctx.referentials.items_by_number(campaign.id),
            locations=ctx.referentials.locations_by_key(campaign.id),
        )
        return [f.model_dump(mode="json") for f in findings]

    # ------------------------------------------------------------ line edits

    def upsert_line(
        self,
        campaign: Campaign,
        journal_id: str,
        *,
        line_id: str | None,
        item_number: str,
        qty: Decimal | None,
        unit: str = "PCE",
        comment: str = "",
        expected_version: int | None = None,
    ) -> CountJournalLine:
        """Create or correct one counted line.

        A manual value is written to ``qty_manual``, leaving ``qty_imported``
        untouched. That is what lets the ERP export be reloaded any number of
        times during the day without erasing what a human decided.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        journal = ctx.journals.get(journal_id)
        if journal.campaign_id != campaign.id:
            raise NotFoundError("Journal introuvable dans cette campagne.")
        if journal.status is JournalStatus.POSTED:
            raise ConflictError(
                "Ce journal est posté : dépostez-le dans l'ERP puis rechargez "
                "l'export pour le corriger."
            )
        if qty is not None and qty < 0:
            raise ValidationError(
                "Une quantité comptée ne peut pas être négative.", qty=str(qty)
            )

        line = CountJournalLine(
            id=line_id or new_id(),
            journal_id=journal_id,
            campaign_id=campaign.id,
            item_number=item_number,
            qty_manual=qty,
            unit=unit,
            source=DataSource.MANUAL,
            comment=comment,
        )
        before = None
        if line_id:
            existing = next(
                (l for l in ctx.journals.list_lines(journal_id) if l.id == line_id), None
            )
            if existing is not None:
                before = existing.model_dump(mode="json")
                line.qty_imported = existing.qty_imported

        saved = ctx.journals.upsert_line(
            line, actor=ctx.actor, expected_version=expected_version
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE if line_id else AuditAction.CREATE,
            entity_type="count_journal_line",
            entity_id=saved.id,
            summary=(
                f"{journal.key} — {item_number} : quantité manuelle "
                f"{'effacée' if qty is None else qty}"
            ),
            before=before,
            after=saved.model_dump(mode="json"),
        )
        return saved

    def delete_line(self, campaign: Campaign, line_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        ctx.journals.delete_line(line_id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="count_journal_line",
            entity_id=line_id,
            summary="Suppression logique d'une ligne de comptage",
        )

    # --------------------------------------------------------- status changes

    def set_status(
        self, campaign: Campaign, journal_ids: Sequence[str], status: JournalStatus
    ) -> int:
        """Change the status of a batch of journals."""
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        if status in (JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED):
            # Posting is what the ERP will be adjusted by. It must not happen
            # against a snapshot that can still move: the variance it settles
            # would not be reproducible the next day.
            ctx.guard(campaign, "post_journal")
        if status is JournalStatus.BOOK_ENFORCED:
            return self.enforce_book_stock(campaign, journal_ids)

        posted_at = utcnow() if status is JournalStatus.POSTED else None
        count = ctx.journals.set_status(
            journal_ids, status, actor=ctx.actor, posted_at=posted_at
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.STATUS_CHANGE,
            entity_type="count_journal",
            summary=f"{count} journal(aux) passé(s) au statut {status}",
            after={"status": str(status), "journalIds": list(journal_ids)},
        )
        return count

    def enforce_book_stock(
        self, campaign: Campaign, journal_ids: Sequence[str]
    ) -> int:
        """Force selected journals to match the book stock exactly.

        Used for locations inventoried separately *before* the snapshot was
        taken (external warehouses, subcontractors, areas counted the previous
        week). Their counted quantity equals the book quantity by definition, so
        the variance is null by construction rather than by accident.

        The counted lines are materialised — not merely implied — so the audit
        trail shows what was posted and the analysis needs no special case.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        ctx.guard(campaign, "post_journal")
        if not journal_ids:
            return 0

        book_lines = ctx.book_stock.list(campaign.id)
        by_key: dict[LocationKey, list] = {}
        for line in book_lines:
            by_key.setdefault(line.key, []).append(line)

        touched = 0
        with ctx.db.transaction() as conn:
            for journal_id in journal_ids:
                journal = ctx.journals.get(journal_id, conn=conn)
                if journal.campaign_id != campaign.id:
                    raise NotFoundError(
                        "Journal introuvable dans cette campagne.",
                        journalId=journal_id,
                    )
                lines = [
                    CountJournalLine(
                        id=new_id(),
                        journal_id=journal_id,
                        campaign_id=campaign.id,
                        item_number=b.item_number,
                        qty_manual=b.qty,
                        unit=b.unit,
                        source=DataSource.SYSTEM,
                        comment="Quantité forcée au stock ERP.",
                    )
                    for b in by_key.get(journal.key, [])
                ]
                ctx.journals.replace_lines_for_journal(
                    journal_id, campaign.id, lines, actor=ctx.actor, conn=conn
                )
                touched += 1

            ctx.journals.set_status(
                journal_ids, JournalStatus.BOOK_ENFORCED,
                actor=ctx.actor, posted_at=utcnow(), conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.STATUS_CHANGE,
                entity_type="count_journal",
                summary=(
                    f"{touched} journal(aux) forcé(s) au stock ERP "
                    "(emplacement inventorié avant le snapshot)."
                ),
                after={"journalIds": list(journal_ids)},
                conn=conn,
            )
        return touched

    # ------------------------------------------------------------- locations

    def set_location_status(
        self,
        campaign: Campaign,
        keys: Sequence[LocationKey],
        status: LocationStatus,
    ) -> dict[str, int]:
        """Activate or disable locations, keeping journals consistent.

        Disabling a location removes its journal: the specification requires a
        disabled location to leave the perimeter entirely — quantities, values
        and progress denominator alike. Re-activating recreates a pending one.
        """
        ctx = self.ctx
        ctx.guard(campaign, "locations")
        locations = ctx.referentials.locations_by_key(campaign.id)
        unknown = [str(k) for k in keys if k not in locations]
        if unknown:
            raise NotFoundError(
                "Emplacement(s) inconnu(s) dans cette campagne.", locations=unknown
            )

        with ctx.db.transaction() as conn:
            updated = ctx.referentials.set_location_status(
                campaign.id, keys, status, actor=ctx.actor, conn=conn
            )
            if status is LocationStatus.DISABLED:
                removed = ctx.journals.delete_journals_for_locations(
                    campaign.id, keys, conn=conn
                )
                created = 0
            else:
                removed = 0
                created = ctx.journals.ensure_journals(
                    campaign.id,
                    keys,
                    kinds={
                        k: _kind_for(locations[k]) for k in keys if k in locations
                    },
                    actor=ctx.actor,
                    conn=conn,
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.STATUS_CHANGE,
                entity_type="location",
                summary=(
                    f"{updated} emplacement(s) → {status} "
                    f"({removed} journal(aux) supprimé(s), {created} créé(s))"
                ),
                after={
                    "status": str(status),
                    "locations": [str(k) for k in keys],
                },
                conn=conn,
            )
        return {"updated": updated, "journalsRemoved": removed, "journalsCreated": created}


def _kind_for(location: Any) -> Any:
    from ..domain.enums import JournalKind, LocationType

    return JournalKind.INVE if location.type is LocationType.LABEL else JournalKind.INVV
