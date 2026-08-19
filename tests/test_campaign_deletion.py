"""Supprimer une campagne : qui le peut, et ce qui reste après.

Une campagne est le travail de quelqu'un. Elle disparaissait donc de deux
manières impossibles à défendre : pas du tout — la liste accumulait les essais,
les doublons et les campagnes créées par erreur —, ou physiquement, ce qui
emporterait avec elle les comptages, les journaux et la piste d'audit d'un
inventaire que quelqu'un a signé.

D'où deux règles, et ces tests ne vérifient rien d'autre : seul l'auteur
supprime, et la suppression est logique.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign
from inventory.errors import PermissionDeniedError
from inventory.services.campaign_service import CampaignService


def campaign(*, created_by: str = "alice@usine") -> Campaign:
    return Campaign(
        id="camp-1",
        code="INV-2026-06",
        label="Inventaire général",
        count_date="2026-06-13",
        status=CampaignStatus.ANALYSIS,
        created_by=created_by,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


def service(
    stored: Campaign, *, actor: str
) -> tuple[CampaignService, list[str], list[dict[str, Any]]]:
    """Le service, la liste des suppressions posées et celle des événements."""
    deleted: list[str] = []
    events: list[dict[str, Any]] = []

    @contextmanager
    def transaction():
        yield None

    ctx = SimpleNamespace(
        actor=actor,
        db=SimpleNamespace(transaction=transaction),
        campaigns=SimpleNamespace(
            get=lambda cid: stored,
            soft_delete=lambda cid, *, actor, conn=None: deleted.append(cid),
        ),
        record=lambda **kw: events.append(kw) or "evt",
    )
    return CampaignService(cast(Any, ctx)), deleted, events


class TestOnlyTheAuthorDeletes:
    def test_the_author_can(self):
        svc, deleted, _ = service(campaign(), actor="alice@usine")
        svc.delete("camp-1")
        assert deleted == ["camp-1"]

    def test_somebody_else_cannot(self):
        """403, et non un silence : une action refusée doit se voir."""
        svc, deleted, _ = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete("camp-1")
        assert deleted == []
        assert caught.value.status_code == 403

    def test_the_refusal_names_the_owner(self):
        """« Vous n'avez pas le droit » n'aide personne à savoir à qui demander."""
        svc, _, _ = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError) as caught:
            svc.delete("camp-1")
        assert "alice@usine" in str(caught.value)

    def test_an_ownerless_campaign_is_not_up_for_grabs(self):
        """Champ vide en base : cela ne fait de personne son auteur."""
        svc, deleted, _ = service(campaign(created_by=""), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete("camp-1")
        assert deleted == []


class TestItLeavesATrace:
    def test_the_deletion_is_recorded_before_it_happens(self):
        """Écrit dans la même transaction : l'un sans l'autre serait un trou."""
        svc, _, events = service(campaign(), actor="alice@usine")
        svc.delete("camp-1")
        assert len(events) == 1
        assert events[0]["entity_type"] == "campaign"
        assert events[0]["before"]["code"] == "INV-2026-06"

    def test_a_refused_deletion_records_nothing(self):
        svc, _, events = service(campaign(), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete("camp-1")
        assert events == []
