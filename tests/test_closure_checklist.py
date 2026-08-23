"""La clôture se prépare, elle ne se subit pas.

Clôturer est le seul geste irréversible du parcours. Ce qui l'empêche était
déjà calculé — ``campaign_transition_blockers`` le fait — mais on ne le
découvrait qu'en ouvrant la fenêtre qui clôture, c'est-à-dire au moment de
cliquer. Trois points bloquants apparaissaient alors d'un coup, un vendredi
soir, et il fallait repartir dans trois écrans.

La liste répond avant, et en trois tons.

**Ce qui bloque** n'est pas recalculé : les entrées bloquantes sont construites
à partir des constats du refus. C'est la garantie centrale de ce module, et
celle qu'il vérifie le plus : un écran qui recalculerait de son côté finirait
par annoncer « prêt » sur une campagne que la clôture refuse.

**Ce qui mérite un regard** n'empêche rien, et c'est pour cela qu'il faut le
montrer. Un écart accepté sans un mot est une décision que personne ne saura
défendre ; l'interdire rendrait la clôture impossible sur des points que
l'exploitant a le droit d'assumer.

**Ce qui est fait** figure aussi : une liste qui ne montre que les reproches se
lit comme une machine à empêcher, là où l'on vient chercher un état des lieux.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from inventory.domain.closure import ChecklistState, closure_checklist
from inventory.domain.enums import CampaignStatus, ControlSeverity
from inventory.domain.models import ControlFinding
from inventory.domain.workflow import campaign_transition_blockers

ROOT = Path(__file__).resolve().parent.parent


def blocker(code: str, message: str = "…") -> ControlFinding:
    return ControlFinding(
        code=code, severity=ControlSeverity.BLOCKER, message=message,
        entity_type="campaign",
    )


def states(items) -> dict[str, ChecklistState]:
    return {item.code: item.state for item in items}


# --------------------------------------------------------------------------- #
# La garantie centrale : la liste et le refus disent la même chose
# --------------------------------------------------------------------------- #

class TestTheListAndTheRefusalAgree:
    def test_every_blocker_appears_as_blocking(self):
        """Un constat bloquant absent de la liste, c'est un écran qui annonce
        « prêt » sur une campagne que la clôture refusera."""
        found = campaign_transition_blockers(
            CampaignStatus.ANALYSIS,
            CampaignStatus.CLOSED,
            unexplained_material=3,
            rejected_imports=[("items", 12)],
            publication_done=False,
        )
        assert len(found) == 3
        by_code = states(closure_checklist(blockers=found))
        for finding in found:
            assert by_code[finding.code] is ChecklistState.BLOCKING

    def test_an_unknown_blocker_code_still_appears(self):
        """Mieux vaut un libellé technique qu'un point bloquant invisible."""
        items = closure_checklist(blockers=[blocker("CODE_INVENTE", "un ennui")])
        entry = next(i for i in items if i.code == "CODE_INVENTE")
        assert entry.state is ChecklistState.BLOCKING
        assert entry.detail == "un ennui"

    def test_the_blocking_message_is_the_one_the_refusal_gives(self):
        """Deux formulations pour un même refus, c'est deux explications à
        tenir à jour — et une qui dérive."""
        found = campaign_transition_blockers(
            CampaignStatus.ANALYSIS, CampaignStatus.CLOSED, unexplained_material=7
        )
        items = closure_checklist(blockers=found)
        entry = next(i for i in items if i.code == "MATERIAL_VARIANCES_UNEXPLAINED")
        assert entry.detail == found[0].message

    def test_nothing_blocking_leaves_no_blocking_item(self):
        items = closure_checklist(blockers=[])
        assert all(i.state is not ChecklistState.BLOCKING for i in items)


# --------------------------------------------------------------------------- #
# Les trois tons
# --------------------------------------------------------------------------- #

class TestWhatIsAlreadyDoneIsShown:
    """Une liste qui ne montre que les reproches se lit comme une machine à
    empêcher ; on vient y chercher un état des lieux."""

    def test_a_clean_campaign_still_has_a_list(self):
        assert closure_checklist(blockers=[]) != []

    @pytest.mark.parametrize(
        "code",
        [
            "MATERIAL_VARIANCES_UNEXPLAINED",
            "IMPORTS_WITH_REJECTS",
            "PUBLICATION_NOT_DONE",
        ],
    )
    def test_a_requirement_that_is_met_appears_in_green(self, code):
        assert states(closure_checklist(blockers=[]))[code] is ChecklistState.DONE

    def test_a_requirement_appears_once_and_not_twice(self):
        """Bloquant *et* vert serait la lecture la plus fausse possible."""
        items = closure_checklist(blockers=[blocker("PUBLICATION_NOT_DONE")])
        codes = [i.code for i in items]
        assert codes.count("PUBLICATION_NOT_DONE") == 1

    def test_the_earlier_phases_requirements_are_shown_too(self):
        """Un dossier dont on ne dit pas que le stock ERP est gelé se relit mal
        six mois plus tard."""
        by_code = states(closure_checklist(blockers=[]))
        for code in ("BOOK_STOCK_FROZEN", "JOURNALS_POSTED", "ZONES_DONE"):
            assert by_code[code] is ChecklistState.DONE


class TestWhatDeservesALook:
    def test_an_acceptance_without_a_word_is_flagged(self):
        items = closure_checklist(blockers=[], accepted_without_comment=4)
        entry = next(i for i in items if i.code == "ACCEPTED_WITHOUT_COMMENT")
        assert entry.state is ChecklistState.ATTENTION
        assert "4 écart(s)" in entry.detail

    def test_it_does_not_block(self):
        """L'acceptation est tracée et signée : elle suffit à clôturer. En
        faire un refus rendrait la clôture impossible sur ce que l'exploitant a
        le droit d'assumer."""
        items = closure_checklist(blockers=[], accepted_without_comment=40)
        assert all(i.state is not ChecklistState.BLOCKING for i in items)

    def test_an_untouched_ai_suggestion_is_flagged(self):
        items = closure_checklist(blockers=[], ai_suggestions_untouched=9)
        entry = next(i for i in items if i.code == "AI_SUGGESTIONS_UNTOUCHED")
        assert entry.state is ChecklistState.ATTENTION

    def test_no_untouched_suggestion_says_nothing_at_all(self):
        """Une ligne verte « 0 suggestion en attente » sur une campagne où l'IA
        n'a jamais tourné serait du bruit."""
        codes = [i.code for i in closure_checklist(blockers=[])]
        assert "AI_SUGGESTIONS_UNTOUCHED" not in codes

    def test_a_consolidation_older_than_the_sheets_is_flagged(self):
        items = closure_checklist(
            blockers=[], sheets_changed_after_consolidation=True
        )
        entry = next(
            i for i in items if i.code == "SHEETS_CHANGED_AFTER_CONSOLIDATION"
        )
        assert entry.state is ChecklistState.ATTENTION

    def test_an_up_to_date_consolidation_is_green(self):
        by_code = states(closure_checklist(blockers=[]))
        assert by_code["SHEETS_CHANGED_AFTER_CONSOLIDATION"] is ChecklistState.DONE


class TestTheReadingOrder:
    def test_what_stops_you_comes_first(self):
        items = closure_checklist(
            blockers=[blocker("PUBLICATION_NOT_DONE")],
            accepted_without_comment=2,
        )
        assert items[0].state is ChecklistState.BLOCKING

    def test_what_is_done_comes_last(self):
        items = closure_checklist(
            blockers=[blocker("PUBLICATION_NOT_DONE")],
            accepted_without_comment=2,
        )
        assert items[-1].state is ChecklistState.DONE

    def test_the_three_tones_are_grouped(self):
        """Alterner rouge, vert, orange, vert force à relire pour compter."""
        items = closure_checklist(
            blockers=[blocker("PUBLICATION_NOT_DONE")],
            accepted_without_comment=2,
            ai_suggestions_untouched=1,
        )
        order = [str(i.state) for i in items]
        assert order == sorted(
            order, key=lambda s: ["BLOCKING", "ATTENTION", "DONE"].index(s)
        )


class TestEachItemSaysWhereToGo:
    @pytest.mark.parametrize(
        "code,where",
        [
            ("MATERIAL_VARIANCES_UNEXPLAINED", "ecarts"),
            ("IMPORTS_WITH_REJECTS", "articles"),
            ("PUBLICATION_NOT_DONE", "compil"),
        ],
    )
    def test_a_blocking_item_names_its_screen(self, code, where):
        """« Rechargez le fichier corrigé » sans l'écran laisse chercher."""
        items = closure_checklist(blockers=[blocker(code)])
        assert next(i for i in items if i.code == code).where == where

    def test_a_done_item_offers_no_link(self):
        """Proposer d'aller « corriger » un point déjà vert est une invitation
        à défaire."""
        items = closure_checklist(blockers=[])
        assert all(i.where is None for i in items if i.state is ChecklistState.DONE)

    def test_the_screens_named_are_real_routes(self):
        """Un fragment inventé donne un lien vers « Page introuvable »."""
        from inventory.domain.closure import WHERE

        routes = (ROOT / "frontend" / "src" / "lib" / "navigation.ts").read_text()
        app = (ROOT / "frontend" / "src" / "App.tsx").read_text()
        for fragment in set(WHERE.values()):
            assert f"'{fragment}'" in routes or f'"{fragment}"' in app or (
                f"path=\"{fragment}\"" in app
            ), fragment


# --------------------------------------------------------------------------- #
# Le service rassemble les faits
# --------------------------------------------------------------------------- #

def campaign(**kwargs) -> Any:
    import datetime as dt

    base = {
        "id": "camp-1",
        "status": CampaignStatus.ANALYSIS,
        "book_stock_frozen_at": dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        "published_at": dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
        "thresholds": [],
    }
    return SimpleNamespace(**{**base, **kwargs})


def service(**overrides):
    """Le service, sur un contexte qui ne porte que ce que la liste consulte.

    ``_unexplained_material`` est remplacé par un nombre. Il a sa propre
    couverture — il traverse le moteur d'écarts, les seuils de matérialité et
    les analyses — et le rejouer ici ferait de ce contrôle un contrôle du
    moteur d'écarts, avec un contexte factice de trente lignes qui masquerait
    ce qui est réellement vérifié : que le service rassemble les faits et les
    passe au domaine.
    """
    import datetime as dt

    from conftest import with_access, with_transactions

    from inventory.services.campaign_service import CampaignService

    facts = {
        "unexplained": 0,
        "analyses": [],
        "run": {"run_at": dt.datetime(2026, 9, 3, tzinfo=dt.UTC)},
        "last_change": dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        "journals": [],
        "zones": [],
        "imports": [],
        "campaign": campaign(),
    }
    facts.update(overrides)
    ctx: Any = SimpleNamespace(
        actor="chef@usine",
        request_id="req-1",
        campaigns=SimpleNamespace(get=lambda cid: facts["campaign"]),
        analysis=SimpleNamespace(list_analyses=lambda cid: facts["analyses"]),
        consolidation=SimpleNamespace(current_run=lambda cid: facts["run"]),
        sheets=SimpleNamespace(
            last_line_change=lambda cid: facts["last_change"],
            list_zones=lambda cid: facts["zones"],
            list_sheets=lambda cid: [],
            lines_by_sheet=lambda cid: {},
        ),
        journals=SimpleNamespace(list=lambda cid: facts["journals"]),
        imports=SimpleNamespace(latest_per_target=lambda cid: facts["imports"]),
    )
    with_transactions(ctx)
    with_access(ctx)
    built = CampaignService(ctx)
    built._unexplained_material = lambda campaign: facts["unexplained"]  # type: ignore[method-assign]
    return built


class TestTheServiceGathersTheFacts:
    def test_a_clean_campaign_is_ready(self):
        got = service().closure_checklist("camp-1")
        assert got["ready"] is True
        assert got["counts"]["blocking"] == 0

    def test_an_unpublished_campaign_is_not(self):
        got = service(campaign=campaign(published_at=None)).closure_checklist("camp-1")
        assert got["ready"] is False
        assert any(
            i["code"] == "PUBLICATION_NOT_DONE" and i["state"] == "BLOCKING"
            for i in got["items"]
        )

    def test_an_acceptance_without_a_comment_is_counted(self):
        analyses = [
            SimpleNamespace(accepted=True, comment="  ", cause_code=None,
                            ai_suggested_cause=None),
            SimpleNamespace(accepted=True, comment="bruit de pesée",
                            cause_code=None, ai_suggested_cause=None),
        ]
        got = service(analyses=analyses).closure_checklist("camp-1")
        entry = next(
            i for i in got["items"] if i["code"] == "ACCEPTED_WITHOUT_COMMENT"
        )
        assert entry["state"] == "ATTENTION"
        assert "1 écart(s)" in entry["detail"]

    def test_a_suggestion_that_was_decided_is_not_counted(self):
        analyses = [
            SimpleNamespace(accepted=False, comment="", cause_code="ERREUR_SAISIE",
                            ai_suggested_cause="ERREUR_SAISIE"),
        ]
        got = service(analyses=analyses).closure_checklist("camp-1")
        assert not any(
            i["code"] == "AI_SUGGESTIONS_UNTOUCHED" for i in got["items"]
        )

    def test_sheets_edited_after_the_consolidation_are_flagged(self):
        import datetime as dt

        got = service(
            last_change=dt.datetime(2026, 9, 4, tzinfo=dt.UTC)
        ).closure_checklist("camp-1")
        entry = next(
            i for i in got["items"]
            if i["code"] == "SHEETS_CHANGED_AFTER_CONSOLIDATION"
        )
        assert entry["state"] == "ATTENTION"

    def test_a_consolidation_run_at_the_same_instant_is_current(self):
        """La consolidation lit les lignes : au même instant, elle les a lues.
        Seul un changement *postérieur* la périme."""
        import datetime as dt

        instant = dt.datetime(2026, 9, 3, tzinfo=dt.UTC)
        got = service(
            run={"run_at": instant}, last_change=instant
        ).closure_checklist("camp-1")
        entry = next(
            i for i in got["items"]
            if i["code"] == "SHEETS_CHANGED_AFTER_CONSOLIDATION"
        )
        assert entry["state"] == "DONE"

    def test_a_campaign_with_no_consolidation_is_not_called_stale(self):
        """Elle n'existe pas ; c'est un autre point, celui de la publication."""
        import datetime as dt

        got = service(
            run=None, last_change=dt.datetime(2026, 9, 9, tzinfo=dt.UTC)
        ).closure_checklist("camp-1")
        entry = next(
            i for i in got["items"]
            if i["code"] == "SHEETS_CHANGED_AFTER_CONSOLIDATION"
        )
        assert entry["state"] == "DONE"

    def test_an_unexplained_material_variance_blocks(self):
        """Le service doit consulter le compte : l'oublier rendrait la liste
        verte sur une campagne que la clôture refuse."""
        got = service(unexplained=5).closure_checklist("camp-1")
        entry = next(
            i for i in got["items"]
            if i["code"] == "MATERIAL_VARIANCES_UNEXPLAINED"
        )
        assert entry["state"] == "BLOCKING"
        assert "5 écart(s)" in entry["detail"]
        assert got["ready"] is False

    def test_the_counts_add_up_to_the_items(self):
        got = service(campaign=campaign(published_at=None)).closure_checklist("camp-1")
        counts = got["counts"]
        assert counts["blocking"] + counts["attention"] + counts["done"] == len(
            got["items"]
        )


# --------------------------------------------------------------------------- #
# Ce que le navigateur en fait
# --------------------------------------------------------------------------- #

def frontend(relative: str) -> str:
    """L'écran entier, ses onglets compris — voir ``conftest``."""
    from conftest import screen_source

    return screen_source(relative)


class TestTheScreenReadsItBeforeTheClick:
    def test_the_closure_modal_shows_the_checklist(self):
        """La condition, pas seulement la balise : un rendu qui ne s'exécute
        jamais laisse le composant dans le source et l'écran sans la liste."""
        source = frontend("features/CampaignShell.tsx")
        assert "{target === 'CLOSED' ? (\n          <ClosureChecklistView" in source

    def test_the_other_transitions_keep_the_short_list(self):
        """Il n'y a rien à préparer pour entrer en comptage : une liste de huit
        points y serait du décor."""
        source = frontend("features/CampaignShell.tsx")
        assert "enabled: target === 'CLOSED'," in source

    def test_the_analysis_screen_shows_it_too(self):
        """C'est le point de S6 : la lire des jours avant, pas au moment de
        cliquer."""
        assert "<ClosurePanel" in frontend("features/Analysis.tsx")

    def test_it_is_hidden_before_the_analysis_phase(self):
        """Plus tôt, journaux et zones y figureraient comme bloquants alors que
        la phase ne les a pas encore exigés : une liste rouge sur une campagne
        normale apprend à ignorer la liste."""
        source = frontend("features/Analysis.tsx")
        assert "overview.campaign.status === 'ANALYSIS'" in source
        assert "if (!inAnalysis) return null" in source

    def test_both_places_use_the_same_component(self):
        """Deux rendus séparés auraient dérivé — celui qu'on ouvre rarement en
        premier."""
        for module in ("features/CampaignShell.tsx", "features/Analysis.tsx"):
            assert "ClosureChecklistView" in frontend(module)

    def test_the_summary_comes_before_the_detail(self):
        """« 2 bloquants, 1 à regarder, 6 faits » se lit en une seconde."""
        source = frontend("components/ClosureChecklist.tsx")
        assert "bloquant(s)" in source
        assert source.index("bloquant(s)") < source.index("data.items.map")

    def test_a_done_item_offers_no_link_on_screen_either(self):
        source = frontend("components/ClosureChecklist.tsx")
        assert "item.where && item.state !== 'DONE'" in source
