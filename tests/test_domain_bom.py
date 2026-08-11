"""Bill-of-materials index and explosion."""

from __future__ import annotations

from decimal import Decimal

import pytest

from inventory.domain.bom import BomCycleError, BomIndex
from inventory.domain.models import BomLink


def link(parent: str, child: str, qty: str, *, active: bool = True) -> BomLink:
    return BomLink(campaign_id="c", parent_item=parent, child_item=child,
                   qty_per=qty, active=active)


LINKS = [
    link("MEL", "STATOR", "1"),
    link("MEL", "ROTOR", "1"),
    link("MEL", "VIS", "8"),
    link("STATOR", "CUIVRE", "4.86"),
    link("STATOR", "TOLE", "11"),
    link("ROTOR", "AIMANT", "4"),
]


class TestIndex:
    def test_counts_distinct_edges(self):
        assert len(BomIndex(LINKS)) == 6
        assert BomIndex(LINKS).parents == frozenset({"MEL", "STATOR", "ROTOR"})

    def test_merges_duplicate_edges_instead_of_double_counting(self):
        """The ERP export repeats a pair when several BOM versions are effective."""
        index = BomIndex([link("A", "B", "2"), link("A", "B", "3")])
        assert index.direct_children("A") == [("B", Decimal("5.000000"))]

    def test_drops_excluded_children(self):
        index = BomIndex(LINKS, excluded_children={"VIS"})
        assert "VIS" not in index.unit_explosion("MEL")
        assert index.unit_explosion("MEL")["STATOR"] == Decimal("1.000000")

    def test_reports_orphan_parents(self):
        index = BomIndex(LINKS)
        assert index.orphan_parents(["MEL", "INCONNU"]) == {"INCONNU"}


class TestExplosion:
    def test_stops_at_the_first_stock_carrying_item(self):
        """Default behaviour is single level: a stator is stock, not raw copper."""
        assert BomIndex(LINKS).unit_explosion("MEL") == {
            "STATOR": Decimal("1.000000"),
            "ROTOR": Decimal("1.000000"),
            "VIS": Decimal("8.000000"),
        }

    def test_expands_through_phantom_levels(self):
        index = BomIndex(LINKS, is_phantom=lambda item: item in {"STATOR", "ROTOR"})
        assert index.unit_explosion("MEL") == {
            "CUIVRE": Decimal("4.860000"),
            "TOLE": Decimal("11.000000"),
            "AIMANT": Decimal("4.000000"),
            "VIS": Decimal("8.000000"),
        }

    def test_multiplies_by_the_counted_quantity(self):
        result = BomIndex(LINKS).explode({"MEL": Decimal("3")})
        assert result.components["VIS"] == Decimal("24.000000")
        assert result.components["STATOR"] == Decimal("3.000000")

    def test_keeps_the_breakdown_for_drill_down(self):
        result = BomIndex(LINKS).explode({"MEL": Decimal("3")}, zone_code="ASSY")
        rows = {(b.parent_item, b.child_item): b for b in result.breakdown}
        assert rows[("MEL", "VIS")].child_qty == Decimal("24.000000")
        assert rows[("MEL", "VIS")].qty_per_parent == Decimal("8.000000")
        assert rows[("MEL", "VIS")].zone_code == "ASSY"

    def test_reports_an_assembly_with_no_bom_instead_of_dropping_it(self):
        """The legacy inner join made the quantity vanish. This one says so."""
        result = BomIndex(LINKS).explode({"SANS_BOM": Decimal("5")})
        assert result.unknown_parents == {"SANS_BOM"}
        assert result.components == {}

    def test_ignores_zero_quantities(self):
        assert BomIndex(LINKS).explode({"MEL": Decimal("0")}).components == {}

    def test_diamond_structure_sums_both_paths(self):
        links = [
            link("TOP", "A", "1"),
            link("TOP", "B", "1"),
            link("A", "SHARED", "2"),
            link("B", "SHARED", "3"),
        ]
        index = BomIndex(links, is_phantom=lambda item: item in {"A", "B"})
        assert index.unit_explosion("TOP") == {"SHARED": Decimal("5.000000")}


class TestCycles:
    def test_detects_a_two_node_cycle(self):
        index = BomIndex([link("A", "B", "1"), link("B", "A", "1")])
        assert index.find_cycles() == [["A", "B", "A"]]

    def test_detects_a_longer_cycle(self):
        index = BomIndex([link("A", "B", "1"), link("B", "C", "1"), link("C", "A", "1")])
        cycles = index.find_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"A", "B", "C"}

    def test_no_cycle_on_a_diamond(self):
        links = [
            link("TOP", "A", "1"),
            link("TOP", "B", "1"),
            link("A", "LEAF", "1"),
            link("B", "LEAF", "1"),
        ]
        assert BomIndex(links).find_cycles() == []

    def test_explosion_raises_on_a_reachable_cycle(self):
        index = BomIndex(
            [link("A", "B", "1"), link("B", "A", "1")],
            is_phantom=lambda _item: True,
        )
        with pytest.raises(BomCycleError) as excinfo:
            index.unit_explosion("A")
        assert "A" in excinfo.value.cycle

    def test_self_reference_is_rejected_at_the_model(self):
        with pytest.raises(ValueError, match="one-node cycle"):
            link("A", "A", "1")


class TestDepthGuard:
    def test_truncates_a_deep_phantom_chain_without_losing_quantity(self):
        chain = [link(f"L{i}", f"L{i + 1}", "2") for i in range(8)]
        index = BomIndex(chain, is_phantom=lambda _item: True, max_depth=3)
        result = index.explode({"L0": Decimal("1")})
        assert result.truncated_parents == {"L0"}
        # The quantity stops at the depth limit but is credited, never dropped.
        assert sum(result.components.values()) > 0


class TestRetiredVersions:
    """L'ERP garde toutes les versions d'une recette ; une seule est en vigueur.

    Elles arrivent toutes dans la campagne, et c'est voulu : un assemblage dont
    la recette a été retirée *a* une structure, et le signaler comme n'en ayant
    aucune produisait une page d'alertes sur lesquelles personne ne pouvait
    agir. Mais l'éclatement, lui, ne prend que l'actif — additionner une
    quantité retirée à une quantité en vigueur ferait apparaître des composants
    que l'assemblage ne contient plus.
    """

    def test_a_retired_link_is_not_exploded(self):
        index = BomIndex([
            link("A", "X", "2"),
            link("A", "Y", "5", active=False),
        ])
        assert index.explode({"A": Decimal("1")}).components == {
            "X": Decimal("2.000000")
        }

    def test_a_retired_version_never_adds_to_the_live_one(self):
        """Le pire cas : le même couple dans deux versions.

        L'index fusionne les doublons — c'est ce qu'il faut pour deux versions
        en vigueur — et c'est exactement ce qui gonflerait le comptage si une
        version retirée entrait dans la somme.
        """
        index = BomIndex([
            link("A", "X", "2"),
            link("A", "X", "30", active=False),
        ])
        assert index.explode({"A": Decimal("1")}).components == {
            "X": Decimal("2.000000")
        }

    def test_an_assembly_with_only_retired_versions_cannot_be_exploded(self):
        index = BomIndex([link("A", "X", "2", active=False)])
        result = index.explode({"A": Decimal("1")})
        assert result.components == {}
        # Signalé comme non éclatable — la quantité ne disparaît pas en silence.
        assert result.unknown_parents == {"A"}
        assert not index.has_bom("A")

    def test_but_it_is_not_reported_as_having_no_structure(self):
        """La distinction que le référentiel réclamait."""
        index = BomIndex([link("A", "X", "2", active=False)])
        assert index.has_any_version("A")
        assert index.retired_only("A")

    def test_an_assembly_the_erp_ignores_entirely_is_a_different_case(self):
        index = BomIndex([link("A", "X", "2")])
        assert not index.has_any_version("B")
        assert not index.retired_only("B")

    def test_a_live_assembly_is_neither(self):
        index = BomIndex([link("A", "X", "2")])
        assert index.has_bom("A") and index.has_any_version("A")
        assert not index.retired_only("A")

    def test_a_link_is_in_force_unless_stated_otherwise(self):
        """Une campagne chargée avant ce changement n'avait que de l'actif."""
        assert BomIndex([link("A", "X", "2")]).has_bom("A")
