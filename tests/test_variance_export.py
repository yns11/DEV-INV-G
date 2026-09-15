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
    "varianceQty": -15.0,
    "varianceValue": -563.25,
    # Un ajustement de +5 a été posté après le comptage : le stock physique
    # vaut donc 3 405, et c'est lui que l'écart mesure. L'écart du comptage
    # seul — −20 — reste exporté à côté, parce que la différence entre les deux
    # est exactement ce que l'ajustement a fait.
    "adjustedQty": 5.0,
    "physicalQty": 3405.0,
    "countedVarianceQty": -20.0,
    "countedVarianceValue": -751.0,
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
        """L'écart exporté est celui du stock *physique*, ajustements compris."""
        row = cells(by_location=by_location)
        assert row["Écart qté"] == -15.0
        assert row["Écart valeur €"] == -563.25

    @pytest.mark.parametrize("by_location", [False, True])
    def test_the_count_before_adjustments_keeps_its_own_columns(self, by_location):
        """Sans elles, on ne pourrait pas lire ce que l'ajustement a changé."""
        row = cells(by_location=by_location)
        assert row["Physique qté"] == 3405.0
        assert row["Ajusté qté"] == 5.0
        assert row["Écart avant ajust. qté"] == -20.0

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


# --------------------------------------------------------------------------- #
# Le papier ne porte que ce qui s'écarte
# --------------------------------------------------------------------------- #


def _service(rows):
    """Un `ReportService` dont les lignes sont données, sans base.

    Ce qui est en cause est le tri que fait `variance_pdf` entre ce qui mérite
    une rangée de papier et ce qui n'en mérite pas. D'où viennent les lignes est
    une autre question, déjà tenue ailleurs : la faire intervenir ici ferait
    dépendre ce contrôle du moteur d'écarts, et le ferait tomber le jour où
    celui-ci bouge pour une raison qui n'est pas la sienne.
    """
    from types import SimpleNamespace
    from typing import Any, cast

    from inventory.services.report_service import ReportService

    ctx = SimpleNamespace(record=lambda **kwargs: "", settings=None)
    service = ReportService(cast(Any, ctx))
    service._variance_rows = lambda campaign, **kwargs: list(rows)  # type: ignore[method-assign]
    return service


def _campaign():
    from inventory.domain.models import Campaign

    return Campaign(
        id="00000000-0000-0000-0000-000000000001",
        code="INV-2026",
        label="Inventaire annuel 2026",
        count_date=dt.date(2026, 8, 31),
        created_by="test",
        created_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
    )


#: Une ligne qui tombe juste : comptée exactement comme l'ERP l'annonce.
JUSTE = {
    **ROW,
    "itemNumber": "P-00099999",
    "name": "Rondelle M6",
    "countedQty": ROW["bookQty"],
    "countedValue": ROW["bookValue"],
    "varianceQty": 0.0,
    "varianceValue": 0.0,
    "physicalQty": ROW["bookQty"],
    "countedVarianceQty": 0.0,
    "countedVarianceValue": 0.0,
}


def _texte(payload: bytes) -> str:
    pdfium = pytest.importorskip("pypdfium2")
    document = pdfium.PdfDocument(payload)
    return "\n".join(p.get_textpage().get_text_range() for p in document)


class TestLePdfNImprimePasLesLignesSansEcart:
    """Demandé tel quel, et pour une raison qui tient au support.

    Une ligne qui tombe juste n'appelle aucune décision. Sur un écran elle se
    fait oublier ; sur papier elle occupe une rangée, repousse d'autant ce qui en
    demande une, et fait d'un document qu'on lit une liste qu'on parcourt. Sur
    une campagne de cinq cents références dont la plupart tombent juste, le
    document utile tenait sur deux pages noyées dans dix.
    """

    def test_la_ligne_sans_ecart_ne_figure_pas(self):
        payload, _ = _service([ROW, JUSTE]).variance_pdf(_campaign())

        texte = _texte(payload)
        assert "P-00012345" in texte
        assert "P-00099999" not in texte

    def test_le_document_dit_combien_il_en_a_écartées(self):
        """Un document qui retire des lignes en silence se lit comme complet."""
        payload, _ = _service([ROW, JUSTE, JUSTE]).variance_pdf(_campaign())

        texte = _texte(payload)
        assert "2 ligne(s) sans écart de quantité" in texte
        assert "Excel" in texte

    def test_et_ne_le_confond_pas_avec_la_troncature(self):
        """Deux omissions, deux phrases.

        Le plafond coupe des écarts **réels**, qu'il faut aller chercher dans le
        classeur ; les lignes sans écart, elles, n'appellent rien. Les additionner
        sous une seule phrase ferait croire à des centaines d'écarts non imprimés
        là où il n'y a que des lignes qui tombent juste.
        """
        texte = _texte(_service([ROW, JUSTE]).variance_pdf(_campaign())[0])

        assert "sans écart de quantité" in texte
        assert "d'écart plus faible" not in texte

    def test_le_total_ne_compte_que_ce_qui_est_imprimé(self):
        """Sinon le total du bas ne tombe pas sur la somme des rangées — le seul
        chiffre d'un document qu'un lecteur peut vérifier lui-même."""
        payload, _ = _service([ROW, JUSTE, JUSTE]).variance_pdf(_campaign())

        assert "Total des 1 ligne(s) imprimée(s)" in _texte(payload)

    def test_l_en_tête_ne_promet_plus_tous_les_écarts(self):
        """« Tous les écarts » ferait chercher sur la page une référence qui n'y
        est pas."""
        texte = _texte(_service([ROW, JUSTE]).variance_pdf(_campaign())[0])

        assert "écarts non nuls" in texte
        assert "tous les écarts" not in texte

    def test_le_filtre_passe_avant_le_plafond(self):
        """Sans cela, les trois cents rangées imprimables pourraient être mangées
        par des lignes à zéro, et le document annoncerait une troncature en
        n'ayant rien à montrer."""
        from inventory.services.report_service import VARIANCE_PDF_CEILING

        lignes = [JUSTE] * VARIANCE_PDF_CEILING + [ROW]
        payload, _ = _service(lignes).variance_pdf(_campaign())

        texte = _texte(payload)
        assert "P-00012345" in texte
        assert "d'écart plus faible" not in texte

    def test_une_campagne_qui_tombe_juste_partout_le_dit(self):
        """Et ne rend pas un document d'une seule ligne de total."""
        from inventory.errors import ValidationError

        with pytest.raises(ValidationError) as refus:
            _service([JUSTE, JUSTE]).variance_pdf(_campaign())

        assert "aucune ligne ne présente d'écart" in str(refus.value)


class TestLeClasseurLesGardeToutes:
    """La contrepartie, et la raison pour laquelle le PDF peut se permettre de
    filtrer : le classeur reste l'exhaustif. Les deux fichiers ont deux usages —
    l'un se traite, l'autre se recoupe — et retirer les lignes des deux ferait
    disparaître la preuve que le comptage a bien couvert ces références."""

    def test_la_ligne_sans_ecart_est_dans_le_classeur(self):
        import io

        openpyxl = pytest.importorskip("openpyxl")

        payload, _ = _service([ROW, JUSTE]).variance_export(_campaign())
        classeur = openpyxl.load_workbook(io.BytesIO(payload))
        texte = "\n".join(
            str(cell.value)
            for sheet in classeur.worksheets
            for row in sheet.iter_rows()
            for cell in row
            if cell.value is not None
        )

        assert "P-00099999" in texte
        assert "P-00012345" in texte
