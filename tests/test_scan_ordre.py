"""La feuille relue garde la forme de la feuille imprimée.

Le modèle rend les quantités dans l'ordre où il les a vues sur l'image, et cet
ordre-là n'est pas celui du papier : il dépend de la mise en page, du cadrage,
de ce qui a été lu en premier. La lecture renumérotait pourtant la feuille de
zéro à n dans **son** ordre.

Deux conséquences, la seconde bien pire que la première.

Les articles changeaient de place — désagréable sur une feuille de trente
lignes que quelqu'un est en train de recopier ligne à ligne. Et les intertitres,
eux, ne bougeaient pas : le scan ne les connaît pas et ils gardaient leur rang
d'origine. « Stock physique B15 » se retrouvait donc au milieu des articles de
« Stock physique B6EST », c'est-à-dire au-dessus de lignes qu'il ne chapeaute
pas — et c'est cet intertitre qui dit au compteur *où aller chercher* la pièce.

La ligne attendue transporte donc sa place et son intertitre, et la lecture les
lui rend.
"""

from __future__ import annotations

from typing import Any

from inventory.ai.client import LlmResponse
from inventory.ai.sheet_extraction import ExpectedLine, SheetExtractor
from inventory.domain.enums import CountLineKind, CountSection
from inventory.domain.models import CountSheetLine

CAMPAIGN, SHEET = "camp-1", "sheet-1"


class _FakeClient:
    """Rend la lecture telle qu'on la lui donne — l'ordre compris."""

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self.payload = {"lines": lines, "counter_name": "", "unexpected": []}

    def complete_json(self, **kwargs: Any):
        return self.payload, LlmResponse(text="", prompt_tokens=1, completion_tokens=1)


def sheet_line(order: int, number: str, **kwargs) -> CountSheetLine:
    return CountSheetLine(
        id=f"src-{order}", sheet_id=SHEET, campaign_id=CAMPAIGN,
        item_number=number, display_order=order, **kwargs,
    )


def read(expected: list[ExpectedLine], lines: list[dict[str, Any]]):
    counter = [0]

    def next_id() -> str:
        counter[0] += 1
        return f"l{counter[0]}"

    return SheetExtractor(client=_FakeClient(lines)).extract(
        campaign_id=CAMPAIGN, sheet_id=SHEET, zone_label="FI ASSY", pass_no=1,
        expected=expected, images=[b"page"], id_factory=next_id,
    )


class TestLaLignePorteSaPlace:
    """``expected_from_items`` — ce qui part vers la lecture."""

    def test_le_rang_de_la_ligne_voyage_avec_elle(self):
        expected = SheetExtractor(client=None).expected_from_items(
            [sheet_line(7, "P-1"), sheet_line(2, "P-2")], {}
        )
        assert [e.display_order for e in expected] == [7, 2]

    def test_l_intertitre_aussi(self):
        """La clé d'unicité le porte : le perdre dédoublonnerait deux comptages
        faits à deux endroits."""
        expected = SheetExtractor(client=None).expected_from_items(
            [sheet_line(0, "P-1", subsection="Stock physique B15")], {}
        )
        assert expected[0].subsection == "Stock physique B15"

    def test_une_ligne_de_mise_en_page_n_est_pas_une_ligne_attendue(self):
        """Le modèle n'a pas de quantité à lire sur un intertitre ; le lui
        annoncer comme un article attendu lui ferait chercher un chiffre là où
        il n'y en a pas."""
        expected = SheetExtractor(client=None).expected_from_items(
            [
                sheet_line(0, "", line_kind=CountLineKind.SUBSECTION, label="B15"),
                sheet_line(1, "P-1"),
            ],
            {},
        )
        assert [e.item_number for e in expected] == ["P-1"]


#: Trois articles sous deux intertitres. Le rang 3 est celui du second
#: intertitre — un rang que la lecture n'a pas le droit de reprendre.
EXPECTED = [
    ExpectedLine("P-1", "VIS", CountSection.LINE_SIDE, "PCE",
                 display_order=1, subsection="Stock physique B6EST"),
    ExpectedLine("P-2", "ECROU", CountSection.LINE_SIDE, "PCE",
                 display_order=2, subsection="Stock physique B6EST"),
    ExpectedLine("P-3", "RONDELLE", CountSection.LINE_SIDE, "PCE",
                 display_order=4, subsection="Stock physique B15"),
]


class TestLaLectureRendLaFeuilleDansSonOrdre:

    def test_l_ordre_de_lecture_du_modele_ne_devient_pas_l_ordre_de_la_feuille(self):
        """Le défaut, dit tel quel : le modèle lit P-3 en premier."""
        result = read(EXPECTED, [
            {"item_number": "P-3", "qty": 3},
            {"item_number": "P-1", "qty": 1},
            {"item_number": "P-2", "qty": 2},
        ])

        by_number = {l.item_number: l for l in result.lines}
        assert by_number["P-1"].display_order == 1
        assert by_number["P-2"].display_order == 2
        assert by_number["P-3"].display_order == 4

    def test_les_rangs_laisses_libres_le_restent(self):
        """Le 3 est celui d'un intertitre que le scan ne connaît pas.

        Renumérotant de zéro à n, la lecture reprenait ce rang pour un article
        et poussait l'intertitre au milieu du groupe précédent.
        """
        result = read(EXPECTED, [{"item_number": "P-1", "qty": 1}])
        assert 3 not in {l.display_order for l in result.lines}

    def test_l_intertitre_revient_sur_la_ligne_lue(self):
        result = read(EXPECTED, [{"item_number": "P-3", "qty": 3}])
        line = next(l for l in result.lines if l.item_number == "P-3")
        assert line.subsection == "Stock physique B15"

    def test_une_ligne_que_le_modele_n_a_pas_lue_garde_sa_place_aussi(self):
        """C'est celle qu'on va chercher des yeux sur le papier pour la saisir."""
        result = read(EXPECTED, [{"item_number": "P-1", "qty": 1}])

        by_number = {l.item_number: l for l in result.lines}
        assert by_number["P-2"].display_order == 2
        assert by_number["P-3"].display_order == 4
        assert by_number["P-3"].subsection == "Stock physique B15"

    def test_deux_lignes_ne_se_retrouvent_jamais_au_même_rang(self):
        """Le départage se ferait alors sur l'identifiant — c'est-à-dire au
        hasard, et différemment d'une relecture à l'autre."""
        result = read(EXPECTED, [
            {"item_number": "P-2", "qty": 2}, {"item_number": "P-1", "qty": 1},
        ])
        orders = [l.display_order for l in result.lines]
        assert len(orders) == len(set(orders))
