"""Un tableau affiché part vers Excel tel qu'il est.

Ce qui compte ici, c'est que le fichier corresponde à l'écran : les lignes
viennent du client parce que ce sont les siennes — filtrées, triées,
sélectionnées — et non celles qu'un serveur reconstruirait à partir des
paramètres de la requête, ce qui marcherait pour deux grilles et mentirait pour
les dix autres.
"""

from __future__ import annotations

import datetime as dt
import io
from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.services.report_service import ReportService

CAMPAIGN = cast(
    Any,
    SimpleNamespace(id="camp-1", code="INV-2026", label="Inventaire annuel",
                    count_date=dt.date(2026, 8, 31)),
)


def service() -> tuple[ReportService, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    ctx = SimpleNamespace(record=lambda **kw: events.append(kw) or "evt")
    return ReportService(cast(Any, ctx)), events


def workbook(payload: bytes):
    openpyxl = pytest.importorskip("openpyxl")
    return openpyxl.load_workbook(io.BytesIO(payload))


COLUMNS = [("item_number", "Article"), ("qty", "Quantité"), ("value", "Valeur €")]
ROWS = [
    {"item_number": "P-1", "qty": 3420, "value": 128_431.0},
    {"item_number": "P-2", "qty": 12, "value": 4.8},
]


class TestWhatReachesTheSpreadsheet:
    def test_the_columns_are_the_ones_the_grid_showed(self):
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS
        )
        sheet = workbook(payload)["Articles"]
        assert [c.value for c in sheet[1]] == ["Article", "Quantité", "Valeur €"]

    def test_the_rows_follow_the_column_order_not_the_dict_order(self):
        """Un dictionnaire n'a pas d'ordre que l'utilisateur ait choisi."""
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN,
            title="Articles",
            columns=COLUMNS,
            rows=[{"value": 4.8, "item_number": "P-2", "qty": 12}],
        )
        sheet = workbook(payload)["Articles"]
        assert [c.value for c in sheet[2]] == ["P-2", 12, 4.8]

    def test_a_missing_key_becomes_an_empty_cell_rather_than_an_error(self):
        """Une colonne ajoutée à une grille manque aux lignes plus anciennes ;
        c'est un détail d'affichage, pas une raison de refuser le fichier."""
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=[{"item_number": "P-3"}]
        )
        sheet = workbook(payload)["Articles"]
        assert [c.value for c in sheet[2]] == ["P-3", None, None]

    def test_numbers_stay_numbers(self):
        """« 3 420 » en texte est une colonne qu'Excel ne totalise pas."""
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS
        )
        sheet = workbook(payload)["Articles"]
        assert isinstance(sheet.cell(row=2, column=2).value, (int, float))

    def test_a_value_column_is_formatted_as_money(self):
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS
        )
        sheet = workbook(payload)["Articles"]
        assert "€" in sheet.cell(row=2, column=3).number_format

    def test_the_table_is_filterable_the_moment_it_opens(self):
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS
        )
        assert workbook(payload)["Articles"].auto_filter.ref is not None

    def test_an_empty_selection_still_produces_a_usable_file(self):
        """Avec ses en-têtes : un fichier vide qui dit quoi remplir vaut mieux
        qu'un refus au moment où quelqu'un croit avoir exporté."""
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=[]
        )
        sheet = workbook(payload)["Articles"]
        assert [c.value for c in sheet[1]] == ["Article", "Quantité", "Valeur €"]


class TestProvenance:
    def test_the_file_says_what_it_is_a_picture_of(self):
        report, _ = service()
        payload, _ = report.table_export(
            CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS
        )
        sheet = workbook(payload)["Provenance"]
        text = "\n".join(
            str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value
        )
        assert "INV-2026" in text
        assert "Articles" in text
        assert "filtres" in text

    def test_the_export_is_recorded(self):
        report, events = service()
        report.table_export(CAMPAIGN, title="Articles", columns=COLUMNS, rows=ROWS)
        (event,) = events
        assert "Articles" in event["summary"]
        assert "2 ligne(s)" in event["summary"]


class TestTheFilename:
    def test_it_names_the_table_and_the_campaign(self):
        report, _ = service()
        _, filename = report.table_export(
            CAMPAIGN, title="Lignes de feuilles", columns=COLUMNS, rows=ROWS
        )
        assert filename == "lignes-de-feuilles_INV-2026.xlsx"

    def test_an_accented_title_still_gives_a_usable_name(self):
        report, _ = service()
        _, filename = report.table_export(
            CAMPAIGN, title="Écarts par référence", columns=COLUMNS, rows=ROWS
        )
        assert filename.endswith("_INV-2026.xlsx")
        assert " " not in filename
