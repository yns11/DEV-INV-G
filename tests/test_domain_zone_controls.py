"""Preparation controls on the GENERIQUE zones."""

from __future__ import annotations

import itertools

from inventory.domain.controls import check_zones
from inventory.domain.enums import SheetPass
from inventory.domain.models import CountSheet, CountSheetLine, Zone

_ids = itertools.count(1)
next_id = lambda: f"id-{next(_ids)}"


def zone(**kwargs) -> Zone:
    return Zone(id=next_id(), campaign_id="c", code=kwargs.pop("code", "Z1"), **kwargs)


def sheets_of(z: Zone, *passes: SheetPass) -> list[CountSheet]:
    return [
        CountSheet(id=next_id(), campaign_id="c", zone_id=z.id, pass_no=p)
        for p in passes
    ]


def line(sheet_id: str) -> CountSheetLine:
    return CountSheetLine(
        id=next_id(), sheet_id=sheet_id, campaign_id="c", item_number="P-1"
    )


class TestMissingArticleList:
    def test_a_zone_with_no_pre_printed_line_is_flagged(self):
        z = zone()
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1,
                                                           SheetPass.PASS_2))
        assert [f.code for f in findings] == ["ZONE_WITHOUT_LINES"]

    def test_a_free_entry_zone_is_not(self):
        """A deliberately blank sheet is not an unprepared one."""
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1,
                                                           SheetPass.PASS_2))
        assert findings == []

    def test_a_zone_carrying_lines_is_not(self):
        z = zone()
        sheets = sheets_of(z, SheetPass.PASS_1, SheetPass.PASS_2)
        findings = check_zones(
            zones=[z], sheets=sheets, lines_by_sheet={sheets[0].id: [line(sheets[0].id)]}
        )
        assert findings == []


class TestMissingSheets:
    def test_a_two_pass_zone_missing_its_second_sheet_is_flagged(self):
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1))
        assert [f.code for f in findings] == ["ZONE_MISSING_SHEET"]

    def test_a_single_pass_zone_with_one_sheet_is_complete(self):
        z = zone(passes=1, free_entry=True)
        findings = check_zones(zones=[z], sheets=sheets_of(z, SheetPass.PASS_1))
        assert findings == []

    def test_a_zone_with_no_sheet_at_all_is_flagged(self):
        z = zone(free_entry=True)
        findings = check_zones(zones=[z], sheets=[])
        assert [f.code for f in findings] == ["ZONE_MISSING_SHEET"]
