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


class TestLEnTeteDeLaZoneAtteintLePapier:
    """Le défaut de ce dépôt est « existe mais n'est pas branché ».

    Une colonne posée en base, un paramètre ajouté au générateur de PDF, et
    personne pour relier les deux : la feuille sort avec le texte par défaut et
    rien ne le signale. Ces deux contrôles partent donc de la zone et lisent la
    page.
    """

    @staticmethod
    def _text(payload: bytes) -> str:
        import pypdfium2

        assert payload[:4] == b"%PDF"
        document = pypdfium2.PdfDocument(payload)
        try:
            return "\n".join(
                page.get_textpage().get_text_bounded() for page in document
            )
        finally:
            document.close()

    @pytest.fixture
    def printable(self, db, sheets, sheet):
        """Une zone dont l'en-tête est personnalisé, et sa feuille garnie."""
        from inventory.config import get_settings
        from inventory.services.context import ServiceContext
        from inventory.services.report_service import ReportService

        campaign_id, zone, sh = sheet
        sheets.upsert_sheet_lines(
            [_line(campaign_id, sh.id, 0, item_number="P-00042")], actor="alice"
        )
        sheets.set_section_labels(
            campaign_id, zone.id,
            {"LINE_SIDE": "Stock physique chez Maldaner"},
            actor="alice",
        )
        ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        return ReportService(ctx), ctx.campaigns.get(campaign_id)

    def test_a_l_impression_d_une_feuille(self, printable):
        reports, campaign = printable
        sheet_id = reports.ctx.sheets.list_sheets(campaign.id)[0].id
        payload, _name = reports.counting_sheet_pdf(campaign, sheet_id)

        text = self._text(payload)
        assert "Stock physique chez Maldaner" in text
        assert "Composants en bord de ligne" not in text

    def test_et_a_l_impression_groupee(self, printable):
        """La pile imprimée la veille est le vrai usage : c'est elle qu'on plie.

        Brancher la feuille seule et oublier la pile donnerait deux documents
        différents pour la même zone — celui qu'on relit à l'écran et celui que
        le compteur tient.
        """
        reports, campaign = printable
        payload, _name = reports.all_counting_sheets_pdf(campaign)

        text = self._text(payload)
        assert "Stock physique chez Maldaner" in text
        assert "Composants en bord de ligne" not in text


class TestLImportPoseLesIntertitres:
    """Le fichier est plat ; la feuille est un document.

    Le fichier ne sait dire qu'une chose par ligne : « cet article est sous
    *Stock physique B15* ». C'est à l'import de tirer de cette colonne le
    séparateur qui s'imprimera, et de le poser **une fois**, à sa place.
    """

    @pytest.fixture
    def imported(self, db):
        from inventory.config import get_settings
        from inventory.domain.models import Item
        from inventory.services.context import ServiceContext
        from inventory.services.import_service import ImportService

        campaign_id = make_campaign(db, f"SS-{uuid.uuid4().hex[:8]}")
        ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        campaign = ctx.campaigns.get(campaign_id)
        ctx.referentials.upsert_items(
            [
                Item(campaign_id=campaign_id, item_number=n, name=n)
                for n in ("P-1", "P-2")
            ],
            actor="test",
        )

        def load(text: str) -> None:
            ImportService(ctx).import_count_sheets(
                campaign, mode="paste", text=text
            )

        def lines() -> list:
            zone = next(
                z for z in ctx.sheets.list_zones(campaign_id) if z.code == "Z-SS"
            )
            sheet = ctx.sheets.list_sheets(campaign_id, zone_id=zone.id)[0]
            return ctx.sheets.list_sheet_lines(sheet.id)

        return load, lines

    HEADER = "Feuille\tArticle\tSection\tSous-section\tUnité\n"

    def test_un_separateur_est_pose_au_changement_de_sous_section(self, imported):
        load, lines = imported
        load(self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B6EST\tPCE\n"
            "Z-SS\tP-2\tBDL\tStock physique B6EST\tPCE\n"
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
        ))

        assert [(l.line_kind, l.label or l.item_number) for l in lines()] == [
            (CountLineKind.SUBSECTION, "Stock physique B6EST"),
            (CountLineKind.ARTICLE, "P-1"),
            (CountLineKind.ARTICLE, "P-2"),
            (CountLineKind.SUBSECTION, "Stock physique B15"),
            (CountLineKind.ARTICLE, "P-1"),
        ]

    def test_le_meme_article_sous_deux_intertitres_n_est_pas_un_doublon(
        self, imported
    ):
        """Le refus qu'on lève : deux comptages, à deux endroits."""
        load, lines = imported
        load(self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B6EST\tPCE\n"
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
        ))

        articles = [l for l in lines() if l.line_kind is CountLineKind.ARTICLE]
        assert [l.subsection for l in articles] == [
            "Stock physique B6EST", "Stock physique B15"
        ]

    def test_la_sous_section_est_recopiee_sur_chaque_ligne_d_article(self, imported):
        """Recopiée, et non déduite de l'ordre — c'est elle qui fait la clé."""
        load, lines = imported
        load(self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
            "Z-SS\tP-2\tBDL\tStock physique B15\tPCE\n"
        ))

        articles = [l for l in lines() if l.line_kind is CountLineKind.ARTICLE]
        assert {l.subsection for l in articles} == {"Stock physique B15"}

    def test_recharger_le_meme_fichier_ne_double_rien(self, imported):
        """Ni les articles, ni les séparateurs.

        Un fichier corrigé se recharge : la feuille se complète, elle ne se
        recrée pas. Un intertitre reposé à chaque chargement aurait fait grossir
        la page à chaque correction.
        """
        load, lines = imported
        text = self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
            "Z-SS\tP-2\tBDL\tStock physique B15\tPCE\n"
        )
        load(text)
        before = [(l.line_kind, l.label, l.item_number) for l in lines()]
        load(text)

        assert [(l.line_kind, l.label, l.item_number) for l in lines()] == before

    def test_un_article_ajoute_ne_repose_pas_l_intertitre(self, imported):
        """Le cas que le rechargement à l'identique ne montre pas.

        Un fichier corrigé apporte une référence de plus sous un intertitre déjà
        présent. La ligne, elle, est bien nouvelle : sans garde, elle traînerait
        avec elle un second « Stock physique B15 », et la feuille en gagnerait un
        à chaque correction.
        """
        load, lines = imported
        load(self.HEADER + "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n")
        load(self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
            "Z-SS\tP-2\tBDL\tStock physique B15\tPCE\n"
        ))

        assert [(l.line_kind, l.label or l.item_number) for l in lines()] == [
            (CountLineKind.SUBSECTION, "Stock physique B15"),
            (CountLineKind.ARTICLE, "P-1"),
            (CountLineKind.ARTICLE, "P-2"),
        ]

    def test_une_feuille_sans_sous_section_n_en_recoit_aucune(self, imported):
        """Les campagnes existantes ne bougent pas."""
        load, lines = imported
        load(self.HEADER + "Z-SS\tP-1\tBDL\t\tPCE\n")

        assert [l.line_kind for l in lines()] == [CountLineKind.ARTICLE]

    def test_l_intertitre_appartient_a_sa_section(self, imported):
        """Le même texte sous deux sections donne deux séparateurs.

        « Stock physique B15 » au bord de ligne et « Stock physique B15 » en WIP
        ne chapeautent pas les mêmes lignes : les confondre placerait les
        articles WIP sous le séparateur du bord de ligne.
        """
        load, lines = imported
        load(self.HEADER + (
            "Z-SS\tP-1\tBDL\tStock physique B15\tPCE\n"
            "Z-SS\tP-1\tWIP\tStock physique B15\tPCE\n"
        ))

        headings = [l for l in lines() if l.line_kind is CountLineKind.SUBSECTION]
        assert [str(l.section) for l in headings] == ["LINE_SIDE", "WIP"]


class TestLaFeuilleSeRelitCommeElleSEcrit:
    """L'aller-retour par l'écran, où la mise en page se perdait.

    ``upsert_sheet_lines`` reconstruisait chaque ligne à partir de trois champs
    — article, section, quantité. Un intertitre qui repassait par là revenait en
    ligne d'article sans référence, c'est-à-dire en ligne à jeter : la feuille
    perdait sa forme au premier enregistrement de l'écran.
    """

    @pytest.fixture
    def bench(self, db, sheets, sheet):
        from inventory.config import get_settings
        from inventory.domain.models import Item
        from inventory.services.context import ServiceContext
        from inventory.services.generic_service import GenericService

        campaign_id, zone, sh = sheet
        ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        # Le séquencement l'exige avant toute feuille, et il a raison : une
        # feuille qui liste des références qu'aucun référentiel ne connaît ne
        # peut être comparée à rien.
        ctx.referentials.upsert_items(
            [
                Item(campaign_id=campaign_id, item_number=n, name=n)
                for n in ("P-1", "P-2")
            ],
            actor="test",
        )
        return GenericService(ctx), ctx.campaigns.get(campaign_id), zone, sh

    def test_un_intertitre_survit_a_l_enregistrement(self, bench, sheets):
        service, campaign, _zone, sh = bench
        service.upsert_sheet_lines(campaign, sh.id, [
            {"line_kind": "SUBSECTION", "label": "Stock physique B15"},
            {"item_number": "P-1", "section": "LINE_SIDE"},
        ], replace=True)

        lines = sheets.list_sheet_lines(sh.id)
        assert [(l.line_kind, l.label) for l in lines] == [
            (CountLineKind.SUBSECTION, "Stock physique B15"),
            (CountLineKind.ARTICLE, ""),
        ]

    def test_la_ligne_vide_aussi(self, bench, sheets):
        service, campaign, _zone, sh = bench
        service.upsert_sheet_lines(campaign, sh.id, [
            {"item_number": "P-1"},
            {"line_kind": "SPACER"},
        ], replace=True)

        assert [l.line_kind for l in sheets.list_sheet_lines(sh.id)] == [
            CountLineKind.ARTICLE, CountLineKind.SPACER
        ]

    def test_la_sous_section_se_deduit_de_la_place_de_l_intertitre(
        self, bench, sheets
    ):
        """L'écran envoie un ordre ; la colonne se lit dans cet ordre.

        La faire saisir séparément laisserait les deux formes diverger, et un
        article serait compté sous un intertitre et dédoublonné sous un autre.
        """
        service, campaign, _zone, sh = bench
        service.upsert_sheet_lines(campaign, sh.id, [
            {"line_kind": "SUBSECTION", "label": "Stock physique B6EST"},
            {"item_number": "P-1"},
            {"line_kind": "SPACER"},
            {"item_number": "P-2"},
            {"line_kind": "SUBSECTION", "label": "Stock physique B15"},
            {"item_number": "P-1"},
        ], replace=True)

        lines = sheets.list_sheet_lines(sh.id)
        # Une ligne de mise en page n'appartient à aucun intertitre — elle en
        # est un, ou elle n'est rien. Mais la ligne vide ne **ferme** pas le
        # groupe : sinon un espace laissé par un préparateur couperait « Stock
        # physique B6EST » en deux, et P-2 se retrouverait sous aucun titre.
        assert [(l.line_kind, l.subsection) for l in lines] == [
            (CountLineKind.SUBSECTION, ""),
            (CountLineKind.ARTICLE, "Stock physique B6EST"),
            (CountLineKind.SPACER, ""),
            (CountLineKind.ARTICLE, "Stock physique B6EST"),
            (CountLineKind.SUBSECTION, ""),
            (CountLineKind.ARTICLE, "Stock physique B15"),
        ]

    def test_deplacer_un_intertitre_deplace_ce_qu_il_chapeaute(
        self, bench, sheets
    ):
        service, campaign, _zone, sh = bench
        service.upsert_sheet_lines(campaign, sh.id, [
            {"line_kind": "SUBSECTION", "label": "B6EST"},
            {"item_number": "P-1"},
        ], replace=True)
        first = {l.item_number: l.id for l in sheets.list_sheet_lines(sh.id)}

        service.upsert_sheet_lines(campaign, sh.id, [
            {"id": first["P-1"], "item_number": "P-1"},
            {"line_kind": "SUBSECTION", "label": "B6EST"},
        ], replace=True)

        moved = {l.item_number: l for l in sheets.list_sheet_lines(sh.id)}
        assert moved["P-1"].subsection == ""

    def test_un_genre_inconnu_est_refuse(self, bench):
        """Stocké, il ne casserait qu'à la relecture, sur tous les écrans."""
        from inventory.errors import ValidationError

        service, campaign, _zone, sh = bench
        with pytest.raises(ValidationError):
            service.upsert_sheet_lines(
                campaign, sh.id, [{"line_kind": "TITRE"}], replace=True
            )


class TestUnScanNEffacePasLaMiseEnPage:
    """La lecture IA porte sur des quantités, pas sur la forme du document.

    Elle réécrit la feuille entière avec ce qu'elle a lu sur la photo. Sans
    garde, scanner une feuille comptée effaçait tous ses intertitres : la
    réimpression ne ressemblait plus au papier qu'on venait de scanner.
    """

    def test_les_lignes_de_mise_en_page_restent(self, db, sheets, sheet):
        from inventory.db import new_id

        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, line_kind=CountLineKind.SUBSECTION,
                  label="Stock physique B15"),
            _line(campaign_id, sh.id, 1, item_number="P-1"),
            _line(campaign_id, sh.id, 2, line_kind=CountLineKind.SPACER),
        ], actor="alice")

        sheets.replace_sheet_lines(
            sh.id,
            [_line(campaign_id, sh.id, 0, id=new_id(), item_number="P-1")],
            actor="ia", keep_layout=True,
        )

        kinds = [l.line_kind for l in sheets.list_sheet_lines(sh.id)]
        assert CountLineKind.SUBSECTION in kinds and CountLineKind.SPACER in kinds

    def test_mais_un_article_absent_de_la_lecture_part_bien(self, db, sheets, sheet):
        """La garde ne doit pas transformer « remplacer » en « ajouter »."""
        from inventory.db import new_id

        campaign_id, _zone, sh = sheet
        sheets.upsert_sheet_lines([
            _line(campaign_id, sh.id, 0, item_number="P-1"),
            _line(campaign_id, sh.id, 1, item_number="P-2"),
        ], actor="alice")

        sheets.replace_sheet_lines(
            sh.id,
            [_line(campaign_id, sh.id, 0, id=new_id(), item_number="P-1")],
            actor="ia", keep_layout=True,
        )

        assert [l.item_number for l in sheets.list_sheet_lines(sh.id)] == ["P-1"]


class TestLEcranPoseLesEnTetes:
    """``set_section_labels`` — ce que l'écran d'aperçu enregistre."""

    @pytest.fixture
    def service(self, db, sheets, sheet):
        from inventory.config import get_settings
        from inventory.domain.models import Item
        from inventory.services.context import ServiceContext
        from inventory.services.generic_service import GenericService

        campaign_id, zone, _sh = sheet
        ctx = ServiceContext(actor="test", db=db, settings=get_settings())
        ctx.referentials.upsert_items(
            [Item(campaign_id=campaign_id, item_number="P-1", name="VIS")],
            actor="test",
        )
        return GenericService(ctx), ctx.campaigns.get(campaign_id), zone

    def test_un_texte_se_pose_et_se_relit(self, service, sheets):
        generic, campaign, zone = service
        generic.set_section_labels(
            campaign, zone.id, {"LINE_SIDE": "Stock physique B6EST"}
        )

        stored = {z.id: z for z in sheets.list_zones(campaign.id)}[zone.id]
        assert stored.section_labels == {"LINE_SIDE": "Stock physique B6EST"}

    def test_un_texte_vide_efface_la_personnalisation(self, service, sheets):
        """Vider le champ veut dire « remets le défaut », pas « n'imprime rien ».

        Une bannière vide laisserait le compteur sans la règle sous laquelle il
        compte — exactement ce que ces en-têtes existent pour dire.
        """
        generic, campaign, zone = service
        generic.set_section_labels(campaign, zone.id, {"WIP": "En attente"})
        generic.set_section_labels(campaign, zone.id, {"WIP": "   "})

        stored = {z.id: z for z in sheets.list_zones(campaign.id)}[zone.id]
        assert stored.section_labels == {}

    def test_une_section_inconnue_est_refusee(self, service):
        """Enregistrée, elle ne s'imprimerait jamais et rien ne le dirait."""
        from inventory.errors import ValidationError

        generic, campaign, zone = service
        with pytest.raises(ValidationError):
            generic.set_section_labels(campaign, zone.id, {"ENTREPOT": "B15"})

    def test_une_zone_inconnue_est_refusee(self, service):
        from inventory.errors import NotFoundError

        generic, campaign, _zone = service
        with pytest.raises(NotFoundError):
            generic.set_section_labels(campaign, new_id(), {"WIP": "x"})
