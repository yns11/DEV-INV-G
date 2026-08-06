"""Manager perimeters: what the focus mode keeps, and what it must never do.

The resolution rules are pure — they take assignments and answer "is this
journal mine?" — so they are tested here without a database, like the rest of
the domain.
"""

from __future__ import annotations

from inventory.domain.models import Manager
from inventory.services.manager_service import CATCH_ALL_WAREHOUSE, Perimeter


def perimeter(
    *, code="GESTIONNAIRE_1", warehouses=(), catch_all=False, assigned=(), zones=()
) -> Perimeter:
    return Perimeter(
        manager=Manager(campaign_id="c", code=code, label=code),
        warehouse_ids=frozenset(warehouses),
        catch_all=catch_all,
        assigned_warehouses=frozenset(assigned),
        zone_ids=frozenset(zones),
    )


class TestWarehouseResolution:
    def test_an_explicitly_assigned_warehouse_is_covered(self):
        assert perimeter(warehouses=["B06"]).covers_warehouse("B06")

    def test_a_warehouse_assigned_to_somebody_else_is_not(self):
        assert not perimeter(warehouses=["B06"]).covers_warehouse("QUAL")

    def test_the_catch_all_covers_whatever_nobody_claimed(self):
        """Otherwise a warehouse discovered by a new book stock belongs to nobody."""
        own = perimeter(
            warehouses=["B06"], catch_all=True, assigned=["B06", "QUAL"]
        )
        assert own.covers_warehouse("NOUVEAU")

    def test_the_catch_all_does_not_steal_an_explicit_assignment(self):
        own = perimeter(
            warehouses=["B06"], catch_all=True, assigned=["B06", "QUAL"]
        )
        assert not own.covers_warehouse("QUAL")

    def test_the_catch_all_key_is_never_treated_as_a_warehouse(self):
        own = perimeter(warehouses=["B06"], catch_all=True, assigned=["B06"])
        assert CATCH_ALL_WAREHOUSE not in own.warehouse_ids


class TestZoneResolution:
    def test_only_assigned_zones_are_covered(self):
        own = perimeter(zones=["z1"])
        assert own.covers_zone("z1")
        assert not own.covers_zone("z2")

    def test_the_warehouse_catch_all_does_not_leak_into_zones(self):
        """A zone is assigned by name or not at all — there is no zone fallback."""
        own = perimeter(catch_all=True, zones=[])
        assert not own.covers_zone("z1")


class TestUnresolvedIdentity:
    def test_an_unknown_user_has_no_perimeter_rather_than_the_whole_site(self):
        """Falling back to "everything" would make the switch a silent no-op."""
        nobody = Perimeter(manager=None)
        assert not nobody.resolved
        assert nobody.is_empty
        assert not nobody.covers_warehouse("B06")
        assert not nobody.covers_zone("z1")

    def test_the_payload_says_it_is_unresolved(self):
        payload = Perimeter(manager=None).as_dict()
        assert payload["resolved"] is False
        assert payload["managerCode"] is None
