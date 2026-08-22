"""Terminer une zone : la seule décision d'état du parcours de comptage.

Une feuille de comptage avait quatre états — en attente, comptage en cours,
encodage en cours, terminée — qu'il fallait faire avancer à la main, une par
une, deux fois par zone. Aucune écriture n'en dépendait : le papier partait au
comptage que le bouton ait été cliqué ou non, et les quantités s'enregistraient
dans tous les cas. Quatre clics par zone pour tenir à jour une donnée dont la
seule lecture était « cette zone est-elle finie ? ».

Cette question-là reste, elle appartient à la zone, et elle se pose une fois.
Ces contrôles portent sur ce qui l'entoure : ce qui la refuse, ce qui ne la
refuse jamais, et ce que la trace en garde.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import ArbitrationLine, Campaign, Zone
from inventory.errors import NotFoundError, PermissionDeniedError, WorkflowError
from inventory.services.generic_service import GenericService

CLOSED_AT = dt.datetime(2026, 8, 31, 17, 0, tzinfo=dt.UTC)


def campaign(status: CampaignStatus = CampaignStatus.COUNTING) -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026-08", label="Inventaire",
        count_date="2026-08-31", status=status,
        created_by="chef@usine", created_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        book_stock_frozen_at=dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
    )


def arbitration(*, resolved: bool, gap: bool = True) -> ArbitrationLine:
    return ArbitrationLine(
        id="a1", campaign_id="camp-1", zone_id="z-1", item_number="MASS-1",
        section="LINE_SIDE",
        qty_pass_1=100, qty_pass_2=90 if gap else 100,
        qty_arbitrated=95 if resolved else None,
        decided_at=CLOSED_AT if resolved else None,
        decided_by="chef@usine" if resolved else None,
    )


def service(
    *,
    closed: bool = False,
    arbitrations: tuple[ArbitrationLine, ...] = (),
    actor: str = "chef@usine",
    zone_id: str = "z-1",
):
    """Le service, sa zone, et ce que l'écriture a réellement enregistré."""
    zone = Zone(
        id="z-1", campaign_id="camp-1", code="FI ASSY", passes=2,
        closed_at=CLOSED_AT if closed else None,
    )
    written: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    refreshed: list[str] = []

    sheets = SimpleNamespace(
        list_zones=lambda cid: [zone],
        list_arbitrations=lambda cid: list(arbitrations),
        set_zone_closed=lambda cid, zid, *, closed, actor, conn=None: written.append(
            {"campaign": cid, "zone": zid, "closed": closed, "actor": actor}
        ),
    )
    ctx = SimpleNamespace(
        actor=actor,
        request_id="req-1",
        sheets=sheets,
        record=lambda **kw: events.append(kw) or "evt",
        progress=lambda c: SimpleNamespace(
            items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
        ),
    )
    with_transactions(ctx)
    with_access(ctx)
    generic = GenericService(cast(Any, ctx))
    # L'arbitrage est recalculé avant toute clôture ; ici on note l'appel
    # plutôt que de refaire tourner la consolidation.
    generic.refresh_arbitrations = (  # type: ignore[method-assign]
        lambda campaign, zid: refreshed.append(zid)
    )
    return generic, written, events, refreshed, zone_id


class TestClosingAZone:
    def test_a_clean_zone_closes(self):
        generic, written, _, _, zid = service()
        out = generic.set_zone_closed(campaign(), zid, closed=True)
        assert out == {"id": "z-1", "closed": True}
        assert written == [
            {"campaign": "camp-1", "zone": "z-1", "closed": True, "actor": "chef@usine"}
        ]

    def test_an_open_discrepancy_refuses_it(self):
        """Fermer sur un écart non tranché promettrait à la consolidation une
        quantité qui n'existe pas encore."""
        generic, written, _, _, zid = service(
            arbitrations=(arbitration(resolved=False),)
        )
        with pytest.raises(WorkflowError, match="écart"):
            generic.set_zone_closed(campaign(), zid, closed=True)
        assert written == []

    def test_a_settled_discrepancy_does_not(self):
        generic, written, _, _, zid = service(
            arbitrations=(arbitration(resolved=True),)
        )
        generic.set_zone_closed(campaign(), zid, closed=True)
        assert written and written[0]["closed"] is True

    def test_two_identical_counts_are_no_discrepancy_at_all(self):
        generic, written, _, _, zid = service(
            arbitrations=(arbitration(resolved=False, gap=False),)
        )
        generic.set_zone_closed(campaign(), zid, closed=True)
        assert written and written[0]["closed"] is True

    def test_the_arbitration_list_is_refreshed_before_deciding(self):
        """Sans ce rafraîchissement, la clôture se prononce sur une liste
        d'écarts d'avant la dernière saisie — et refuse un écart déjà tranché,
        ou accepte un écart que la frappe précédente vient de créer."""
        generic, _, _, refreshed, zid = service()
        generic.set_zone_closed(campaign(), zid, closed=True)
        assert refreshed == ["z-1"]


class TestReopeningAZone:
    def test_reopening_is_never_refused(self):
        """C'est le geste qui répare une clôture trop rapide : le bloquer sur
        un écart enfermerait la zone dans l'état qu'on cherche à corriger."""
        generic, written, _, _, zid = service(
            closed=True, arbitrations=(arbitration(resolved=False),)
        )
        generic.set_zone_closed(campaign(), zid, closed=False)
        assert written == [
            {"campaign": "camp-1", "zone": "z-1", "closed": False, "actor": "chef@usine"}
        ]

    def test_reopening_does_not_recompute_the_arbitrations(self):
        generic, _, _, refreshed, zid = service(closed=True)
        generic.set_zone_closed(campaign(), zid, closed=False)
        assert refreshed == []


class TestWhatIsRefusedBeforeAnythingIsWritten:
    def test_a_zone_of_another_campaign_is_a_404(self):
        generic, written, _, _, _ = service()
        with pytest.raises(NotFoundError):
            generic.set_zone_closed(campaign(), "z-VOISINE", closed=True)
        assert written == []

    def test_a_reader_may_not_close(self):
        generic, written, _, _, zid = service(actor="tiers@usine")
        with pytest.raises(PermissionDeniedError):
            generic.set_zone_closed(campaign(), zid, closed=True)
        assert written == []

    def test_a_frozen_phase_refuses_it(self):
        from inventory.errors import InventoryError

        generic, written, _, _, zid = service()
        with pytest.raises(InventoryError):
            generic.set_zone_closed(campaign(CampaignStatus.CLOSED), zid, closed=True)
        assert written == []


class TestTheTrace:
    def test_closing_is_recorded_against_the_zone(self):
        generic, _, events, _, zid = service()
        generic.set_zone_closed(campaign(), zid, closed=True)
        assert events[0]["entity_type"] == "zone"
        assert events[0]["entity_id"] == "z-1"
        assert "terminée" in events[0]["summary"]

    def test_reopening_says_so(self):
        generic, _, events, _, zid = service(closed=True)
        generic.set_zone_closed(campaign(), zid, closed=False)
        assert "rouverte" in events[0]["summary"]
        assert events[0]["before"] == {"closed": True}
        assert events[0]["after"] == {"closed": False}
