"""Le jour J, qui doit faire quoi.

L'écran de campagne donnait des barres de progression : « 62 % des zones »,
« 8 journaux sur 14 ». Un pourcentage répond à « où en est-on », jamais à « que
faire maintenant ». Le matin d'un inventaire, un responsable de secteur pose
trois questions, et aucune n'a de réponse dans un pourcentage : qu'est-ce qui
attend **une décision de moi**, qu'est-ce que je peux **fermer tout de suite**,
qui **n'a pas commencé** ?

Deux propriétés font la différence entre un tableau de bord et un tableau de
commandement, et ce sont celles que ce module vérifie le plus.

**Une file est nommée, pas seulement comptée.** « 3 zones à arbitrer » oblige à
ouvrir un écran pour savoir lesquelles ; « Z04, Z07, Z12 » permet d'y aller.

**Une file vide disparaît.** Six cases dont quatre à zéro se lisent comme un
décor ; les deux qui appellent quelqu'un se lisent.

Le classement des zones est l'endroit délicat. Une zone dont les deux comptages
se contredisent n'est **pas** « prête à fermer » même si tout est relevé : la
consolidation ne saurait pas quelle quantité retenir. L'arbitrage passe donc
avant, et un contrôle en fait la démonstration sur une zone entièrement comptée.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from inventory.domain.queues import NAMES_SHOWN, work_queues

ROOT = Path(__file__).resolve().parent.parent


def by_code(queues) -> dict[str, Any]:
    return {q.code: q for q in queues}


# --------------------------------------------------------------------------- #
# La composition des files
# --------------------------------------------------------------------------- #

class TestAQueueIsNamedNotJustCounted:
    def test_the_names_come_back(self):
        """« 3 zones à arbitrer » oblige à ouvrir un écran pour savoir
        lesquelles ; les noms permettent d'y aller."""
        queues = by_code(work_queues(zones_to_arbitrate=["Z04", "Z07", "Z12"]))
        assert queues["ZONES_TO_ARBITRATE"].names == ("Z04", "Z07", "Z12")

    def test_they_are_sorted(self):
        """L'ordre d'arrivée en base n'a aucun sens pour qui lit ; l'ordre
        alphabétique en a un, et il est stable d'un rafraîchissement à l'autre."""
        queues = by_code(work_queues(zones_to_arbitrate=["Z12", "Z04", "Z07"]))
        assert queues["ZONES_TO_ARBITRATE"].names == ("Z04", "Z07", "Z12")

    def test_a_long_queue_is_truncated(self):
        """Deux cents codes ne se lisent pas davantage qu'un nombre."""
        queues = by_code(
            work_queues(zones_not_started=[f"Z{n:03d}" for n in range(40)])
        )
        assert len(queues["ZONES_NOT_STARTED"].names) == NAMES_SHOWN

    def test_the_count_is_the_whole_queue(self):
        """Le nombre porte sur la file, pas sur les noms montrés : les
        confondre ferait dire « 12 » d'une file de quarante."""
        queues = by_code(
            work_queues(zones_not_started=[f"Z{n:03d}" for n in range(40)])
        )
        assert queues["ZONES_NOT_STARTED"].count == 40

    def test_it_says_how_many_it_is_not_showing(self):
        queues = by_code(
            work_queues(zones_not_started=[f"Z{n:03d}" for n in range(40)])
        )
        assert queues["ZONES_NOT_STARTED"].hidden == 40 - NAMES_SHOWN

    def test_a_short_queue_hides_nothing(self):
        queues = by_code(work_queues(zones_to_arbitrate=["Z01"]))
        assert queues["ZONES_TO_ARBITRATE"].hidden == 0


class TestAnEmptyQueueDisappears:
    def test_nothing_waiting_gives_no_queue_at_all(self):
        assert work_queues() == []

    def test_only_the_queues_with_work_are_returned(self):
        queues = work_queues(zones_to_arbitrate=["Z01"])
        assert [q.code for q in queues] == ["ZONES_TO_ARBITRATE"]

    def test_the_controls_can_still_see_the_whole_shape(self):
        """``include_empty`` existe pour les contrôles, pas pour l'écran."""
        assert len(work_queues(include_empty=True)) == 6


class TestTheOrderIsTheOrderOfAction:
    def test_what_awaits_a_decision_comes_first(self):
        queues = work_queues(
            zones_not_started=["Z09"],
            zones_to_arbitrate=["Z01"],
            zones_ready_to_close=["Z05"],
        )
        assert queues[0].code == "ZONES_TO_ARBITRATE"

    def test_what_can_be_closed_comes_before_what_is_running(self):
        queues = work_queues(zones_in_progress=["Z03"], zones_ready_to_close=["Z05"])
        assert [q.code for q in queues] == [
            "ZONES_READY_TO_CLOSE",
            "ZONES_IN_PROGRESS",
        ]

    def test_what_has_not_started_comes_last(self):
        """C'est la file sur laquelle le responsable ne peut rien faire
        lui-même : la mettre en tête serait trier par phase, pas par action."""
        queues = work_queues(
            zones_not_started=["Z09"], zones_in_progress=["Z03"]
        )
        assert queues[-1].code == "ZONES_NOT_STARTED"


class TestEachQueueSaysWhatToDo:
    @pytest.mark.parametrize(
        "kwargs,code",
        [
            ({"zones_to_arbitrate": ["Z1"]}, "ZONES_TO_ARBITRATE"),
            ({"zones_ready_to_close": ["Z1"]}, "ZONES_READY_TO_CLOSE"),
            ({"zones_in_progress": ["Z1"]}, "ZONES_IN_PROGRESS"),
            ({"zones_not_started": ["Z1"]}, "ZONES_NOT_STARTED"),
            ({"journals_in_progress": ["B06 / L1"]}, "JOURNALS_IN_PROGRESS"),
            ({"journals_not_started": ["B06 / L1"]}, "JOURNALS_NOT_STARTED"),
        ],
    )
    def test_it_carries_a_sentence_and_a_screen(self, kwargs, code):
        """« Arbitrages » ne dit pas qu'il faut trancher ; le libellé nomme la
        file, la phrase dit le geste."""
        queue = by_code(work_queues(**kwargs))[code]
        assert queue.action
        assert queue.where

    def test_the_screens_named_are_real_routes(self):
        """Un fragment inventé donne un lien vers « Page introuvable ».

        Ce contrôle a déjà servi : la file des arbitrages pointait sur
        « arbitrage », qui n'est pas une route mais une sous-section de
        « compil ».
        """
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
        for queue in work_queues(include_empty=True):
            route = queue.where.split("?")[0]
            assert f'path="{route}"' in app, queue.where

    def test_a_sub_section_is_addressed_by_the_parameter_the_screens_read(self):
        """`?vue=` est ce que `useSubSection` lit ; tout autre nom ouvrirait
        l'onglet par défaut sans que rien ne le signale."""
        from inventory.domain.queues import work_queues as build

        generic = (ROOT / "frontend" / "src" / "features" / "Generic.tsx").read_text()
        for queue in build(include_empty=True):
            if "?" not in queue.where:
                continue
            param, _, value = queue.where.partition("?")[2].partition("=")
            assert param == "vue", queue.where
            assert f"'{value}'" in generic, value


# --------------------------------------------------------------------------- #
# Le classement des zones, là où c'est délicat
# --------------------------------------------------------------------------- #

def zone(code: str, *, closed: bool = False) -> Any:
    return SimpleNamespace(
        id=f"id-{code}",
        code=code,
        closed_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC) if closed else None,
    )


def line(counted: bool) -> Any:
    return SimpleNamespace(is_counted=counted)


def board(
    *,
    zones=(),
    lines_by_zone=None,
    arbitrations=(),
    journals=(),
    perimeter=None,
):
    """Le service, sur un contexte qui ne porte que ce que le tableau consulte."""
    from inventory.services.board_service import BoardService

    lines_by_zone = lines_by_zone or {}
    sheets = [
        SimpleNamespace(id=f"sheet-{z.code}", zone_id=z.id, campaign_id="camp-1")
        for z in zones
    ]
    ctx: Any = SimpleNamespace(
        actor="chef@usine",
        sheets=SimpleNamespace(
            list_zones=lambda cid: list(zones),
            list_sheets=lambda cid: sheets,
            lines_by_sheet=lambda cid: {
                f"sheet-{z.code}": lines_by_zone.get(z.code, []) for z in zones
            },
            list_arbitrations=lambda cid: list(arbitrations),
        ),
        journals=SimpleNamespace(list=lambda cid: list(journals)),
    )
    built = BoardService(ctx)
    if perimeter is not None:
        built._perimeter = lambda campaign, zones: perimeter  # type: ignore[method-assign]
    return built


CAMPAIGN: Any = SimpleNamespace(id="camp-1")


def arbitration(zone_code: str, *, resolved: bool = False, same: bool = False) -> Any:
    from decimal import Decimal

    return SimpleNamespace(
        zone_id=f"id-{zone_code}",
        is_resolved=resolved,
        qty_pass_1=Decimal("10"),
        qty_pass_2=Decimal("10") if same else Decimal("12"),
    )


class TestHowAZoneIsClassified:
    def test_a_zone_with_no_counted_line_has_not_started(self):
        got = board(
            zones=[zone("Z1")], lines_by_zone={"Z1": [line(False), line(False)]}
        ).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_NOT_STARTED"]["names"] == ["Z1"]

    def test_a_partly_counted_zone_is_in_progress(self):
        got = board(
            zones=[zone("Z1")], lines_by_zone={"Z1": [line(True), line(False)]}
        ).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_IN_PROGRESS"]["names"] == ["Z1"]

    def test_a_fully_counted_zone_is_ready_to_close(self):
        got = board(
            zones=[zone("Z1")], lines_by_zone={"Z1": [line(True), line(True)]}
        ).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_READY_TO_CLOSE"]["names"] == ["Z1"]

    def test_a_closed_zone_is_in_no_queue(self):
        got = board(
            zones=[zone("Z1", closed=True)],
            lines_by_zone={"Z1": [line(True)]},
        ).work_queues(CAMPAIGN)
        assert got["queues"] == []

    def test_a_disputed_zone_is_never_ready_to_close(self):
        """Tout est relevé, et pourtant : la consolidation ne saurait pas
        quelle quantité retenir entre les deux comptages."""
        got = board(
            zones=[zone("Z1")],
            lines_by_zone={"Z1": [line(True), line(True)]},
            arbitrations=[arbitration("Z1")],
        ).work_queues(CAMPAIGN)
        queues = by_code_dict(got)
        assert queues["ZONES_TO_ARBITRATE"]["names"] == ["Z1"]
        assert "ZONES_READY_TO_CLOSE" not in queues

    def test_a_settled_dispute_does_not_hold_the_zone(self):
        """Les compter tous ferait apparaître comme « à arbitrer » des zones
        dont le litige est réglé depuis hier."""
        got = board(
            zones=[zone("Z1")],
            lines_by_zone={"Z1": [line(True)]},
            arbitrations=[arbitration("Z1", resolved=True)],
        ).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_READY_TO_CLOSE"]["names"] == ["Z1"]

    def test_two_passes_that_agree_are_no_dispute(self):
        """Une ligne d'arbitrage existe dès qu'il y a deux comptages ; seul un
        désaccord appelle une décision."""
        got = board(
            zones=[zone("Z1")],
            lines_by_zone={"Z1": [line(True)]},
            arbitrations=[arbitration("Z1", same=True)],
        ).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_READY_TO_CLOSE"]["names"] == ["Z1"]

    def test_a_zone_with_no_sheet_line_at_all_has_not_started(self):
        """Sans lignes, « tout est compté » serait vrai par vacuité — et la
        zone se déclarerait prête à fermer sans qu'on y soit allé."""
        got = board(zones=[zone("Z1")], lines_by_zone={"Z1": []}).work_queues(CAMPAIGN)
        assert by_code_dict(got)["ZONES_NOT_STARTED"]["names"] == ["Z1"]


def by_code_dict(payload) -> dict[str, Any]:
    return {q["code"]: q for q in payload["queues"]}


def journal(warehouse: str, location: str, status: str) -> Any:
    from inventory.domain.enums import JournalStatus

    return SimpleNamespace(
        warehouse_id=warehouse,
        location_id=location,
        status=JournalStatus(status),
    )


class TestHowAJournalIsClassified:
    def test_a_posted_journal_is_in_no_queue(self):
        got = board(journals=[journal("B06", "L1", "POSTED")]).work_queues(CAMPAIGN)
        assert got["queues"] == []

    def test_a_book_enforced_journal_is_in_no_queue_either(self):
        """Il est clos par construction : l'écart y est nul, et rien n'attend."""
        got = board(journals=[journal("B06", "L1", "BOOK_ENFORCED")]).work_queues(
            CAMPAIGN
        )
        assert got["queues"] == []

    def test_a_started_journal_waits_for_its_posting(self):
        got = board(journals=[journal("B06", "L1", "IN_PROGRESS")]).work_queues(
            CAMPAIGN
        )
        assert by_code_dict(got)["JOURNALS_IN_PROGRESS"]["names"] == ["B06 / L1"]

    def test_a_pending_journal_waits_for_somebody(self):
        got = board(journals=[journal("B06", "L1", "PENDING")]).work_queues(CAMPAIGN)
        assert by_code_dict(got)["JOURNALS_NOT_STARTED"]["names"] == ["B06 / L1"]

    def test_the_name_carries_the_warehouse_and_the_location(self):
        """« L1 » seul est ambigu : le même emplacement existe sous deux
        entrepôts."""
        got = board(journals=[journal("QUAL", "L1", "PENDING")]).work_queues(CAMPAIGN)
        assert by_code_dict(got)["JOURNALS_NOT_STARTED"]["names"] == ["QUAL / L1"]


# --------------------------------------------------------------------------- #
# Le périmètre
# --------------------------------------------------------------------------- #

def perimeter(*, zone_ids=(), warehouses=(), resolved=True) -> Any:
    return SimpleNamespace(
        resolved=resolved,
        covers_zone=lambda zid: zid in zone_ids,
        covers_warehouse=lambda wid: wid in warehouses,
    )


class TestTheFilter:
    def test_without_focus_everything_is_shown(self):
        got = board(
            zones=[zone("Z1"), zone("Z2")],
            lines_by_zone={"Z1": [line(False)], "Z2": [line(False)]},
            perimeter=perimeter(zone_ids={"id-Z1"}),
        ).work_queues(CAMPAIGN, focus=False)
        assert by_code_dict(got)["ZONES_NOT_STARTED"]["names"] == ["Z1", "Z2"]

    def test_with_focus_only_the_perimeter_is_shown(self):
        got = board(
            zones=[zone("Z1"), zone("Z2")],
            lines_by_zone={"Z1": [line(False)], "Z2": [line(False)]},
            perimeter=perimeter(zone_ids={"id-Z1"}),
        ).work_queues(CAMPAIGN, focus=True)
        assert by_code_dict(got)["ZONES_NOT_STARTED"]["names"] == ["Z1"]

    def test_journals_follow_the_warehouse_perimeter(self):
        got = board(
            journals=[journal("B06", "L1", "PENDING"), journal("QUAL", "L2", "PENDING")],
            perimeter=perimeter(warehouses={"B06"}),
        ).work_queues(CAMPAIGN, focus=True)
        assert by_code_dict(got)["JOURNALS_NOT_STARTED"]["names"] == ["B06 / L1"]

    def test_a_user_who_is_not_a_manager_sees_everything(self):
        """Le mode focus est un filtre, pas une permission : quelqu'un qui
        n'est rattaché à aucun gestionnaire ne doit pas voir un écran vide."""
        got = board(
            zones=[zone("Z1"), zone("Z2")],
            lines_by_zone={"Z1": [line(False)], "Z2": [line(False)]},
            perimeter=perimeter(resolved=False),
        ).work_queues(CAMPAIGN, focus=True)
        assert by_code_dict(got)["ZONES_NOT_STARTED"]["names"] == ["Z1", "Z2"]

    def test_the_payload_says_whether_it_is_filtered(self):
        """Un « 3 » sur une campagne de quarante zones se lit comme une erreur
        si l'écran ne dit pas qu'il filtre."""
        got = board(
            zones=[zone("Z1")],
            lines_by_zone={"Z1": [line(False)]},
            perimeter=perimeter(zone_ids={"id-Z1"}),
        ).work_queues(CAMPAIGN, focus=True)
        assert got["focus"] is True

    def test_an_unresolved_perimeter_does_not_claim_to_filter(self):
        got = board(
            zones=[zone("Z1")],
            lines_by_zone={"Z1": [line(False)]},
            perimeter=perimeter(resolved=False),
        ).work_queues(CAMPAIGN, focus=True)
        assert got["focus"] is False


class TestTheTotal:
    def test_it_adds_up_the_queues(self):
        """Le total compte ce qui attend, pas le nombre de files : trois zones
        non commencées et deux journaux font cinq choses à faire, pas deux."""
        got = board(
            zones=[zone("Z1"), zone("Z2"), zone("Z3")],
            lines_by_zone={code: [line(False)] for code in ("Z1", "Z2", "Z3")},
            journals=[
                journal("B06", "L1", "PENDING"),
                journal("B06", "L2", "PENDING"),
            ],
        ).work_queues(CAMPAIGN)
        assert len(got["queues"]) == 2
        assert got["waiting"] == 5

    def test_nothing_waiting_is_zero_and_no_queue(self):
        got = board().work_queues(CAMPAIGN)
        assert (got["waiting"], got["queues"]) == (0, [])


# --------------------------------------------------------------------------- #
# Ce que le navigateur en fait
# --------------------------------------------------------------------------- #

def frontend(relative: str) -> str:
    return (ROOT / "frontend" / "src" / relative).read_text()


class TestTheScreen:
    def test_the_board_is_where_the_counting_phase_lands(self):
        """C'est le point de S7 : le jour J, « que faire » avant « où en
        est-on »."""
        assert "COUNTING: 'journee'," in frontend("App.tsx")

    def test_the_route_exists(self):
        assert 'path="journee"' in frontend("App.tsx")

    def test_the_screen_passes_the_focus_mode_on(self):
        source = frontend("features/Board.tsx")
        assert "api.workQueues(campaignId, focus)" in source

    def test_it_refreshes_by_itself(self):
        """Plusieurs personnes saisissent en même temps : un tableau figé
        depuis vingt minutes envoie quelqu'un sur une zone déjà finie."""
        assert "refetchInterval" in frontend("features/Board.tsx")

    def test_it_says_what_it_is_not_showing(self):
        assert "et {queue.hidden} autre(s)" in frontend("features/Board.tsx")

    def test_it_says_when_it_is_filtered(self):
        assert "Votre périmètre seulement" in frontend("features/Board.tsx")

    def test_an_empty_board_says_so_rather_than_showing_nothing(self):
        assert "Rien n’attend personne" in frontend("features/Board.tsx")

    def test_the_navigation_offers_it(self):
        assert "to: 'journee'," in frontend("lib/navigation.ts")
