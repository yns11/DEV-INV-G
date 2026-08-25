"""Ce que le modèle voit d'un écart — et ce qu'il ne voyait pas.

Ce qui est arrivé
-----------------
Le site déduit ses consommations de composants de la production déclarée, au
prorata de la nomenclature : c'est le backflush, et c'est le mécanisme qui
explique la plus grosse part des dérives d'un stock qui n'est jamais sorti à la
main. L'application le charge, le fige par campagne, le soustrait de l'écart et
en fait une vue entière. Le référentiel des causes du site porte même un code
dédié — « Écart consommation (backflush) ».

Rien de tout cela n'atteignait le modèle. Le dossier envoyé portait le stock
ERP, le comptage, l'écart, et s'arrêtait là. Un prompt à qui il manque un fait
n'échoue pas : il devine. Les propositions se rabattaient donc sur les causes
qu'on devine sans rien savoir du site — l'erreur de comptage, l'écart de
réception — et la cause backflush ne pouvait être choisie par personne, faute
du chiffre qui l'aurait fondée.

Ce que ces contrôles tiennent
-----------------------------
Les faits présents dans le dossier, le vocabulaire qui permet de les lire, et le
fait que le service les y mette réellement. Ce dernier point est le plus
important : un contexte défini et jamais transmis est le défaut récurrent de ce
dépôt, et il ne se voit nulle part — la réponse reste plausible.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from inventory.ai import insights
from inventory.ai.insights import InsightEngine
from inventory.domain.enums import ItemType
from inventory.domain.models import (
    AssignableCause,
    BackflushLine,
    Campaign,
    ConsolidatedLine,
    Item,
    Thresholds,
    VarianceLine,
)


class Recorder:
    """Un client qui ne parle à rien et retient ce qu'on lui a demandé."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.system = ""
        self.user = ""
        self._payload = payload or {"suggestions": []}

    def complete(self, *, system: str, user: str, **_: Any) -> Any:
        self.system, self.user = system, user
        return type("R", (), {"text": "ok"})()

    def complete_json(self, *, system: str, user: str, **_: Any) -> Any:
        self.system, self.user = system, user
        return self._payload, type("R", (), {"text": "{}"})()

    @property
    def facts(self) -> dict[str, Any]:
        """L'objet JSON du message, quel que soit ce qui l'entoure."""
        return json.loads(_json_between(self.user, "{", "}"))

    @property
    def diagnosed(self) -> list[dict[str, Any]]:
        """La liste d'écarts soumise au diagnostic."""
        return json.loads(_json_between(self.user, "[", "]"))


def _json_between(text: str, opening: str, closing: str) -> str:
    """Le premier bloc JSON équilibré du message."""
    start = text.index(opening)
    depth = 0
    for index in range(start, len(text)):
        depth += {opening: 1, closing: -1}.get(text[index], 0)
        if depth == 0:
            return text[start : index + 1]
    raise AssertionError("bloc JSON non refermé")


def variance(**over: Any) -> VarianceLine:
    base: dict[str, Any] = {
        "campaign_id": "c",
        "item_number": "P-00324093",
        "item_type": ItemType.COMPONENT,
        "unit_cost": Decimal("12.5"),
        "book_qty": Decimal("100"),
        "counted_qty": Decimal("80"),
        "backflush_qty": Decimal("18"),
        "backflush_measured": True,
    }
    return VarianceLine(**{**base, **over})


def backflush(**over: Any) -> BackflushLine:
    base: dict[str, Any] = {
        "campaign_id": "c",
        "item_number": "P-00324093",
        "period_start": dt.date(2026, 6, 1),
        "period_end": dt.date(2026, 6, 29),
        "net_qty": Decimal("18"),
        "under_consumed_qty": Decimal("40"),
        "over_consumed_qty": Decimal("22"),
        "theoretical_qty": Decimal("1200"),
        "actual_qty": Decimal("1182"),
        "parent_count": 3,
        "week_count": 4,
    }
    return BackflushLine(**{**base, **over})


CAUSES = [
    AssignableCause(code="6", label="Écarts de comptage (manuel)", family="Counting"),
    AssignableCause(
        code="11", label="Écart consommation (backflush)", family="ERP consumption"
    ),
]


# --------------------------------------------------------------------------- #
# Les faits
# --------------------------------------------------------------------------- #

class TestTheDiagnosisSeesTheBackflush:
    @staticmethod
    def _sent() -> Recorder:
        client = Recorder()
        InsightEngine(client=client).suggest_causes(
            variances=[variance()],
            causes=CAUSES,
            items={"P-00324093": Item(campaign_id="c", item_number="P-00324093")},
        )
        return client

    @pytest.mark.parametrize(
        "key",
        ["backflushMesure", "partBackflushQte", "partBackflushValeur",
         "inexpliqueQte", "inexpliqueValeur", "tauxExplication"],
    )
    def test_each_production_figure_is_in_the_file(self, key):
        [line] = self._sent().diagnosed
        assert key in line, f"{key} absent du dossier envoyé"

    def test_the_share_arrives_in_the_inventory_convention(self):
        """Le backflush lit « théorique − réel », l'inventaire « compté − ERP ».

        Envoyer le chiffre sans retourner son signe inversait le diagnostic :
        une non-consommation se serait lue comme un excédent en magasin.
        """
        line = variance()
        payload = insights._variance_payload(line, None, None)
        assert payload["partBackflushQte"] == -18.0
        assert payload["inexpliqueQte"] == float(line.unexplained_qty)

    def test_a_never_measured_article_says_so(self):
        """« Pas de ligne sur la période » et « écart nul mesuré » sont deux
        réponses différentes, et le modèle ne doit pas conclure de l'une à
        l'autre."""
        payload = insights._variance_payload(
            variance(backflush_qty=Decimal("0"), backflush_measured=False), None, None
        )
        assert payload["backflushMesure"] is False


class TestTheModelIsToldHowToReadTheseNumbers:
    """Des chiffres sans convention se lisent au hasard.

    Le signe du backflush est contre-intuitif — un écart positif veut dire que
    le stock système est *surévalué* — et rien ne le disait.
    """

    @pytest.mark.parametrize(
        "prompt",
        [insights._CAUSE_SYSTEM, insights._NARRATIVE_SYSTEM, insights._EXPLAIN_SYSTEM],
    )
    def test_every_prompt_defines_the_backflush(self, prompt):
        assert "backflush" in prompt.lower()
        assert "consommation théorique − consommation réelle" in prompt

    @pytest.mark.parametrize(
        "prompt",
        [insights._CAUSE_SYSTEM, insights._NARRATIVE_SYSTEM, insights._EXPLAIN_SYSTEM],
    )
    def test_every_prompt_says_which_way_the_sign_points(self, prompt):
        assert "surévalué" in prompt

    @pytest.mark.parametrize(
        "prompt",
        [insights._CAUSE_SYSTEM, insights._NARRATIVE_SYSTEM, insights._EXPLAIN_SYSTEM],
    )
    def test_every_prompt_explains_the_counting_sections(self, prompt):
        """Une quantité venue d'un WIP éclaté sort d'un calcul de nomenclature,
        pas d'un décompte : cela change la vérification recommandée."""
        assert "Bord de ligne" in prompt
        assert "éclat" in prompt

    def test_the_cause_prompt_tells_it_to_look_there_first(self):
        assert "backflush avant de conclure" in insights._CAUSE_SYSTEM

    def test_the_explanation_has_its_own_instructions(self):
        """Elle empruntait celles de la synthèse au comité de direction, qui
        demandent un rapport structuré là où on attend cinq puces."""
        assert insights._EXPLAIN_SYSTEM is not insights._NARRATIVE_SYSTEM
        assert "comité de direction" not in insights._EXPLAIN_SYSTEM

    def test_and_actually_uses_them(self):
        """Écrites sans être passées, elles ne changeraient rien à la réponse."""
        client = Recorder()
        InsightEngine(client=client).explain_variance(line=variance(), item=None)
        assert client.system == insights._EXPLAIN_SYSTEM


class TestTheExplanationCarriesWhatTheLineDoesNot:
    @staticmethod
    def _sent(**extra: Any) -> Recorder:
        client = Recorder()
        InsightEngine(client=client).explain_variance(
            line=variance(), item=None, **extra
        )
        return client

    def test_the_net_is_broken_down(self):
        """40 de sous-consommation contre 22 de sur-consommation ne se lit pas
        comme 18, et ne mène pas à la même vérification."""
        facts = self._sent(backflush=backflush()).facts
        assert facts["backflush"]["sousConsommeQte"] == 40.0
        assert facts["backflush"]["surConsommeQte"] == 22.0

    def test_the_period_travels_with_it(self):
        """Le même écart sur une semaine et sur un trimestre ne veut pas dire la
        même chose ; sans la borne, le modèle la supposait."""
        facts = self._sent(backflush=backflush()).facts
        assert facts["backflush"]["periodeDebut"] == "2026-06-01"
        assert facts["backflush"]["periodeFin"] == "2026-06-29"
        assert facts["backflush"]["semaines"] == 4

    def test_where_the_counted_quantity_came_from(self):
        facts = self._sent(
            counting={"dontWipEclateParNomenclature": 60.0, "zones": ["FI ASSY M3.1"]}
        ).facts
        assert facts["comptage"]["dontWipEclateParNomenclature"] == 60.0

    def test_what_significant_means_here(self):
        facts = self._sent(thresholds={"valeurAbsolueEuros": 1000.0}).facts
        assert facts["seuilsDeMaterialite"]["valeurAbsolueEuros"] == 1000.0

    def test_nothing_is_invented_when_nothing_is_given(self):
        """Un bloc vide vaudrait « mesuré, et nul » : c'est une autre réponse."""
        facts = self._sent().facts
        assert "backflush" not in facts
        assert "comptage" not in facts
        assert "seuilsDeMaterialite" not in facts


class TestTheSummarySaysWhatProductionExplains:
    @staticmethod
    def _sent(**extra: Any) -> Recorder:
        from inventory.domain.variance import KpiBlock

        client = Recorder()
        InsightEngine(client=client).campaign_summary(
            campaign_label="INV-2026-T3",
            count_date="2026-06-27",
            kpis=KpiBlock(),
            top_variances=[],
            by_warehouse=[],
            control_summary={},
            **extra,
        )
        return client

    def test_the_block_travels(self):
        facts = self._sent(backflush={"tauxExplication": 0.62}).facts
        assert facts["backflush"]["tauxExplication"] == 0.62

    def test_an_absent_block_is_not_a_zero(self):
        """Annoncer « 0 € de part production » sur une campagne où le backflush
        n'a pas été chargé se lirait comme un résultat."""
        assert "backflush" not in self._sent().facts


# --------------------------------------------------------------------------- #
# Et le service le transmet bien
# --------------------------------------------------------------------------- #

class Spy:
    """Un moteur qui n'appelle rien et retient les arguments reçus."""

    calls: ClassVar[list[dict[str, Any]]] = []

    def explain_variance(self, **kwargs: Any) -> str:
        Spy.calls.append(kwargs)
        return "texte"

    def campaign_summary(self, **kwargs: Any) -> str:
        Spy.calls.append(kwargs)
        return "texte"


@pytest.fixture(autouse=True)
def _forget_spy_calls():
    Spy.calls.clear()


def insight_service(monkeypatch, *, line: VarianceLine, **repositories: Any):
    """Le vrai service, avec le strict nécessaire autour et le moteur espionné."""
    import inventory.ai as ai_package
    from inventory.services.analysis_service import AnalysisService
    from inventory.services.insight_service import InsightService

    monkeypatch.setattr(ai_package, "InsightEngine", Spy)
    campaign = Campaign(
        id="camp-1",
        code="INV-2026-T3",
        label="T3",
        count_date=dt.date(2026, 6, 27),
        created_by="chef@usine",
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
        thresholds=[
            Thresholds(item_type=ItemType.COMPONENT, value_abs_eur=Decimal("1000")),
        ],
    )
    ctx = cast(Any, SimpleNamespace(
        actor="chef@usine",
        guard=lambda campaign, aspect: None,
        record=lambda **kw: None,
        referentials=SimpleNamespace(items_by_number=lambda cid: {}),
        adjustments=SimpleNamespace(list=lambda cid: []),
        consolidation=SimpleNamespace(
            wip_breakdown=lambda cid, child_item=None: [],
            current_lines=lambda cid: repositories.get("consolidated", []),
        ),
        backflush=SimpleNamespace(
            by_item=lambda cid: repositories.get("backflush", {}),
        ),
    ))
    from inventory.domain.variance import KpiBlock

    analysis = cast(Any, SimpleNamespace())
    analysis.variances = lambda campaign, granularity="item": [line]
    analysis.kpis = lambda campaign: KpiBlock()
    analysis.controls = lambda campaign: {"summary": {}}
    analysis.cause_split = lambda campaign: {}
    service = InsightService(ctx, analysis)
    assert isinstance(AnalysisService(ctx).insights, InsightService)
    return service, campaign


class TestTheServiceActuallyHandsItOver:
    """Composer un dossier sans le transmettre ne se voit nulle part.

    La réponse du modèle reste plausible — c'est tout le problème — et c'est le
    défaut le plus fréquent de ce dépôt : la règle est écrite quelque part, et
    personne ne l'appelle.
    """

    def test_the_backflush_line_reaches_the_prompt(self, monkeypatch):
        service, campaign = insight_service(
            monkeypatch,
            line=variance(),
            backflush={"P-00324093": backflush()},
        )
        service.explain(campaign, "P-00324093")
        assert Spy.calls[0]["backflush"] is not None
        assert Spy.calls[0]["backflush"].under_consumed_qty == Decimal("40")

    def test_how_the_article_was_counted_reaches_it_too(self, monkeypatch):
        counted = ConsolidatedLine(
            campaign_id="camp-1",
            item_number="P-00324093",
            qty=Decimal("80"),
            qty_line_side=Decimal("20"),
            qty_wip_exploded=Decimal("60"),
            zone_codes=["FI ASSY M3.1"],
        )
        service, campaign = insight_service(
            monkeypatch, line=variance(), consolidated=[counted]
        )
        service.explain(campaign, "P-00324093")
        assert Spy.calls[0]["counting"]["dontWipEclateParNomenclature"] == 60.0
        assert Spy.calls[0]["counting"]["zones"] == ["FI ASSY M3.1"]

    def test_the_campaign_thresholds_reach_it(self, monkeypatch):
        service, campaign = insight_service(monkeypatch, line=variance())
        service.explain(campaign, "P-00324093")
        assert Spy.calls[0]["thresholds"]["valeurAbsolueEuros"] == 1000.0

    def test_an_article_the_production_never_touched_sends_nothing(self, monkeypatch):
        service, campaign = insight_service(monkeypatch, line=variance())
        service.explain(campaign, "P-00324093")
        assert Spy.calls[0]["backflush"] is None
        assert Spy.calls[0]["counting"] is None

    def test_the_summary_receives_what_production_explains(self, monkeypatch):
        """Sans ce bloc, la synthèse présentait au comité l'écart entier comme
        restant à élucider — ce qui est faux dès que le backflush est chargé, et
        oriente vers une enquête terrain là où le chantier est sur l'ERP."""
        service, campaign = insight_service(monkeypatch, line=variance())
        service.narrative(campaign)
        assert Spy.calls[0]["backflush"] is not None
        assert Spy.calls[0]["backflush"]["articlesMesures"] == 1

    def test_a_campaign_without_backflush_sends_no_block(self, monkeypatch):
        service, campaign = insight_service(
            monkeypatch, line=variance(backflush_measured=False)
        )
        service.narrative(campaign)
        assert Spy.calls[0]["backflush"] is None


class TestTheCampaignTotals:
    def test_the_measured_articles_are_counted(self):
        from inventory.services.insight_service import _backflush_totals

        totals = _backflush_totals([variance(), variance(backflush_measured=False)])
        assert totals is not None
        assert totals["articlesMesures"] == 1
        assert totals["articlesTotal"] == 2

    def test_nothing_measured_gives_nothing(self):
        """« 0 € expliqué par la production » se lirait comme un résultat sur
        une campagne où le backflush n'a jamais été chargé."""
        from inventory.services.insight_service import _backflush_totals

        assert _backflush_totals([variance(backflush_measured=False)]) is None

    def test_two_articles_that_offset_still_explain_their_own(self):
        """Une somme signée serait nulle et dirait que rien n'est expliqué."""
        from inventory.services.insight_service import _backflush_totals

        totals = _backflush_totals([
            variance(item_number="A", backflush_qty=Decimal("18")),
            variance(item_number="B", backflush_qty=Decimal("-18")),
        ])
        assert totals is not None
        assert totals["partProductionAbsolueValeur"] > 0
