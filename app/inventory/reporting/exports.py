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
from collections.abc import Sequence
from typing import Any

__all__ = [
    "build_workbook",
    "build_counting_sheet_pdf",
    "build_journal_export",
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
) -> bytes:
    """Render a printable counting sheet.

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
      footer of every page, so a page separated from its stack is still
      traceable;
    * one identity line closes the header — name, times and signature side by
      side rather than stacked.
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
    section_style = ParagraphStyle(
        "section", parent=styles["BodyText"], fontSize=9.5, leading=12,
        textColor=colors.HexColor("#0F172A"), spaceBefore=4, spaceAfter=2,
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

    by_section: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_section.setdefault(str(line.get("section", "LINE_SIDE")), []).append(line)

    for section in ("LINE_SIDE", "WIP", "WIP_OK"):
        section_lines = by_section.get(section)
        if not section_lines:
            continue
        story.append(Paragraph(
            f"<b>{_SECTION_TITLES[section]}</b> — {_SECTION_HINTS[section]}",
            section_style,
        ))
        data: list[list[Any]] = [["Référence", "Désignation", "Comptage", "Unité"]]
        for line in section_lines:
            data.append([
                Paragraph(str(line.get("item_number", "")), cell_style),
                Paragraph(_shorten(str(line.get("name", ""))), cell_style),
                "",  # left blank on purpose: this is what the counter fills in
                str(line.get("unit", "PCE")),
            ])
        table = Table(
            data,
            colWidths=[36 * mm, 84 * mm, 38 * mm, 28 * mm],
            # Tall rows: a figure written with gloves on, in a workshop, needs
            # room. The header keeps its natural height.
            rowHeights=[_BASE_ROW_HEIGHT] + [_ROW_HEIGHT] * len(section_lines),
            repeatRows=1,
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#F8FAFC")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (2, 0), (3, -1), "CENTER"),
        ]))
        story.append(table)
        story.append(Spacer(1, 4 * mm))

    doc.build(story)
    return buffer.getvalue()


#: Side margin of the printable sheet, in millimetres. Tight on purpose: every
#: millimetre of paper recovered is a row that stays on the first page.
_SIDE_MARGIN_MM = 12

#: Natural height of a body row before V2 — leading 10.5 pt plus 4 pt of padding
#: above and below. Named so the 62 % increase below reads as a stated decision
#: rather than a magic number nobody dares touch.
_BASE_ROW_HEIGHT = 18.5
_ROW_HEIGHT = _BASE_ROW_HEIGHT * 1.62

#: Designations are truncated, not wrapped: a counter identifies a part by its
#: reference, and letting a long label wrap onto a second line would halve the
#: number of rows a page can hold.
_NAME_MAX_CHARS = 32


def _shorten(name: str) -> str:
    return name if len(name) <= _NAME_MAX_CHARS else name[: _NAME_MAX_CHARS - 1] + "…"


_SECTION_TITLES = {
    "LINE_SIDE": "Composants en bord de ligne",
    "WIP": "WIP — en-cours non déclaré",
    "WIP_OK": "WIP — ensembles déclarés",
}

_SECTION_HINTS = {
    "LINE_SIDE": "compter les pièces à l'unité",
    "WIP": "compter les ensembles ; ils seront éclatés en nomenclature",
    "WIP_OK": "compter les ensembles terminés et déclarés dans l'ERP",
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
