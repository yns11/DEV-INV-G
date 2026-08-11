"""L'export des écarts : ce que le tableur reçoit, et ce que le papier montre.

Ce qui est vérifié ici, c'est la promesse faite à qui clique : « quantité,
valeur et écart bien séparés, pour le stock ERP et pour le stock compté ». Une
cellule qui porterait deux chiffres serait invisible à la relecture et fatale au
premier tableau croisé dynamique.
"""

from __future__ import annotations

import datetime as dt

import pytest

from inventory.reporting.exports import build_variance_pdf
from inventory.services.report_service import variance_columns, variance_row

ROW = {
    "itemNumber": "P-00012345",
    "name": "Stator assemblé M3 GEN2",
    "itemType": "SEMI_FINISHED",
    "category": "STATOR",
    "program": "M3GEN2",
    "warehouseId": "B06",
    "locationId": "PICKING-A1",
    "unit": "PCE",
    "unitCost": 37.55,
    "bookQty": 3420.0,
    "bookValue": 128_421.0,
    "countedQty": 3400.0,
    "countedValue": 127_670.0,
    "varianceQty": -20.0,
    "varianceValue": -751.0,
    "adjustedQty": 0.0,
    "residualQty": -20.0,
    "residualValue": -751.0,
    "isMaterial": True,
    "causeCode": "TRANSFERT",
    "comment": "palette déplacée",
}


def cells(*, by_location: bool) -> dict[str, object]:
    """The row read the way a human reads it: by column heading."""
    headers = variance_columns(by_location=by_location)
    values = variance_row(ROW, by_location=by_location)
    assert len(headers) == len(values), "en-têtes et valeurs désalignés"
    return dict(zip(headers, values, strict=True))


class TestQuantitiesAndValuesAreNeverInTheSameColumn:
    """La demande, mot pour mot — et la raison d'être du fichier."""

    @pytest.mark.parametrize("by_location", [False, True])
    def test_the_erp_stock_gives_a_quantity_and_a_value(self, by_location):
        row = cells(by_location=by_location)
        assert row["Stock ERP qté"] == 3420.0
        assert row["Stock ERP valeur €"] == 128_421.0

    @pytest.mark.parametrize("by_location", [False, True])
    def test_the_counted_stock_gives_a_quantity_and_a_value(self, by_location):
        row = cells(by_location=by_location)
        assert row["Compté qté"] == 3400.0
        assert row["Compté valeur €"] == 127_670.0

    @pytest.mark.parametrize("by_location", [False, True])
    def test_the_variance_too(self, by_location):
        row = cells(by_location=by_location)
        assert row["Écart qté"] == -20.0
        assert row["Écart valeur €"] == -751.0

    def test_every_figure_is_a_number_the_spreadsheet_can_sum(self):
        """Un « 3 420 PCE » en texte est une colonne qu'Excel ne totalise pas."""
        row = cells(by_location=False)
        for column in (
            "Stock ERP qté", "Stock ERP valeur €", "Compté qté",
            "Compté valeur €", "Écart qté", "Écart valeur €",
        ):
            assert isinstance(row[column], (int, float)), column


class TestTheLocationColumns:
    def test_they_appear_only_in_the_detailed_view(self):
        assert "Emplacement" not in variance_columns(by_location=False)
        assert "Emplacement" in variance_columns(by_location=True)

    def test_they_carry_the_warehouse_and_the_location(self):
        row = cells(by_location=True)
        assert (row["Entrepôt"], row["Emplacement"]) == ("B06", "PICKING-A1")

    def test_the_two_layouts_stay_aligned(self):
        """Un décalage d'une colonne mettrait les valeurs sous d'autres titres."""
        for by_location in (False, True):
            cells(by_location=by_location)


class TestNumbersOnPaper:
    """Ce que le PDF montre réellement, relu depuis le PDF."""

    def render(self, rows, *, by_location=False, omitted=0) -> str:
        pdfium = pytest.importorskip("pypdfium2")
        payload = build_variance_pdf(
            campaign_label="Inventaire annuel 2026",
            campaign_code="INV-2026",
            count_date=dt.date(2026, 8, 31),
            rows=rows,
            by_location=by_location,
            material_only=False,
            generated_at=dt.datetime(2026, 8, 11, 14, 30),
            omitted=omitted,
        )
        document = pdfium.PdfDocument(payload)
        return "\n".join(p.get_textpage().get_text_range() for p in document)

    def test_a_quantity_is_never_abbreviated(self):
        """3 420 pièces comptées ne s'écrivent pas « 3k »."""
        text = self.render([ROW])
        assert "3 420" in text
        assert "3k" not in text

    def test_no_character_is_drawn_as_a_black_box(self):
        """Le séparateur de milliers doit exister dans la police employée."""
        assert "■" not in self.render([ROW])

    def test_a_positive_variance_carries_its_sign(self):
        """Sans le signe, un gain se lit comme un niveau de stock."""
        gain = {**ROW, "varianceQty": 28.0, "varianceValue": 11.0}
        assert "+28" in self.render([gain])

    def test_the_total_says_it_only_covers_what_is_printed(self):
        text = self.render([ROW, ROW])
        assert "Total des 2 ligne(s) imprimée(s)" in text

    def test_a_truncation_is_announced_on_the_page(self):
        """Une troncature muette se lit comme un document complet."""
        text = self.render([ROW], omitted=17)
        assert "17 ligne(s)" in text
        assert "Excel" in text

    def test_nothing_is_announced_when_nothing_was_dropped(self):
        assert "ne sont pas reprises" not in self.render([ROW])

    def test_the_location_columns_reach_the_paper(self):
        text = self.render([ROW], by_location=True)
        assert "PICKING-A1" in text
        assert "Emplacement" in text

    def test_the_page_says_which_view_it_is(self):
        """Les deux vues donnent des totaux différents : les confondre en réunion
        coûte une demi-heure d'explication."""
        detailed = self.render([ROW], by_location=True)
        aggregated = self.render([ROW])
        assert "par référence et emplacement" in detailed
        assert "par référence" in aggregated
        assert "et emplacement" not in aggregated
