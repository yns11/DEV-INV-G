"""Lire les mouvements de la période dans l'ERP, plutôt que de les retaper.

Trois des quatre étapes de la comparaison se chargeaient à la main : réceptions,
expéditions, rebuts. L'ERP les connaît toutes les trois, et retaper ce qu'un
magasin sait déjà est exactement ce qui a produit l'essentiel des erreurs du
processus Excel. Une erreur de saisie y est en plus invisible : un total de
réceptions faux décale tous les stocks attendus du même montant, et rien à
l'écran n'a l'air anormal.

Ce que ces tests fixent, ce sont les décisions du guide ERP qui portent le
résultat — d'où viennent les chiffres, comment ils sont bornés, et ce que
devient leur signe — puis la règle des grilles éditables : ce qu'on voit à
l'écran *est* l'étape, donc l'enregistrer remplace, et ce qu'une main a validé
ne se présente plus comme une lecture ERP.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import FlowKind, FlowSource, ItemType
from inventory.domain.models import Item, StockFlowInput, StockFlowRun
from inventory.errors import ValidationError
from inventory.ingest.erp import ErpReader
from inventory.services.stock_flow_service import StockFlowService

MONDAY_START = dt.date(2026, 3, 30)
MONDAY_END = dt.date(2026, 6, 29)


def reader() -> ErpReader:
    return ErpReader(warehouse_id="w-1")


def query(kind: FlowKind) -> str:
    _, statement = reader()._movement_query(
        kind, start="2026-03-30", end="2026-06-29", limit=1000
    )
    return " ".join(statement.split())


class TestWhereEachQuantityIsRead:
    """Le guide désigne une table par domaine, et ce choix n'est pas neutre."""

    def test_receipts_come_from_the_supplier_packing_slips(self):
        """Des lignes rattachées à une commande : un chiffre contesté s'ouvre."""
        assert "vend_packing_slip_trans" in query(FlowKind.RECEIPT)

    def test_shipments_come_from_the_customer_packing_slips(self):
        assert "cust_packing_slip_trans" in query(FlowKind.SHIPMENT)

    def test_scrap_is_identified_by_its_bin_not_by_a_journal(self):
        """Production NOK, blocage qualité et sortie manuelle passent par trois
        journaux différents et finissent tous au même emplacement. Filtrer sur
        le journal en manquerait deux sur trois."""
        sql = query(FlowKind.SCRAP)
        assert "invent_trans" in sql
        assert "invent_dim" in sql
        assert "UPPER(d.`inventlocationid`) = 'QUAL VRAC'" in sql
        assert "UPPER(d.`wmslocationid`) = 'QUA REBUT'" in sql

    def test_every_query_excludes_deleted_rows(self):
        for kind in FlowKind:
            assert "IsDelete" in query(kind)

    def test_every_query_is_scoped_to_one_legal_entity(self):
        """Sans ce filtre, la comparaison d'un site reçoit les flux d'un autre."""
        for kind in FlowKind:
            assert "`dataareaid` = 'NPEM'" in query(kind)


class TestTheBoundsMatchTheOtherHalfOfTheComparison:
    def test_the_end_is_exclusive_like_the_backflush_window(self):
        """Une borne incluse ici et exclue là compterait deux fois le dernier
        lundi, dans une seule et même comparaison."""
        for kind in FlowKind:
            sql = query(kind)
            assert ">= DATE '2026-03-30'" in sql
            assert "<  DATE '2026-06-29'".replace("  ", " ") in sql

    def test_a_period_that_is_not_two_mondays_is_refused(self):
        with pytest.raises(ValidationError):
            reader().fetch_movements(
                FlowKind.RECEIPT,
                period_start=dt.date(2026, 4, 1),
                period_end=MONDAY_END,
                limit=10,
            )


class TestConfigurationCannotSmuggleSqlIn:
    def test_a_quote_in_a_configured_value_is_refused(self):
        """Ces valeurs viennent du déploiement, pas d'une requête — mais elles
        sont interpolées, et une erreur de configuration doit se dire."""
        rdr = reader()
        rdr._settings = rdr._settings.model_copy(
            update={"erp_legal_entity": "NPEM' OR '1'='1"}
        )
        with pytest.raises(ValidationError):
            rdr._movement_query(
                FlowKind.RECEIPT, start="2026-03-30", end="2026-06-29", limit=10
            )


# --------------------------------------------------------------------------- #
# Le service
# --------------------------------------------------------------------------- #

def run() -> StockFlowRun:
    return StockFlowRun(
        id="run-1",
        campaign_id="camp-1",
        baseline_campaign_id="camp-0",
        period_start=MONDAY_START,
        period_end=MONDAY_END,
    )


def campaign() -> Any:
    return cast(Any, SimpleNamespace(id="camp-1", code="INV-2026-06"))


def items() -> dict[str, Item]:
    return {
        "ART-1": Item(campaign_id="camp-1", item_number="ART-1", unit="PCE",
                      item_type=ItemType.COMPONENT, name="Stator"),
        "ART-2": Item(campaign_id="camp-1", item_number="ART-2", unit="KG",
                      item_type=ItemType.COMPONENT),
    }


class Recorder:
    """Le dépôt, réduit à ce que ces tests observent."""

    def __init__(self) -> None:
        self.written: list[StockFlowInput] = []
        self.refreshed: list[tuple[str, FlowKind]] = []
        self.scrap_marked = False
        self.erp: list[Any] = []

    def get_run(self, run_id: str) -> StockFlowRun:
        return run()

    def replace_inputs(self, run_id, kind, lines, *, conn=None) -> int:
        self.written = list(lines)
        return len(lines)

    def replace_erp(self, run_id, lines, *, conn=None) -> int:
        self.erp = list(lines)
        return len(lines)

    def mark_refreshed(self, run_id, kind, *, at, actor, conn=None) -> None:
        self.refreshed.append((run_id, kind))

    def mark_scrap_loaded(self, run_id, *, actor) -> None:
        self.scrap_marked = True


def service(repo: Recorder) -> StockFlowService:
    @contextmanager
    def transaction():
        yield None

    ctx = SimpleNamespace(
        actor="testeur",
        guard=lambda campaign, aspect: None,
        db=SimpleNamespace(transaction=transaction),
        settings=SimpleNamespace(max_import_rows=200_000),
        referentials=SimpleNamespace(items_by_number=lambda cid: items()),
        stock_flow=repo,
        record=lambda **kw: "evt",
    )
    return StockFlowService(cast(Any, ctx))


def erp_returning(rows: list[dict[str, Any]], monkeypatch) -> Recorder:
    """Un lecteur ERP qui rend *rows*, et le dépôt qui reçoit le résultat."""
    monkeypatch.setattr(ErpReader, "fetch_movements", lambda self, k, **kw: rows)
    monkeypatch.setattr(ErpReader, "movements_source", lambda self, k: "table")
    return Recorder()


class TestReadingOneStepFromTheErp:
    def test_the_quantities_land_in_the_step(self, monkeypatch):
        repo = erp_returning(
            [{"item_number": "ART-1", "qty": 120}], monkeypatch
        )
        result = service(repo).refresh_movements(
            campaign(), "run-1", FlowKind.RECEIPT
        )
        assert result["items"] == 1
        assert repo.written[0].item_number == "ART-1"
        assert repo.written[0].qty == 120

    def test_they_are_marked_as_read_from_the_erp(self, monkeypatch):
        """« Lu dans l'ERP » et « saisi à la main » ne se défendent pas pareil."""
        repo = erp_returning([{"item_number": "ART-1", "qty": 5}], monkeypatch)
        service(repo).refresh_movements(campaign(), "run-1", FlowKind.RECEIPT)
        assert repo.written[0].source is FlowSource.ERP

    def test_the_unit_comes_from_the_campaign_referential(self, monkeypatch):
        repo = erp_returning([{"item_number": "ART-2", "qty": 5}], monkeypatch)
        service(repo).refresh_movements(campaign(), "run-1", FlowKind.RECEIPT)
        assert repo.written[0].unit == "KG"

    def test_an_article_outside_the_campaign_is_counted_not_written(
        self, monkeypatch
    ):
        """« 0 article » sans dire pourquoi est ce qui a déjà coûté une soirée."""
        repo = erp_returning([
            {"item_number": "ART-1", "qty": 10},
            {"item_number": "HORS-PERIMETRE", "qty": 999},
        ], monkeypatch)
        result = service(repo).refresh_movements(
            campaign(), "run-1", FlowKind.RECEIPT
        )
        assert result["items"] == 1
        assert result["rowsRead"] == 2
        assert result["outOfScope"] == 1

    def test_the_reference_is_normalised_before_being_matched(self, monkeypatch):
        """L'ERP écrit ses identifiants comme il veut ; le référentiel les
        stocke normalisés. Comparer les deux bruts écartait des lignes valides."""
        repo = erp_returning([{"item_number": " art-1 ", "qty": 7}], monkeypatch)
        result = service(repo).refresh_movements(
            campaign(), "run-1", FlowKind.RECEIPT
        )
        assert result["items"] == 1
        assert repo.written[0].item_number == "ART-1"

    def test_the_direction_belongs_to_the_step_not_to_the_sign(self, monkeypatch):
        """L'ERP signe un rebut négativement ; l'étape le retranche déjà. Le
        stocker signé le rajouterait au stock."""
        repo = erp_returning([{"item_number": "ART-1", "qty": -40}], monkeypatch)
        result = service(repo).refresh_movements(
            campaign(), "run-1", FlowKind.SCRAP
        )
        assert repo.written[0].qty == 40
        # Le signe net reste reporté : une période dont les retours dépassent
        # les expéditions doit se voir plutôt que d'être retournée en silence.
        assert result["netQty"] == -40

    def test_reading_scrap_marks_the_optional_step_as_provided(self, monkeypatch):
        repo = erp_returning([], monkeypatch)
        service(repo).refresh_movements(campaign(), "run-1", FlowKind.SCRAP)
        assert repo.scrap_marked is True

    def test_the_read_is_dated_on_its_own_step(self, monkeypatch):
        """Quatre tables : l'une qui échoue ne doit pas vieillir les trois autres."""
        repo = erp_returning([], monkeypatch)
        service(repo).refresh_movements(campaign(), "run-1", FlowKind.SHIPMENT)
        assert repo.refreshed == [("run-1", FlowKind.SHIPMENT)]


class TestLoadingEverythingAtOnce:
    def test_one_step_failing_does_not_cancel_the_others(self, monkeypatch):
        """« Les réceptions sont là, les rebuts non » est un état utilisable."""
        def fetch(self, kind, **kw):
            if kind is FlowKind.SCRAP:
                raise ValidationError("Table indisponible.")
            return [{"item_number": "ART-1", "qty": 3}]

        monkeypatch.setattr(ErpReader, "fetch_movements", fetch)
        monkeypatch.setattr(ErpReader, "movements_source", lambda self, k: "t")
        svc = service(Recorder())
        monkeypatch.setattr(svc, "refresh_erp", lambda c, r: {"items": 4})

        result = svc.refresh_all(campaign(), "run-1")
        assert result["loaded"] == 3
        assert result["failed"] == 1
        failed = next(s for s in result["steps"] if not s["ok"])
        assert failed["kind"] == "SCRAP"
        assert "indisponible" in failed["error"]

    def test_the_four_measures_are_reported_one_by_one(self, monkeypatch):
        monkeypatch.setattr(ErpReader, "fetch_movements", lambda self, k, **kw: [])
        monkeypatch.setattr(ErpReader, "movements_source", lambda self, k: "t")
        svc = service(Recorder())
        monkeypatch.setattr(svc, "refresh_erp", lambda c, r: {"items": 0})
        result = svc.refresh_all(campaign(), "run-1")
        assert [s["kind"] for s in result["steps"]] == [
            "RECEIPT", "SHIPMENT", "SCRAP", "ERP"
        ]


class TestSavingAnEditedGrid:
    def test_what_the_grid_shows_replaces_the_step(self):
        """Une ligne supprimée à l'écran doit disparaître : fusionner ferait de
        la suppression la seule correction que la grille ne sait pas exprimer."""
        repo = Recorder()
        service(repo).save_inputs(campaign(), "run-1", FlowKind.RECEIPT, [
            {"itemNumber": "ART-1", "qty": 42, "unit": "PCE"},
        ])
        assert [line.item_number for line in repo.written] == ["ART-1"]

    def test_an_emptied_grid_empties_the_step(self):
        repo = Recorder()
        result = service(repo).save_inputs(
            campaign(), "run-1", FlowKind.RECEIPT, []
        )
        assert result["rows"] == 0
        assert repo.written == []

    def test_saving_marks_the_whole_step_as_a_human_figure(self):
        """Une main est passée sur la grille et l'a validée : prétendre que les
        lignes non touchées restent « lues dans l'ERP » serait une distinction
        que l'écran ne sait pas honorer."""
        repo = Recorder()
        service(repo).save_inputs(campaign(), "run-1", FlowKind.RECEIPT, [
            {"itemNumber": "ART-1", "qty": 42},
        ])
        assert repo.written[0].source is FlowSource.MANUAL

    def test_an_unknown_reference_is_named_rather_than_dropped(self):
        """C'est presque toujours une faute de frappe, et elle se corrige."""
        repo = Recorder()
        result = service(repo).save_inputs(
            campaign(), "run-1", FlowKind.RECEIPT,
            [{"itemNumber": "ART-1", "qty": 1}, {"itemNumber": "ART-404", "qty": 9}],
        )
        assert result["rows"] == 1
        assert result["unknown"] == ["ART-404"]

    def test_a_blank_row_is_ignored_not_rejected(self):
        """La grille en garde toujours une en bas : ce n'est pas une erreur."""
        repo = Recorder()
        result = service(repo).save_inputs(
            campaign(), "run-1", FlowKind.RECEIPT,
            [{"itemNumber": "ART-1", "qty": 1}, {"itemNumber": "", "qty": ""}],
        )
        assert result["rows"] == 1
        assert result["unknownCount"] == 0

    def test_a_negative_quantity_is_stored_as_its_magnitude(self):
        """Le sens vient de l'étape : une expédition saisie en négatif serait
        ajoutée au stock au lieu d'en être retirée."""
        repo = Recorder()
        service(repo).save_inputs(campaign(), "run-1", FlowKind.SHIPMENT, [
            {"itemNumber": "ART-1", "qty": -70},
        ])
        assert repo.written[0].qty == 70


class TestSavingTheProductionGrid:
    def test_both_measures_are_written(self):
        repo = Recorder()
        result = service(repo).save_erp(campaign(), "run-1", [
            {"itemNumber": "ART-1", "producedQty": 100, "consumedQty": 250},
        ])
        assert result["rows"] == 1
        assert repo.erp[0].produced_qty == 100
        assert repo.erp[0].consumed_qty == 250

    def test_a_corrected_line_stops_claiming_to_come_from_the_erp(self):
        repo = Recorder()
        service(repo).save_erp(campaign(), "run-1", [
            {"itemNumber": "ART-1", "producedQty": 1, "consumedQty": 2},
        ])
        assert repo.erp[0].source is FlowSource.MANUAL

    def test_these_two_keep_their_sign(self):
        """Une consommation négative est une non-consommation, pas une saisie
        à l'envers : contrairement aux étapes, le signe porte ici l'information."""
        repo = Recorder()
        service(repo).save_erp(campaign(), "run-1", [
            {"itemNumber": "ART-1", "producedQty": 0, "consumedQty": -30},
        ])
        assert repo.erp[0].consumed_qty == Decimal("-30")
