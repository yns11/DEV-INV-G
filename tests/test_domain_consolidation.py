"""GENERIQUE consolidation — the replacement for ``Compil GENERIQUE.xlsx``."""

from __future__ import annotations

import datetime as dt
import itertools
from decimal import Decimal

from inventory.domain.bom import BomIndex
from inventory.domain.consolidation import (
    ConsolidationInput,
    ZoneCounts,
    build_arbitration_lines,
    consolidate_generic,
    resolve_zone_quantities,
)
from inventory.domain.enums import (
    ControlSeverity,
    CountSection,
    ItemType,
    SheetPass,
    SheetStatus,
)
from inventory.domain.models import (
    ArbitrationLine,
    BomLink,
    CountSheet,
    CountSheetLine,
    Item,
    Zone,
)

_ids = itertools.count(1)
next_id = lambda: f"id-{next(_ids)}"


def item(number: str, item_type: ItemType = ItemType.COMPONENT, **kwargs) -> Item:
    return Item(campaign_id="c", item_number=number, item_type=item_type, **kwargs)


ITEMS = {
    i.item_number: i
    for i in [
        item("MEL", ItemType.FINISHED, std_price="1000"),
        item("STATOR", ItemType.SEMI_FINISHED, std_price="300"),
        item("VIS", std_price="0.5"),
        item("COLLE", std_price="80", unit="KG"),
        item("EMBLG", ItemType.PACKAGING, std_price="2", exclusions=["GENERIC"]),
        item("HORS", std_price="10", exclusions=["ALL"]),
    ]
}

BOM = BomIndex([
    BomLink(campaign_id="c", parent_item="MEL", child_item="STATOR", qty_per="1"),
    BomLink(campaign_id="c", parent_item="MEL", child_item="VIS", qty_per="8"),
])


def sheet(zone_id: str, pass_no: SheetPass, status=SheetStatus.DONE) -> CountSheet:
    return CountSheet(
        id=next_id(), campaign_id="c", zone_id=zone_id, pass_no=pass_no, status=status
    )


def line(sheet_id: str, number: str, section: CountSection, qty) -> CountSheetLine:
    return CountSheetLine(
        id=next_id(),
        sheet_id=sheet_id,
        campaign_id="c",
        item_number=number,
        section=section,
        qty_manual=qty,
    )


def zone_counts(
    *, code="Z1", rows_1, rows_2=None, status=SheetStatus.DONE, arbitrations=(),
    passes=2,
) -> ZoneCounts:
    zone = Zone(id=next_id(), campaign_id="c", code=code, passes=passes)
    s1 = sheet(zone.id, SheetPass.PASS_1, status)
    lines = {s1.id: [line(s1.id, *row) for row in rows_1]}
    sheets = [s1]
    if passes >= 2:
        s2 = sheet(zone.id, SheetPass.PASS_2, status)
        sheets.append(s2)
        lines[s2.id] = [
            line(s2.id, *row) for row in (rows_2 if rows_2 is not None else rows_1)
        ]
    return ZoneCounts(zone=zone, sheets=sheets, lines_by_sheet=lines,
                      arbitrations=arbitrations)


def _decided(zone, qty, *, decided=True) -> ArbitrationLine:
    return ArbitrationLine(
        id="a1", campaign_id="c", zone_id=zone.zone.id, item_number="VIS",
        section=CountSection.LINE_SIDE, qty_pass_1=100, qty_pass_2=90,
        qty_arbitrated=qty,
        decided_at=dt.datetime(2026, 6, 30, tzinfo=dt.UTC) if decided else None,
        decided_by="alice" if decided else None,
    )


def run(*zones, require_done=True):
    return consolidate_generic(
        ConsolidationInput(
            campaign_id="c", zones=list(zones), items=ITEMS, bom=BOM,
            require_done_zones=require_done,
        )
    )


class TestSections:
    def test_line_side_counted_as_is(self):
        result = run(zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)]))
        assert {l.item_number: l.qty for l in result.lines} == {
            "VIS": Decimal("100.000000")
        }

    def test_wip_ok_counted_as_the_assembly_itself(self):
        result = run(zone_counts(rows_1=[("MEL", CountSection.WIP_OK, 2)]))
        line_mel = next(l for l in result.lines if l.item_number == "MEL")
        assert line_mel.qty == Decimal("2.000000")
        assert line_mel.qty_wip_ok == Decimal("2.000000")
        assert line_mel.qty_wip_exploded == 0

    def test_wip_exploded_through_the_bom(self):
        result = run(zone_counts(rows_1=[("MEL", CountSection.WIP, 3)]))
        by_item = {l.item_number: l for l in result.lines}
        assert "MEL" not in by_item  # the assembly itself is not credited
        assert by_item["STATOR"].qty == Decimal("3.000000")
        assert by_item["VIS"].qty == Decimal("24.000000")
        assert by_item["VIS"].qty_wip_exploded == Decimal("24.000000")

    def test_sections_add_up_on_the_same_article(self):
        result = run(
            zone_counts(
                rows_1=[
                    ("VIS", CountSection.LINE_SIDE, 100),
                    ("MEL", CountSection.WIP, 3),
                ]
            )
        )
        vis = next(l for l in result.lines if l.item_number == "VIS")
        assert vis.qty == Decimal("124.000000")
        assert vis.qty_line_side == Decimal("100.000000")
        assert vis.qty_wip_exploded == Decimal("24.000000")

    def test_wip_ok_on_a_component_is_flagged(self):
        result = run(zone_counts(rows_1=[("VIS", CountSection.WIP_OK, 5)]))
        assert any(f.code == "WIP_OK_NOT_ASSEMBLY" for f in result.findings)


class TestBlankIsNotZero:
    def test_uncounted_line_does_not_produce_a_journal_line(self):
        zone = zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, None)])
        result = consolidate_generic(
            ConsolidationInput(campaign_id="c", zones=[zone], items=ITEMS, bom=BOM)
        )
        assert result.lines == []

    def test_an_explicit_zero_is_kept_out_of_the_journal_but_not_an_error(self):
        """Zero means "counted, nothing there" — no line, and no finding either."""
        result = run(zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 0)]))
        assert result.lines == []
        assert [f for f in result.findings if f.severity is ControlSeverity.BLOCKER] == []


class TestExclusions:
    def test_generic_exclusion_drops_the_article_from_the_journal(self):
        result = run(zone_counts(rows_1=[("EMBLG", CountSection.LINE_SIDE, 50)]))
        assert result.lines == []

    def test_full_exclusion_drops_the_article(self):
        result = run(zone_counts(rows_1=[("HORS", CountSection.LINE_SIDE, 7)]))
        assert result.lines == []

    def test_an_excluded_assembly_still_credits_its_in_scope_components(self):
        """Exclusion is applied after explosion, so components are not lost."""
        items = dict(ITEMS)
        items["MEL"] = item("MEL", ItemType.FINISHED, std_price="1000",
                            exclusions=["GENERIC"])
        result = consolidate_generic(
            ConsolidationInput(
                campaign_id="c",
                zones=[zone_counts(rows_1=[("MEL", CountSection.WIP, 2)])],
                items=items,
                bom=BOM,
            )
        )
        assert {l.item_number for l in result.lines} == {"STATOR", "VIS"}


class TestTwoPassResolution:
    def test_agreement_needs_no_arbitration(self):
        zone = zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)])
        retained, findings = resolve_zone_quantities(zone)
        assert retained[("VIS", CountSection.LINE_SIDE)] == Decimal("100.000000")
        assert findings == []

    def test_divergence_blocks_until_arbitrated(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[("VIS", CountSection.LINE_SIDE, 90)],
        )
        retained, findings = resolve_zone_quantities(zone)
        assert ("VIS", CountSection.LINE_SIDE) not in retained
        assert findings[0].code == "ARBITRATION_PENDING"
        assert findings[0].severity is ControlSeverity.BLOCKER

    def test_an_arbitration_decision_wins(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[("VIS", CountSection.LINE_SIDE, 90)],
        )
        zone.arbitrations = [_decided(zone, 95)]
        retained, findings = resolve_zone_quantities(zone)
        assert retained[("VIS", CountSection.LINE_SIDE)] == Decimal("95.000000")
        assert findings == []

    def test_a_pre_filled_quantity_does_not_unblock_the_consolidation(self):
        """Bulk pre-fill saves typing; it does not say anybody looked.

        Treating it as a decision would post forty quantities nobody chose,
        which is exactly the silent automation the arbitration screen exists to
        replace.
        """
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[("VIS", CountSection.LINE_SIDE, 90)],
        )
        zone.arbitrations = [_decided(zone, 90, decided=False)]
        retained, findings = resolve_zone_quantities(zone)
        assert ("VIS", CountSection.LINE_SIDE) not in retained
        assert findings[0].code == "ARBITRATION_PENDING"

    def test_tolerance_accepts_pass_two_without_a_decision(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[("VIS", CountSection.LINE_SIDE, 99)],
        )
        retained, findings = resolve_zone_quantities(
            zone, arbitration_tolerance=Decimal("0.02")
        )
        assert retained[("VIS", CountSection.LINE_SIDE)] == Decimal("99.000000")
        assert findings == []

    def test_single_pass_is_retained_but_warned(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[],
        )
        retained, findings = resolve_zone_quantities(zone)
        assert retained[("VIS", CountSection.LINE_SIDE)] == Decimal("100.000000")
        assert findings[0].code == "SINGLE_PASS_ONLY"
        assert findings[0].severity is ControlSeverity.WARNING

    def test_arbitration_lines_cover_items_present_in_only_one_pass(self):
        """The legacy comparison only saw what both sheets happened to contain."""
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            rows_2=[("COLLE", CountSection.LINE_SIDE, 4)],
        )
        lines = build_arbitration_lines(zone, campaign_id="c", id_factory=next_id)
        assert {l.item_number for l in lines} == {"VIS", "COLLE"}
        vis = next(l for l in lines if l.item_number == "VIS")
        assert vis.qty_pass_1 == Decimal("100.000000")
        assert vis.qty_pass_2 is None


class TestZoneCompleteness:
    def test_unfinished_zones_are_skipped_when_posting(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            status=SheetStatus.ENCODING,
        )
        result = run(zone)
        assert result.zones_skipped == [zone.zone.code]
        assert result.lines == []

    def test_preview_includes_unfinished_zones(self):
        zone = zone_counts(
            rows_1=[("VIS", CountSection.LINE_SIDE, 100)],
            status=SheetStatus.COUNTING,
        )
        result = run(zone, require_done=False)
        assert result.zones_included == [zone.zone.code]
        assert result.lines[0].qty == Decimal("100.000000")


class TestDeterminism:
    def test_same_input_produces_identical_output(self):
        def build():
            return run(
                zone_counts(
                    code="A",
                    rows_1=[("VIS", CountSection.LINE_SIDE, 100),
                            ("MEL", CountSection.WIP, 3)],
                ),
                zone_counts(code="B", rows_1=[("COLLE", CountSection.LINE_SIDE, "4.5")]),
            )

        first, second = build(), build()
        assert [(l.item_number, l.qty) for l in first.lines] == [
            (l.item_number, l.qty) for l in second.lines
        ]

    def test_multiple_zones_are_summed_and_contributors_tracked(self):
        result = run(
            zone_counts(code="A", rows_1=[("VIS", CountSection.LINE_SIDE, 100)]),
            zone_counts(code="B", rows_1=[("VIS", CountSection.LINE_SIDE, 40)]),
        )
        vis = result.lines[0]
        assert vis.qty == Decimal("140.000000")
        assert vis.zone_codes == ["A", "B"]


class TestUnknownItem:
    def test_counted_article_absent_from_the_referential_is_reported(self):
        result = run(zone_counts(rows_1=[("NOUVEAU", CountSection.LINE_SIDE, 12)]))
        assert any(f.code == "UNKNOWN_ITEM" for f in result.findings)
        # …but the quantity is still posted: losing it would be worse.
        assert result.lines[0].item_number == "NOUVEAU"


class TestSinglePassZones:
    """A zone counted once is not a zone missing a count.

    The number of passes is carried by the zone, not by the campaign: a
    metrology room with three references does not need the dispositif a line
    side needs, and telling it that "only one team counted" every single time
    turned the warning into noise nobody reads.
    """

    def test_single_pass_zone_is_resolved_without_a_warning(self):
        zone = zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)], passes=1)
        retained, findings = resolve_zone_quantities(zone)
        assert retained[("VIS", CountSection.LINE_SIDE)] == Decimal("100.000000")
        assert findings == []

    def test_two_pass_zone_still_warns_when_a_pass_is_missing(self):
        zone = zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)], rows_2=[])
        _, findings = resolve_zone_quantities(zone)
        assert [f.code for f in findings] == ["SINGLE_PASS_ONLY"]

    def test_single_pass_zone_contributes_once_its_only_sheet_is_done(self):
        result = run(
            zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)], passes=1)
        )
        assert result.zones_skipped == []
        assert result.lines[0].qty == Decimal("100.000000")

    def test_a_two_pass_zone_with_one_done_sheet_is_still_skipped(self):
        zone = zone_counts(rows_1=[("VIS", CountSection.LINE_SIDE, 100)])
        zone.sheets[1].status = SheetStatus.ENCODING
        result = run(zone)
        assert result.zones_skipped == [zone.zone.code]


def zone_with(*, p1, p2=None, arbitrated=None):
    """Une zone dont l'article A est compté une ou deux fois."""
    rows_2 = None if p2 is None else [("A", CountSection.LINE_SIDE, p2)]
    zone = zone_counts(
        rows_1=[("A", CountSection.LINE_SIDE, p1)],
        rows_2=rows_2,
        passes=1 if p2 is None else 2,
    )
    if arbitrated is not None:
        zone.arbitrations = [ArbitrationLine(
            id="a1", campaign_id="c", zone_id=zone.zone.id, item_number="A",
            section=CountSection.LINE_SIDE, qty_pass_1=p1, qty_pass_2=p2,
            qty_arbitrated=arbitrated,
            decided_at=dt.datetime(2026, 6, 30, tzinfo=dt.UTC), decided_by="alice",
        )]
    return zone


class TestTheProvisionalReading:
    """L'écart doit bouger à chaque saisie, sans attendre l'arbitrage.

    La consolidation *postée* ne devine jamais : une zone dont les deux
    comptages divergent n'a pas de quantité retenue tant que personne n'a
    tranché. Mais l'écart affiché *pendant* le comptage restait alors figé sur
    le stock ERP jusqu'au dernier arbitrage, et une équipe qui venait de
    terminer une zone ne voyait rien bouger.

    Le mode provisoire prend donc la meilleure lecture disponible. La règle
    n'est pas « le dernier gagne » : un comptage n°2 à zéro et une case laissée
    vide se ressemblent sur le papier, et retenir zéro ferait apparaître un
    écart de tout le stock d'une référence sur la foi d'un encodage peut-être
    inachevé.
    """

    def resolve(self, zone, **kwargs):
        retained, _ = resolve_zone_quantities(zone, provisional=True, **kwargs)
        return {key[0]: qty for key, qty in retained.items()}

    def test_the_posted_run_still_refuses_to_choose(self):
        """Le mode par défaut ne change pas : c'est lui qui part à l'ERP."""
        retained, findings = resolve_zone_quantities(zone_with(p1=10, p2=7))
        assert retained == {}
        assert any(f.code == "ARBITRATION_PENDING" for f in findings)

    def test_a_pending_arbitration_takes_the_second_count(self):
        """Le plus tardif, donc le mieux informé."""
        assert self.resolve(zone_with(p1=10, p2=7)) == {"A": Decimal("7")}

    def test_but_a_second_count_at_zero_falls_back_to_the_first(self):
        """Zéro peut vouloir dire « rien trouvé » comme « pas encore saisi »."""
        assert self.resolve(zone_with(p1=10, p2=0)) == {"A": Decimal("10")}

    def test_a_second_count_not_started_leaves_the_first_in_place(self):
        assert self.resolve(zone_with(p1=10)) == {"A": Decimal("10")}

    def test_an_arbitration_already_decided_still_wins(self):
        """Le provisoire ne recouvre jamais une décision humaine."""
        zone = zone_with(p1=10, p2=7, arbitrated=9)
        assert self.resolve(zone) == {"A": Decimal("9")}

    def test_two_counts_that_agree_need_no_rule(self):
        assert self.resolve(zone_with(p1=10, p2=10)) == {"A": Decimal("10")}

    def test_the_finding_is_still_raised(self):
        """Le provisoire affiche une quantité ; il ne fait pas taire l'alerte."""
        _, findings = resolve_zone_quantities(zone_with(p1=10, p2=7), provisional=True)
        assert any(f.code == "ARBITRATION_PENDING" for f in findings)
