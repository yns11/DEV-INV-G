"""Parcourir les campagnes rend des campagnes, pas une paire.

Le défaut, et comment il est passé
----------------------------------
La pagination a changé ``CampaignRepository.list`` pour qu'elle rende
``(page, total)`` : un écran qui pagine a besoin des deux, et sans le total la
liste se tronquait en silence. Le changement était juste. Ce qui ne l'était pas,
c'est d'avoir gardé le nom ``list`` pour une méthode qui ne rend plus une liste.

Quatre services parcouraient déjà cette méthode pour une tout autre raison —
« quelle est la campagne précédente ? » — et n'ont que faire du total. Ils
itéraient désormais la paire : deux éléments, dont le premier est une liste.
``other.id`` levait alors ``AttributeError: 'list' object has no attribute 'id'``,
c'est-à-dire un 500, sur trois écrans :

* la période proposée à l'écran Backflush,
* la réconciliation de flux,
* la liste des campagnes comparables.

Python ne dit rien : une paire s'itère aussi bien qu'une liste. Aucun contrôle
ne frappait ces routes, et l'écran affichait « une erreur est survenue ».

Ce que ces contrôles verrouillent
---------------------------------
Le nom dit ce qu'on reçoit : ``list`` rend des campagnes, ``page`` rend la paire.
Et chacun des quatre appelants est exercé sur la vraie forme rendue par le
dépôt, parce que c'est la seule façon de constater qu'il sait la lire — un
contrôle qui doublerait la méthode par un simple ``[]`` vérifierait sa propre
doublure et laisserait passer exactement ce défaut-ci.
"""

from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.db.repositories import CampaignRepository
from inventory.domain.enums import CampaignStatus
from inventory.domain.models import Campaign

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "app" / "inventory" / "services"


def campaign(n: int, *, day: int = 1) -> Campaign:
    return Campaign(
        id=f"camp-{n}", code=f"INV-{n:04d}", label="Inventaire",
        count_date=dt.date(2026, 6, day), status=CampaignStatus.CLOSED,
        created_by="chef@usine", created_at=dt.datetime(2026, 6, day, tzinfo=dt.UTC),
    )


def repository(rows: list[Campaign], total: int = 0) -> CampaignRepository:
    """Le vrai dépôt, avec ses seules lectures SQL doublées.

    Le point du contrôle est la **forme rendue**, et elle est calculée par le
    dépôt lui-même. Doubler ``list`` renverrait ce que le test a décidé, pas ce
    que le code livré produit.
    """
    repo = CampaignRepository.__new__(CampaignRepository)
    repo._fetch_all = lambda q, p=(), *, conn=None: rows  # type: ignore[method-assign]
    repo._fetch_one = lambda q, p=(), *, conn=None: {"n": total}  # type: ignore[method-assign]
    repo._to_model = lambda row: row  # type: ignore[method-assign]
    return repo


# --------------------------------------------------------------------------- #
# Le nom dit ce qu'on reçoit
# --------------------------------------------------------------------------- #

class TestTheTwoShapesAreNamedApart:
    def test_listing_gives_campaigns(self):
        rows = repository([campaign(1), campaign(2)]).list()
        assert [c.code for c in rows] == ["INV-0001", "INV-0002"]

    def test_listing_gives_no_total_alongside(self):
        """C'est le défaut exact : deux éléments là où on en attend n."""
        rows = repository([campaign(1)], total=137).list()
        assert len(rows) == 1

    def test_every_element_is_a_campaign(self):
        """`other.id` sur un `list` est le 500 qu'on a servi en production."""
        for element in repository([campaign(1)], total=137).list():
            assert isinstance(element, Campaign), element

    def test_paging_gives_the_pair(self):
        rows, total = repository([campaign(1)], total=137).page()
        assert [c.code for c in rows] == ["INV-0001"]
        assert total == 137

    def test_the_page_is_the_same_rows_as_the_listing(self):
        """Deux lectures qui divergeraient feraient deux vérités."""
        rows = [campaign(1), campaign(2)]
        assert repository(rows).list() == repository(rows).page()[0]


# --------------------------------------------------------------------------- #
# Les quatre appelants, sur la vraie forme
# --------------------------------------------------------------------------- #

def context(rows: list[Campaign]) -> Any:
    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    ctx.campaigns = repository(rows)
    with_access(ctx)
    return ctx


class TestTheCallersThatScanCampaigns:
    """Chacun de ces trois écrans rendait 500."""

    def test_the_backflush_period_is_computed(self):
        from inventory.services.analysis_service import AnalysisService

        ctx = context([campaign(1, day=1), campaign(2, day=20)])
        period = AnalysisService(ctx).suggested_backflush_period(campaign(2, day=20))
        assert set(period) == {"periodStart", "periodEnd"}

    def test_the_backflush_period_ignores_a_later_campaign(self):
        """La précédente est la plus proche **antérieure**, jamais la plus proche.

        Deux campagnes créées dans un ordre et comptées dans l'autre existent.
        Sans le filtre sur la date de comptage, celle du 28 serait retenue comme
        « précédente » de celle du 20, et la période remonterait à l'envers.
        """
        from inventory.services.analysis_service import AnalysisService

        ctx = context([campaign(1, day=5), campaign(3, day=28)])
        asked = campaign(2, day=20)
        period = AnalysisService(ctx).suggested_backflush_period(asked)
        alone = AnalysisService(context([campaign(1, day=5)]))
        assert period == alone.suggested_backflush_period(asked)

    def test_the_comparable_campaigns_are_listed(self):
        from inventory.services.stock_flow_service import StockFlowService

        ctx = context([campaign(1, day=1), campaign(2, day=20)])
        rows = StockFlowService(ctx).comparable_campaigns(campaign(2, day=20))
        assert [r["code"] for r in rows] == ["INV-0001"]

    def test_a_later_campaign_is_not_comparable(self):
        """La comparaison avance dans le temps : partir d'après est un non-sens."""
        from inventory.services.stock_flow_service import StockFlowService

        ctx = context([campaign(1, day=1), campaign(3, day=28)])
        rows = StockFlowService(ctx).comparable_campaigns(campaign(2, day=20))
        assert [r["code"] for r in rows] == ["INV-0001"]

    def test_a_comparable_campaign_carries_its_fields(self):
        from inventory.services.stock_flow_service import StockFlowService

        ctx = context([campaign(1, day=1), campaign(2, day=20)])
        row = StockFlowService(ctx).comparable_campaigns(campaign(2, day=20))[0]
        assert {"id", "code", "label", "countDate", "status", "weeks"} <= set(row)


# --------------------------------------------------------------------------- #
# Personne ne parcourt la paire
# --------------------------------------------------------------------------- #

class TestNobodyIteratesThePair:
    """Le contrôle structurel, pour les appelants qu'aucun écran n'exerce ici.

    Les deux contrôles précédents frappent trois services. Un quatrième —
    l'assistant — cherche la campagne d'origine d'un clonage, et demande un
    modèle hébergé pour être exercé de bout en bout. Ce qu'on peut vérifier sans
    lui, c'est qu'il appelle la méthode qui rend des campagnes.
    """

    def callers(self) -> list[tuple[str, str]]:
        """(fichier, méthode appelée) pour chaque `ctx.campaigns.<x>(...)`."""
        found = []
        for path in sorted(SERVICES.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                owner = func.value
                if (
                    isinstance(owner, ast.Attribute)
                    and owner.attr == "campaigns"
                    and func.attr in ("list", "page")
                ):
                    found.append((path.name, func.attr))
        return found

    def test_the_scanners_are_found_at_all(self):
        """Un contrôle qui ne trouve rien passerait pour une bonne nouvelle."""
        assert len(self.callers()) >= 4

    def test_only_the_campaign_service_asks_for_the_pair(self):
        """Ailleurs, demander la paire c'est se préparer à l'itérer."""
        asking = {name for name, method in self.callers() if method == "page"}
        assert asking == {"campaign_service.py"}

    @pytest.mark.parametrize(
        "module",
        ["analysis_service.py", "stock_flow_service.py", "assistant_service.py"],
    )
    def test_the_scanners_ask_for_campaigns(self, module):
        methods = {method for name, method in self.callers() if name == module}
        assert methods == {"list"}, methods
