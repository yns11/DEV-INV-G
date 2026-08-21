"""Supprimer des zones : ce qui part avec elles, et ce qui est refusé.

Une zone préparée par erreur — un doublon de feuille, une aire finalement
inventoriée par un prestataire — devait jusqu'ici rester dans la campagne : rien
ne permettait de la retirer. Elle continuait de peser dans le dénominateur
d'avancement et sortait à l'impression.

Trois choses tiennent cette suppression, et une seule qui lâche la rend
dangereuse :

* **les feuilles partent avec la zone**, sans quoi la campagne garde des feuilles
  rattachées à une zone qui n'existe plus — invisibles dans les listes par zone,
  bien présentes dans la liste à plat ;
* **la préparation seulement**, parce qu'au-delà les feuilles portent des
  quantités relevées sur le terrain ;
* **les identifiants sont résolus contre la campagne**, comme pour les lignes de
  feuille : ils viennent d'une requête, et rien d'autre n'empêcherait de
  supprimer la zone du voisin.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access

from inventory.domain.enums import CampaignStatus, SheetPass
from inventory.domain.models import Campaign, CountSheet, Manager, Zone
from inventory.errors import InventoryError, PermissionDeniedError, ValidationError
from inventory.services.generic_service import GenericService

OWNER = "alice@usine"

#: La connexion distribuée par la transaction de test. Objet identifiable : c'est
#: ce qui permet de vérifier que les deux écritures passent bien par la *même*.
CONN = object()


def campaign(status: CampaignStatus = CampaignStatus.PREPARATION) -> Campaign:
    return Campaign(
        id="camp-1",
        code="INV-2026-06",
        label="Inventaire général",
        count_date="2026-06-13",
        status=status,
        created_by=OWNER,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )


def zone(zone_id: str, code: str) -> Zone:
    return Zone(id=zone_id, campaign_id="camp-1", code=code, label=code)


def sheet(sheet_id: str, zone_id: str, pass_no: SheetPass = SheetPass.PASS_1):
    return CountSheet(
        id=sheet_id, campaign_id="camp-1", zone_id=zone_id, pass_no=pass_no
    )


def service(
    *,
    zones: tuple[Zone, ...],
    sheets: tuple[CountSheet, ...] = (),
    actor: str = OWNER,
    managers: tuple[Manager, ...] = (),
) -> tuple[GenericService, dict[str, list]]:
    """Le service, et le journal de ce qu'il a fait."""
    log: dict[str, list] = {"zones": [], "sheets": [], "events": [], "conns": []}

    @contextmanager
    def transaction():
        yield CONN

    def delete_sheets(campaign_id, sheet_ids, *, conn=None):
        log["conns"].append(conn)
        log["sheets"].extend(sheet_ids)
        return len(sheet_ids)

    def delete_zone(zone_id, *, actor, conn=None):
        log["conns"].append(conn)
        log["zones"].append(zone_id)

    ctx = SimpleNamespace(
        actor=actor,
        # Le séquencement est traversé, pas contourné : une campagne qui porte
        # déjà des articles et des zones laisse passer l'aspect « zones ».
        progress=lambda c: SimpleNamespace(
            items=10, zones=len(zones), book_stock_lines=0, book_stock_frozen=False
        ),
        db=SimpleNamespace(transaction=transaction),
        sheets=SimpleNamespace(
            list_zones=lambda cid: list(zones),
            list_sheets=lambda cid: list(sheets),
            delete_sheets=delete_sheets,
            delete_zone=delete_zone,
        ),
        record=lambda **kw: log["events"].append(kw) or "evt",
        forget_progress=lambda cid: None,
    )
    with_access(ctx, managers=managers)
    return GenericService(cast(Any, ctx)), log


class TestTheSheetsGoWithTheZone:
    def test_a_zone_and_its_two_sheets(self):
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),),
            sheets=(
                sheet("s-1", "z-1", SheetPass.PASS_1),
                sheet("s-2", "z-1", SheetPass.PASS_2),
            ),
        )
        assert svc.delete_zones(campaign(), ["z-1"]) == {"zones": 1, "sheets": 2}
        assert log["zones"] == ["z-1"]
        assert sorted(log["sheets"]) == ["s-1", "s-2"]

    def test_another_zone_keeps_its_sheets(self):
        """Le filtre porte sur la zone visée, pas sur la campagne entière."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"), zone("z-2", "ZONE-B")),
            sheets=(sheet("s-1", "z-1"), sheet("s-2", "z-2")),
        )
        svc.delete_zones(campaign(), ["z-1"])
        assert log["sheets"] == ["s-1"]

    def test_a_batch_takes_every_sheet_of_every_zone(self):
        svc, _ = service(
            zones=(zone("z-1", "ZONE-A"), zone("z-2", "ZONE-B")),
            sheets=(
                sheet("s-1", "z-1"),
                sheet("s-2", "z-2", SheetPass.PASS_1),
                sheet("s-3", "z-2", SheetPass.PASS_2),
            ),
        )
        assert svc.delete_zones(campaign(), ["z-1", "z-2"]) == {
            "zones": 2,
            "sheets": 3,
        }

    def test_both_writes_share_one_transaction(self):
        """Une zone retirée sans ses feuilles est exactement le trou à éviter."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),), sheets=(sheet("s-1", "z-1"),)
        )
        svc.delete_zones(campaign(), ["z-1"])
        assert log["conns"] == [CONN, CONN]

    def test_a_zone_without_sheets_is_still_removed(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        assert svc.delete_zones(campaign(), ["z-1"]) == {"zones": 1, "sheets": 0}
        assert log["zones"] == ["z-1"]


class TestPreparationOnly:
    @pytest.mark.parametrize(
        "status",
        [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, CampaignStatus.CLOSED],
    )
    def test_it_is_refused_after_preparation(self, status: CampaignStatus):
        """Refusée par la phase ou par le service, mais refusée."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),), sheets=(sheet("s-1", "z-1"),)
        )
        with pytest.raises(InventoryError):
            svc.delete_zones(campaign(status), ["z-1"])
        assert log["zones"] == [] and log["sheets"] == []

    def test_the_refusal_says_why(self):
        """« Interdit » n'apprend rien ; la raison, si."""
        svc, _ = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(ValidationError) as caught:
            svc.delete_zones(campaign(CampaignStatus.COUNTING), ["z-1"])
        assert "préparation" in str(caught.value).lower()


class TestIdentifiersAreResolvedAgainstTheCampaign:
    def test_an_unknown_zone_stops_the_whole_batch(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(ValidationError) as caught:
            svc.delete_zones(campaign(), ["z-1", "z-99"])
        assert "z-99" in str(caught.value)
        assert log["zones"] == [] and log["sheets"] == []

    def test_an_empty_batch_is_refused_before_anything_else(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        with pytest.raises(ValidationError):
            svc.delete_zones(campaign(), [])
        assert log["zones"] == []

    def test_the_same_zone_twice_counts_once(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        assert svc.delete_zones(campaign(), ["z-1", "z-1"])["zones"] == 1
        assert log["zones"] == ["z-1"]


class TestOnlyTheOwnerAndTheManagersDelete:
    def test_a_reader_cannot(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete_zones(campaign(), ["z-1"])
        assert log["zones"] == []

    def test_a_declared_manager_can(self):
        """Le jour J commence à six heures, et le créateur dort."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),),
            actor="bob@usine",
            managers=(
                Manager(
                    campaign_id="camp-1",
                    code="GESTIONNAIRE_1",
                    label="Bob",
                    actor="bob@usine",
                ),
            ),
        )
        svc.delete_zones(campaign(), ["z-1"])
        assert log["zones"] == ["z-1"]

    def test_a_deactivated_manager_cannot(self):
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),),
            actor="bob@usine",
            managers=(
                Manager(
                    campaign_id="camp-1",
                    code="GESTIONNAIRE_1",
                    label="Bob",
                    actor="bob@usine",
                    active=False,
                ),
            ),
        )
        with pytest.raises(PermissionDeniedError):
            svc.delete_zones(campaign(), ["z-1"])
        assert log["zones"] == []


class TestItLeavesATrace:
    def test_the_codes_are_recorded_not_the_identifiers(self):
        """Un UUID dans le journal d'audit ne se relit pas six mois plus tard."""
        svc, log = service(
            zones=(zone("z-1", "ZONE-A"),), sheets=(sheet("s-1", "z-1"),)
        )
        svc.delete_zones(campaign(), ["z-1"])
        assert len(log["events"]) == 1
        event = log["events"][0]
        assert event["entity_type"] == "zone"
        assert event["before"]["codes"] == ["ZONE-A"]
        assert "1 feuille" in event["summary"]

    def test_the_trace_is_written_in_the_same_transaction(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),))
        svc.delete_zones(campaign(), ["z-1"])
        assert log["events"][0]["conn"] is CONN

    def test_a_refused_deletion_records_nothing(self):
        svc, log = service(zones=(zone("z-1", "ZONE-A"),), actor="bob@usine")
        with pytest.raises(PermissionDeniedError):
            svc.delete_zones(campaign(), ["z-1"])
        assert log["events"] == []
