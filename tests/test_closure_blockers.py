"""La clôture est le seul geste irréversible, et elle n'exigeait rien.

Le parcours contrôle sérieusement l'entrée en analyse : stock ERP gelé, journaux
postés, zones terminées. La clôture, elle, ne demandait rien de particulier.
Le paramètre ``blocking_controls`` existait dans la fonction du domaine ; le
service ne le remplissait jamais, et aucune branche ``CLOSED`` n'y était écrite.

Une campagne pouvait donc être scellée avec ses écarts matériels sans
explication et ses référentiels issus d'un chargement amputé. Après quoi plus
rien ne se corrige : c'est la définition de la clôture.

Trois exigences s'ajoutent, et chacune doit suffire seule à refuser.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from conftest import with_access, with_transactions

from inventory.domain.enums import CampaignStatus, ItemType, JournalStatus
from inventory.domain.models import Campaign, Thresholds, VarianceLine
from inventory.domain.workflow import campaign_transition_blockers

CLOSED = CampaignStatus.CLOSED
ANALYSIS = CampaignStatus.ANALYSIS


def codes(blockers) -> set[str]:
    return {b.code for b in blockers}


# --------------------------------------------------------------------------- #
# La règle du domaine
# --------------------------------------------------------------------------- #

class TestTheClosureGate:
    def test_a_clean_campaign_closes(self):
        """Le refus doit être levable, sinon il ne distingue rien."""
        assert campaign_transition_blockers(ANALYSIS, CLOSED) == []

    def test_an_unexplained_material_variance_alone_refuses(self):
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED, unexplained_material=4
        )
        assert codes(blockers) == {"MATERIAL_VARIANCES_UNEXPLAINED"}
        assert "4 écart(s)" in blockers[0].message

    def test_a_partial_import_alone_refuses(self):
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED, rejected_imports=[("items", 3)]
        )
        assert codes(blockers) == {"IMPORTS_WITH_REJECTS"}
        assert "items (3 ligne(s))" in blockers[0].message

    def test_a_missing_publication_alone_refuses(self):
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED, publication_done=False
        )
        assert codes(blockers) == {"PUBLICATION_NOT_DONE"}

    def test_a_clean_import_is_not_a_blocker(self):
        """Zéro ligne refusée n'est pas « des lignes refusées »."""
        assert campaign_transition_blockers(
            ANALYSIS, CLOSED, rejected_imports=[("items", 0), ("boms", 0)]
        ) == []

    def test_the_three_accumulate(self):
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED,
            unexplained_material=1,
            rejected_imports=[("boms", 2)],
            publication_done=False,
        )
        assert codes(blockers) == {
            "MATERIAL_VARIANCES_UNEXPLAINED",
            "IMPORTS_WITH_REJECTS",
            "PUBLICATION_NOT_DONE",
        }

    def test_the_refusal_names_the_grids_concerned(self):
        """« Un import a des rejets » n'est pas actionnable ; le nom l'est."""
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED, rejected_imports=[("items", 1), ("book_stock", 9)]
        )
        assert set(blockers[0].context["targets"]) == {"items", "book_stock"}

    def test_it_stops_naming_after_five_grids(self):
        blockers = campaign_transition_blockers(
            ANALYSIS, CLOSED,
            rejected_imports=[(f"grille{n}", 1) for n in range(8)],
        )
        assert "grille6" not in blockers[0].message
        assert "8 chargement(s)" in blockers[0].message


class TestTheseChecksBelongToClosureOnly:
    """Les exiger ailleurs empêcherait d'avancer pour de mauvaises raisons.

    Un écart matériel sans cause est normal en début d'analyse — c'est
    précisément le travail qui commence. Un chargement amputé se corrige en
    rechargeant, ce qui suppose d'être encore en préparation.
    """

    @pytest.mark.parametrize("target", [CampaignStatus.COUNTING, ANALYSIS])
    def test_no_closure_check_fires_on_an_earlier_transition(self, target):
        blockers = campaign_transition_blockers(
            CampaignStatus.PREPARATION if target is CampaignStatus.COUNTING
            else CampaignStatus.COUNTING,
            target,
            unexplained_material=40,
            rejected_imports=[("items", 12)],
            publication_done=False,
            book_stock_frozen=True,
        )
        assert not (codes(blockers) & {
            "MATERIAL_VARIANCES_UNEXPLAINED",
            "IMPORTS_WITH_REJECTS",
            "PUBLICATION_NOT_DONE",
        })

    def test_the_analysis_gate_still_works(self):
        """La garde qui existait déjà ne doit pas avoir bougé."""
        blockers = campaign_transition_blockers(
            CampaignStatus.COUNTING, ANALYSIS,
            book_stock_frozen=False,
            journal_statuses=[JournalStatus.PENDING],
        )
        assert "BOOK_STOCK_NOT_FROZEN" in codes(blockers)
        assert "JOURNALS_NOT_POSTED" in codes(blockers)


# --------------------------------------------------------------------------- #
# Ce que le service compte réellement
# --------------------------------------------------------------------------- #

PUBLISHED = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)


def campaign(*, published: bool = True) -> Campaign:
    return Campaign(
        id="camp-1", code="INV-2026", label="Inventaire",
        count_date="2026-09-01", status=ANALYSIS,
        created_by="chef@usine", created_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        published_at=PUBLISHED if published else None,
        thresholds=[
            Thresholds(
                item_type=ItemType.COMPONENT,
                value_abs_eur=Decimal("1000"), qty_relative=None,
            )
        ],
    )


def variance(item: str, *, unit_cost: str, counted: str = "150") -> VarianceLine:
    """Un écart dont la valeur se déduit — `variance_value` est un calcul.

    100 en stock ERP, `counted` compté : l'écart vaut la différence multipliée
    par le coût unitaire, et c'est cette valeur que les seuils comparent.
    """
    return VarianceLine(
        campaign_id="camp-1", item_number=item, item_type=ItemType.COMPONENT,
        book_qty=Decimal("100"), counted_qty=Decimal(counted),
        unit_cost=Decimal(unit_cost),
    )


def service(*, variances, analyses, batches, published: bool = True):
    from inventory.services.campaign_service import CampaignService

    ctx = cast(Any, SimpleNamespace(actor="chef@usine", request_id="req-1"))
    with_transactions(ctx)
    ctx.campaigns = SimpleNamespace(get=lambda cid: campaign(published=published))
    ctx.journals = SimpleNamespace(list=lambda cid: [])
    ctx.sheets = SimpleNamespace(
        list_zones=lambda cid: [], list_sheets=lambda cid: [],
        list_arbitrations=lambda cid: [], lines_by_sheet=lambda cid: {},
    )
    ctx.analysis = SimpleNamespace(list_analyses=lambda cid: analyses)
    ctx.imports = SimpleNamespace(latest_per_target=lambda cid: batches)
    ctx.progress = lambda c: SimpleNamespace(
        items=10, zones=1, book_stock_lines=5, book_stock_frozen=True
    )
    with_access(ctx)
    svc = CampaignService(ctx)
    # L'écart lui-même est calculé ailleurs et testé ailleurs : ce qui se
    # vérifie ici, c'est le tri entre expliqué et matériel.
    svc._unexplained_material = (  # type: ignore[method-assign]
        lambda c: CampaignService._unexplained_material(svc, c)
    )
    import inventory.services.analysis_service as analysis_module

    analysis_module.AnalysisService.variances = (  # type: ignore[method-assign]
        lambda self, campaign, granularity="item": variances
    )
    return svc


class TestWhatCountsAsUnexplained:
    def test_a_material_variance_with_no_analysis_counts(self):
        svc = service(
            variances=[variance("P-1", unit_cost="200")], analyses=[], batches=[]
        )
        assert svc._unexplained_material(campaign()) == 1

    def test_an_immaterial_variance_never_counts(self):
        """En dessous du seuil, ce n'est pas un écart qu'on demande d'expliquer."""
        svc = service(
            variances=[variance("P-1", unit_cost="1")], analyses=[], batches=[]
        )
        assert svc._unexplained_material(campaign()) == 0

    def test_an_assigned_cause_explains_it(self):
        from inventory.domain.models import VarianceAnalysis

        svc = service(
            variances=[variance("P-1", unit_cost="200")],
            analyses=[VarianceAnalysis(
                id="a-1", campaign_id="camp-1", item_number="P-1",
                cause_code="SAISIE",
            )],
            batches=[],
        )
        assert svc._unexplained_material(campaign()) == 0

    def test_an_explicit_acceptance_explains_it_too(self):
        """Assumer un résiduel après examen *est* une décision, tracée."""
        from inventory.domain.models import VarianceAnalysis

        svc = service(
            variances=[variance("P-1", unit_cost="200")],
            analyses=[VarianceAnalysis(
                id="a-1", campaign_id="camp-1", item_number="P-1", accepted=True,
            )],
            batches=[],
        )
        assert svc._unexplained_material(campaign()) == 0

    def test_an_empty_analysis_explains_nothing(self):
        """Ouvrir la fiche sans rien décider n'est pas une explication."""
        from inventory.domain.models import VarianceAnalysis

        svc = service(
            variances=[variance("P-1", unit_cost="200")],
            analyses=[VarianceAnalysis(
                id="a-1", campaign_id="camp-1", item_number="P-1", comment="à voir",
            )],
            batches=[],
        )
        assert svc._unexplained_material(campaign()) == 1


class TestOnlyTheLatestLoadOfEachGridCounts:
    """Dix rechargements sont le déroulement normal d'une préparation.

    La question posée à la clôture est « l'état actuel de cette grille vient-il
    d'un chargement amputé », pas « un chargement a-t-il déjà échoué ». Le
    dépôt ne rend donc que le dernier lot par grille, et c'est cette requête
    qui porte la règle.
    """

    def test_the_query_keeps_one_row_per_target(self):
        from inventory.db.repositories import ImportBatchRepository

        repo = ImportBatchRepository.__new__(ImportBatchRepository)
        seen: list[str] = []
        repo._fetch_all = lambda q, p=(), *, conn=None: (  # type: ignore[method-assign]
            seen.append(" ".join(q.split())) or []
        )
        repo.latest_per_target("camp-1")
        assert "DISTINCT ON (target)" in seen[0]
        assert "ORDER BY target, imported_at DESC" in seen[0]

    def test_a_reload_that_fixed_the_file_unblocks_the_closure(self):
        svc = service(
            variances=[], analyses=[],
            batches=[{"target": "items", "rows_rejected": 0}],
        )
        assert svc.transition_readiness("camp-1", CLOSED)["blockers"] == []

    def test_a_grid_still_on_a_partial_load_blocks_it(self):
        svc = service(
            variances=[], analyses=[],
            batches=[{"target": "items", "rows_rejected": 3}],
        )
        blockers = svc.transition_readiness("camp-1", CLOSED)["blockers"]
        assert [b["code"] for b in blockers] == ["IMPORTS_WITH_REJECTS"]


class TestTheArchiveMustExistBeforeTheSeal:
    """La base opérationnelle est vivante ; l'archive est ce qui reste.

    Le job de publication écrit dans Delta, l'application lit Lakebase : les
    deux ne se parlaient pas, et rien côté application ne savait répondre à
    « l'archive existe-t-elle ». Le job pose désormais l'horodatage sur la
    campagne, après son manifeste Delta et jamais avant.
    """

    def test_a_campaign_never_published_cannot_close(self):
        svc = service(variances=[], analyses=[], batches=[], published=False)
        blockers = svc.transition_readiness("camp-1", CLOSED)["blockers"]
        assert [b["code"] for b in blockers] == ["PUBLICATION_NOT_DONE"]

    def test_a_published_campaign_closes(self):
        svc = service(variances=[], analyses=[], batches=[], published=True)
        assert svc.transition_readiness("camp-1", CLOSED)["blockers"] == []

    def test_it_is_not_asked_of_an_earlier_transition(self):
        """Publier avant d'avoir compté n'aurait aucun sens."""
        svc = service(variances=[], analyses=[], batches=[], published=False)
        blockers = svc.transition_readiness("camp-1", ANALYSIS)["blockers"]
        assert "PUBLICATION_NOT_DONE" not in [b["code"] for b in blockers]


class TestTheJobClosesTheLoop:
    """L'horodatage n'a de valeur que si une seule chose le pose, et en dernier."""

    def source(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        return (root / "jobs" / "publish_campaign_to_delta.py").read_text()

    def test_the_job_writes_it_back_to_lakebase(self):
        assert "SET published_at = %(at)s" in self.source()

    def test_it_happens_after_the_delta_manifest(self):
        """Avant, il déclarerait archivée une campagne qui ne l'est pas encore."""
        source = self.source()
        assert source.index('"publication",') < source.index("SET published_at")

    def test_nothing_else_in_the_application_sets_it(self):
        """Une campagne ne se déclare pas publiée elle-même."""
        from pathlib import Path

        app = Path(__file__).resolve().parent.parent / "app"
        offenders = [
            path.name
            for path in app.rglob("*.py")
            if "published_at =" in path.read_text()
        ]
        assert offenders == [], offenders


class TestTheCostOfTheCheck:
    """Ces faits coûtent un calcul d'écarts : ils ne se paient qu'à la clôture.

    Le panneau « ce qui manque pour avancer » est lu à chaque affichage de la
    campagne. Y faire tourner l'analyse complète pour un passage en comptage
    rendrait l'écran lent pour une réponse qui ne dépend pas d'elle.
    """

    def test_no_variance_is_computed_for_an_earlier_transition(self):
        computed: list[str] = []
        svc = service(variances=[], analyses=[], batches=[])
        svc._unexplained_material = (  # type: ignore[method-assign]
            lambda c: computed.append(c.id) or 0
        )
        svc.transition_readiness("camp-1", ANALYSIS)
        assert computed == []

    def test_it_is_computed_for_the_closure(self):
        computed: list[str] = []
        svc = service(variances=[], analyses=[], batches=[])
        svc._unexplained_material = (  # type: ignore[method-assign]
            lambda c: computed.append(c.id) or 0
        )
        svc.transition_readiness("camp-1", CLOSED)
        assert computed == ["camp-1"]
