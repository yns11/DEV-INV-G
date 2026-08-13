"""Supprimer des lignes de feuille : ce qui est refusé, et comment.

En production, `POST /generic/lines/delete` a reçu l'identifiant « 11 ». Ce
n'était pas un identifiant : c'était l'indice d'une ligne que l'écran venait
d'ajouter et qui n'existait nulle part ailleurs que dans le navigateur. Postgres
l'a refusé — « invalid input syntax for type uuid » — six couches plus bas, en
500, sans que l'utilisateur apprenne quoi que ce soit.

Le service résout donc les identifiants contre les lignes *de cette campagne*
avant d'écrire quoi que ce soit. Cela couvre deux choses d'un coup : ce qui n'est
pas un identifiant, et ce qui en est un mais appartient à quelqu'un d'autre.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import CampaignStatus, SheetPass
from inventory.domain.models import CountSheetLine
from inventory.errors import ValidationError
from inventory.services.generic_service import GenericService

CAMPAIGN = cast(Any, SimpleNamespace(id="camp-1", status=CampaignStatus.PREPARATION))


def line(line_id: str, *, sheet_id: str = "sheet-1") -> CountSheetLine:
    return CountSheetLine(
        id=line_id, sheet_id=sheet_id, campaign_id="camp-1", item_number="P-1"
    )


def service(*lines: CountSheetLine) -> tuple[GenericService, list[str]]:
    deleted: list[str] = []
    by_sheet: dict[str, list[CountSheetLine]] = {}
    for item in lines:
        by_sheet.setdefault(item.sheet_id, []).append(item)

    sheets = SimpleNamespace(
        lines_by_sheet=lambda cid: by_sheet,
        delete_sheet_line=lambda line_id, *, actor: deleted.append(line_id),
    )
    ctx = SimpleNamespace(
        actor="testeur",
        guard=lambda campaign, aspect: None,
        sheets=sheets,
        record=lambda **kw: "evt",
    )
    return GenericService(cast(Any, ctx)), deleted


class TestAnIdentifierThatIsNotOne:
    def test_a_row_index_is_refused_rather_than_handed_to_the_driver(self):
        """Le cas exact vu en production : « 11 » au lieu d'un UUID."""
        generic, deleted = service(line("a1b2c3d4-0000-0000-0000-000000000001"))
        with pytest.raises(ValidationError) as caught:
            generic.delete_sheet_lines(CAMPAIGN, ["11"])

        assert "11" in str(caught.value)
        assert deleted == []

    def test_the_refusal_says_what_to_do(self):
        generic, _ = service(line("a1b2c3d4-0000-0000-0000-000000000001"))
        with pytest.raises(ValidationError) as caught:
            generic.delete_sheet_lines(CAMPAIGN, ["11"])
        assert "Rechargez" in str(caught.value)

    def test_one_bad_identifier_stops_the_whole_batch(self):
        """Sinon la moitié disparaît et l'écran affiche quand même une erreur."""
        good = "a1b2c3d4-0000-0000-0000-000000000001"
        generic, deleted = service(line(good))
        with pytest.raises(ValidationError):
            generic.delete_sheet_lines(CAMPAIGN, [good, "11"])
        assert deleted == []


class TestALineFromAnotherCampaign:
    def test_it_is_not_deleted(self):
        """La suppression portait sur l'identifiant seul, sans regarder à qui
        il appartenait."""
        generic, deleted = service(line("a1b2c3d4-0000-0000-0000-000000000001"))
        with pytest.raises(ValidationError):
            generic.delete_sheet_lines(
                CAMPAIGN, ["b9999999-0000-0000-0000-000000000009"]
            )
        assert deleted == []


class TestWhatDoesWork:
    def test_a_known_line_is_deleted(self):
        line_id = "a1b2c3d4-0000-0000-0000-000000000001"
        generic, deleted = service(line(line_id))
        assert generic.delete_sheet_lines(CAMPAIGN, [line_id]) == 1
        assert deleted == [line_id]

    def test_lines_spread_over_several_sheets_go_together(self):
        first = "a1b2c3d4-0000-0000-0000-000000000001"
        second = "a1b2c3d4-0000-0000-0000-000000000002"
        generic, deleted = service(line(first), line(second, sheet_id="sheet-2"))
        assert generic.delete_sheet_lines(CAMPAIGN, [first, second]) == 2
        assert sorted(deleted) == [first, second]

    def test_the_same_line_twice_counts_once(self):
        line_id = "a1b2c3d4-0000-0000-0000-000000000001"
        generic, deleted = service(line(line_id))
        assert generic.delete_sheet_lines(CAMPAIGN, [line_id, line_id]) == 1
        assert deleted == [line_id]

    def test_an_empty_batch_is_refused_before_anything_else(self):
        generic, deleted = service(line("a1b2c3d4-0000-0000-0000-000000000001"))
        with pytest.raises(ValidationError):
            generic.delete_sheet_lines(CAMPAIGN, [])
        assert deleted == []


class TestTheSingleDeleteTakesTheSamePath:
    """Elle avait le même trou : un identifiant, et rien d'autre à vérifier."""

    def test_an_unknown_line_is_refused(self):
        generic, deleted = service(line("a1b2c3d4-0000-0000-0000-000000000001"))
        with pytest.raises(ValidationError):
            generic.delete_sheet_line(CAMPAIGN, "11")
        assert deleted == []

    def test_a_known_line_is_deleted(self):
        line_id = "a1b2c3d4-0000-0000-0000-000000000001"
        generic, deleted = service(line(line_id))
        generic.delete_sheet_line(CAMPAIGN, line_id)
        assert deleted == [line_id]


class TestThePassIsIrrelevantHere:
    """Une ligne se supprime par son identifiant, pas par sa feuille."""

    def test_both_passes_are_reachable(self):
        first = "a1b2c3d4-0000-0000-0000-000000000001"
        second = "a1b2c3d4-0000-0000-0000-000000000002"
        generic, _ = service(
            line(first, sheet_id=f"sheet-{SheetPass.PASS_1}"),
            line(second, sheet_id=f"sheet-{SheetPass.PASS_2}"),
        )
        assert generic.delete_sheet_lines(CAMPAIGN, [first, second]) == 2
