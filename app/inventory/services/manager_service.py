"""Managers (« gestionnaires »), their perimeters, and the focus mode.

A campaign is run by several people. Each one owns a handful of warehouses —
and therefore of counting journals — plus a handful of GENERIQUE zones. Without
somewhere to record that, every screen shows the whole site to everybody, and on
a forty-zone campaign that is the difference between a working tool and a wall
of rows.

Two ideas hold this module together, and both are load-bearing:

**The perimeter is resolved server-side.** ``?focus=true`` carries no manager
name: the server maps the signed-in identity onto a manager and filters before
answering. Filtering in the browser would still ship every journal of the site
to every workstation, which stops being acceptable the moment a contractor
counts one zone.

**Focus is a filter, never a permission.** A manager keeps the right to act
outside their perimeter — somebody has to cover for a colleague at 6 a.m. on
inventory day. What focus removes is noise, not authority. The interface says so
in as many words, because otherwise it reads as an entitlement.

Being *declared* here is a permission, though, and the distinction matters. The
identity attached to a slot is what makes somebody a manager of the campaign
rather than a reader of it — see :mod:`inventory.domain.access`. Which is why
this screen is the one place a manager may not touch: only the campaign's owner
edits the list, or a manager would be granting the right to grant.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.enums import AuditAction
from ..domain.models import Campaign, Manager, normalise_key
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = [
    "CATCH_ALL_WAREHOUSE",
    "DEFAULT_MANAGERS",
    "SUGGESTED_WAREHOUSES",
    "ManagerService",
    "Perimeter",
]

#: The manager slots offered on every campaign. They are *slots*, not people:
#: the label and the identity behind each one are edited per campaign, which is
#: what lets a campaign be duplicated without carrying last quarter's staffing
#: along with it.
#:
#: Neuf depuis que le site en compte neuf. Rien à migrer : les cases sont
#: fusionnées à la lecture plutôt que semées à la création, donc une campagne
#: ouverte quand il y en avait cinq en montre neuf à sa prochaine visite, les
#: cinq premières gardant ce qui y était saisi.
DEFAULT_MANAGERS: tuple[tuple[str, str], ...] = tuple(
    (f"GESTIONNAIRE_{n}", f"Gestionnaire {n}") for n in range(1, 10)
)

#: Reserved warehouse key meaning "every warehouse without an explicit owner".
#: Without it, each new warehouse discovered by a book-stock import would fall
#: outside everybody's perimeter until somebody noticed.
CATCH_ALL_WAREHOUSE = "AUTRES"

#: The site's warehouses, offered before the book stock has been loaded so that
#: the assignment can be prepared in advance. Warehouses actually found in the
#: campaign are added to this list, never replaced by it.
SUGGESTED_WAREHOUSES: tuple[str, ...] = (
    "B06", "B06VRAC", "QUAL", "QUAL VRAC", CATCH_ALL_WAREHOUSE,
)


@dataclass(frozen=True, slots=True)
class Perimeter:
    """What one manager owns, in a form the read paths can filter with."""

    manager: Manager | None
    #: Warehouses explicitly assigned to this manager.
    warehouse_ids: frozenset[str] = frozenset()
    #: True when this manager owns the ``AUTRES`` catch-all.
    catch_all: bool = False
    #: Every warehouse explicitly assigned to *anybody*, so the catch-all knows
    #: what is left over.
    assigned_warehouses: frozenset[str] = frozenset()
    zone_ids: frozenset[str] = frozenset()

    @property
    def resolved(self) -> bool:
        """False when the signed-in user is not registered as a manager."""
        return self.manager is not None

    @property
    def is_empty(self) -> bool:
        return not self.warehouse_ids and not self.zone_ids and not self.catch_all

    def covers_warehouse(self, warehouse_id: str) -> bool:
        if warehouse_id in self.warehouse_ids:
            return True
        return self.catch_all and warehouse_id not in self.assigned_warehouses

    def covers_zone(self, zone_id: str) -> bool:
        return zone_id in self.zone_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "managerCode": self.manager.code if self.manager else None,
            "managerLabel": self.manager.label if self.manager else None,
            "warehouses": sorted(self.warehouse_ids),
            "catchAll": self.catch_all,
            "zoneCount": len(self.zone_ids),
        }


class ManagerService:
    """Administration of the manager referential and of both assignment grids."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list_managers(self, campaign: Campaign) -> list[Manager]:
        """The slots of :data:`DEFAULT_MANAGERS`, merged with what was saved.

        Merged rather than seeded on first read: a GET must not write. It also
        means a campaign created before this feature existed — or before the
        list grew — shows every slot immediately, with nothing to migrate.
        """
        stored = {m.code: m for m in self.ctx.referentials.list_managers(campaign.id)}
        out: list[Manager] = []
        for order, (code, label) in enumerate(DEFAULT_MANAGERS):
            existing = stored.pop(code, None)
            out.append(
                existing
                if existing is not None
                else Manager(
                    campaign_id=campaign.id, code=code, label=label,
                    display_order=order,
                )
            )
        # A code saved before the default list changed is kept rather than
        # dropped: it may still be carrying assignments.
        out.extend(sorted(stored.values(), key=lambda m: (m.display_order, m.code)))
        return out

    def overview(self, campaign: Campaign) -> dict[str, Any]:
        """Both administration grids and their current counts, in one call."""
        ctx = self.ctx
        managers = self.list_managers(campaign)
        assignments = ctx.referentials.warehouse_assignments(campaign.id)
        zones = ctx.sheets.list_zones(campaign.id)
        journals = ctx.journals.list(campaign.id)

        journals_per_warehouse: dict[str, int] = {}
        for journal in journals:
            journals_per_warehouse[journal.warehouse_id] = (
                journals_per_warehouse.get(journal.warehouse_id, 0) + 1
            )

        known = sorted(
            {w.warehouse_id for w in ctx.referentials.list_warehouses(campaign.id)}
            | set(assignments)
            | set(SUGGESTED_WAREHOUSES)
        )
        explicit = {w for w in assignments if w != CATCH_ALL_WAREHOUSE}

        zones_per_manager: dict[str, int] = {}
        for zone in zones:
            if zone.manager_code:
                zones_per_manager[zone.manager_code] = (
                    zones_per_manager.get(zone.manager_code, 0) + 1
                )

        journals_per_manager: dict[str, int] = {}
        for journal in journals:
            code = self._owner_of(journal.warehouse_id, assignments, explicit)
            if code:
                journals_per_manager[code] = journals_per_manager.get(code, 0) + 1

        return {
            "managers": [
                {
                    **m.model_dump(mode="json"),
                    "zoneCount": zones_per_manager.get(m.code, 0),
                    "journalCount": journals_per_manager.get(m.code, 0),
                }
                for m in managers
            ],
            "warehouses": [
                {
                    "warehouseId": warehouse,
                    "managerCode": assignments.get(warehouse, ""),
                    "journalCount": journals_per_warehouse.get(warehouse, 0),
                    # The catch-all is not a warehouse: it is a rule about the
                    # ones nobody named.
                    "isCatchAll": warehouse == CATCH_ALL_WAREHOUSE,
                    "known": warehouse in journals_per_warehouse,
                }
                for warehouse in known
            ],
            "zones": [
                {
                    "id": zone.id,
                    "code": zone.code,
                    "label": zone.label,
                    "sector": zone.sector,
                    "managerCode": zone.manager_code,
                }
                for zone in zones
            ],
        }

    # ----------------------------------------------------------------- write

    def save_managers(
        self, campaign: Campaign, rows: Sequence[Mapping[str, Any]]
    ) -> list[Manager]:
        """Rename the slots and attach the identity behind each one."""
        ctx = self.ctx
        # Le propriétaire seul : un gestionnaire qui pourrait en déclarer
        # d'autres s'accorderait le droit d'en accorder, et rien ne
        # l'empêcherait d'en retirer le propriétaire.
        ctx.require_owner(campaign, "déclarer les gestionnaires")
        ctx.guard(campaign, "thresholds")  # configuration follows the same gate
        known = {m.code for m in self.list_managers(campaign)}
        managers: list[Manager] = []
        seen_actors: dict[str, str] = {}
        for order, row in enumerate(rows):
            code = normalise_key(str(row.get("code") or "")).replace(" ", "_")
            if code not in known:
                raise ValidationError(
                    f"Gestionnaire inconnu : {code!r}.", allowed=sorted(known)
                )
            manager = Manager(
                campaign_id=campaign.id,
                code=code,
                label=str(row.get("label") or "").strip(),
                actor=str(row.get("actor") or ""),
                active=bool(row.get("active", True)),
                display_order=int(row.get("display_order") or order),
            )
            if manager.actor:
                clash = seen_actors.get(manager.actor)
                if clash:
                    # Two managers sharing an identity would make "my perimeter"
                    # ambiguous, and the resolution would silently pick one.
                    raise ValidationError(
                        f"L'identité {manager.actor} est affectée à deux "
                        f"gestionnaires ({clash} et {code}).",
                        actor=manager.actor,
                    )
                seen_actors[manager.actor] = code
            managers.append(manager)

        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_managers(managers, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="manager",
                summary=f"Mise à jour de {len(managers)} gestionnaire(s)",
                after={m.code: {"label": m.label, "actor": m.actor} for m in managers},
                conn=conn,
            )
        return self.list_managers(campaign)

    def assign_warehouses(
        self, campaign: Campaign, assignments: Mapping[str, str]
    ) -> int:
        """Attach warehouses — and with them their journals — to managers."""
        ctx = self.ctx
        ctx.guard(campaign, "thresholds")
        known = {m.code for m in self.list_managers(campaign)}
        cleaned: dict[str, str] = {}
        for warehouse, code in assignments.items():
            key = normalise_key(str(warehouse or ""))
            if not key:
                raise ValidationError("Entrepôt vide dans l'affectation.")
            manager_code = normalise_key(str(code or "")).replace(" ", "_")
            if manager_code and manager_code not in known:
                raise ValidationError(
                    f"Gestionnaire inconnu : {manager_code!r}.",
                    allowed=sorted(known),
                )
            cleaned[key] = manager_code

        with ctx.db.transaction() as conn:
            written = ctx.referentials.set_warehouse_assignments(
                campaign.id, cleaned, actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="warehouse_manager",
                summary=f"Affectation de {len(cleaned)} entrepôt(s) à un gestionnaire",
                after=dict(cleaned),
                conn=conn,
            )
        return written

    def assign_zones(
        self, campaign: Campaign, zone_ids: Sequence[str], manager_code: str
    ) -> int:
        """Attach GENERIQUE zones to a manager; an empty code detaches them."""
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        code = normalise_key(str(manager_code or "")).replace(" ", "_")
        known = {m.code for m in self.list_managers(campaign)}
        if code and code not in known:
            raise ValidationError(
                f"Gestionnaire inconnu : {code!r}.", allowed=sorted(known)
            )
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, list(zone_ids), actor=ctx.actor,
                manager_code=code, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) affectée(s) à "
                    f"{code or 'aucun gestionnaire'}"
                ),
                after={
                    "managerCode": code,
                    "zones": sorted(zones[z].code for z in zone_ids),
                },
                conn=conn,
            )
        return updated

    # ------------------------------------------------------------- perimeter

    def perimeter(
        self,
        campaign: Campaign,
        *,
        manager_code: str | None = None,
        zones: Sequence[Any] | None = None,
    ) -> Perimeter:
        """The perimeter of the signed-in user, or of *manager_code*.

        Resolution runs against the **stored** rows first, so a campaign where
        nobody has configured a manager costs exactly one query and returns
        immediately. That matters: this is called on every campaign overview,
        which is rendered on every screen.

        :param manager_code: explicit override. Legitimate — one person often
            covers two slots, and focus is a display filter, not an entitlement
            — but the default is always the identity the platform forwarded,
            because that is the one nobody can spoof.
        :param zones: already-loaded zones, to save a query when the caller has
            them in hand.
        """
        ctx = self.ctx
        stored = ctx.referentials.list_managers(campaign.id)
        if manager_code:
            wanted = normalise_key(manager_code).replace(" ", "_")
            manager = next(
                (m for m in self.list_managers(campaign) if m.code == wanted), None
            )
            if manager is None:
                raise NotFoundError("Gestionnaire introuvable.", code=manager_code)
        else:
            actor = (ctx.actor or "").strip().lower()
            manager = next(
                (m for m in stored if m.actor and m.actor == actor and m.active),
                None,
            )
        if manager is None:
            return Perimeter(manager=None)

        assignments = ctx.referentials.warehouse_assignments(campaign.id)
        explicit = frozenset(
            w for w in assignments if w != CATCH_ALL_WAREHOUSE
        )
        if zones is None:
            zones = ctx.sheets.list_zones(campaign.id)
        return Perimeter(
            manager=manager,
            warehouse_ids=frozenset(
                w for w, code in assignments.items()
                if code == manager.code and w != CATCH_ALL_WAREHOUSE
            ),
            catch_all=assignments.get(CATCH_ALL_WAREHOUSE) == manager.code,
            assigned_warehouses=explicit,
            zone_ids=frozenset(
                z.id for z in zones if z.manager_code == manager.code
            ),
        )

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _owner_of(
        warehouse_id: str, assignments: Mapping[str, str], explicit: set[str]
    ) -> str:
        code = assignments.get(warehouse_id)
        if code:
            return code
        if warehouse_id in explicit:
            return ""
        return assignments.get(CATCH_ALL_WAREHOUSE, "")
