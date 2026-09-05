"""Downloadable artefacts: Excel workbooks and printable counting sheets.

Two deliverables, two very different jobs:

* **Excel exports** are the bridge to the ERP and to anyone who still wants to
  slice the data themselves. They are written with ``XlsxWriter`` in constant-
  memory mode so a 100 000-row export does not need 100 000 rows of RAM.
* **Printable counting sheets** are the physical artefact handed to a counter.
  Their layout is deliberately close to the sheets people already use — the
  point is to change the system, not to retrain the shop floor.

Every export carries a provenance block (campaign, generation date, engine
version, filters applied). A spreadsheet leaving this application must always
say what it is a picture of.
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from ..domain.printing import BLANK_ROWS_PER_SECTION, PrintMode

__all__ = [
    "build_workbook",
    "build_counting_sheet_pdf",
    "build_journal_export",
    "build_variance_pdf",
    "EXPORT_FORMATS",
]

EXPORT_FORMATS = ("xlsx", "csv")


# --------------------------------------------------------------------------- #
# Excel
# --------------------------------------------------------------------------- #

def build_workbook(
    sheets: dict[str, tuple[Sequence[str], Sequence[Sequence[Any]]]],
    *,
    provenance: dict[str, Any] | None = None,
    title: str = "Export inventaire",
) -> bytes:
    """Build an ``.xlsx`` from ``{sheet name: (headers, rows)}``.

    ``constant_memory`` keeps a single row in memory at a time, which is what
    makes a full campaign export survive the app container's 6 GB budget.
    Because that mode requires rows to be written in order, the layout is built
    top-to-bottom and column widths are set from the headers rather than from
    the data.
    """
    import xlsxwriter

    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        buffer,
        {
            "constant_memory": True,
            "in_memory": True,
            "default_date_format": "yyyy-mm-dd",
            "strings_to_numbers": False,
        },
    )
    header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#1E293B", "font_color": "#FFFFFF",
        "border": 1, "border_color": "#334155", "valign": "vcenter",
        "text_wrap": True,
    })
    title_fmt = workbook.add_format({"bold": True, "font_size": 14})
    label_fmt = workbook.add_format({"bold": True, "font_color": "#475569"})
    number_fmt = workbook.add_format({"num_format": "#,##0.######"})
    money_fmt = workbook.add_format({"num_format": "#,##0.00 €"})

    if provenance:
        info = workbook.add_worksheet("Provenance")
        info.set_column(0, 0, 34)
        info.set_column(1, 1, 72)
        info.write(0, 0, title, title_fmt)
        row = 2
        for key, value in provenance.items():
            info.write(row, 0, key, label_fmt)
            info.write(row, 1, _cell(value))
            row += 1

    for name, (headers, rows) in sheets.items():
        worksheet = workbook.add_worksheet(_safe_sheet_name(name))
        worksheet.freeze_panes(1, 0)
        for column, header in enumerate(headers):
            worksheet.write(0, column, str(header), header_fmt)
            worksheet.set_column(column, column, _width(str(header)))
        if headers:
            worksheet.autofilter(0, 0, max(len(rows), 1), len(headers) - 1)

        for r, record in enumerate(rows, start=1):
            for c, value in enumerate(record):
                fmt = None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    header = str(headers[c]).lower() if c < len(headers) else ""
                    fmt = money_fmt if ("€" in header or "valeur" in header) else number_fmt
                worksheet.write(r, c, _cell(value), fmt)

    workbook.close()
    return buffer.getvalue()


def build_journal_export(
    lines: Sequence[dict[str, Any]],
    *,
    campaign_code: str,
    warehouse_id: str,
    location_id: str,
    journal_kind: str = "INVV",
) -> bytes:
    """Export a counting journal in the exact shape the ERP import expects.

    Column names and order match the ERP's counting-journal import template, so
    the file is loaded rather than copy-pasted — removing the transcription and
    row-shift errors that the manual paste produced every campaign.
    """
    headers = ("JournalNameId", "WarehouseId", "WarehouseLocationId",
               "ItemNumber", "CountedQuantity", "Unit")
    rows = [
        (
            journal_kind,
            warehouse_id,
            location_id,
            line["item_number"],
            float(line["qty"]),
            line.get("unit", "PCE"),
        )
        for line in lines
    ]
    return build_workbook(
        {"Journal": (headers, rows)},
        title=f"Journal de comptage {journal_kind} — {warehouse_id}/{location_id}",
        provenance={
            "Campagne": campaign_code,
            "Entrepôt / emplacement": f"{warehouse_id} / {location_id}",
            "Type de journal": journal_kind,
            "Lignes": len(rows),
            "Généré le": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "Usage": (
                "Importer tel quel dans le journal de comptage ERP. "
                "Ne pas modifier les en-têtes."
            ),
        },
    )


# --------------------------------------------------------------------------- #
# Printable counting sheets
# --------------------------------------------------------------------------- #

def build_counting_sheet_pdf(
    *,
    campaign_label: str,
    campaign_code: str,
    count_date: dt.date,
    zone_code: str,
    zone_label: str,
    pass_no: int,
    lines: Sequence[dict[str, Any]],
    sheet_id: str = "",
    mode: PrintMode = PrintMode.LIST,
    with_sources: bool = False,
    blank_lines: int = 0,
    section_titles: Mapping[str, str] | None = None,
) -> bytes:
    """Render a printable counting sheet in one of its three modes.

    Layout choices are operational, not decorative:

    * the quantity column is wide and empty — people write in it with gloves on,
      and the rows are deliberately tall for the same reason;
    * margins are tight: every millimetre of paper recovered is a row that does
      not spill onto a second page somebody has to keep track of;
    * sections are visually separated, because mixing a line-side component with
      a WIP assembly is precisely the confusion that produced wrong counts;
    * the designation is truncated rather than wrapped — a counter identifies a
      part by its reference, and a two-line cell halves the rows per page;
    * the sheet identity (campaign, zone, pass, sheet id) is repeated in the
      footer of every page, and the section title is repeated at the top of each
      page a table spills onto, so a page separated from its stack still says
      what it is *and* what is being counted on it;
    * one identity line closes the header — name, times and signature side by
      side rather than stacked.

    :param mode: which of the three documents to produce.
        :attr:`~inventory.domain.printing.PrintMode.LIST` prints the article
        list with an empty quantity column plus a few free rows per section —
        the sheet handed to a counter.
        :attr:`~inventory.domain.printing.PrintMode.FILLED` prints the same
        layout with the counted quantities and *no* free rows: a record must
        not carry invitations to write more.
        :attr:`~inventory.domain.printing.PrintMode.BLANK` prints *blank_lines*
        empty rows and nothing else — the free-entry sheet.
    :param with_sources: add the provenance and comment columns. Only meaningful
        in :attr:`~inventory.domain.printing.PrintMode.FILLED`.
    :param blank_lines: number of rows on a free-entry sheet. Exactly what was
        asked for: somebody who says forty lines gets forty.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    _side_margin = _SIDE_MARGIN_MM * mm
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "cell", parent=styles["BodyText"], fontSize=8.5, leading=10.5, spaceAfter=0
    )
    footer_text = (
        f"{campaign_code} · zone {zone_code} · comptage n°{pass_no}"
        + (f" · feuille {sheet_id[:8]}" if sheet_id else "")
    )

    def draw_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(_side_margin, A4[1] - 11 * mm, zone_label or zone_code)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(
            _side_margin, A4[1] - 16 * mm,
            f"{campaign_label} — comptage du {count_date:%d/%m/%Y} — "
            f"passage n°{pass_no}",
        )
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(
            _side_margin, A4[1] - 19 * mm, A4[0] - _side_margin, A4[1] - 19 * mm
        )

        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(_side_margin, 8 * mm, footer_text)
        canvas.drawRightString(A4[0] - _side_margin, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=_side_margin, rightMargin=_side_margin,
        topMargin=24 * mm, bottomMargin=13 * mm,
        title=f"Feuille de comptage — {zone_code} — n°{pass_no}",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body"
    )
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=draw_page)
    ])

    story: list[Any] = []

    # One line, not two: the four fields fit side by side, and the 9 mm they
    # give back is half a dozen extra rows on the page.
    identity = Table(
        [["Nom / Prénom :", "", "Début :", "", "Fin :", "", "Signature :", ""]],
        colWidths=[
            26 * mm, 40 * mm, 15 * mm, 18 * mm, 12 * mm, 18 * mm, 20 * mm, 24 * mm,
        ],
        rowHeights=[9 * mm],
    )
    identity.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#334155")),
        # Underline the fill-in cells only — the odd-indexed ones.
        *[
            ("LINEBELOW", (c, 0), (c, 0), 0.5, colors.HexColor("#94A3B8"))
            for c in (1, 3, 5, 7)
        ],
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(identity)
    story.append(Spacer(1, 4 * mm))

    quiet_style = ParagraphStyle(
        "quiet", parent=cell_style, textColor=colors.HexColor("#94A3B8")
    )
    banner_style = ParagraphStyle(
        "banner", parent=styles["BodyText"], fontSize=9.5, leading=11.5,
        textColor=colors.white, spaceBefore=0, spaceAfter=0,
    )
    subsection_style = ParagraphStyle(
        "subsection", parent=styles["BodyText"], fontSize=9, leading=11,
        textColor=colors.HexColor("#0F172A"), spaceBefore=0, spaceAfter=0,
    )

    # Le texte de la zone quand elle en a un, celui par défaut sinon. Résolu
    # ici et non à l'appel : une section absente du dictionnaire doit garder son
    # défaut, ce qui permet d'en personnaliser une sans recopier les deux autres.
    section_titles = {**DEFAULT_SECTION_TITLES, **{
        code: text for code, text in (section_titles or {}).items() if text.strip()
    }}

    by_section: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_section.setdefault(str(line.get("section", "LINE_SIDE")), []).append(line)

    columns = _SOURCE_COLUMNS if with_sources else _PLAIN_COLUMNS
    widths = [w * mm for w in (
        _WIDTHS_WITH_SOURCES if with_sources else _WIDTHS_PLAIN
    )]
    # The designation column shrinks when the provenance columns appear, so the
    # truncation has to shrink with it — a wrapped cell would blow through the
    # fixed row height and undo the very legibility the tall rows buy.
    name_width = (
        _NAME_MAX_CHARS_WITH_SOURCES if with_sources else _NAME_MAX_CHARS
    )

    filled = mode is PrintMode.FILLED
    printed_a_section = False
    for section in ("LINE_SIDE", "WIP", "WIP_OK"):
        section_lines = [] if mode is PrintMode.BLANK else by_section.get(section, [])
        extras = _blank_rows_for(section, mode=mode, requested=blank_lines)
        if not section_lines and not extras:
            continue

        # The section title travels *inside* the table as a repeated row rather
        # than sitting above it as a paragraph. That is what makes it reappear
        # at the top of every page the table spills onto: a second page landing
        # on somebody's desk without saying whether it is line side or WIP is
        # exactly how a component gets counted under the wrong rule.
        banner = Paragraph(
            f"<b>{escape(section_titles[section])}</b>", banner_style
        )
        data: list[list[Any]] = [
            [banner] + [""] * (len(columns) - 1),
            list(columns),
        ]
        # Les lignes de mise en page — intertitres et lignes vides — sont
        # rendues **dans le tableau, à leur place**, et sans colonne
        # supplémentaire : un intertitre occupe toute la largeur, une ligne vide
        # est une ligne vide. C'est ce que faisaient les classeurs qu'on
        # remplace, et c'est ce que le compteur cherche des yeux sur la page.
        subsection_rows: list[int] = []
        spacer_rows: list[int] = []
        for line in section_lines:
            kind = str(line.get("line_kind") or "ARTICLE")
            if kind == "SUBSECTION":
                subsection_rows.append(len(data))
                data.append(
                    [Paragraph(f"<b>{escape(str(line.get('label') or ''))}</b>",
                               subsection_style)]
                    + [""] * (len(columns) - 1)
                )
            elif kind == "SPACER":
                spacer_rows.append(len(data))
                data.append([""] * len(columns))
            else:
                data.append(_body_row(
                    line, filled=filled, with_sources=with_sources,
                    cell=cell_style, quiet=quiet_style, paragraph=Paragraph,
                    name_width=name_width,
                ))
        data.extend([[""] * len(columns) for _ in range(extras)])

        body_rows = len(data) - 2
        # Les hauteurs sont imposées **sauf** sur le relevé qui porte source et
        # commentaire. Ailleurs, une ligne haute est un choix : on y écrit un
        # chiffre à la main, avec des gants. Là, plus personne n'écrit — c'est
        # une archive — et le commentaire du modèle (« Réf. manuscrite ajoutée
        # en bas du tableau MOM OK ; lecture des chiffres incertaine ») fait
        # trois lignes. Imposée, la hauteur le laissait déborder par-dessus les
        # lignes suivantes, jusque sur le pied de page.
        table = Table(
            data,
            colWidths=widths,
            rowHeights=(
                None if with_sources
                else [_BANNER_ROW_HEIGHT, _BASE_ROW_HEIGHT]
                + [_ROW_HEIGHT] * body_rows
            ),
            repeatRows=2,
        )
        table.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#1E293B")),
            ("TEXTCOLOR", (0, 1), (-1, 1), colors.white),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 1), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("BOX", (0, 0), (-1, 0), 0.4, colors.HexColor("#1E293B")),
            ("LEFTPADDING", (0, 0), (-1, 0), 6),
            ("ROWBACKGROUNDS", (0, 2), (-1, -1),
             [colors.white, colors.HexColor("#F8FAFC")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            # Quantity and unit centred; the trailing provenance columns are
            # prose and stay left-aligned.
            ("ALIGN", (2, 1), (3, -1), "CENTER"),
            # L'intertitre traverse la largeur et se distingue du fond alterné :
            # sans cela, « Stock physique B15 » se lirait comme une référence.
            *[("SPAN", (0, r), (-1, r)) for r in subsection_rows],
            *[("BACKGROUND", (0, r), (-1, r), colors.HexColor("#E2E8F0"))
              for r in subsection_rows],
            *[("LEFTPADDING", (0, r), (-1, r), 6) for r in subsection_rows],
            # Une ligne vide n'a pas de quadrillage : c'est une respiration, pas
            # une ligne à remplir.
            *[("SPAN", (0, r), (-1, r)) for r in spacer_rows],
            *[("BACKGROUND", (0, r), (-1, r), colors.white) for r in spacer_rows],
        ]))
        # The gap goes *before* each table but the first, never after the last:
        # a trailing spacer is still a flowable, so a table ending exactly at the
        # bottom of a page pushed it onto a new one and the stack came out with a
        # page carrying only a header and a footer.
        if printed_a_section:
            story.append(Spacer(1, 4 * mm))
        story.append(table)
        printed_a_section = True

    if not printed_a_section:
        # Only the identity block: nothing to count and nothing to write on.
        story.append(Paragraph(
            "<i>Cette feuille ne contient aucune ligne.</i>", quiet_style
        ))

    doc.build(story)
    return buffer.getvalue()


def _body_row(
    line: dict[str, Any],
    *,
    filled: bool,
    with_sources: bool,
    cell: Any,
    quiet: Any,
    paragraph: Any,
    name_width: int,
) -> list[Any]:
    """One printed line — blank for a counter, or carrying what was counted."""
    if not filled:
        quantity: Any = ""  # left blank on purpose: the counter fills this in
    else:
        # Une case laissée vide vaut zéro, et c'est zéro qui s'imprime. Le relevé
        # portait « non compté », ce qui laissait le lecteur devant deux
        # documents à rapprocher : la feuille disait « non compté » et l'analyse
        # comptait la référence à zéro.
        quantity = _fr_number(line.get("qty") or 0)

    row: list[Any] = [
        paragraph(str(line.get("item_number", "")), cell),
        paragraph(_shorten(str(line.get("name", "")), name_width), cell),
        quantity,
        str(line.get("unit", "PCE")),
    ]
    if with_sources:
        row.append(paragraph(
            _SOURCE_LABELS.get(str(line.get("source", "")), str(line.get("source", ""))),
            cell,
        ))
        # Borné : une note du modèle peut faire dix lignes, et une seule ligne
        # de tableau prendrait alors le tiers de la page. Ce qui compte est le
        # début — « lecture incertaine », « référence manuscrite » — et le
        # détail se relit à l'écran.
        row.append(paragraph(
            _shorten(str(line.get("comment", "") or ""), _COMMENT_MAX_CHARS), cell
        ))
    return row


#: Side margin of the printable sheet, in millimetres. Tight on purpose: every
#: millimetre of paper recovered is a row that stays on the first page.
_SIDE_MARGIN_MM = 12

#: Natural height of a body row — leading 10.5 pt plus 4 pt of padding above and
#: below. Named so the enlargement below reads as a stated decision rather than
#: a magic number nobody dares touch: +62 % to write a figure with gloves on,
#: then −6 % once the field trial said it was a shade generous.
_BASE_ROW_HEIGHT = 18.5
_ROW_HEIGHT = _BASE_ROW_HEIGHT * 1.62 * 0.94
#: The repeated section banner needs one line of text, no more.
_BANNER_ROW_HEIGHT = 14.0

#: Column widths in millimetres, summing to the 186 mm of usable page.
#:
#: La désignation, le comptage et l'unité rendent 10 %, 5 % et 20 % de leur
#: largeur, et tout va à la référence. C'est elle qu'on lit pour identifier la
#: pièce — « MASS-00049952 » tenait tout juste, et un préfixe d'atelier de plus
#: la coupait. La désignation, elle, est déjà tronquée par construction : elle
#: perd trois caractères, pas une information.
_PLAIN_COLUMNS = ("Référence", "Désignation", "Comptage", "Unité")
_WIDTHS_PLAIN = (48.34, 84.33, 34.29, 19.04)

#: With provenance, the designation gives back what the two extra columns need.
_SOURCE_COLUMNS = (*_PLAIN_COLUMNS, "Source", "Commentaire")
_WIDTHS_WITH_SOURCES = (43.1, 50.4, 28.5, 14.4, 22.0, 27.6)

_SOURCE_LABELS = {
    "MANUAL": "saisie",
    "SCAN_AI": "IA",
    "FILE_IMPORT": "import",
    "ERP_IMPORT": "ERP",
    "CONSOLIDATION": "consolidation",
    "ARBITRATION": "arbitrage",
    "SYSTEM": "système",
}

#: Designations are truncated, not wrapped: a counter identifies a part by its
#: reference, and letting a long label wrap onto a second line would halve the
#: number of rows a page can hold. The narrower layout gets a tighter budget.
#: Le commentaire est coupé bien plus tard que la désignation : c'est de la
#: prose, elle s'enroule sur plusieurs lignes, et la hauteur de la ligne suit.
_COMMENT_MAX_CHARS = 180

_NAME_MAX_CHARS = 29
_NAME_MAX_CHARS_WITH_SOURCES = 18


def _blank_rows_for(section: str, *, mode: PrintMode, requested: int) -> int:
    """How many empty rows a section gets.

    A record of what was counted gets none. A free-entry sheet is nothing *but*
    empty rows, all in one table — the counter writes both the reference and the
    quantity, so splitting a requested total across three sections would only
    make the number they asked for come out wrong. The sheet handed to a counter
    keeps a small allowance per section, so an article nobody listed has
    somewhere to go.
    """
    if mode is PrintMode.FILLED:
        return 0
    if mode is PrintMode.BLANK:
        return requested if section == "LINE_SIDE" else 0
    return BLANK_ROWS_PER_SECTION.get(section, 0)


def _shorten(name: str, limit: int = _NAME_MAX_CHARS) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def build_variance_pdf(
    *,
    campaign_label: str,
    campaign_code: str,
    count_date: dt.date,
    rows: Sequence[dict[str, Any]],
    by_location: bool,
    material_only: bool,
    generated_at: dt.datetime,
    omitted: int = 0,
) -> bytes:
    """The variance table as a document one can hand over or file.

    Landscape, and deliberately narrower than the Excel export: paper has a
    fixed width, and a table that keeps every column of the workbook would give
    six-point type nobody reads. What survives is what a reading needs — the
    reference, where it is, the two stocks it compares, and the gap between them
    — while the workbook remains the place to slice the rest.

    Quantity and value stay in separate columns rather than stacked in one cell
    as on screen: on paper there is no room for the second line, and a column
    that sometimes holds a quantity and sometimes an amount is unreadable in a
    print-out that is going to be annotated by hand.

    Rows arrive already sorted and capped by the caller; the total line sums
    *what is printed*, and says so, because a total that included rows absent
    from the page would be the one number nobody could check. ``omitted`` is
    what the cap left out, printed under the table — a truncation nobody is told
    about is read as a complete document.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Table, TableStyle

    page = landscape(A4)
    margin = _SIDE_MARGIN_MM * mm
    buffer = io.BytesIO()

    scope = "par référence et emplacement" if by_location else "par référence"
    filters = "au-delà des seuils uniquement" if material_only else "tous les écarts"

    def draw_page(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(margin, page[1] - 11 * mm, f"Écarts d'inventaire — {scope}")
        canvas.setFont("Helvetica", 9)
        canvas.drawString(
            margin, page[1] - 16 * mm,
            f"{campaign_label} — comptage du {count_date:%d/%m/%Y} — {filters}",
        )
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(margin, page[1] - 19 * mm, page[0] - margin, page[1] - 19 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(
            margin, 8 * mm,
            f"{campaign_code} · édité le {generated_at:%d/%m/%Y à %H:%M}",
        )
        canvas.drawRightString(page[0] - margin, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(
        buffer, pagesize=page,
        leftMargin=margin, rightMargin=margin,
        topMargin=24 * mm, bottomMargin=13 * mm,
        title=f"Écarts — {campaign_code}",
    )
    doc.addPageTemplates([
        PageTemplate(
            id="main",
            frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height)],
            onPage=draw_page,
        )
    ])

    header = ["Article", "Désignation"]
    widths = [30 * mm, 62 * mm]
    if by_location:
        header += ["Entrepôt", "Emplacement"]
        widths += [20 * mm, 28 * mm]
    header += [
        "Qté ERP", "Valeur ERP", "Qté comptée", "Valeur comptée",
        "Écart qté", "Écart valeur",
    ]
    widths += [22 * mm, 26 * mm, 24 * mm, 28 * mm, 22 * mm, 26 * mm]

    body: list[list[str]] = []
    for row in rows:
        cells = [str(row["itemNumber"]), _shorten(str(row["name"]), 46)]
        if by_location:
            cells += [str(row["warehouseId"]), str(row["locationId"])]
        cells += [
            _fr_number(row["bookQty"]),
            _fr_money(row["bookValue"]),
            _fr_number(row["countedQty"]),
            _fr_money(row["countedValue"]),
            _fr_number(row["varianceQty"], signed=True),
            _fr_money(row["varianceValue"], signed=True),
        ]
        body.append(cells)

    total_label = f"Total des {len(rows)} ligne(s) imprimée(s)"
    total: list[str] = [total_label, ""]
    if by_location:
        total += ["", ""]
    total += [
        "",
        _fr_money(sum(float(r["bookValue"]) for r in rows)),
        "",
        _fr_money(sum(float(r["countedValue"]) for r in rows)),
        "",
        _fr_money(sum(float(r["varianceValue"]) for r in rows), signed=True),
    ]

    table = Table([header, *body, total], colWidths=widths, repeatRows=1)
    numeric_from = 4 if by_location else 2
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("LEADING", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [colors.white, colors.HexColor("#F1F5F9")]),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))

    story: list[Any] = [table]
    if omitted > 0:
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, Spacer

        note = ParagraphStyle(
            "note", parent=getSampleStyleSheet()["BodyText"],
            fontSize=8, leading=10, textColor=colors.HexColor("#B45309"),
        )
        story += [
            Spacer(1, 4 * mm),
            Paragraph(
                f"{omitted} ligne(s) d'écart plus faible ne sont pas reprises sur "
                "ce document. L'export Excel les contient toutes.",
                note,
            ),
        ]

    doc.build(story)
    return buffer.getvalue()


def _fr_money(value: Any, *, signed: bool = False) -> str:
    """An amount, French-formatted, rounded to the euro.

    Cents on a variance line are noise: the figure is a valuation of a physical
    count, and nobody acts on the difference between 12 431 € and 12 431,40 €.
    """
    number = round(float(value))
    text = f"{abs(number):,.0f}".replace(",", " ")
    sign = "-" if number < 0 else ("+" if signed and number > 0 else "")
    return f"{sign}{text} €"


def _fr_number(value: Any, *, signed: bool = False) -> str:
    """A counted quantity, French-formatted, without trailing decimal noise.

    ``signed`` prefixes a ``+`` on a positive: in a variance column the sign is
    the information, and a bare number there reads as a stock level.
    """
    number = float(value)
    text = f"{number:,.6f}".rstrip("0").rstrip(".") if number % 1 else f"{number:,.0f}"
    if signed and number > 0:
        text = f"+{text}"
    # A *no-break* space as the thousands separator, French convention. The
    # narrow one typography would prefer (U+202F) is absent from Helvetica's
    # Latin-1 encoding, and ReportLab draws a missing glyph as a black box:
    # « 2■724 », right where the counter reads a digit.
    return text.replace(",", " ")


#: Le texte imprimé en tête de chaque section, par défaut.
#:
#: Un seul texte, et non un titre plus un indice comme auparavant : ce que le
#: métier dicte est une phrase entière, dont la moitié utile est justement la
#: consigne. « WIP — ensembles déclarés » ne dit pas au compteur de noter le
#: numéro de Galia ; c'est pourtant ce qu'il doit faire.
#:
#: Chaque zone peut les remplacer — voir ``section_labels`` sur
#: :class:`~inventory.domain.models.Zone`. Le défaut est ce qui s'imprime quand
#: personne n'a rien dit, pas une valeur qu'on recopie pour la modifier.
DEFAULT_SECTION_TITLES = {
    "LINE_SIDE": "Composants en bord de ligne",
    "WIP": (
        "WIP — en-cours non déclaré "
        "(Statut MOM : on progress / waiting for decision)"
    ),
    "WIP_OK": (
        "MOM OK — si MEL, notez le numéro de Galia ou le numéro de série sur "
        "la feuille accompagnante"
    ),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _cell(value: Any) -> Any:
    """Coerce a value into something XlsxWriter accepts."""
    from decimal import Decimal

    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return str(value)
    return value


def _width(header: str) -> float:
    return max(11.0, min(46.0, len(header) * 1.15 + 4))


def _safe_sheet_name(name: str) -> str:
    """Excel sheet names: 31 characters, no ``[]:*?/\\``."""
    cleaned = "".join("-" if ch in "[]:*?/\\" else ch for ch in name)
    return cleaned[:31] or "Feuille"
