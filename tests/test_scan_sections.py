"""Le trio feuille + article + section, et rien de moins.

Une feuille de comptage porte légitimement le **même article deux fois**, dans
deux sections différentes — en bord de ligne pour les bacs de la ligne, en
en-cours pour ce qui est monté sur un assemblage non déclaré. Le guide
utilisateur le dit depuis toujours : « c'est le trio feuille + article + section
qui doit être unique, pas l'article ».

Le rapprochement de la lecture IA, lui, indexait les lignes attendues sur la
**référence seule**. Sur une feuille portant ce doublon :

* le dictionnaire n'en gardait qu'une — la dernière — donc la lecture ne rendait
  qu'une ligne au lieu de deux ;
* ``replace_sheet_lines`` supprimait alors logiquement la ligne manquante de la
  feuille ;
* et la quantité relevée sur l'une atterrissait sur la section de l'autre ;
* ``missing_items`` restait vide : rien n'avertissait.

Deux quantités fausses et une ligne perdue, en silence. Ces contrôles portent
sur la clé, sur ce que le modèle doit rendre pour départager, et sur le refus
de deviner quand il ne le rend pas.
"""

from __future__ import annotations

from typing import Any

from inventory.ai.client import LlmResponse
from inventory.ai.sheet_extraction import ExpectedLine, SheetExtractor
from inventory.domain.enums import CountSection
from inventory.domain.models import Item

CAMPAIGN = "camp-1"
SHEET = "sheet-1"

#: La feuille du cas litigieux : MASS-1 des deux côtés, MASS-2 d'un seul.
DOUBLED = [
    ExpectedLine("MASS-1", "CARTER ARRIERE", CountSection.LINE_SIDE, "PCE"),
    ExpectedLine("MASS-1", "CARTER ARRIERE", CountSection.WIP, "PCE"),
    ExpectedLine("MASS-2", "VIS M6", CountSection.LINE_SIDE, "PCE"),
]


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete_json(self, *, system: str, user: str, **kwargs: Any):
        self.prompts.append(user)
        return self.payload, LlmResponse(text="", prompt_tokens=1, completion_tokens=1)


def read(lines, expected=None):
    client = _FakeClient({"lines": lines, "counter_name": "", "unexpected": []})
    counter = [0]

    def next_id() -> str:
        counter[0] += 1
        return f"l{counter[0]}"

    result = SheetExtractor(client=client).extract(
        campaign_id=CAMPAIGN,
        sheet_id=SHEET,
        zone_label="FI ASSY",
        pass_no=1,
        expected=expected if expected is not None else DOUBLED,
        images=[b"page"],
        id_factory=next_id,
    )
    return result, client


def pairs(result):
    return sorted((l.item_number, str(l.section)) for l in result.lines)


def qty_of(result, number: str, section: CountSection):
    for line in result.lines:
        if line.item_number == number and line.section is section:
            return line.qty_imported
    return "ABSENTE"


class TestTheSameArticleInTwoSections:
    def test_both_lines_come_back(self):
        """Une ligne perdue ici est supprimée de la feuille à l'écriture."""
        result, _ = read([
            {"item_number": "MASS-1", "section": "BDL", "qty": 10, "confidence": 0.9},
            {"item_number": "MASS-1", "section": "WIP", "qty": 4, "confidence": 0.9},
            {"item_number": "MASS-2", "section": "BDL", "qty": 7, "confidence": 0.9},
        ])
        assert len(result.lines) == 3
        assert pairs(result) == [
            ("MASS-1", "LINE_SIDE"), ("MASS-1", "WIP"), ("MASS-2", "LINE_SIDE"),
        ]

    def test_each_quantity_lands_on_its_own_section(self):
        """C'est l'autre moitié du défaut : 10 était posé sur la ligne WIP."""
        result, _ = read([
            {"item_number": "MASS-1", "section": "BDL", "qty": 10, "confidence": 0.9},
            {"item_number": "MASS-1", "section": "WIP", "qty": 4, "confidence": 0.9},
        ])
        assert qty_of(result, "MASS-1", CountSection.LINE_SIDE) == 10
        assert qty_of(result, "MASS-1", CountSection.WIP) == 4

    def test_the_same_pair_twice_is_still_counted_once(self):
        """Le dédoublonnage reste, sur le couple au lieu de la référence."""
        result, _ = read([
            {"item_number": "MASS-1", "section": "BDL", "qty": 10, "confidence": 0.9},
            {"item_number": "MASS-1", "section": "BDL", "qty": 99, "confidence": 0.9},
        ])
        assert qty_of(result, "MASS-1", CountSection.LINE_SIDE) == 10

    def test_a_half_read_doubling_reports_the_other_half(self):
        result, _ = read([
            {"item_number": "MASS-1", "section": "BDL", "qty": 10, "confidence": 0.9},
            {"item_number": "MASS-2", "section": "BDL", "qty": 7, "confidence": 0.9},
        ])
        assert result.missing_items == ["MASS-1 [WIP non déclaré]"]
        # La ligne non lue existe quand même, vide, pour être saisie à la main.
        assert qty_of(result, "MASS-1", CountSection.WIP) is None

    def test_the_label_names_the_section_only_when_it_departs(self):
        """« MASS-2 » suffit ; « MASS-1 » tout court ne dirait pas laquelle."""
        result, _ = read([])
        assert set(result.missing_items) == {
            "MASS-1 [bord de ligne]", "MASS-1 [WIP non déclaré]", "MASS-2",
        }


class TestWhenTheSectionIsMissing:
    """Le modèle peut ne pas rendre la section, ou en rendre une inconnue."""

    def test_an_unambiguous_reference_does_not_need_one(self):
        """Exiger une section correcte sur le cas courant ajouterait un mode
        d'échec là où il n'y a rien à départager."""
        result, _ = read(
            [{"item_number": "P-1", "qty": 5, "confidence": 0.9}],
            expected=[ExpectedLine("P-1", "VIS", CountSection.WIP, "PCE")],
        )
        assert qty_of(result, "P-1", CountSection.WIP) == 5

    def test_an_ambiguous_reference_is_refused_not_guessed(self):
        """Poser un comptage d'en-cours sur le bord de ligne fausse deux
        quantités d'un coup, et rien en aval ne peut le rattraper."""
        result, _ = read([
            {"item_number": "MASS-1", "qty": 10, "confidence": 0.9},
            {"item_number": "MASS-2", "section": "BDL", "qty": 7, "confidence": 0.9},
        ])
        assert qty_of(result, "MASS-1", CountSection.LINE_SIDE) is None
        assert qty_of(result, "MASS-1", CountSection.WIP) is None
        assert qty_of(result, "MASS-2", CountSection.LINE_SIDE) == 7

    def test_the_refusal_says_what_to_do(self):
        result, _ = read([{"item_number": "MASS-1", "qty": 10, "confidence": 0.9}])
        note = result.unexpected[0]["note"]
        assert "figure deux fois" in note
        assert "bord de ligne" in note and "WIP" in note
        assert "à la main" in note

    def test_an_unknown_section_label_is_treated_as_missing(self):
        result, _ = read([
            {"item_number": "MASS-1", "section": "???", "qty": 10, "confidence": 0.9},
        ])
        assert result.unexpected[0]["qty"] == 10
        assert qty_of(result, "MASS-1", CountSection.LINE_SIDE) is None

    def test_a_legacy_section_label_still_resolves(self):
        """Les anciens libellés du classeur restent reconnus."""
        result, _ = read([
            {"item_number": "MASS-1", "section": "MOM waiting", "qty": 4,
             "confidence": 0.9},
        ])
        assert qty_of(result, "MASS-1", CountSection.WIP) == 4


class TestTheModelIsAskedForTheSection:
    def test_the_prompt_requires_it(self):
        _, client = read([])
        assert '"section"' in client.prompts[0]

    def test_the_prompt_lists_the_values_the_code_accepts(self):
        """Le rapprochement n'accepte que ces valeurs-là ; les demander en
        toutes lettres est ce qui empêche les deux moitiés de diverger."""
        _, client = read([])
        for section in CountSection:
            assert str(section) in client.prompts[0], section

    def test_the_prompt_says_a_reference_may_appear_twice(self):
        """Sans cette phrase, le modèle rend une entrée par référence."""
        _, client = read([])
        assert "DEUX lignes" in client.prompts[0]

    def test_the_expected_listing_carries_the_section(self):
        _, client = read([])
        assert "[bord de ligne]" in client.prompts[0]
        assert "[WIP non déclaré]" in client.prompts[0]


class TestFreeEntrySheets:
    """Même règle, un cran plus loin : c'est le référentiel qui tranche."""

    def free_read(self, lines):
        client = _FakeClient({"lines": lines, "counter_name": ""})
        counter = [0]

        def next_id() -> str:
            counter[0] += 1
            return f"l{counter[0]}"

        items = {
            "P-1": Item(campaign_id=CAMPAIGN, item_number="P-1", name="VIS"),
        }
        return SheetExtractor(client=client).extract_free_entry(
            campaign_id=CAMPAIGN, sheet_id=SHEET, zone_label="Z", pass_no=1,
            known_items=items, images=[b"page"], id_factory=next_id,
        )

    def test_the_same_reference_in_two_sections_gives_two_lines(self):
        """Le compteur qui l'écrit des deux côtés relève deux quantités."""
        result = self.free_read([
            {"item_number": "P-1", "section": "BDL", "qty": 3, "confidence": 0.9},
            {"item_number": "P-1", "section": "WIP", "qty": 8, "confidence": 0.9},
        ])
        assert len(result.lines) == 2
        assert qty_of(result, "P-1", CountSection.LINE_SIDE) == 3
        assert qty_of(result, "P-1", CountSection.WIP) == 8

    def test_the_same_pair_twice_is_still_one_line(self):
        result = self.free_read([
            {"item_number": "P-1", "section": "BDL", "qty": 3, "confidence": 0.9},
            {"item_number": "P-1", "section": "BDL", "qty": 99, "confidence": 0.9},
        ])
        assert len(result.lines) == 1
        assert qty_of(result, "P-1", CountSection.LINE_SIDE) == 3

    def test_a_plain_reference_keeps_a_plain_label(self):
        """L'étiquette ne se charge de la section que si elle départage."""
        result = self.free_read([
            {"item_number": "P-1", "section": "BDL", "qty": 3, "confidence": 0.2},
        ])
        assert result.low_confidence_items == ["P-1"]

    def test_a_doubled_reference_gets_a_labelled_one(self):
        result = self.free_read([
            {"item_number": "P-1", "section": "BDL", "qty": 3, "confidence": 0.2},
            {"item_number": "P-1", "section": "WIP", "qty": 8, "confidence": 0.2},
        ])
        assert result.low_confidence_items == [
            "P-1 [bord de ligne]", "P-1 [WIP non déclaré]",
        ]
