"""La feuille de comptage est un document, pas une liste.

Les feuilles Excel que l'application remplace alternent des intertitres — « Stock
physique B6EST », « Stock physique B15 », « Stock physique chez Maldaner » — et
des lignes vides qui aèrent la page. Ce découpage n'est pas décoratif : il dit au
compteur *où aller*, et c'est lui qui fait qu'un même article revient trois fois
sur la même feuille sans être un doublon.

Ces contrôles portent sur ce que la base sait maintenant en garder : le genre
d'une ligne, le texte d'un intertitre, l'intertitre auquel une ligne d'article
appartient, et les en-têtes de section personnalisés d'une zone.
"""

from __future__ import annotations

import uuid

import pytest
from tests.early_count_db import disposable_database, make_campaign

from inventory.db import new_id
from inventory.db.repositories import SheetRepository
from inventory.domain.enums import CountLineKind, SheetPass
from inventory.domain.models import CountSheetLine, Zone

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def db():
    with disposable_database("inventaire_mise_en_page") as database:
        yield database


@pytest.fixture
def sheets(db):
    return SheetRepository(db)


@pytest.fixture
def sheet(db, sheets):
    """Une zone, sa feuille, et rien d'autre."""
    campaign_id = make_campaign(db, f"MEP-{uuid.uuid4().hex[:8]}")
    zone = sheets.create_zone(
        Zone(id=new_id(), campaign_id=campaign_id, code="ZONE-1"), actor="alice"
    )
    sheets.ensure_sheets(campaign_id, zone.id, [SheetPass.PASS_1], actor="alice")
    return campaign_id, zone, sheets.list_sheets(campaign_id)[0]


def _line(campaign_id, sheet_id, order, **kwargs) -> CountSheetLine:
    base = {
        "id": new_id(),
        "sheet_id": sheet_id,
        "campaign_id": campaign_id,
        "item_number": "",
        "display_order": order,
    }
    return CountSheetLine(**{**base, **kwargs})


class TestLaMiseEnPageSurvitAUnAllerRetour:
    def test_les_trois_genres_de_ligne(self, sheets, sheet):
        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, line_kind=CountLineKind.SUBSECTION,
                  label="Stock physique B6EST"),
            _line(campaign_id, sh.id, 1, item_number="mass-00040707",
                  subsection="Stock physique B6EST"),
            _line(campaign_id, sh.id, 2, line_kind=CountLineKind.SPACER),
        ], actor="alice")

        lines = sheets.list_sheet_lines(sh.id)
        assert [l.line_kind for l in lines] == [
            CountLineKind.SUBSECTION,
            CountLineKind.ARTICLE,
            CountLineKind.SPACER,
        ]

    def test_l_ordre_du_document_est_l_ordre_rendu(self, sheets, sheet):
        """C'est **la place** de l'intertitre qu'il faut conserver.

        Rangé dans une table à côté, il faudrait le réinsérer à l'affichage, à
        l'impression et à la saisie — et les trois finiraient par diverger.
        """
        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 2, line_kind=CountLineKind.SPACER),
            _line(campaign_id, sh.id, 0, line_kind=CountLineKind.SUBSECTION,
                  label="B6EST"),
            _line(campaign_id, sh.id, 1, item_number="mass-00040707"),
        ], actor="alice")

        assert [l.display_order for l in sheets.list_sheet_lines(sh.id)] == [0, 1, 2]

    def test_le_texte_de_l_intertitre(self, sheets, sheet):
        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, line_kind=CountLineKind.SUBSECTION,
                  label="Stock physique chez Maldaner"),
        ], actor="alice")

        [line] = sheets.list_sheet_lines(sh.id)
        assert line.label == "Stock physique chez Maldaner"

    def test_la_sous_section_est_portee_par_la_ligne_d_article(self, sheets, sheet):
        """Recopiée, et non déduite de l'ordre.

        La clé d'unicité doit se calculer sur une ligne **seule** — à l'import,
        où l'ordre du fichier ne veut encore rien dire.
        """
        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, item_number="mass-00040707",
                  subsection="Stock physique B15"),
        ], actor="alice")

        [line] = sheets.list_sheet_lines(sh.id)
        assert line.subsection == "Stock physique B15"

    def test_une_ligne_ordinaire_ne_porte_ni_l_un_ni_l_autre(self, sheets, sheet):
        """Le défaut est l'article : les campagnes existantes ne bougent pas."""
        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, item_number="mass-00040707"),
        ], actor="alice")

        [line] = sheets.list_sheet_lines(sh.id)
        assert line.line_kind is CountLineKind.ARTICLE
        assert line.label == "" and line.subsection == ""


class TestLesEnTetesDeSectionDeLaZone:
    def test_une_zone_neuve_n_en_porte_aucun(self, sheets, sheet):
        """Le dictionnaire vide est l'état normal, pas un manque.

        Une section absente prend le texte par défaut — c'est ce qui permet d'en
        personnaliser une sans recopier les deux autres.
        """
        _campaign_id, zone, _sh = sheet
        assert zone.section_labels == {}

    def test_ils_se_posent_et_se_relisent(self, sheets, sheet):
        campaign_id, zone, _sh = sheet
        sheets.set_section_labels(
            campaign_id, zone.id,
            {"LINE_SIDE": "Composants en bord de ligne"},
            actor="alice",
        )

        stored = {z.id: z for z in sheets.list_zones(campaign_id)}[zone.id]
        assert stored.section_labels == {"LINE_SIDE": "Composants en bord de ligne"}

    def test_ils_appartiennent_a_la_zone_et_non_a_la_feuille(self, sheets, sheet):
        """Les deux passages sont le même document imprimé deux fois.

        Les laisser diverger n'aurait aucun sens : le compteur du passage 2 lit
        la même feuille que celui du passage 1.
        """
        campaign_id, zone, _sh = sheet
        sheets.ensure_sheets(
            campaign_id, zone.id, [SheetPass.PASS_1, SheetPass.PASS_2], actor="alice"
        )
        sheets.set_section_labels(
            campaign_id, zone.id, {"WIP_OK": "MOM : OK"}, actor="alice"
        )

        stored = {z.id: z for z in sheets.list_zones(campaign_id)}[zone.id]
        assert len(sheets.list_sheets(campaign_id)) == 2
        assert stored.section_labels == {"WIP_OK": "MOM : OK"}
