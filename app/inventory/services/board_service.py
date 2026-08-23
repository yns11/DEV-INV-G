"""Le tableau de commandement du jour d'inventaire.

Un service à part, et volontairement petit. Il ne décide de rien : il rassemble
l'état des zones et des journaux, et laisse :mod:`inventory.domain.queues`
composer les files. Le mettre dans ``CampaignService`` l'aurait noyé dans le
cycle de vie d'une campagne, alors qu'il répond à une question d'une seule
journée.

Le périmètre du gestionnaire s'y applique, et c'est là qu'il gagne vraiment sa
place. Une campagne de quarante zones réparties sur neuf responsables donne un
tableau illisible si chacun voit tout ; filtré, chacun voit ses cinq lignes et
sait quoi faire. Le filtre reste un filtre : il retire du bruit, jamais des
droits — quelqu'un doit pouvoir couvrir un collègue à six heures du matin.
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.enums import JournalStatus, ZoneStatus
from ..domain.models import Campaign
from ..domain.queues import work_queues
from ..domain.workflow import derive_zone_status
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["BoardService"]


class BoardService:
    """Ce qui attend quelqu'un, maintenant."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def work_queues(self, campaign: Campaign, *, focus: bool = False) -> dict[str, Any]:
        """Les files de travail de la campagne, filtrées ou non par périmètre."""
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        perimeter = self._perimeter(campaign, zones) if focus else None
        if perimeter is not None and perimeter.resolved:
            zones = [z for z in zones if perimeter.covers_zone(z.id)]

        sheets_by_zone: dict[str, list[Any]] = {}
        for sheet in ctx.sheets.list_sheets(campaign.id):
            sheets_by_zone.setdefault(sheet.zone_id, []).append(sheet)
        lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)

        # Un arbitrage compte quand les deux comptages divergent *et* que
        # personne n'a tranché. Les compter tous ferait apparaître comme
        # « à arbitrer » des zones dont le litige est réglé depuis hier.
        pending_by_zone: dict[str, int] = {}
        for arbitration in ctx.sheets.list_arbitrations(campaign.id):
            if not arbitration.is_resolved and (
                arbitration.qty_pass_1 != arbitration.qty_pass_2
            ):
                pending_by_zone[arbitration.zone_id] = (
                    pending_by_zone.get(arbitration.zone_id, 0) + 1
                )

        to_arbitrate: list[str] = []
        ready_to_close: list[str] = []
        in_progress: list[str] = []
        not_started: list[str] = []
        for zone in zones:
            lines = [
                line
                for sheet in sheets_by_zone.get(zone.id, ())
                for line in lines_by_sheet.get(sheet.id, ())
            ]
            counted = sum(1 for line in lines if line.is_counted)
            status = derive_zone_status(
                counted_lines=counted, closed=zone.closed_at is not None
            )
            if status is ZoneStatus.DONE:
                continue
            if pending_by_zone.get(zone.id):
                to_arbitrate.append(zone.code)
            elif lines and counted == len(lines):
                # Tout est relevé, rien n'est en litige : la fermeture ne
                # demande plus qu'un clic, et c'est elle qui débloque le
                # passage en analyse.
                ready_to_close.append(zone.code)
            elif status is ZoneStatus.IN_PROGRESS:
                in_progress.append(zone.code)
            else:
                not_started.append(zone.code)

        journals_running: list[str] = []
        journals_pending: list[str] = []
        for journal in ctx.journals.list(campaign.id):
            if journal.status in (JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED):
                continue
            if perimeter is not None and perimeter.resolved and (
                not perimeter.covers_warehouse(journal.warehouse_id)
            ):
                continue
            key = f"{journal.warehouse_id} / {journal.location_id}"
            if journal.status is JournalStatus.IN_PROGRESS:
                journals_running.append(key)
            else:
                journals_pending.append(key)

        queues = work_queues(
            zones_to_arbitrate=to_arbitrate,
            zones_ready_to_close=ready_to_close,
            zones_in_progress=in_progress,
            zones_not_started=not_started,
            journals_in_progress=journals_running,
            journals_not_started=journals_pending,
        )
        return {
            "focus": focus and bool(perimeter and perimeter.resolved),
            "queues": [q.as_dict() for q in queues],
            "waiting": sum(q.count for q in queues),
        }

    def _perimeter(self, campaign: Campaign, zones: list[Any]) -> Any:
        from .manager_service import ManagerService

        return ManagerService(self.ctx).perimeter(campaign, zones=zones)

