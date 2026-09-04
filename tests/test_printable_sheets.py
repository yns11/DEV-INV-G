"""The printed counting sheet — the only artefact the shop floor actually holds.

Assertions read the generated PDF back rather than inspecting the builder's
internals: what matters is what lands on the paper.
"""

from __future__ import annotations

import datetime as dt

import pytest

from inventory.domain.enums import CampaignStatus
from inventory.domain.printing import (
    BLANK_ROWS_PER_SECTION,
    PrintMode,
    available_print_modes,
    print_refusal,
)
from inventory.reporting.exports import build_counting_sheet_pdf


def line(number: str, section: str = "LINE_SIDE", **kwargs) -> dict:
    return {
        "item_number": number,
        "name": kwargs.get("name", "VIS TETE HEXAGONALE M6"),
        "section": section,
        "unit": kwargs.get("unit", "PCE"),
        "qty": kwargs.get("qty"),
        "source": kwargs.get("source", "MANUAL"),
        "comment": kwargs.get("comment", ""),
    }


def render(lines, **kwargs) -> list[str]:
    """Build the sheet and return the text of each page."""
    import pypdfium2

    payload = build_counting_sheet_pdf(
        campaign_label="INV-2026-06 — Inventaire",
        campaign_code="INV-2026-06",
        count_date=dt.date(2026, 6, 30),
        zone_code="FI ASSY",
        zone_label="Assemblage final",
        pass_no=1,
        lines=lines,
        sheet_id="abcdef12-3456",
        **kwargs,
    )
    assert payload[:4] == b"%PDF"
    document = pypdfium2.PdfDocument(payload)
    try:
        return [page.get_textpage().get_text_bounded() for page in document]
    finally:
        document.close()


class TestSheetWithoutQuantities:
    """``mode=list`` — the article list, handed to a counter."""

    def test_the_quantity_column_is_left_empty(self):
        pages = render([line("P-001", qty=42)])
        assert "P-001" in pages[0]
        assert "42" not in pages[0]

    def test_every_section_gets_room_for_an_unlisted_article(self):
        """A part found in a corner has to have somewhere to be written down."""
        pages = render([line("P-001")])
        text = "\n".join(pages)
        # All three tables print, even though only the line side has content.
        assert "Composants en bord de ligne" in text
        assert "en-cours non déclaré" in text
        assert "MOM OK" in text

    def test_a_line_that_was_never_counted_is_still_printed(self):
        pages = render([line("P-001", qty=None), line("P-002", qty=7)])
        assert "P-001" in pages[0] and "P-002" in pages[0]

    def test_the_free_row_allowance_is_five_three_two(self):
        """Sized from where surprises actually turn up, not evenly."""
        assert BLANK_ROWS_PER_SECTION == {"LINE_SIDE": 5, "WIP": 3, "WIP_OK": 2}


class TestFilledSheet:
    def test_quantities_are_printed(self):
        pages = render([line("P-001", qty=42)], mode=PrintMode.FILLED)
        assert "42" in pages[0]

    def test_a_listed_article_nobody_counted_prints_a_zero(self):
        """Le relevé et l'analyse doivent dire la même chose.

        La feuille portait « non compté » là où l'analyse comptait désormais la
        référence à zéro : deux documents à rapprocher pour une seule ligne, et
        c'est celui qu'on tient en main qui semblait dire qu'on ne sait pas.

        La ligne reste imprimée : la retirer cacherait le seul fait que le
        relevé existe pour porter — cette référence était sur la feuille, et il
        n'y en avait pas.
        """
        pages = render(
            [line("P-001", qty=42), line("P-002", qty=None)], mode=PrintMode.FILLED
        )
        assert "P-001" in pages[0] and "P-002" in pages[0]
        assert "non compté" not in pages[0]
        assert "0" in pages[0]

    def test_no_blank_rows_are_appended(self):
        """Inviting somebody to write on a record would make it not a record."""
        blank = render([line("P-001", qty=1)])
        filled = render([line("P-001", qty=1)], mode=PrintMode.FILLED)
        assert len("\n".join(filled)) < len("\n".join(blank))

    def test_sources_and_comments_are_optional_columns(self):
        without = render([line("P-001", qty=1, comment="raturé")], mode=PrintMode.FILLED)
        with_them = render(
            [line("P-001", qty=1, source="SCAN_AI", comment="raturé")],
            mode=PrintMode.FILLED, with_sources=True,
        )
        assert "Commentaire" not in without[0]
        assert "Commentaire" in with_them[0]
        assert "raturé" in with_them[0]
        assert "IA" in with_them[0]


class TestFreeEntrySheet:
    """``mode=blank`` — nothing is known, the counter writes both columns."""

    def test_blank_lines_are_printed_without_any_article(self):
        pages = render([], mode=PrintMode.BLANK, blank_lines=40)
        text = "\n".join(pages)
        assert "Composants en bord de ligne" in text
        # 40 line-side rows do not fit on one page.
        assert len(pages) > 1

    def test_the_article_list_is_ignored_rather_than_printed(self):
        """The whole point of this mode is a grid with nothing pre-filled."""
        pages = render([line("P-001")], mode=PrintMode.BLANK, blank_lines=10)
        assert "P-001" not in "\n".join(pages)

    def test_only_the_asked_for_rows_are_printed(self):
        """Somebody who asks for ten lines gets ten, not ten plus an allowance."""
        pages = render([], mode=PrintMode.BLANK, blank_lines=10)
        text = "\n".join(pages)
        assert "en-cours non déclaré" not in text
        assert "ensembles déclarés" not in text

    @pytest.mark.parametrize("count", [10, 60, 180])
    def test_the_accepted_range_renders(self, count):
        assert render([], mode=PrintMode.BLANK, blank_lines=count)


class TestWhichModesAreOffered:
    """The matrix: what a zone can be printed as, and when.

    Two facts decide it — whether the zone has a pre-printed article list, and
    whether anything has been counted yet. Everything else follows.
    """

    @pytest.mark.parametrize(
        "status", [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, CampaignStatus.CLOSED]
    )
    def test_a_listed_zone_prints_its_list_then_its_record(self, status):
        assert available_print_modes(free_entry=False, status=status) == (
            PrintMode.LIST, PrintMode.FILLED,
        )

    @pytest.mark.parametrize(
        "status", [CampaignStatus.COUNTING, CampaignStatus.ANALYSIS, CampaignStatus.CLOSED]
    )
    def test_a_free_entry_zone_prints_a_grid_then_its_record(self, status):
        assert available_print_modes(free_entry=True, status=status) == (
            PrintMode.BLANK, PrintMode.FILLED,
        )

    def test_the_record_does_not_exist_before_the_count(self):
        for free_entry in (True, False):
            assert PrintMode.FILLED not in available_print_modes(
                free_entry=free_entry, status=CampaignStatus.PREPARATION
            )

    def test_the_sheet_to_hand_out_exists_from_the_first_phase(self):
        """Paper is prepared *before* inventory day — that is the whole job."""
        assert available_print_modes(
            free_entry=False, status=CampaignStatus.PREPARATION
        ) == (PrintMode.LIST,)
        assert available_print_modes(
            free_entry=True, status=CampaignStatus.PREPARATION
        ) == (PrintMode.BLANK,)

    def test_a_listed_zone_is_never_offered_a_blank_grid(self):
        """It would ask the counter to rewrite a list the application holds."""
        assert print_refusal(
            PrintMode.BLANK, free_entry=False, status=CampaignStatus.COUNTING
        )

    def test_a_free_entry_zone_has_no_list_to_print(self):
        assert print_refusal(
            PrintMode.LIST, free_entry=True, status=CampaignStatus.COUNTING
        )

    def test_an_allowed_mode_is_not_refused(self):
        assert print_refusal(
            PrintMode.LIST, free_entry=False, status=CampaignStatus.PREPARATION
        ) is None


class TestPagination:
    def test_the_section_title_repeats_on_every_page_of_a_table(self):
        """A page separated from its stack must still say what it is counting.

        Otherwise a WIP assembly on page 3 gets counted as a line-side part —
        which is the confusion the sections exist to prevent.
        """
        pages = render([line(f"P-{i:04d}") for i in range(60)])
        assert len(pages) > 1
        assert all("Composants en bord de ligne" in page for page in pages[:2])

    def test_the_sheet_identity_repeats_in_every_footer(self):
        pages = render([line(f"P-{i:04d}") for i in range(60)])
        assert all("abcdef12" in page for page in pages)


class TestDesignation:
    def test_a_long_designation_is_truncated_not_wrapped(self):
        long_name = "STATOR ASSEMBLE M3 GEN2 AVEC CONNECTIQUE ET CAPOT ARRIERE"
        pages = render([line("P-001", name=long_name)])
        assert long_name not in pages[0]
        assert "STATOR ASSEMBLE M3 GEN2 AVEC CO" in pages[0]

    def test_the_budget_shrinks_when_the_provenance_columns_appear(self):
        long_name = "STATOR ASSEMBLE M3 GEN2 AVEC CONNECTIQUE"
        wide = render([line("P-001", name=long_name, qty=1)], mode=PrintMode.FILLED)
        narrow = render(
            [line("P-001", name=long_name, qty=1)], mode=PrintMode.FILLED, with_sources=True
        )
        assert "STATOR ASSEMBLE M3 GEN2 AVEC CO" in wide[0]
        assert "STATOR ASSEMBLE M3 " in narrow[0]
        assert "STATOR ASSEMBLE M3 GEN2" not in narrow[0]


class TestNoBlankPages:
    """A printed stack must not contain a page with nothing on it.

    The gap between two section tables used to be appended *after* each table,
    including the last. A trailing spacer is still a flowable: when a table ended
    exactly at the bottom of a page it flowed onto the next one, and the sheet
    came out with a page carrying only its header and footer. It turned up once
    in sixty-two pages of a real workbook — rare enough to ship, frequent enough
    to confuse a counter every campaign.

    The cases below are the exact fits found by sweeping the old builder; the
    surrounding range guards the neighbourhood, because the boundary moves with
    any change to row height or margins.
    """

    def _assert_every_page_carries_a_line(self, line_side, wip=0, wip_ok=0):
        rows = (
            [line(f"P-{i:04d}", qty=1) for i in range(line_side)]
            + [line(f"W-{i:04d}", section="WIP", qty=1) for i in range(wip)]
            + [line(f"K-{i:04d}", section="WIP_OK", qty=1) for i in range(wip_ok)]
        )
        pages = render(rows, mode=PrintMode.FILLED)
        for number, page in enumerate(pages, start=1):
            assert any(k in page for k in ("P-", "W-", "K-")), (
                f"page {number} sur {len(pages)} est vide "
                f"({line_side}/{wip}/{wip_ok} lignes)"
            )

    @pytest.mark.parametrize("line_side,wip,wip_ok", [
        (23, 0, 0),   # une seule section, qui finit pile en bas de page
        (15, 3, 2),   # trois sections, la dernière finit pile
        (44, 2, 0),
        (44, 0, 2),
    ])
    def test_the_known_exact_fits_leave_no_empty_page(self, line_side, wip, wip_ok):
        self._assert_every_page_carries_a_line(line_side, wip, wip_ok)

    @pytest.mark.parametrize("count", range(20, 27))
    def test_nor_does_the_neighbourhood_of_a_page_boundary(self, count):
        self._assert_every_page_carries_a_line(count)


class TestNumbersOnPaper:
    """Every character of a quantity has to be drawable by the PDF's font.

    The sheet is drawn in Helvetica, whose Latin-1 encoding has no narrow
    no-break space. Using one as the thousands separator printed « 2■724 »: a
    black box exactly where the counter reads a digit. It only showed above a
    thousand, so the demo campaign never caught it.

    Reading the page back is what makes these tests worth having: a character
    the font cannot draw comes back as U+25A0 — the box itself — so the failure
    appears here in the same shape it has on paper. Asserting on the formatter's
    output instead would have passed throughout the bug.
    """

    @pytest.mark.parametrize("quantity,expected", [
        (2724, "2 724"),
        (15600, "15 600"),
        (1_234_567, "1 234 567"),
        (999, "999"),
    ])
    def test_a_thousands_separator_survives_the_page(self, quantity, expected):
        """The separator drawn is a no-break space; extraction normalises it."""
        pages = render([line("P-001", qty=quantity)], mode=PrintMode.FILLED)
        assert expected in pages[0]

    def test_no_undrawable_character_reaches_the_paper(self):
        pages = render(
            [line(f"P-{i:03d}", qty=1000 * (i + 1)) for i in range(5)],
            mode=PrintMode.FILLED,
        )
        text = "\n".join(pages)
        assert "■" not in text, "un caractère non dessinable a atteint le papier"


def layout(kind: str, label: str = "", section: str = "LINE_SIDE") -> dict:
    """Un intertitre ou une ligne vide, à la forme que le service leur donne."""
    return {
        "item_number": "", "name": "", "section": section,
        "line_kind": kind, "label": label,
        "unit": "", "qty": None, "source": "MANUAL", "comment": "",
    }


class TestLesEnTetesDeSection:
    """Le texte imprimé en tête d'une section, par défaut et personnalisé."""

    def test_le_defaut_est_la_phrase_entiere_et_non_un_titre(self):
        """La consigne *est* l'en-tête.

        « WIP — ensembles déclarés » ne dit pas au compteur de relever un numéro
        de Galia ; c'est pourtant ce qu'il doit faire, et la feuille est le seul
        endroit où il le lira.
        """
        text = "\n".join(render([line("P-001", section="WIP_OK", qty=None)]))
        assert "MOM OK" in text
        assert "notez le numéro de Galia" in text

    def test_la_zone_remplace_le_texte(self):
        text = "\n".join(render(
            [line("P-001")],
            section_titles={"LINE_SIDE": "Stock physique B6EST — pièces à l'unité"},
        ))
        assert "Stock physique B6EST — pièces à l'unité" in text
        assert "Composants en bord de ligne" not in text

    def test_les_sections_non_personnalisees_gardent_leur_defaut(self):
        """Sinon personnaliser une section reviendrait à recopier les deux autres."""
        text = "\n".join(render(
            [line("P-001")], section_titles={"LINE_SIDE": "Bord de ligne, zone B15"}
        ))
        assert "Bord de ligne, zone B15" in text
        assert "en-cours non déclaré" in text

    def test_un_texte_vide_ne_remplace_rien(self):
        """Un champ effacé dans l'écran d'édition veut dire « remets le défaut ».

        Imprimer une bannière vide donnerait une page où le compteur ne sait plus
        sous quelle règle il compte.
        """
        text = "\n".join(render([line("P-001")], section_titles={"LINE_SIDE": "   "}))
        assert "Composants en bord de ligne" in text

    def test_le_texte_personnalise_est_echappe(self):
        """Il vient d'un champ libre : « <B6 & B15> » doit s'imprimer tel quel."""
        text = "\n".join(render(
            [line("P-001")], section_titles={"LINE_SIDE": "Stock <B6 & B15>"}
        ))
        assert "Stock <B6 & B15>" in text


class TestLesLignesDeMiseEnPage:
    """Intertitres et lignes vides — ce que la feuille Excel savait faire."""

    def test_l_intertitre_s_imprime(self):
        text = "\n".join(render([
            layout("SUBSECTION", "Stock physique B6EST"),
            line("P-001"),
        ]))
        assert "Stock physique B6EST" in text

    def test_il_s_imprime_a_sa_place_dans_la_feuille(self):
        """C'est *l'ordre* qui dit au compteur où aller chercher l'article."""
        page = render([
            layout("SUBSECTION", "Stock physique B6EST"),
            line("P-001"),
            layout("SUBSECTION", "Stock physique chez Maldaner"),
            line("P-002"),
        ])[0]
        assert (
            page.index("Stock physique B6EST")
            < page.index("P-001")
            < page.index("Stock physique chez Maldaner")
            < page.index("P-002")
        )

    def test_le_meme_article_revient_sous_deux_intertitres(self):
        """Trois emplacements, trois comptages du même article : pas un doublon."""
        page = render([
            layout("SUBSECTION", "Stock physique B6EST"),
            line("P-001"),
            layout("SUBSECTION", "Stock physique B15"),
            line("P-001"),
        ])[0]
        assert page.count("P-001") == 2

    def test_l_intertitre_ne_porte_ni_quantite_ni_unite(self):
        """Ce n'est pas une ligne à compter : rien à écrire dessus."""
        page = render(
            [layout("SUBSECTION", "Stock physique B15"), line("P-001", qty=7)],
            mode=PrintMode.FILLED,
        )[0]
        assert "Stock physique B15" in page
        assert page.count("PCE") == 1

    def test_la_ligne_vide_n_imprime_aucun_texte(self):
        """Une respiration, pas une ligne de plus à remplir."""
        page = render([line("P-001"), layout("SPACER"), line("P-002")])[0]
        assert "P-001" in page and "P-002" in page

    def test_l_intertitre_suit_sa_section(self):
        """Un intertitre WIP n'a rien à faire au-dessus du bord de ligne."""
        page = render([
            line("P-001"),
            layout("SUBSECTION", "Ensembles en attente de décision", section="WIP"),
            line("P-002", section="WIP"),
        ])[0]
        assert (
            page.index("Composants en bord de ligne")
            < page.index("P-001")
            < page.index("Ensembles en attente de décision")
        )

    def test_le_texte_de_l_intertitre_est_echappe(self):
        page = render([layout("SUBSECTION", "Stock <B6 & B15>"), line("P-001")])[0]
        assert "Stock <B6 & B15>" in page

    def test_la_feuille_vierge_ignore_la_mise_en_page(self):
        """Elle n'a pas de liste ; elle n'a donc pas d'intertitres non plus."""
        text = "\n".join(render(
            [layout("SUBSECTION", "Stock physique B6EST"), line("P-001")],
            mode=PrintMode.BLANK, blank_lines=10,
        ))
        assert "Stock physique B6EST" not in text


class TestCeQueLeServiceEnvoieALImpression:
    """``_printable_lines`` — la traduction d'une feuille en lignes imprimables.

    Le filtre qui la précédait était « pas de référence, on saute » : c'est
    exactement la forme d'un intertitre et d'une ligne vide, qui auraient donc
    disparu de la page — sans erreur, sans trace, comme le classeur qu'on
    remplace perdait des lignes.
    """

    @staticmethod
    def _line(**kwargs):
        from inventory.domain.models import CountSheetLine

        base = {"id": "l", "sheet_id": "s", "campaign_id": "c", "item_number": ""}
        return CountSheetLine(**{**base, **kwargs})

    def _printable(self, lines):
        from inventory.services.report_service import _printable_lines

        return _printable_lines(lines, {})

    def test_l_intertitre_arrive_jusqu_a_l_impression(self):
        from inventory.domain.enums import CountLineKind

        [row] = self._printable([
            self._line(line_kind=CountLineKind.SUBSECTION, label="Stock physique B15")
        ])
        assert row["line_kind"] == "SUBSECTION"
        assert row["label"] == "Stock physique B15"

    def test_la_ligne_vide_aussi(self):
        from inventory.domain.enums import CountLineKind

        [row] = self._printable([self._line(line_kind=CountLineKind.SPACER)])
        assert row["line_kind"] == "SPACER"

    def test_une_ligne_d_article_sans_reference_reste_ecartee(self):
        """Elle, oui : c'est une ligne à jeter, pas un objet de mise en page."""
        assert self._printable([self._line(item_number="")]) == []

    def test_l_ordre_du_document_est_conserve(self):
        from inventory.domain.enums import CountLineKind

        rows = self._printable([
            self._line(line_kind=CountLineKind.SUBSECTION, label="B6EST"),
            self._line(item_number="P-001"),
            self._line(line_kind=CountLineKind.SPACER),
        ])
        assert [r["line_kind"] for r in rows] == ["SUBSECTION", "ARTICLE", "SPACER"]
