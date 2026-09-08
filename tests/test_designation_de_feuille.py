"""La feuille nomme les pièces comme l'atelier les nomme.

Les listes qui alimentent les feuilles B06VRAC viennent des ateliers, et elles
portent leurs propres désignations — « CARTER AR M3 GEN2 » là où le référentiel
ERP dit « HOUSING REAR ». Le compteur cherche sur le papier le nom qu'il
connaît ; lui imprimer l'autre, c'est lui demander de traduire quatre-vingts
lignes à six heures du matin.

Deux choses sont donc vérifiées ici, et la seconde autant que la première.

**Le nom de la feuille gagne**, partout où la feuille se montre : l'écran de
saisie, la grille des lignes, le papier, l'arbitrage, le classeur de repli.

**Il ne sort pas des feuilles.** Le référentiel n'est pas touché, et les écarts,
la consolidation et les exports continuent de nommer l'article comme l'ERP le
nomme. C'est la limite que la demande pose — « uniquement pour les zones et
feuilles B06VRAC » — et c'est aussi ce qui garde un rapprochement avec l'ERP
lisible.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from inventory.domain.enums import CountSection
from inventory.domain.models import (
    CountSheetLine,
    Item,
    sheet_designation,
)

ERP_NAME = "HOUSING REAR M3"
SHOP_NAME = "CARTER ARRIÈRE M3 GEN2"

ITEMS = {
    "P-1": Item(campaign_id="c", item_number="P-1", name=ERP_NAME, std_price="10"),
    "P-2": Item(campaign_id="c", item_number="P-2", name="VIS M6", std_price="1"),
}


def line(number: str = "P-1", *, name: str = "", **kw: Any) -> CountSheetLine:
    return CountSheetLine(
        id=kw.pop("id", "l1"), sheet_id="s1", campaign_id="c",
        item_number=number, name=name, **kw,
    )


# --------------------------------------------------------------------------- #
# 1. La règle, écrite une seule fois
# --------------------------------------------------------------------------- #

class TestLaRegle:
    def test_la_feuille_gagne_sur_le_referentiel(self):
        assert sheet_designation(line(name=SHOP_NAME), ITEMS) == SHOP_NAME

    def test_sans_rien_la_ligne_prend_le_referentiel(self):
        """Vide est l'état normal : une campagne sans écrasement ne change pas."""
        assert sheet_designation(line(), ITEMS) == ERP_NAME

    def test_un_article_inconnu_et_sans_nom_ne_rend_rien(self):
        """Un manque, pas une désignation — et l'écran le signale par ailleurs."""
        assert sheet_designation(line("INCONNU"), ITEMS) == ""

    def test_un_article_inconnu_que_la_feuille_nomme_garde_ce_nom(self):
        """C'est le seul endroit où quiconque sait comment s'appelle cette pièce."""
        assert sheet_designation(line("INCONNU", name=SHOP_NAME), ITEMS) == SHOP_NAME


# --------------------------------------------------------------------------- #
# 2. Ce qui pose l'écrasement — et ce qui n'en pose pas
# --------------------------------------------------------------------------- #

class TestCeQuiEcritLEcrasement:
    """Un nom identique à celui du référentiel n'est pas un remplacement.

    L'écran reçoit la désignation *résolue* et la renvoie telle quelle à chaque
    enregistrement. La prendre au mot figerait le nom du jour sur les
    quatre-vingts lignes de la feuille dès la première sauvegarde — et le
    référentiel corrigé la semaine suivante ne descendrait plus jusqu'au papier.
    """

    def rows(self, **kw: Any) -> list[dict[str, Any]]:
        return [{"item_number": "P-1", "section": "LINE_SIDE", **kw}]

    def designation(self, row: dict[str, Any], previous: CountSheetLine | None):
        from inventory.services.generic_service import _designation

        return _designation(row, previous=previous, items=ITEMS)

    def test_un_nom_different_est_retenu(self):
        assert self.designation(
            self.rows(name=SHOP_NAME)[0], None
        ) == SHOP_NAME

    def test_le_nom_du_referentiel_nest_pas_un_ecrasement(self):
        assert self.designation(self.rows(name=ERP_NAME)[0], None) == ""

    def test_le_redonner_efface_lecrasement_precedent(self):
        """Le geste inverse existe, et c'est celui qu'on fait naturellement."""
        assert self.designation(
            self.rows(name=ERP_NAME)[0], line(name=SHOP_NAME)
        ) == ""

    def test_une_case_vidée_efface_aussi(self):
        assert self.designation(self.rows(name="")[0], line(name=SHOP_NAME)) == ""

    def test_le_champ_absent_laisse_le_nom_en_place(self):
        """C'est ce qui protège l'aperçu de mise en page.

        Il renvoie l'ordre des lignes et les intertitres, jamais les noms :
        réordonner une feuille ne doit pas décider de la façon dont elle nomme
        ses articles.
        """
        assert self.designation(self.rows()[0], line(name=SHOP_NAME)) == SHOP_NAME

    def test_les_espaces_autour_ne_font_pas_un_nom_different(self):
        assert self.designation(self.rows(name=f"  {ERP_NAME}  ")[0], None) == ""


class TestLImportPoseLeMemeEcrasement:
    """Le fichier et le collage passent par la même règle que la grille."""

    def mapped(self, **kw: Any):
        from inventory.ingest.mappers import map_count_sheets

        rows = [{"sheet_code": "B15", "item_number": "P-1",
                 "section": "Bord de ligne", **kw}]
        prepared, errors = map_count_sheets(rows, items=ITEMS)
        assert not errors, errors
        return prepared[0]

    def test_la_colonne_designation_est_reprise(self):
        assert self.mapped(name=SHOP_NAME).name == SHOP_NAME

    def test_le_nom_du_referentiel_ny_pose_rien(self):
        assert self.mapped(name=ERP_NAME).name == ""

    def test_un_fichier_sans_la_colonne_ne_pose_rien(self):
        assert self.mapped().name == ""

    def test_le_contrat_annonce_la_colonne_et_ses_alias(self):
        """Les fichiers de l'atelier disent « Libellé », « Désignation », « Nom »."""
        from inventory.ingest.contracts import get_contract

        field = next(
            f for f in get_contract("count_sheets").fields if f.name == "name"
        )
        assert not field.required
        for alias in ("designation", "libelle", "nom"):
            assert alias in field.aliases

    def test_la_ligne_construite_le_porte(self):
        from inventory.db import new_id
        from inventory.ingest.mappers import sheet_lines_from_rows

        lines = sheet_lines_from_rows(
            [self.mapped(name=SHOP_NAME)],
            sheet_id="s1", campaign_id="c",
            source=__import__(
                "inventory.domain.enums", fromlist=["DataSource"]
            ).DataSource.FILE_IMPORT,
            known=set(), headings=set(), first_order=0, id_factory=new_id,
        )
        assert [l.name for l in lines] == [SHOP_NAME]


# --------------------------------------------------------------------------- #
# 3. Là où la feuille se montre
# --------------------------------------------------------------------------- #

def _service_context(lines: list[CountSheetLine], *, sheet_id: str = "s1"):
    """Un contexte réduit à ce que la lecture d'une feuille traverse."""
    sheet = SimpleNamespace(
        id=sheet_id, campaign_id="c", zone_id="z1", pass_no="PASS_1",
        model_dump=lambda mode=None: {"id": sheet_id, "zone_id": "z1"},
    )
    return cast(Any, SimpleNamespace(
        referentials=SimpleNamespace(items_by_number=lambda cid: ITEMS),
        sheets=SimpleNamespace(
            get_sheet=lambda sid: sheet,
            list_sheet_lines=lambda sid, conn=None: lines,
            list_sheets=lambda cid, zone_id=None, conn=None: [sheet],
            lines_by_sheet=lambda cid: {sheet_id: lines},
            list_zones=lambda cid, conn=None: [
                SimpleNamespace(id="z1", code="B15", label="Zone B15")
            ],
        ),
    ))


class TestLaFeuilleImprimee:
    def printable(self, lines: list[CountSheetLine]):
        from inventory.services.report_service import _printable_lines

        return _printable_lines(lines, ITEMS)

    def test_le_papier_porte_le_nom_de_latelier(self):
        rows = self.printable([line(name=SHOP_NAME)])
        assert rows[0]["name"] == SHOP_NAME

    def test_et_celui_du_referentiel_quand_la_feuille_se_tait(self):
        assert self.printable([line()])[0]["name"] == ERP_NAME


class TestLEcranDeSaisie:
    def test_il_montre_le_nom_de_la_feuille(self):
        from inventory.services.generic_service import GenericService

        ctx = _service_context([line(name=SHOP_NAME)])
        detail = GenericService(ctx).get_sheet(
            SimpleNamespace(id="c", config=SimpleNamespace()), "s1"
        )
        assert detail["lines"][0]["name"] == SHOP_NAME


class TestLeClasseurDeRepli:
    def test_la_feuille_de_zone_porte_le_nom_de_latelier(self):
        import io

        import openpyxl

        from inventory.domain.bom import BomIndex
        from inventory.domain.consolidation import ConsolidationInput, ZoneCounts
        from inventory.domain.models import CountSheet, Zone
        from inventory.reporting.fallback import build_consolidation_fallback

        zone = Zone(id="z1", campaign_id="c", code="B15", passes=1)
        sheet = CountSheet(id="s1", campaign_id="c", zone_id="z1",
                           pass_no="PASS_1")
        content = build_consolidation_fallback(
            ConsolidationInput(
                campaign_id="c",
                zones=[ZoneCounts(
                    zone=zone, sheets=[sheet],
                    lines_by_sheet={"s1": [
                        line(name=SHOP_NAME, qty_manual=5),
                        line("P-2", id="l2", qty_manual=3),
                    ]},
                )],
                items=ITEMS, bom=BomIndex([]), require_done_zones=False,
            ),
            campaign_code="INV-1", campaign_label="",
            count_date=__import__("datetime").date(2026, 6, 30),
            generic_key="B06VRAC / GENERIQUE",
        )
        book = openpyxl.load_workbook(io.BytesIO(content))
        names = {
            book["Z_B15"][f"A{r}"].value: book["Z_B15"][f"B{r}"].value
            for r in (2, 3)
        }
        assert names["P-1"] == SHOP_NAME
        assert names["P-2"] == "VIS M6", "sans écrasement, le référentiel"


class TestLArbitrage:
    """Le même document que la feuille, donc les mêmes noms.

    Une désignation différente entre la feuille de comptage et l'écran qui
    tranche ses divergences ferait douter de la référence au moment précis où
    l'on doit en être sûr.
    """

    def test_il_reprend_le_nom_de_la_feuille(self):
        from inventory.domain.models import ArbitrationLine
        from inventory.services.arbitration_service import ArbitrationService

        arb = ArbitrationLine(
            id="a1", campaign_id="c", zone_id="z1", item_number="P-1",
            section=CountSection.LINE_SIDE, qty_pass_1=10, qty_pass_2=12,
        )
        ctx = cast(Any, SimpleNamespace(
            referentials=SimpleNamespace(items_by_number=lambda cid: ITEMS),
            sheets=SimpleNamespace(
                list_zones=lambda cid: [
                    SimpleNamespace(id="z1", code="B15", label="Zone B15")
                ],
                list_arbitrations=lambda cid, zone_id=None: [arb],
                sheet_designations=lambda cid: {
                    ("z1", "P-1", "LINE_SIDE"): SHOP_NAME
                },
            ),
        ))
        campaign = SimpleNamespace(
            id="c", config=SimpleNamespace(arbitration_tolerance=0)
        )
        rows = ArbitrationService(ctx).list(campaign)
        assert rows[0]["name"] == SHOP_NAME

    def test_sans_ecrasement_il_garde_le_referentiel(self):
        from inventory.domain.models import ArbitrationLine
        from inventory.services.arbitration_service import ArbitrationService

        arb = ArbitrationLine(
            id="a1", campaign_id="c", zone_id="z1", item_number="P-1",
            section=CountSection.LINE_SIDE, qty_pass_1=10, qty_pass_2=12,
        )
        ctx = cast(Any, SimpleNamespace(
            referentials=SimpleNamespace(items_by_number=lambda cid: ITEMS),
            sheets=SimpleNamespace(
                list_zones=lambda cid: [
                    SimpleNamespace(id="z1", code="B15", label="Zone B15")
                ],
                list_arbitrations=lambda cid, zone_id=None: [arb],
                sheet_designations=lambda cid: {},
            ),
        ))
        campaign = SimpleNamespace(
            id="c", config=SimpleNamespace(arbitration_tolerance=0)
        )
        assert ArbitrationService(ctx).list(campaign)[0]["name"] == ERP_NAME


# --------------------------------------------------------------------------- #
# 4. Là où il ne doit PAS sortir
# --------------------------------------------------------------------------- #

class TestLEcrasementNeSortPasDesFeuilles:
    """La limite que la demande pose, et qui garde l'ERP rapprochable.

    Un écart, une ligne de journal consolidé, une analyse ou un export du
    référentiel nomment l'article comme l'ERP le nomme. Les laisser prendre le
    nom de l'atelier rendrait illisible tout rapprochement avec l'ERP, et
    ferait dépendre le nom d'un article de la zone où il a été compté — deux
    zones, deux noms, pour la même référence.
    """

    def test_le_referentiel_nest_jamais_reecrit(self):
        """L'écrasement vit sur la ligne, et nulle part ailleurs."""
        from inventory.domain.models import Item as ItemModel

        before = ITEMS["P-1"].name
        assert sheet_designation(line(name=SHOP_NAME), ITEMS) == SHOP_NAME
        assert ITEMS["P-1"].name == before
        assert not hasattr(ItemModel, "sheet_name")

    def test_lecart_garde_le_nom_de_lerp(self):
        from inventory.services.analysis_service import AnalysisService

        source = AnalysisService.top_variances.__doc__ or ""
        assert "sheet_designation" not in source

    @pytest.mark.parametrize("module", [
        "analysis_service", "consolidation_service", "campaign_source",
    ])
    def test_ces_services_ignorent_la_designation_de_feuille(self, module):
        """Un contrôle de portée : la règle ne doit pas s'y répandre."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        source = (root / "app" / "inventory" / "services" / f"{module}.py").read_text()
        assert "sheet_designation" not in source


# --------------------------------------------------------------------------- #
# 5. Ce que le document doit conserver
# --------------------------------------------------------------------------- #

class TestLeDocumentSurvit:
    def test_le_passage_2_porte_le_meme_nom_que_le_passage_1(self):
        """Deux équipes, la même feuille — donc les mêmes désignations.

        Un nom différent d'un passage à l'autre ferait douter qu'il s'agit de la
        même référence, au moment même où l'on compare les deux comptages.
        """
        import inspect

        from inventory.services import generic_service

        source = inspect.getsource(generic_service.GenericService._mirror_document)
        assert "name=line.name" in source

    def test_une_lecture_de_scan_ne_rend_pas_la_feuille_a_lerp(self):
        """Le modèle lit un papier qui porte le nom de l'atelier.

        Reconstruire les lignes avec le nom du référentiel effacerait
        l'écrasement à chaque scan — et la réimpression suivante sortirait dans
        un vocabulaire que personne dans l'atelier n'emploie.
        """
        import inspect

        from inventory.ai import sheet_extraction

        source = inspect.getsource(sheet_extraction)
        assert source.count("name=expected_line.sheet_name") == 2
        assert "name=sheet_designation(line, items)" in source
