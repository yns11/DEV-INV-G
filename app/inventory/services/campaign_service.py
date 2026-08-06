"""Campaign lifecycle: creation, cloning, phase transitions and closure."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction, CampaignStatus, ItemType, JournalStatus
from ..domain.models import Campaign, CampaignConfig, Thresholds
from ..domain.workflow import (
    Editable,
    assert_campaign_transition,
    campaign_transition_blockers,
    derive_zone_status,
    mutability_of,
    passes_for,
)
from ..errors import ConflictError, ValidationError
from .context import ENGINE_VERSION, ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["CampaignService", "DEFAULT_THRESHOLDS"]


#: Sensible starting thresholds, derived from the historical analysis of the
#: site's campaigns: a €1 000 gate on components keeps the exception list to a
#: workable size, while assemblies are worth far more per unit and deserve a
#: tighter relative gate.
DEFAULT_THRESHOLDS: tuple[Thresholds, ...] = (
    Thresholds(
        item_type=ItemType.COMPONENT,
        value_abs_eur="1000",
        qty_relative="0.02",
        ira_tolerance="0.005",
    ),
    Thresholds(
        item_type=ItemType.SEMI_FINISHED,
        value_abs_eur="2000",
        qty_relative="0.01",
        ira_tolerance="0",
    ),
    Thresholds(
        item_type=ItemType.FINISHED,
        value_abs_eur="2000",
        qty_relative="0.005",
        ira_tolerance="0",
    ),
    Thresholds(
        item_type=ItemType.PACKAGING,
        value_abs_eur="500",
        qty_relative="0.10",
        ira_tolerance="0.05",
    ),
    Thresholds(
        item_type=ItemType.UNKNOWN,
        value_abs_eur="500",
        qty_relative="0.02",
        ira_tolerance="0",
    ),
)


class CampaignService:
    """Use cases around the campaign aggregate."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list(self, *, include_closed: bool = True) -> list[Campaign]:
        return self.ctx.campaigns.list(include_closed=include_closed)

    def get(self, campaign_id: str) -> Campaign:
        return self.ctx.campaigns.get(campaign_id)

    def permissions(self, campaign: Campaign) -> Editable:
        return mutability_of(campaign.status)

    def overview(self, campaign_id: str) -> dict[str, Any]:
        """Everything the campaign header needs, in one round trip.

        Deliberately a single method: the header is on every screen, and issuing
        six queries per page render is how an app becomes slow enough that people
        go back to Excel.
        """
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        journal_progress = ctx.journals.progress(campaign_id)

        zones = ctx.sheets.list_zones(campaign_id)
        sheets = ctx.sheets.list_sheets(campaign_id)
        arbitrations = ctx.sheets.list_arbitrations(campaign_id)
        sheets_by_zone: dict[str, list] = {}
        for sheet in sheets:
            sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)
        pending_by_zone: dict[str, int] = {}
        for arb in arbitrations:
            if not arb.is_resolved and arb.qty_pass_1 != arb.qty_pass_2:
                pending_by_zone[arb.zone_id] = pending_by_zone.get(arb.zone_id, 0) + 1

        zone_states = {
            zone.code: derive_zone_status(
                sheets_by_zone.get(zone.id, ()),
                passes_required=zone.passes,
                pending_arbitrations=pending_by_zone.get(zone.id, 0),
            )
            for zone in zones
        }
        zones_done = sum(1 for s in zone_states.values() if str(s) == "DONE")

        # The header carries the focus switch, so it also carries the count of
        # what the switch would leave on screen. Announcing "0 journal, 0 zone"
        # up front is what stops an empty perimeter from being read as an empty
        # campaign.
        from .manager_service import ManagerService

        perimeter = ManagerService(ctx).perimeter(campaign)
        journals = ctx.journals.list(campaign_id)
        perimeter_payload = {
            **perimeter.as_dict(),
            "journalCount": sum(
                1 for j in journals if perimeter.covers_warehouse(j.warehouse_id)
            ) if perimeter.resolved else 0,
            "zoneCount": sum(
                1 for z in zones if perimeter.covers_zone(z.id)
            ) if perimeter.resolved else 0,
        }

        return {
            "campaign": campaign,
            "permissions": mutability_of(campaign.status).as_dict(),
            "journalProgress": {
                "total": journal_progress.get("total", 0),
                "complete": journal_progress.get("complete", 0),
                "running": journal_progress.get("running", 0),
                "pending": journal_progress.get("pending", 0),
                "ratio": _ratio(
                    journal_progress.get("complete", 0), journal_progress.get("total", 0)
                ),
            },
            "genericProgress": {
                "zones": len(zones),
                "done": zones_done,
                "ratio": _ratio(zones_done, len(zones)),
                "byStatus": _count_by(zone_states.values()),
                "pendingArbitrations": sum(pending_by_zone.values()),
            },
            "counts": {
                "items": ctx.referentials.count_items(campaign_id),
                "bookStockLines": ctx.book_stock.count(campaign_id),
            },
            "perimeter": perimeter_payload,
        }

    # ---------------------------------------------------------------- create

    def create(
        self,
        *,
        code: str,
        label: str,
        count_date: dt.date,
        config: CampaignConfig | None = None,
        thresholds: list[Thresholds] | None = None,
    ) -> Campaign:
        """Create a campaign in ``PREPARATION``."""
        ctx = self.ctx
        if ctx.campaigns.get_by_code(code) is not None:
            raise ConflictError(
                f"Une campagne portant le code « {code} » existe déjà.", code=code
            )
        now = utcnow()
        campaign = Campaign(
            id=new_id(),
            code=code,
            label=label,
            count_date=count_date,
            status=CampaignStatus.PREPARATION,
            config=config or CampaignConfig(
                generic_warehouse=ctx.settings.generic_warehouse,
                generic_location=ctx.settings.generic_location,
            ),
            thresholds=list(thresholds or DEFAULT_THRESHOLDS),
            created_by=ctx.actor,
            created_at=now,
            engine_version=ENGINE_VERSION,
        )
        with ctx.db.transaction() as conn:
            ctx.campaigns.create(campaign, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.CREATE,
                entity_type="campaign",
                entity_id=campaign.id,
                summary=f"Création de la campagne {campaign.code}",
                after={"code": campaign.code, "countDate": str(count_date)},
                conn=conn,
            )
        log.info("Campaign %s created by %s", campaign.code, ctx.actor)
        return campaign

    def clone(
        self,
        *,
        source_campaign_id: str,
        code: str,
        label: str,
        count_date: dt.date,
        include_zones: bool = True,
        include_sheet_lines: bool = True,
    ) -> Campaign:
        """Start a campaign from a previous one's referentials.

        Copies thresholds, articles, BOMs, the location referential (with its
        disabled flags) and, optionally, the GENERIQUE zones together with their
        pre-printed article lists. Nothing operational is copied — no counts, no
        journals, no adjustments — because a campaign is a photograph of one
        moment and copying its measurements would be meaningless.

        This is the feature that removes the two days spent every campaign
        rebuilding ``Compil GENERIQUE`` by hand.
        """
        ctx = self.ctx
        source = ctx.campaigns.get(source_campaign_id)
        target = self.create(
            code=code,
            label=label,
            count_date=count_date,
            config=source.config.model_copy(),
            thresholds=[t.model_copy() for t in source.thresholds],
        )
        items = ctx.referentials.list_items(source_campaign_id)
        for item in items:
            item.campaign_id = target.id
        bom_links = ctx.referentials.list_bom_links(source_campaign_id)
        for link in bom_links:
            link.campaign_id = target.id
        warehouses = ctx.referentials.list_warehouses(source_campaign_id)
        for warehouse in warehouses:
            warehouse.campaign_id = target.id
        locations = ctx.referentials.list_locations(source_campaign_id)
        for location in locations:
            location.campaign_id = target.id

        # Managers and their perimeters are staffing, and staffing is stable
        # between two campaigns of the same site: re-typing the five names and
        # forty zone assignments every quarter is exactly the kind of work this
        # application exists to remove.
        managers = [
            m.model_copy(update={"campaign_id": target.id})
            for m in ctx.referentials.list_managers(source_campaign_id)
        ]
        warehouse_assignments = ctx.referentials.warehouse_assignments(
            source_campaign_id
        )

        copied_zones = 0
        copied_lines = 0
        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_items(items, actor=ctx.actor, conn=conn)
            ctx.referentials.upsert_bom_links(bom_links, actor=ctx.actor, conn=conn)
            ctx.referentials.upsert_warehouses(warehouses, actor=ctx.actor, conn=conn)
            ctx.referentials.upsert_locations(locations, actor=ctx.actor, conn=conn)
            if managers:
                ctx.referentials.upsert_managers(managers, actor=ctx.actor, conn=conn)
            if warehouse_assignments:
                ctx.referentials.set_warehouse_assignments(
                    target.id, warehouse_assignments, actor=ctx.actor, conn=conn
                )

            if include_zones:
                source_lines = ctx.sheets.lines_by_sheet(source_campaign_id)
                source_sheets = ctx.sheets.list_sheets(source_campaign_id)
                sheets_by_zone: dict[str, list] = {}
                for sheet in source_sheets:
                    sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)

                for zone in ctx.sheets.list_zones(source_campaign_id):
                    new_zone = zone.model_copy(
                        update={"id": new_id(), "campaign_id": target.id}
                    )
                    ctx.sheets.create_zone(new_zone, actor=ctx.actor)
                    # The zone's own count requirement travels with it: a
                    # single-pass metrology room does not become a double-count
                    # zone because the campaign default says so.
                    ctx.sheets.ensure_sheets(
                        target.id, new_zone.id, passes_for(new_zone.passes),
                        actor=ctx.actor,
                    )
                    copied_zones += 1

                    if not include_sheet_lines:
                        continue
                    # The article list of a zone is its real value: it took years
                    # to build. Copy it from pass 1, blank of any quantity.
                    template = _pick_template_sheet(sheets_by_zone.get(zone.id, ()))
                    if template is None:
                        continue
                    new_sheets = ctx.sheets.list_sheets(target.id, zone_id=new_zone.id)
                    for new_sheet in new_sheets:
                        blanks = [
                            line.model_copy(update={
                                "id": new_id(),
                                "sheet_id": new_sheet.id,
                                "campaign_id": target.id,
                                "qty_imported": None,
                                "qty_manual": None,
                                "confidence": None,
                                "comment": "",
                            })
                            for line in source_lines.get(template.id, ())
                        ]
                        if blanks:
                            ctx.sheets.upsert_sheet_lines(
                                blanks, actor=ctx.actor, conn=conn
                            )
                            copied_lines += len(blanks)

            # Persist the lineage: "which campaign was this built from?" is the
            # first question asked when a referential looks wrong.
            ctx.campaigns.set_cloned_from(target.id, source.code, conn=conn)
            ctx.record(
                campaign_id=target.id,
                action=AuditAction.CREATE,
                entity_type="campaign",
                entity_id=target.id,
                summary=(
                    f"Campagne {target.code} dupliquée depuis {source.code} : "
                    f"{len(items)} articles, {len(bom_links)} liens BOM, "
                    f"{len(locations)} emplacements, {copied_zones} zones, "
                    f"{copied_lines} lignes de feuille."
                ),
                after={
                    "sourceCode": source.code,
                    "items": len(items),
                    "bomLinks": len(bom_links),
                    "locations": len(locations),
                    "zones": copied_zones,
                    "sheetLines": copied_lines,
                },
                conn=conn,
            )
        return ctx.campaigns.get(target.id)

    # ----------------------------------------------------------- transitions

    def transition_readiness(
        self, campaign_id: str, target: CampaignStatus
    ) -> dict[str, Any]:
        """What still blocks a transition — without attempting it.

        Powers the live "what is missing to move on" panel, so the user is never
        surprised by a refusal at the moment they click.
        """
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        allowed = target in _allowed_targets(campaign.status)

        journal_statuses: list[JournalStatus] = []
        zone_statuses: list[Any] = []
        if target is CampaignStatus.ANALYSIS:
            journal_statuses = [j.status for j in ctx.journals.list(campaign_id)]
            zone_statuses = list(self._zone_statuses(campaign).values())

        blockers = campaign_transition_blockers(
            campaign.status,
            target,
            journal_statuses=journal_statuses,
            zone_statuses=zone_statuses,
            book_stock_frozen=campaign.book_stock_frozen_at is not None,
        )
        return {
            "current": str(campaign.status),
            "target": str(target),
            "allowed": allowed,
            "ready": allowed and not blockers,
            "blockers": [b.model_dump(mode="json") for b in blockers],
        }

    def transition(self, campaign_id: str, target: CampaignStatus) -> Campaign:
        """Move the campaign forward, freezing what the new phase freezes."""
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        assert_campaign_transition(campaign.status, target)

        readiness = self.transition_readiness(campaign_id, target)
        if readiness["blockers"]:
            raise ValidationError(
                "La campagne ne peut pas changer de statut : "
                f"{len(readiness['blockers'])} point(s) bloquant(s).",
                blockers=readiness["blockers"],
            )

        now = utcnow()
        timestamps: dict[str, dt.datetime] = {}
        if target is CampaignStatus.COUNTING:
            timestamps["referentials_frozen_at"] = now
        elif target is CampaignStatus.ANALYSIS:
            timestamps["counting_frozen_at"] = now
        elif target is CampaignStatus.CLOSED:
            timestamps["closed_at"] = now

        with ctx.db.transaction() as conn:
            ctx.campaigns.update_status(
                campaign_id, target, actor=ctx.actor, timestamps=timestamps, conn=conn
            )
            ctx.record(
                campaign_id=campaign_id,
                action=AuditAction.STATUS_CHANGE,
                entity_type="campaign",
                entity_id=campaign_id,
                summary=f"Campagne {campaign.code} : {campaign.status} → {target}",
                before={"status": str(campaign.status)},
                after={"status": str(target), "frozen": list(timestamps)},
                conn=conn,
            )
        log.info("Campaign %s moved to %s by %s", campaign.code, target, ctx.actor)
        return ctx.campaigns.get(campaign_id)

    # ------------------------------------------------------------ thresholds

    def update_thresholds(
        self, campaign_id: str, thresholds: list[Thresholds]
    ) -> list[Thresholds]:
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        ctx.guard(campaign, "thresholds")
        before = {str(t.item_type): t.model_dump(mode="json") for t in campaign.thresholds}
        with ctx.db.transaction() as conn:
            ctx.campaigns.replace_thresholds(
                campaign_id, thresholds, actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign_id,
                action=AuditAction.UPDATE,
                entity_type="threshold",
                summary="Mise à jour des seuils de matérialité",
                before=before,
                after={str(t.item_type): t.model_dump(mode="json") for t in thresholds},
                conn=conn,
            )
        return ctx.campaigns.list_thresholds(campaign_id)

    def update_config(self, campaign_id: str, config: CampaignConfig) -> Campaign:
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        ctx.guard(campaign, "thresholds")  # configuration follows the same gate
        ctx.campaigns.update_config(campaign_id, config, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign_id,
            action=AuditAction.UPDATE,
            entity_type="campaign",
            entity_id=campaign_id,
            summary="Mise à jour de la configuration de campagne",
            before=campaign.config.model_dump(mode="json"),
            after=config.model_dump(mode="json"),
        )
        return ctx.campaigns.get(campaign_id)

    # --------------------------------------------------------------- helpers

    def _zone_statuses(self, campaign: Campaign) -> dict[str, Any]:
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        sheets = ctx.sheets.list_sheets(campaign.id)
        arbitrations = ctx.sheets.list_arbitrations(campaign.id)
        by_zone: dict[str, list] = {}
        for sheet in sheets:
            by_zone.setdefault(sheet.zone_id, []).append(sheet)
        pending: dict[str, int] = {}
        for arb in arbitrations:
            if not arb.is_resolved and arb.qty_pass_1 != arb.qty_pass_2:
                pending[arb.zone_id] = pending.get(arb.zone_id, 0) + 1
        return {
            zone.id: derive_zone_status(
                by_zone.get(zone.id, ()),
                passes_required=zone.passes,
                pending_arbitrations=pending.get(zone.id, 0),
            )
            for zone in zones
        }


def _allowed_targets(status: CampaignStatus) -> set[CampaignStatus]:
    from ..domain.workflow import CAMPAIGN_TRANSITIONS

    return set(CAMPAIGN_TRANSITIONS[status])


def _pick_template_sheet(sheets) -> Any:
    """Pass 1 if it exists, otherwise whichever sheet has content."""
    from ..domain.enums import SheetPass

    for sheet in sheets:
        if sheet.pass_no is SheetPass.PASS_1:
            return sheet
    return sheets[0] if sheets else None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _count_by(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out
