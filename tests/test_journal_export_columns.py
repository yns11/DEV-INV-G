"""L'export réel des lignes de journaux se charge sans être retouché.

Les en-têtes et les valeurs de ces contrôles sont repris tels quels de l'export
post-campagne du 13 juin 2026 (58 345 lignes, 73 journaux). Le fichier n'est pas
versionné — il pèse quinze mégaoctets et contient des données de production —
mais ses colonnes et quelques-unes de ses lignes le sont, parce que c'est
précisément ce qui doit continuer de passer.

Trois choses s'y jouent :

* le journal **porte sa propre référence** (« Stock ERP » = ``OnHandQuantity``),
  ce qui rend un comptage avancé autonome ;
* l'étiquette et le numéro de série arrivent **intacts**, zéros de tête compris ;
* le doublon d'un export de journaux, c'est « Journal ERP + Numéro de ligne » —
  et surtout **pas** (journal, article, entrepôt, emplacement), qui déclarerait
  doublons neuf palettes sur dix du même article au même endroit.
"""

from __future__ import annotations

import pytest

from inventory.ingest.contracts import get_contract
from inventory.ingest.mappers import _identifier, map_journal_lines
from inventory.ingest.parser import (
    _alias_map,
    normalise_header,
    parse_clipboard,
    parse_rows,
)

CONTRACT = get_contract("count_journal_lines")

#: Les en-têtes de l'export, dans leur ordre et leur orthographe d'origine.
EXPORT_HEADERS = (
    "Journal ERP", "Numéro de ligne", "Site", "Entrepôt", "Emplacement",
    "Etiquette", "Numéro de série", "Numéro d'article", "Stock ERP",
    "Qté Comptée", "Statut qualité", "Journal ERP Source", "Description Journal",
    "Type Journal", "Est posté ERP", "Date et heure postage ERP",
)

#: Le champ que chaque en-tête doit désigner. « Journal ERP Source » est le
#: numéro de journal repris depuis l'en-tête ERP : il tombe sur le même champ.
EXPECTED_FIELD = {
    "Journal ERP": "journal_number",
    "Numéro de ligne": "erp_line_number",
    "Site": "site_id",
    "Entrepôt": "warehouse_id",
    "Emplacement": "location_id",
    "Etiquette": "label_id",
    "Numéro de série": "serial_number",
    "Numéro d'article": "item_number",
    "Stock ERP": "qty_on_hand",
    "Qté Comptée": "counted_quantity",
    "Statut qualité": "inventory_status_id",
    "Description Journal": "description",
    "Type Journal": "journal_name_id",
    "Est posté ERP": "is_posted",
    "Date et heure postage ERP": "posted_date_time",
}

#: Deux lignes réelles : la même étiquette part d'un emplacement et arrive dans
#: un autre. C'est le cas dominant de l'export, et ce n'est pas une anomalie.
MOVED_PALLET = (
    "NPEM-521213\t7\tTRE\tATP\tSOL\t001609231\tT12611100220\tmass-00049094"
    "\t1\t0\t\tNPEM-521213\tInventaire par étiquette\tINVE\tYes\t10/06/2026 08:27",
    "NPEM-521213\t8\tTRE\tQUAL\tAPQP C0\t001609231\tT12611100220\tmass-00049094"
    "\t0\t1\t\tNPEM-521213\tInventaire par étiquette\tINVE\tYes\t10/06/2026 08:27",
)


def _paste(*rows: str) -> str:
    return "\n".join(["\t".join(EXPORT_HEADERS), *rows])


class TestTheExportHeadersAreUnderstood:
    @pytest.mark.parametrize("header", sorted(EXPECTED_FIELD))
    def test_each_one_names_the_right_field(self, header):
        spec = _alias_map(CONTRACT).get(normalise_header(header))
        assert spec is not None, f"« {header} » n'est reconnu par aucun champ"
        assert spec.name == EXPECTED_FIELD[header]

    def test_no_alias_is_claimed_twice(self):
        """`_alias_map` retient la première colonne qui revendique un nom.

        Une collision enverrait donc silencieusement une valeur dans le mauvais
        champ — « Stock ERP » dans la quantité comptée, par exemple.
        """
        claimed: dict[str, str] = {}
        for spec in CONTRACT.fields:
            for name in (spec.name, spec.label, *spec.aliases):
                key = normalise_header(name)
                assert key not in claimed or claimed[key] == spec.name, (
                    f"« {name} » est revendiqué par {claimed.get(key)} et {spec.name}"
                )
                claimed[key] = spec.name

    def test_the_whole_export_row_parses(self):
        result = parse_clipboard(CONTRACT, _paste(MOVED_PALLET[0]))
        assert result.errors == []
        assert len(result.rows) == 1


class TestTheJournalCarriesItsOwnReference:
    """« Stock ERP » est ce contre quoi la ligne a été comptée."""

    def test_the_reference_reaches_the_mapped_line(self):
        result = parse_clipboard(CONTRACT, _paste(*MOVED_PALLET))
        lines, errors, _ = map_journal_lines(result.rows)
        assert errors == []
        assert [line.qty_on_hand for line in lines] == [1, 0]
        assert [line.qty for line in lines] == [0, 1]

    def test_a_departure_and_an_arrival_are_not_an_anomaly(self):
        """Un moins ici, un plus là-bas : la palette a bougé, elle n'a pas disparu."""
        result = parse_clipboard(CONTRACT, _paste(*MOVED_PALLET))
        lines, _, _ = map_journal_lines(result.rows)
        assert [line.qty - line.qty_on_hand for line in lines] == [-1, 1]
        assert lines[0].key == ("ATP", "SOL")
        assert lines[1].key == ("QUAL", "APQP C0")

    def test_a_missing_reference_reads_as_zero_not_as_a_refusal(self):
        """La colonne est facultative : un export plus ancien n'en a pas."""
        row = MOVED_PALLET[0].split("\t")
        row[8] = ""
        result = parse_clipboard(CONTRACT, _paste("\t".join(row)))
        lines, errors, _ = map_journal_lines(result.rows)
        assert errors == []
        assert lines[0].qty_on_hand == 0


class TestIdentifiersArriveIntact:
    def test_the_leading_zeros_survive_the_whole_path(self):
        result = parse_clipboard(CONTRACT, _paste(MOVED_PALLET[0]))
        lines, _, _ = map_journal_lines(result.rows)
        assert lines[0].label_id == "001609231"
        assert lines[0].serial_number == "T12611100220"

    def test_the_case_is_not_touched(self):
        """Les clés métier sont majusculées ; une étiquette ne l'est pas."""
        row = MOVED_PALLET[0].split("\t")
        row[5] = "ab00cd"
        result = parse_clipboard(CONTRACT, _paste("\t".join(row)))
        lines, _, _ = map_journal_lines(result.rows)
        assert lines[0].label_id == "ab00cd"

    def test_a_paste_from_excel_does_not_gain_a_decimal_point(self):
        """Un tableur rend l'étiquette en nombre ; « 1609231.0 » n'existe pas.

        Les zéros de tête, eux, sont déjà perdus à ce stade — c'est le tableur
        qui les a mangés, et rien ici ne peut les inventer. La colonne est en
        texte de bout en bout pour que ce cas reste l'exception.
        """
        assert _identifier(1609231.0) == "1609231"

    def test_an_absent_identifier_is_empty_not_none(self):
        assert _identifier(None) == ""


class TestTheDuplicateIsJournalPlusLine:
    def _rows(self, *pairs: tuple[str, int | None]) -> list[dict]:
        return [
            {
                "journal_number": journal,
                "erp_line_number": line,
                "warehouse_id": "ATP",
                "location_id": "SOL",
                "item_number": "MASS-1",
                "counted_quantity": 1,
            }
            for journal, line in pairs
        ]

    def test_the_same_line_number_twice_is_a_duplicate(self):
        result = parse_rows(CONTRACT, self._rows(("NPEM-1", 7), ("NPEM-1", 7)))
        assert len(result.duplicate_keys) == 1

    def test_the_same_number_in_two_journals_is_not(self):
        result = parse_rows(CONTRACT, self._rows(("NPEM-1", 7), ("NPEM-2", 7)))
        assert result.duplicate_keys == []

    def test_ten_labels_of_one_article_in_one_place_are_not_duplicates(self):
        """Le cœur du changement de clé.

        Un journal INVE porte une ligne **par étiquette**. Sous l'ancienne clé —
        journal, article, entrepôt, emplacement — dix palettes du même article au
        même endroit produisaient neuf doublons signalés, sur un export qui n'a
        rien d'anormal : 57 936 des 58 345 lignes du 13 juin sont des lignes
        d'étiquettes.
        """
        rows = [
            {
                "journal_number": "NPEM-1",
                "erp_line_number": number,
                "warehouse_id": "ATP",
                "location_id": "SOL",
                "item_number": "MASS-1",
                "label_id": f"0016092{number:02d}",
                "counted_quantity": 1,
            }
            for number in range(1, 11)
        ]
        result = parse_rows(CONTRACT, rows)
        assert result.duplicate_keys == []
        assert len(result.rows) == 10

    def test_lines_without_a_number_are_not_duplicates_of_each_other(self):
        """Même règle que l'index de la migration 025 : deux absences sont distinctes."""
        result = parse_rows(CONTRACT, self._rows(("NPEM-1", None), ("NPEM-1", None)))
        assert result.duplicate_keys == []
        assert len(result.rows) == 2
