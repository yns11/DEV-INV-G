"""D'où vient un chiffre — la décomposition, colonne par colonne.

Seule la colonne WIP s'ouvrait ; les autres devaient être crues sur parole. Une
quantité qu'on ne peut pas expliquer est une quantité qu'on ne peut pas
défendre, et la question se pose en réunion, six mois après la campagne.

Ce que ces tests fixent, ce n'est pas la mise en page de la fenêtre : c'est le
contrat que toutes les colonnes partagent. Une seule forme de ligne — origine,
endroit, détail, quantité, valeur — un total calculé sur les lignes rendues et
non ailleurs, et un filtre par emplacement qui restreint sans mentir sur le
total.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import AdjustmentKind, ItemType
from inventory.domain.models import AdjustmentLine, BookStockLine, Item, LocationKey
from inventory.errors import NotFoundError, ValidationError
from inventory.services.analysis_service import AnalysisService

GENERIC = LocationKey(warehouse_id="B06", location_id="VRAC")


def campaign() -> Any:
    return cast(
        Any,
        SimpleNamespace(
            id="camp-1",
            config=SimpleNamespace(generic_key=GENERIC),
        ),
    )


def item(number: str = "ART-1", *, price: str = "10") -> Item:
    return Item(
        campaign_id="camp-1",
        item_number=number,
        name="Stator M3",
        item_type=ItemType.COMPONENT,
        unit="PCE",
        std_price=Decimal(price),
    )


def book_line(place: str, qty: str, *, number: str = "ART-1") -> BookStockLine:
    return BookStockLine(
        campaign_id="camp-1",
        item_number=number,
        warehouse_id="B06",
        location_id=place,
        qty=Decimal(qty),
        unit_cost=Decimal("10"),
    )


def variance(place: str, book: float, counted: float, adjusted: float = 0) -> Any:
    """Une ligne d'écart, réduite à ce que la décomposition en lit.

    Le stock physique est le compté augmenté des mouvements postés après, et
    c'est lui que l'écart mesure — comme partout ailleurs depuis que le physique
    ajusté est devenu la référence.
    """
    physical = counted + adjusted
    return cast(
        Any,
        SimpleNamespace(
            item_number="ART-1",
            warehouse_id="B06",
            location_id=place,
            book_qty=book,
            counted_qty=counted,
            adjusted_qty=adjusted,
            physical_qty=physical,
            variance_qty=physical - book,
            variance_value=(physical - book) * 10,
        ),
    )


def service(
    *,
    items: dict[str, Item] | None = None,
    book: list[BookStockLine] | None = None,
    adjustments: list[AdjustmentLine] | None = None,
    wip: list[dict[str, Any]] | None = None,
) -> AnalysisService:
    ctx = SimpleNamespace(
        referentials=SimpleNamespace(
            items_by_number=lambda cid: items if items is not None else {"ART-1": item()}
        ),
        book_stock=SimpleNamespace(list=lambda cid: book or []),
        adjustments=SimpleNamespace(list=lambda cid, **kw: adjustments or []),
        consolidation=SimpleNamespace(
            wip_breakdown=lambda cid, child_item="": wip or []
        ),
    )
    return AnalysisService(cast(Any, ctx))


class TestTheEnvelope:
    """Ce que la fenêtre reçoit, quelle que soit la colonne cliquée."""

    def test_an_unknown_aspect_is_refused_rather_than_guessed(self):
        with pytest.raises(ValidationError):
            service().breakdown(campaign(), "ART-1", "chiffre-daffaires")

    def test_an_article_absent_du_referentiel_is_a_not_found(self):
        with pytest.raises(NotFoundError):
            service(items={}).breakdown(campaign(), "ART-1", "book")

    def test_the_article_is_matched_after_normalisation(self):
        """Une référence tapée en minuscules désigne le même article."""
        result = service(book=[book_line("ALLEE-A", "12")]).breakdown(
            campaign(), " art-1 ", "book"
        )
        assert result["itemNumber"] == "ART-1"
        assert result["name"] == "Stator M3"
        assert result["unit"] == "PCE"

    def test_the_total_is_the_sum_of_the_rows_shown(self):
        """Un total qui contredit ses propres lignes est pire que pas de total."""
        result = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-B", "8")]
        ).breakdown(campaign(), "ART-1", "book")
        assert result["total"] == 20
        assert result["totalValue"] == 200
        assert sum(row["qty"] for row in result["rows"]) == result["total"]

    def test_a_row_without_its_own_value_is_valued_at_standard_price(self):
        """Les quantités de feuilles n'ont pas de valeur propre : elle se déduit."""
        result = service(
            wip=[{"parent_item": "ENS-9", "child_qty": 4, "zone_code": "Z1"}]
        ).breakdown(campaign(), "ART-1", "wip")
        assert result["rows"][0]["value"] == 40
        assert result["totalValue"] == 40


class TestFilteringByPlace:
    """Une cellule cliquée en vue par emplacement en désigne un seul."""

    def test_the_location_filter_keeps_only_that_bin(self):
        result = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-B", "8")]
        ).breakdown(campaign(), "ART-1", "book", location_id="ALLEE-B")
        assert [row["locationId"] for row in result["rows"]] == ["ALLEE-B"]

    def test_the_total_follows_the_filter(self):
        """Sinon la fenêtre montrerait une ligne et en annoncerait deux."""
        result = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-B", "8")]
        ).breakdown(campaign(), "ART-1", "book", location_id="ALLEE-B")
        assert result["total"] == 8
        assert result["totalValue"] == 80

    def test_the_warehouse_filter_excludes_another_warehouse(self):
        line = book_line("ALLEE-A", "12")
        result = service(book=[line]).breakdown(
            campaign(), "ART-1", "book", warehouse_id="B07"
        )
        assert result["rows"] == []
        assert result["total"] == 0


class TestTheBookStockColumn:
    def test_it_lists_the_bins_the_erp_carried(self):
        rows = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-B", "8")]
        ).breakdown(campaign(), "ART-1", "book")["rows"]
        assert [row["where"] for row in rows] == ["B06 / ALLEE-A", "B06 / ALLEE-B"]

    def test_another_article_is_left_out(self):
        rows = service(
            items={"ART-1": item(), "ART-2": item("ART-2")},
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-A", "5", number="ART-2")],
        ).breakdown(campaign(), "ART-1", "book")["rows"]
        assert len(rows) == 1
        assert rows[0]["qty"] == 12


class TestTheWipColumn:
    def test_each_row_names_the_assembly_that_produced_it(self):
        rows = service(
            wip=[
                {
                    "parent_item": "ENS-9",
                    "child_qty": 4,
                    "parent_qty": 2,
                    "qty_per": 2,
                    "zone_code": "Z1",
                }
            ]
        ).breakdown(campaign(), "ART-1", "wip")["rows"]
        assert rows[0]["origin"] == "ENS-9"
        assert "2 × 2" in rows[0]["detail"]
        assert rows[0]["where"] == "B06 / VRAC"


class TestTheVarianceColumn:
    def test_it_shows_the_gap_bin_by_bin(self, monkeypatch):
        svc = service()
        monkeypatch.setattr(
            svc,
            "variances",
            lambda c, **kw: [variance("ALLEE-A", 10, 7), variance("ALLEE-B", 5, 5)],
        )
        rows = svc.breakdown(campaign(), "ART-1", "variance")["rows"]
        # L'emplacement juste n'a rien à expliquer : il encombrerait la lecture.
        assert [row["locationId"] for row in rows] == ["ALLEE-A"]
        assert rows[0]["qty"] == -3
        assert "ERP 10" in rows[0]["detail"]

    def test_the_total_is_the_net_gap(self, monkeypatch):
        svc = service()
        monkeypatch.setattr(
            svc,
            "variances",
            lambda c, **kw: [variance("ALLEE-A", 10, 7), variance("ALLEE-B", 0, 3)],
        )
        result = svc.breakdown(campaign(), "ART-1", "variance")
        assert result["total"] == 0
        assert result["totalValue"] == 0


class TestThePhysicalColumn:
    """Le stock physique : ce qui a été compté, puis ce qui a bougé après.

    Remplace l'ancienne décomposition « résiduelle », qui retranchait les
    ajustements d'un écart. Ils s'ajoutent maintenant au *comptage*, parce qu'un
    ajustement est un mouvement réel — ce qui se lit comme une colonne de stock
    et non comme une correction appliquée à un écart.
    """

    def adjustment(self, qty: str, value: str) -> AdjustmentLine:
        return AdjustmentLine(
            id="adj-1",
            campaign_id="camp-1",
            item_number="ART-1",
            warehouse_id="B06",
            location_id="ALLEE-A",
            kind=AdjustmentKind.ADJUSTMENT,
            qty=Decimal(qty),
            value=Decimal(value),
            journal_number="AJ-42",
        )

    def rows(self, svc, monkeypatch):
        # Le physique part du compté : on neutralise la consolidation GENERIQUE,
        # qui n'a rien à voir avec ce que ce test vérifie.
        monkeypatch.setattr(svc, "_counted_rows", lambda c, i: [
            {"origin": "Journal de comptage", "where": "B06 / ALLEE-A",
             "warehouseId": "B06", "locationId": "ALLEE-A", "detail": "",
             "qty": 100.0},
        ])
        return svc.breakdown(campaign(), "ART-1", "physical")

    def test_an_adjustment_is_added_not_subtracted(self, monkeypatch):
        """Il déplace du stock : le retrancher inverserait le mouvement."""
        svc = service(adjustments=[self.adjustment("-3", "-30")])
        result = self.rows(svc, monkeypatch)
        assert result["total"] == 97
        assert result["totalValue"] == 970

    def test_a_positive_movement_adds_stock(self, monkeypatch):
        svc = service(adjustments=[self.adjustment("12", "120")])
        assert self.rows(svc, monkeypatch)["total"] == 112

    def test_the_adjustment_row_names_its_journal(self, monkeypatch):
        svc = service(adjustments=[self.adjustment("-3", "-30")])
        rows = self.rows(svc, monkeypatch)["rows"]
        assert rows[1]["detail"] == "AJ-42"
        assert rows[1]["qty"] == -3

    def test_the_movement_is_named_in_french_not_by_its_enum(self, monkeypatch):
        """« Ajustement ADJUSTMENT » n'est pas une phrase écrite exprès."""
        svc = service(adjustments=[self.adjustment("-3", "-30")])
        rows = self.rows(svc, monkeypatch)["rows"]
        assert rows[1]["origin"] == "Ajustement saisi"

    def test_an_adjustment_on_another_article_is_ignored(self, monkeypatch):
        other = self.adjustment("-3", "-30").model_copy(
            update={"item_number": "ART-2"}
        )
        svc = service(adjustments=[other])
        assert self.rows(svc, monkeypatch)["total"] == 100

    def test_the_residual_aspect_no_longer_exists(self):
        """Il ne dénotait plus rien de distinct : l'écart *est* le post-ajustement."""
        with pytest.raises(ValidationError):
            service().breakdown(campaign(), "ART-1", "residual")


class TestLesLignesNullesNeSontPasMontrees:
    """« D'où vient ce chiffre ? » — une ligne à zéro n'en vient pas.

    Ce n'est pas une gêne de confort. Depuis qu'une case vide vaut zéro, une
    référence listée dans quarante zones et trouvée dans deux produit quarante
    lignes : les deux qui expliquent le total, et trente-huit qui ne
    l'expliquent pas. La fenêtre ouverte pour comprendre un chiffre devenait
    l'endroit où le chiffre se perdait.

    Écartées après le calcul de la valeur et avant les totaux : le total reste
    la somme de ce qui est affiché, ce que le reste de ce fichier vérifie déjà
    et qu'un filtre mal placé casserait.
    """

    def test_une_ligne_a_zero_disparait(self):
        result = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-VIDE", "0")]
        ).breakdown(campaign(), "ART-1", "book")
        assert [r["where"] for r in result["rows"]] == ["B06 / ALLEE-A"]

    def test_le_total_ne_bouge_pas_pour_autant(self):
        """Une ligne nulle n'apportait rien : la retirer n'enlève rien."""
        result = service(
            book=[book_line("ALLEE-A", "12"), book_line("ALLEE-VIDE", "0")]
        ).breakdown(campaign(), "ART-1", "book")
        assert result["total"] == 12
        assert sum(r["qty"] for r in result["rows"]) == result["total"]

    def test_une_quantite_negative_reste(self):
        """« Non nulle » et « positive » ne sont pas la même chose : un écart
        négatif est précisément ce qu'on ouvre la fenêtre pour comprendre."""
        result = service(
            book=[book_line("RETOURS", "-4"), book_line("ALLEE-A", "12")]
        ).breakdown(campaign(), "ART-1", "book")
        assert sorted(r["qty"] for r in result["rows"]) == [-4.0, 12.0]

    def test_une_ligne_sans_quantite_mais_avec_une_valeur_reste(self):
        """Le cas qu'un filtre sur la seule quantité aurait fait disparaître —
        avec la valeur qu'il portait, et le total s'en serait trouvé faux."""
        analysis = service(book=[book_line("ALLEE-A", "0")])
        analysis._book_rows = lambda c, i: [  # type: ignore[method-assign]
            {"origin": "Stock ERP", "where": "B06 / A", "warehouseId": "B06",
             "locationId": "A", "detail": "", "qty": 0.0, "value": 30.0},
        ]
        result = analysis.breakdown(campaign(), "ART-1", "book")
        assert len(result["rows"]) == 1
        assert result["totalValue"] == 30

    def test_une_fenetre_qui_n_a_que_des_zeros_est_vide(self):
        """Et l'écran le dit — « aucune ligne » vaut mieux qu'une liste de
        zéros dont le total est zéro."""
        result = service(book=[book_line("ALLEE-A", "0")]).breakdown(
            campaign(), "ART-1", "book"
        )
        assert result["rows"] == [] and result["total"] == 0
