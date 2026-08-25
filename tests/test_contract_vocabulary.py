"""Un code n'arrive jamais nu sous les yeux de quelqu'un.

Ce qui est arrivé
-----------------
Une capture d'écran de production montrait `LINE_SIDE` à trois endroits à la
fois : dans la liste déroulante d'une grille d'import, dans la colonne
« Section » des feuilles préparées, et dans le fichier Excel exporté puis
envoyé au gestionnaire. Le vocabulaire existait pourtant — « Bord de ligne » —
et il était déclaré dans le frontend depuis le début. Il n'était simplement
branché que sur le filtre.

Ce que ces contrôles tiennent
-----------------------------
1. Toute colonne codée déclare comment ses codes se lisent (aucun oubli).
2. Le libellé revient au code à la relecture : l'application peut exporter ce
   qu'elle affiche sans qu'un aller-retour perde la colonne.
3. Le contrat livre bien ces libellés au frontend — c'est par ce fil qu'ils
   arrivent dans la liste déroulante et dans l'export.
4. Le vocabulaire du serveur et celui du frontend disent la même chose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from inventory.api.responses import GridField
from inventory.domain.enums import CountSection, ExclusionScope, legacy_section_alias
from inventory.ingest.contracts import CONTRACTS, GridContract
from inventory.ingest.mappers import _exclusions
from inventory.ingest.parser import parse_rows

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"


def _coded_fields() -> list[tuple[str, str]]:
    """``(contrat, colonne)`` de toute colonne à valeurs listées."""
    return [
        (key, spec.name)
        for key, contract in CONTRACTS.items()
        for spec in contract.fields
        if spec.choices
    ]


def _spec(contract_key: str, field_name: str):
    contract: GridContract = CONTRACTS[contract_key]
    return next(f for f in contract.fields if f.name == field_name)


def _filled_row(contract: GridContract) -> dict[str, str]:
    """Une ligne dont seules les colonnes obligatoires sont remplies."""
    placeholder = {"number": "1", "integer": "1", "date": "2026-01-31",
                   "datetime": "2026-01-31", "boolean": "non"}
    return {
        f.label: placeholder.get(f.type, "X")
        for f in contract.fields
        if f.required
    }


def _labelled_codes() -> list[tuple[str, str, str, str]]:
    """``(contrat, colonne, code, libellé)`` pour chaque valeur nommée."""
    return [
        (key, spec.name, code, label)
        for key, contract in CONTRACTS.items()
        for spec in contract.fields
        for code, label in spec.choice_labels
        if code
    ]


class TestEveryCodeHasAName:
    def test_there_are_coded_columns_to_check(self):
        """Un contrôle qui ne porterait sur rien ne prouverait rien."""
        assert len(_coded_fields()) >= 8

    @pytest.mark.parametrize("contract_key,field_name", _coded_fields())
    def test_the_column_says_how_its_codes_read(self, contract_key, field_name):
        spec = _spec(contract_key, field_name)
        assert spec.choice_labels, (
            f"{contract_key}.{field_name} propose des valeurs sans dire comment "
            "elles se lisent : la liste déroulante et l'export montreront le code."
        )

    @pytest.mark.parametrize("contract_key,field_name", _coded_fields())
    def test_no_choice_is_left_unnamed(self, contract_key, field_name):
        spec = _spec(contract_key, field_name)
        missing = [c for c in spec.choices if c and c not in spec.labels]
        assert not missing, f"{contract_key}.{field_name} : {missing} sans libellé"

    @pytest.mark.parametrize("contract_key,field_name", _coded_fields())
    def test_no_label_names_a_value_the_column_refuses(self, contract_key, field_name):
        """Un libellé sans code correspondant n'apparaîtrait jamais."""
        spec = _spec(contract_key, field_name)
        stray = [c for c in spec.labels if c not in spec.choices]
        assert not stray, f"{contract_key}.{field_name} : {stray} hors des valeurs"

    @pytest.mark.parametrize("contract_key,field_name", _coded_fields())
    def test_a_label_is_not_the_code_again(self, contract_key, field_name):
        """« LINE_SIDE » comme libellé de `LINE_SIDE` ne traduit rien."""
        spec = _spec(contract_key, field_name)
        echoed = [c for c, lab in spec.choice_labels if c and lab == c]
        assert not echoed, f"{contract_key}.{field_name} : {echoed} non traduits"


class TestALabelReadsBackAsItsCode:
    """Ce que l'application écrit, l'application doit savoir le relire.

    L'export part maintenant en toutes lettres. Sans cette reprise, le fichier
    qu'un gestionnaire exporte, corrige et recharge revenait avec une section
    inconnue sur chaque ligne — sur des lignes que l'outil venait d'écrire.
    """

    @pytest.mark.parametrize("contract_key,field_name,code,label", _labelled_codes())
    def test_the_parser_turns_the_label_back_into_the_code(
        self, contract_key, field_name, code, label
    ):
        contract = CONTRACTS[contract_key]
        spec = _spec(contract_key, field_name)
        if spec.type != "enum":
            pytest.skip("colonne libre : la reprise se fait dans le mapper")
        row = _filled_row(contract)
        row[spec.label] = label
        result = parse_rows(contract, [row])
        assert result.rows, result.errors
        assert result.rows[0][field_name] == code

    @pytest.mark.parametrize("contract_key,field_name,code,label", _labelled_codes())
    def test_the_code_itself_still_loads(self, contract_key, field_name, code, label):
        """La reprise du libellé n'a pas remplacé celle du code."""
        contract = CONTRACTS[contract_key]
        spec = _spec(contract_key, field_name)
        if spec.type != "enum":
            pytest.skip("colonne libre : la reprise se fait dans le mapper")
        row = _filled_row(contract)
        row[spec.label] = code
        result = parse_rows(contract, [row])
        assert result.rows, result.errors
        assert result.rows[0][field_name] == code


class TestTheSectionLabelsAlsoResolveInTheDomain:
    """La section se relit aussi hors contrat : collée à la main, ou scannée."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("Bord de ligne", CountSection.LINE_SIDE),
            ("WIP (à éclater)", CountSection.WIP),
            ("WIP (A ECLATER)", CountSection.WIP),
            ("WIP assemblé", CountSection.WIP_OK),
            ("wip assemble", CountSection.WIP_OK),
        ],
    )
    def test_the_displayed_label_names_a_section(self, label, expected):
        assert legacy_section_alias(label) is expected


class TestTheExclusionLabelsAlsoResolve:
    """La colonne Exclusion s'exporte en toutes lettres, et parfois par deux."""

    @pytest.mark.parametrize(
        "written,expected",
        [
            ("Hors périmètre", {ExclusionScope.ALL}),
            ("Hors GENERIQUE", {ExclusionScope.GENERIC}),
            ("Ignoré en BOM", {ExclusionScope.BOM}),
            ("Ignore en BOM", {ExclusionScope.BOM}),
            (
                "Hors GENERIQUE + Ignoré en BOM",
                {ExclusionScope.GENERIC, ExclusionScope.BOM},
            ),
        ],
    )
    def test_what_the_grid_shows_reads_back(self, written, expected):
        assert _exclusions(written) == expected


class TestTheLabelsReachTheFrontend:
    """Déclarés sans être livrés, ils ne changeraient rien à l'écran."""

    @pytest.mark.parametrize("contract_key,field_name", _coded_fields())
    def test_the_contract_payload_carries_them(self, contract_key, field_name):
        payload = CONTRACTS[contract_key].as_dict()
        field = next(f for f in payload["fields"] if f["name"] == field_name)
        assert field["choiceLabels"] == _spec(contract_key, field_name).labels

    def test_the_response_model_declares_the_field(self):
        """Sans déclaration, le champ tombait du schéma servi au frontend."""
        assert "choice_labels" in GridField.model_fields
        assert GridField.model_fields["choice_labels"].alias == "choiceLabels"

    def test_a_grid_field_serialises_under_its_alias(self):
        field = GridField.model_validate(
            {
                "name": "section",
                "label": "Section",
                "type": "enum",
                "required": False,
                "aliases": [],
                "choices": ["LINE_SIDE"],
                "choiceLabels": {"LINE_SIDE": "Bord de ligne"},
                "help": "",
                "width": 150,
            }
        )
        assert field.model_dump(by_alias=True)["choiceLabels"] == {
            "LINE_SIDE": "Bord de ligne"
        }


class TestTheTwoVocabulariesAgree:
    """Le frontend garde sa propre table pour les grilles hors contrat.

    Les lignes d'une feuille ne viennent pas d'un contrat mais de l'API, et leur
    colonne Section se nomme depuis `format.ts`. Deux tables pour un même
    vocabulaire tiennent tant qu'elles disent la même chose ; ce contrôle est ce
    qui le garantit.
    """

    @staticmethod
    def _frontend_section_labels() -> dict[str, str]:
        source = (FRONTEND / "lib" / "format.ts").read_text(encoding="utf8")
        block = re.search(
            r"SECTION_LABELS: Record<string, string> = \{(.*?)\}", source, re.S
        )
        assert block, "SECTION_LABELS introuvable dans format.ts"
        return dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))

    def test_the_frontend_table_was_actually_read(self):
        assert len(self._frontend_section_labels()) == 3

    def test_it_says_the_same_as_the_contract(self):
        contract_labels = _spec("count_sheets", "section").labels
        assert self._frontend_section_labels() == contract_labels
