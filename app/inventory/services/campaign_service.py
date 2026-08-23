"""Campaign lifecycle: creation, cloning, phase transitions and closure."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction, CampaignStatus, ItemType, JournalStatus
from ..domain.models import Campaign, CampaignConfig, Thresholds
from ..domain.sequence import PREREQUISITES, blocking_reason, unlocked_aspects
from ..domain.workflow import (
    Editable,
    assert_campaign_transition,
    campaign_transition_blockers,
    derive_zone_status,
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
#: Packaging is deliberately absent: the ERP referential excludes it, so a row
#: for it was a line nobody could ever act on. An article that somehow carries
#: that type still falls back to a permissive default rather than being skipped.
DEFAULT_THRESHOLDS: tuple[Thresholds, ...] = (
    Thresholds(item_type=ItemType.COMPONENT, value_abs_eur="1000", qty_relative="0.02"),
    Thresholds(
        item_type=ItemType.SEMI_FINISHED, value_abs_eur="2000", qty_relative="0.01"
    ),
    Thresholds(item_type=ItemType.FINISHED, value_abs_eur="2000", qty_relative="0.005"),
    Thresholds(item_type=ItemType.UNKNOWN, value_abs_eur="500", qty_relative="0.02"),
)


class CampaignService:
    """Use cases around the campaign aggregate."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    #: Combien de campagnes une page en rend. Cent tenaient dans l'écran des
    #: années durant ; ce qui manquait n'était pas une borne plus haute mais de
    #: savoir qu'il y en avait davantage.
    PAGE = 100

    def page(
        self, *, include_closed: bool = True, limit: int | None = None, offset: int = 0
    ) -> tuple[list[Campaign], int]:
        """Une page de campagnes, et le total. Voir le dépôt pour le pourquoi."""
        return self.ctx.campaigns.page(
            include_closed=include_closed,
            limit=self.PAGE if limit is None else max(1, min(limit, 500)),
            offset=max(0, offset),
        )

    def get(self, campaign_id: str) -> Campaign:
        return self.ctx.campaigns.get(campaign_id)

    def permissions(self, campaign: Campaign) -> Editable:
        """La phase **et** l'identité. Voir :meth:`ServiceContext.permissions`."""
        return self.ctx.permissions(campaign)

    def overview(self, campaign_id: str) -> dict[str, Any]:
        """Everything the campaign header needs, in one round trip.

        Deliberately a single method: the header is on every screen, and issuing
        six queries per page render is how an app becomes slow enough that people
        go back to Excel.
        """
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        progress = ctx.progress(campaign)
        journal_progress = ctx.journals.progress(campaign_id)

        zones = ctx.sheets.list_zones(campaign_id)
        sheets = ctx.sheets.list_sheets(campaign_id)
        arbitrations = ctx.sheets.list_arbitrations(campaign_id)
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign_id)
        sheets_by_zone: dict[str, list] = {}
        for sheet in sheets:
            sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)
        pending_by_zone: dict[str, int] = {}
        for arb in arbitrations:
            if not arb.is_resolved and arb.qty_pass_1 != arb.qty_pass_2:
                pending_by_zone[arb.zone_id] = pending_by_zone.get(arb.zone_id, 0) + 1

        zone_states = {
            zone.code: derive_zone_status(
                counted_lines=_counted(sheets_by_zone.get(zone.id, ()), lines_by_sheet),
                closed=zone.closed_at is not None,
            )
            for zone in zones
        }
        zones_done = sum(1 for s in zone_states.values() if str(s) == "DONE")

        # The header carries the focus switch, so it also carries the count of
        # what the switch would leave on screen. Announcing "0 journal, 0 zone"
        # up front is what stops an empty perimeter from being read as an empty
        # campaign.
        from .manager_service import ManagerService

        perimeter = ManagerService(ctx).perimeter(campaign, zones=zones)
        perimeter_payload = {
            **perimeter.as_dict(), "journalCount": 0, "zoneCount": 0
        }
        if perimeter.resolved:
            perimeter_payload["journalCount"] = sum(
                1 for j in ctx.journals.list(campaign_id)
                if perimeter.covers_warehouse(j.warehouse_id)
            )
            perimeter_payload["zoneCount"] = sum(
                1 for z in zones if perimeter.covers_zone(z.id)
            )

        # Le rôle voyage à côté des permissions : sans lui, un écran entièrement
        # grisé ne se distingue pas d'une campagne clôturée, et la seule chose
        # utile à savoir — qui demander pour obtenir le droit — manquerait.
        role = ctx.role(campaign)
        return {
            "campaign": campaign,
            "permissions": ctx.permissions(campaign).as_dict(),
            "access": {
                "role": str(role),
                "canWrite": role.may_write,
                "isOwner": role.is_owner,
                "owner": campaign.created_by,
            },
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
            # What is unlocked, and why not — the same function the guard uses,
            # so a step is never offered and then refused.
            "sequence": {
                "unlocked": unlocked_aspects(progress),
                "blockedBy": {
                    aspect: blocking_reason(aspect, progress)
                    for aspect in PREREQUISITES
                    if blocking_reason(aspect, progress)
                },
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

    def delete(self, campaign_id: str) -> None:
        """Retire a campaign — logically, and only if you created it.

        Two rules, and both are about the same thing: a campaign is somebody's
        work, and it stays theirs. Only its author can retire it, because a
        campaign disappearing from under the person running it is a far worse
        accident than one lingering a week too long. And nothing is erased: the
        counts, the journals and the audit trail all stay on disk, so a deletion
        made in error is undone by a line of SQL rather than by a restore.
        """
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        ctx.require_owner(campaign, "supprimer une campagne")
        with ctx.db.transaction() as conn:
            ctx.record(
                campaign_id=campaign_id,
                action=AuditAction.DELETE,
                entity_type="campaign",
                entity_id=campaign_id,
                summary=f"Suppression de la campagne {campaign.code}",
                before={"code": campaign.code, "status": str(campaign.status)},
                conn=conn,
            )
            ctx.campaigns.soft_delete(campaign_id, actor=ctx.actor, conn=conn)
        log.info("Campaign %s deleted by %s", campaign.code, ctx.actor)

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
        # between two campaigns of the same site: re-typing nine names and forty
        # zone assignments every quarter is exactly the kind of work this
        # application exists to remove.
        managers = [
            m.model_copy(update={"campaign_id": target.id})
            for m in ctx.referentials.list_managers(source_campaign_id)
        ]
        warehouse_assignments = ctx.referentials.warehouse_assignments(
            source_campaign_id
        )

        # Tout ce qui vient de la source est lu avant d'ouvrir la transaction :
        # celle-ci n'écrit que sur la nouvelle campagne, et une lecture faite
        # dedans mobiliserait une seconde connexion du pool sans rien y gagner.
        source_zones = ctx.sheets.list_zones(source_campaign_id) if include_zones else []
        sheets_by_zone: dict[str, list] = {}
        source_lines: dict[str, list] = {}
        if source_zones and include_sheet_lines:
            for sheet in ctx.sheets.list_sheets(source_campaign_id):
                sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)
            source_lines = ctx.sheets.lines_by_sheet(source_campaign_id)

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

            for zone in source_zones:
                new_zone = zone.model_copy(
                    update={"id": new_id(), "campaign_id": target.id}
                )
                ctx.sheets.create_zone(new_zone, actor=ctx.actor, conn=conn)
                # The zone's own count requirement travels with it: a
                # single-pass metrology room does not become a double-count
                # zone because the campaign default says so.
                ctx.sheets.ensure_sheets(
                    target.id, new_zone.id, passes_for(new_zone.passes),
                    actor=ctx.actor, conn=conn,
                )
                copied_zones += 1

                if not include_sheet_lines:
                    continue
                # The article list of a zone is its real value: it took years
                # to build. Copy it from pass 1, blank of any quantity.
                template = _pick_template_sheet(sheets_by_zone.get(zone.id, ()))
                if template is None:
                    continue
                new_sheets = ctx.sheets.list_sheets(
                    target.id, zone_id=new_zone.id, conn=conn
                )
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

        # Les faits que seule la clôture consulte. Ils coûtent chacun une
        # requête et un calcul d'écarts : les rassembler pour un passage en
        # comptage reviendrait à payer l'analyse complète à chaque clic sur le
        # panneau « ce qui manque pour avancer ».
        unexplained = 0
        rejected_imports: list[tuple[str, int]] = []
        publication_done = True
        if target is CampaignStatus.CLOSED:
            # Posé par le job de publication après son manifeste Delta. Le lire
            # ici évite de faire dépendre la clôture d'un entrepôt SQL éveillé,
            # pour une réponse qui ne change qu'une fois par campagne.
            publication_done = campaign.published_at is not None
            unexplained = self._unexplained_material(campaign)
            rejected_imports = [
                (str(b["target"]), int(b["rows_rejected"] or 0))
                for b in ctx.imports.latest_per_target(campaign_id)
            ]

        blockers = campaign_transition_blockers(
            campaign.status,
            target,
            journal_statuses=journal_statuses,
            zone_statuses=zone_statuses,
            book_stock_frozen=campaign.book_stock_frozen_at is not None,
            unexplained_material=unexplained,
            rejected_imports=rejected_imports,
            publication_done=publication_done,
        )
        return {
            "current": str(campaign.status),
            "target": str(target),
            "allowed": allowed,
            "ready": allowed and not blockers,
            "blockers": [b.model_dump(mode="json") for b in blockers],
        }

    def closure_checklist(self, campaign_id: str) -> dict[str, Any]:
        """L'état des lieux du dossier avant le geste irréversible.

        Ce qui bloque n'est **pas recalculé** : les entrées bloquantes viennent
        de :meth:`transition_readiness`, donc de la même fonction que le refus.
        Les rejouer autrement serait la façon dont l'écran finit par annoncer
        « prêt » sur une campagne que la clôture refuse.
        """
        from ..domain.closure import closure_checklist as build

        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        readiness = self.transition_readiness(campaign_id, CampaignStatus.CLOSED)
        blockers = campaign_transition_blockers(
            campaign.status,
            CampaignStatus.CLOSED,
            unexplained_material=self._unexplained_material(campaign),
            rejected_imports=[
                (str(b["target"]), int(b["rows_rejected"] or 0))
                for b in ctx.imports.latest_per_target(campaign_id)
            ],
            publication_done=campaign.published_at is not None,
        )

        analyses = ctx.analysis.list_analyses(campaign_id)
        run = ctx.consolidation.current_run(campaign_id)
        last_change = ctx.sheets.last_line_change(campaign_id)
        items = build(
            blockers=blockers,
            accepted_without_comment=sum(
                1 for a in analyses if a.accepted and not a.comment.strip()
            ),
            ai_suggestions_untouched=sum(
                1 for a in analyses
                if a.ai_suggested_cause and not a.cause_code and not a.accepted
            ),
            # Une consolidation absente ne compte pas comme périmée : elle
            # n'existe pas, et c'est un autre point — celui de la publication.
            sheets_changed_after_consolidation=bool(
                run and last_change and last_change > run["run_at"]
            ),
            book_stock_frozen=campaign.book_stock_frozen_at is not None,
            journals_pending=sum(
                1 for j in ctx.journals.list(campaign_id)
                if j.status not in (JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED)
            ),
            zones_open=sum(
                1 for status in self._zone_statuses(campaign).values()
                if str(status) != "DONE"
            ),
        )
        return {
            "ready": readiness["ready"],
            "allowed": readiness["allowed"],
            "items": [item.as_dict() for item in items],
            "counts": {
                "blocking": sum(1 for i in items if str(i.state) == "BLOCKING"),
                "attention": sum(1 for i in items if str(i.state) == "ATTENTION"),
                "done": sum(1 for i in items if str(i.state) == "DONE"),
            },
        }

    def transition(self, campaign_id: str, target: CampaignStatus) -> Campaign:
        """Move the campaign forward, freezing what the new phase freezes."""
        ctx = self.ctx
        campaign = ctx.campaigns.get(campaign_id)
        # Le changement de phase gèle des données de façon irréversible : c'est
        # l'action la plus lourde de l'écran, et elle ne passe par aucune garde
        # d'aspect — celles-ci portent sur ce que la phase autorise, pas sur la
        # phase elle-même.
        ctx.require_write(campaign)
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

    def thresholds(self, campaign: Campaign) -> list[Thresholds]:
        """Les seuils en vigueur sur cette campagne."""
        return self.ctx.campaigns.list_thresholds(campaign.id)

    def audit_trail(
        self,
        campaign: Campaign,
        *,
        entity_type: str | None = None,
        actor: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Any]:
        """Le journal d'audit de la campagne, filtré et paginé."""
        return self.ctx.audit.list(
            campaign.id,
            entity_type=entity_type,
            actor=actor,
            limit=limit,
            offset=offset,
        )

    def import_history(
        self, campaign: Campaign, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Les chargements de la campagne, sans le chemin du volume.

        L'écran a seulement besoin de savoir s'il y a une pièce à proposer ; la
        route de téléchargement résout elle-même le chemin à partir de
        l'identifiant du lot. Le retirer **ici** plutôt qu'à l'affichage est ce
        qui garantit qu'aucun autre appelant ne le fera sortir.
        """
        return [
            {
                **{k: v for k, v in row.items() if k != "storage_path"},
                "id": str(row["id"]),
                "archived": row["storage_path"] is not None,
            }
            for row in self.ctx.imports.list(campaign.id, limit=limit)
        ]

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

    # --------------------------------------------------------------- helpers

    def _unexplained_material(self, campaign: Campaign) -> int:
        """Combien d'écarts matériels n'ont ni cause assignée ni acceptation.

        « Matériel » est la définition du domaine — les seuils de la campagne,
        par type d'article — et non un jugement porté ici. Un écart accepté
        explicitement compte comme expliqué : accepter un résiduel après examen
        *est* une décision, tracée et signée, et l'exiger en plus d'une cause
        rendrait la clôture impossible sur les écarts qui n'ont pas d'autre
        explication que « c'est du bruit, et on l'assume ».
        """
        from ..domain.variance import is_material
        from .analysis_service import AnalysisService

        lines = AnalysisService(self.ctx).variances(campaign)
        thresholds = {t.item_type: t for t in campaign.thresholds}
        explained = {
            a.item_number
            for a in self.ctx.analysis.list_analyses(campaign.id)
            if a.cause_code or a.accepted
        }
        return sum(
            1
            for line in lines
            if line.item_number not in explained
            and (gate := thresholds.get(line.item_type)) is not None
            and is_material(line, gate)
        )

    def _zone_statuses(self, campaign: Campaign) -> dict[str, Any]:
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        by_zone: dict[str, list] = {}
        for sheet in sheets:
            by_zone.setdefault(sheet.zone_id, []).append(sheet)
        return {
            zone.id: derive_zone_status(
                counted_lines=_counted(by_zone.get(zone.id, ()), lines_by_sheet),
                closed=zone.closed_at is not None,
            )
            for zone in zones
        }


def _counted(sheets, lines_by_sheet) -> int:
    """Combien de quantités relevées portent les feuilles d'une zone.

    C'est ce qui distingue « à compter » de « en cours » : les deux se lisent
    dans les quantités, et ne peuvent donc pas mentir.
    """
    return sum(
        1
        for sheet in sheets
        for line in lines_by_sheet.get(sheet.id, ())
        if line.is_counted
    )


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
