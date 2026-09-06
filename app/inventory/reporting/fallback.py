"""Le classeur de repli : la consolidation GENERIQUE refaite par formules.

Pourquoi un second fichier
--------------------------
L'export de campagne est une **photo** : il dit ce que le moteur a calculé, et
modifier une de ses cases ne change rien. C'est exactement ce qu'on veut d'une
archive, et exactement ce qu'on ne veut pas le jour où l'application n'est pas
joignable et où le journal doit partir quand même.

Ce classeur-ci est l'autre document. Il ne porte pas le résultat : il porte les
**données** — le référentiel, les nomenclatures, les deux comptages de chaque
zone — et le résultat s'y **recalcule**. Corriger une quantité sur la feuille
d'une zone met à jour la feuille ``Data``, qui met à jour le journal consolidé,
sans ressaisie et sans repasser par l'application.

C'est le fichier ``Compil GENERIQUE.xlsx`` qu'il remplace, mais construit à
l'envers : là où le classeur historique était la source de vérité qu'on
maintenait à la main, celui-ci est **engendré** à chaque export à partir de la
campagne. Il n'y a donc plus de recopie entre les onglets, plus de plage à
étendre quand une zone grandit, et plus de requête Power Query à réparer.

Les règles, écrites deux fois exprès
------------------------------------
Chaque règle de :mod:`inventory.domain.consolidation` est ici réécrite en
formule de tableur. C'est une duplication, et elle est volontaire : un repli qui
recopierait les chiffres du moteur ne serait pas un repli, ce serait la même
photo dans un autre cadre. Ce que cela coûte — deux expressions d'une même règle
qui peuvent diverger — est payé par le contrôle qui va avec : la recette
recalcule réellement le classeur et compare, ligne à ligne, avec ce que
:func:`~inventory.domain.consolidation.consolidate_generic` produit sur les
mêmes données.

Ce que le classeur ne sait pas faire
------------------------------------
Un tableur n'ajoute pas de lignes tout seul. Le journal porte donc une ligne par
article du référentiel, et les zones réservent quelques lignes libres déjà
câblées vers ``Data`` : on écrit dedans, le total suit. Au-delà, il faut
insérer une ligne *et* la recopier dans ``Data`` — la feuille « Lisez-moi » le
dit, plutôt que de laisser découvrir qu'une quantité ajoutée en bas ne comptait
pas.
"""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from ..domain.bom import BomCycleError
from ..domain.consolidation import ConsolidationInput, ZonePassLine, zone_pass_lines
from ..domain.enums import CountSection, ItemType
from ..domain.models import Item

__all__ = ["build_consolidation_fallback", "SPARE_ROWS_PER_ZONE"]


#: Lignes libres réservées en bas de chaque feuille de zone.
#:
#: Elles sont déjà rattachées à ``Data`` : une référence oubliée s'y ajoute et
#: compte immédiatement. Vingt est un compromis assumé — assez pour la poignée
#: d'oublis qu'une zone produit réellement, pas assez pour que la feuille
#: imprimée se termine sur une page de vide.
SPARE_ROWS_PER_ZONE = 20

#: Plafond de la feuille « Éclatement ».
#:
#: Elle contient une ligne par couple (composé, composant) de tout le
#: référentiel, ce qui reste très en deçà sur une campagne réelle. Le plafond
#: existe pour qu'un référentiel aberrant produise un fichier tronqué **et
#: annoncé** plutôt qu'un fichier que rien n'ouvre.
MAX_EXPLOSION_ROWS = 60_000

#: Les trois sections, et ce qu'elles veulent dire. Reprises telles quelles sur
#: la feuille « Lisez-moi » : les formules du journal filtrent sur ces codes, et
#: quelqu'un qui saisit « BDL » dans la colonne Section ne comptera nulle part.
SECTION_HELP = (
    (CountSection.LINE_SIDE, "Composants en bord de ligne — comptés tels quels"),
    (CountSection.WIP, "En-cours non déclaré — éclaté en composants"),
    (CountSection.WIP_OK, "En-cours déclaré — compté tel quel"),
)

_YES = "oui"
_NO = "non"

#: Les libellés d'état d'une ligne de zone, et les statuts du journal. Nommés
#: une fois : ils sont écrits dans une formule et relus par une autre.
_ARBITRATED = "arbitré"
_SINGLE_PASS = "un seul comptage"
_PENDING = "à arbitrer"
_AGREED = "accord"

_EXCLUDED = "exclu"
_KEPT = "retenu"
_OFFSET = "compensé — non retenu"
_ZEROED = "soldé à zéro"
_UNCOUNTED = "non compté"

# Noms des feuilles. Le préfixe ``Z_`` des zones garantit qu'aucun code de zone
# ne peut entrer en collision avec une feuille de structure.
_README = "Lisez-moi"
_ITEMS = "Articles"
_BOM = "Nomenclatures"
_EXPLOSION = "Éclatement"
_DATA = "Data"
_JOURNAL = "Journal consolidé"
_ZONE_PREFIX = "Z_"

#: Adresse de la cellule qui porte la tolérance d'arbitrage, et le nom défini
#: qui la désigne dans les formules des zones.
_TOLERANCE_NAME = "Tolerance"


def build_consolidation_fallback(
    payload: ConsolidationInput,
    *,
    campaign_code: str,
    campaign_label: str,
    count_date: dt.date,
    generic_key: str,
    provenance: Mapping[str, Any] | None = None,
) -> bytes:
    """Construire le classeur de repli d'une consolidation GENERIQUE.

    :param payload: **la même entrée que le moteur**, et non une relecture de la
        base faite pour l'occasion. C'est ce qui garantit que le classeur porte
        le référentiel, les nomenclatures et les zones que la consolidation
        aurait utilisés — un second chemin de lecture aurait pu, lui, diverger.
    :param generic_key: l'emplacement couvert, tel qu'on l'écrit — ``B06VRAC /
        GENERIQUE``. Il figure sur la première feuille : un classeur qui ne dit
        pas de quel emplacement il parle finit importé dans le mauvais journal.
    """
    import xlsxwriter

    items = payload.items
    book = payload.book_stock

    # ---- tout ce qui se compte avant d'écrire quoi que ce soit ---------------
    # ``constant_memory`` interdit de revenir sur une feuille déjà écrite : les
    # plages des formules doivent donc être connues avant la première cellule.
    item_numbers = sorted(items)
    bom_rows = _bom_rows(payload)
    explosion_rows, cycles, truncated = _explosion_rows(payload)
    zones = _zone_pages(payload)

    n_items = len(item_numbers)
    n_explosion = len(explosion_rows)
    n_data = sum(len(z.lines) + SPARE_ROWS_PER_ZONE for z in zones)

    items_all = _range(_ITEMS, "A", "I", n_items)
    items_ref = _column(_ITEMS, "A", n_items)
    data_ref = _column(_DATA, "B", n_data)
    data_section = _column(_DATA, "C", n_data)
    data_qty = _column(_DATA, "D", n_data)
    data_state = _column(_DATA, "E", n_data)
    data_known = _column(_DATA, "F", n_data)
    expl_parent = _column(_EXPLOSION, "A", n_explosion)
    expl_child = _column(_EXPLOSION, "C", n_explosion)
    expl_from_wip = _column(_EXPLOSION, "G", n_explosion)
    journal_status = _column(_JOURNAL, "L", n_items)
    journal_posted = _column(_JOURNAL, "M", n_items)
    journal_total = _column(_JOURNAL, "J", n_items)
    journal_value = _column(_JOURNAL, "O", n_items)

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
    fmt = _formats(workbook)

    # ---- Lisez-moi ----------------------------------------------------------
    readme = workbook.add_worksheet(_README)
    readme.set_column(0, 0, 42)
    readme.set_column(1, 1, 78)
    tolerance_row = _write_readme(
        readme,
        fmt,
        campaign_code=campaign_code,
        campaign_label=campaign_label,
        count_date=count_date,
        generic_key=generic_key,
        provenance=provenance or {},
        tolerance=payload.arbitration_tolerance,
        zones=zones,
        cycles=cycles,
        truncated=truncated,
        controls=(
            ("Quantité retenue, toutes zones", f"=SUM({data_qty})"),
            ("Lignes en attente d'arbitrage", f'=COUNTIF({data_state},"{_PENDING}")'),
            ("Lignes hors référentiel", f'=COUNTIF({data_known},"{_NO}")'),
            (
                "En-cours comptés sans nomenclature",
                f'=SUMPRODUCT(({data_section}="{CountSection.WIP}")'
                f"*({data_ref}<>\"\")*(COUNTIF({expl_parent},{data_ref})=0))",
            ),
            ("Articles retenus au journal", f'=COUNTIF({journal_status},"{_KEPT}")'),
            (
                "Quantité postée au journal",
                f'=SUMIF({journal_posted},"{_YES}",{journal_total})',
            ),
            ("Valeur postée au journal", f"=SUM({journal_value})"),
        ),
    )
    workbook.define_name(
        _TOLERANCE_NAME, f"={_sheet(_README)}!$B${tolerance_row + 1}"
    )

    # ---- Articles -----------------------------------------------------------
    sheet = _grid(workbook, fmt, _ITEMS, (
        "Référence", "Désignation", "Type", "Unité", "Prix standard €",
        "Exclu GENERIQUE", "Exclu nomenclature", "Produit fini",
        "Stock ERP GENERIQUE",
    ))
    for r, number in enumerate(item_numbers, start=1):
        item = items[number]
        sheet.write_string(r, 0, number)
        sheet.write_string(r, 1, item.name)
        sheet.write_string(r, 2, str(item.item_type))
        sheet.write_string(r, 3, item.unit)
        sheet.write_number(r, 4, float(item.std_price), fmt["money"])
        sheet.write_string(r, 5, _flag(item.excluded_from_generic))
        sheet.write_string(r, 6, _flag(item.excluded_from_bom))
        sheet.write_string(r, 7, _flag(item.item_type is ItemType.FINISHED))
        sheet.write_number(r, 8, float(book.get(number, 0)), fmt["qty"])

    # ---- Nomenclatures ------------------------------------------------------
    sheet = _grid(workbook, fmt, _BOM, (
        "Composé", "Désignation composé", "Composant", "Désignation composant",
        "Quantité par composé",
    ))
    for r, (parent, child, qty) in enumerate(bom_rows, start=1):
        sheet.write_string(r, 0, parent)
        sheet.write_string(r, 1, _name(items, parent))
        sheet.write_string(r, 2, child)
        sheet.write_string(r, 3, _name(items, child))
        sheet.write_number(r, 4, float(qty), fmt["qty"])

    # ---- Éclatement ---------------------------------------------------------
    sheet = _grid(workbook, fmt, _EXPLOSION, (
        "Composé", "Désignation composé", "Composant", "Désignation composant",
        "Quantité par composé", "Qté WIP comptée du composé",
        "Qté composant issue du WIP",
    ))
    for r, (parent, child, qty) in enumerate(explosion_rows, start=1):
        row = r + 1
        sheet.write_string(r, 0, parent)
        sheet.write_string(r, 1, _name(items, parent))
        sheet.write_string(r, 2, child)
        sheet.write_string(r, 3, _name(items, child))
        sheet.write_number(r, 4, float(qty), fmt["qty"])
        sheet.write_formula(
            r, 5,
            f'=SUMIFS({data_qty},{data_ref},$A{row},'
            f'{data_section},"{CountSection.WIP}")',
            fmt["qty"],
        )
        sheet.write_formula(r, 6, f"=ROUND($E{row}*$F{row},6)", fmt["qty"])

    # ---- une feuille par zone ----------------------------------------------
    for page in zones:
        _write_zone(workbook, fmt, page, items)

    # ---- Data ---------------------------------------------------------------
    sheet = _grid(workbook, fmt, _DATA, (
        "Zone", "Référence", "Section", "Quantité retenue", "État",
        "Au référentiel",
    ))
    r = 0
    for page in zones:
        ref = _sheet(page.sheet_name)
        for offset in range(len(page.lines) + SPARE_ROWS_PER_ZONE):
            r += 1
            row, src = r + 1, offset + 2
            guard = f'{ref}!$A{src}=""'
            sheet.write_string(r, 0, page.zone_code)
            sheet.write_formula(r, 1, f'=IF({guard},"",{ref}!$A{src})')
            sheet.write_formula(r, 2, f'=IF({guard},"",{ref}!$C{src})')
            sheet.write_formula(r, 3, f'=IF({guard},"",{ref}!$H{src})', fmt["qty"])
            sheet.write_formula(r, 4, f'=IF({guard},"",{ref}!$I{src})')
            sheet.write_formula(
                r, 5,
                f'=IF($B{row}="","",'
                f'IF(ISNA(MATCH($B{row},{items_ref},0)),"{_NO}","{_YES}"))',
            )

    # ---- Journal consolidé --------------------------------------------------
    sheet = _grid(workbook, fmt, _JOURNAL, (
        "Référence", "Désignation", "Type", "Unité", "Exclu GENERIQUE",
        "Produit fini", "Bord de ligne", "WIP assemblé", "WIP éclaté",
        "Quantité totale", "Stock ERP GENERIQUE", "Statut", "Postée",
        "Prix standard €", "Valeur €",
    ))
    for r, number in enumerate(item_numbers, start=1):
        row = r + 1
        look = f"VLOOKUP($A{row},{items_all},"
        sheet.write_string(r, 0, number)
        sheet.write_formula(r, 1, f'=IFERROR({look}2,0),"")')
        sheet.write_formula(r, 2, f'=IFERROR({look}3,0),"")')
        sheet.write_formula(r, 3, f'=IFERROR({look}4,0),"")')
        sheet.write_formula(r, 4, f'=IFERROR({look}6,0),"{_NO}")')
        sheet.write_formula(r, 5, f'=IFERROR({look}8,0),"{_NO}")')
        # Un produit fini compté en bord de ligne ou en WIP assemblé ne compte
        # pas : ses composants sont déjà comptés par l'éclatement, et lui vaut
        # bien plus cher qu'eux.
        for col, section in ((6, CountSection.LINE_SIDE), (7, CountSection.WIP_OK)):
            sheet.write_formula(
                r, col,
                f'=IF($F{row}="{_YES}",0,SUMIFS({data_qty},{data_ref},$A{row},'
                f'{data_section},"{section}"))',
                fmt["qty"],
            )
        sheet.write_formula(
            r, 8, f"=SUMIFS({expl_from_wip},{expl_child},$A{row})", fmt["qty"]
        )
        sheet.write_formula(
            r, 9,
            f'=IF($E{row}="{_YES}",0,ROUND($G{row}+$H{row}+$I{row},6))',
            fmt["qty"],
        )
        sheet.write_formula(r, 10, f"=IFERROR({look}9,0),0)", fmt["qty"])
        sheet.write_formula(
            r, 11,
            f'=IF($E{row}="{_YES}","{_EXCLUDED}",'
            f'IF($J{row}<>0,"{_KEPT}",'
            f'IF(OR($G{row}<>0,$H{row}<>0,$I{row}<>0),"{_OFFSET}",'
            f'IF(AND($K{row}<>0,$F{row}<>"{_YES}"),"{_ZEROED}","{_UNCOUNTED}"))))',
        )
        sheet.write_formula(
            r, 12,
            f'=IF(OR($L{row}="{_KEPT}",$L{row}="{_ZEROED}"),"{_YES}","{_NO}")',
        )
        sheet.write_formula(r, 13, f"=IFERROR({look}5,0),0)", fmt["money"])
        sheet.write_formula(
            r, 14,
            f'=IF($M{row}="{_YES}",ROUND($J{row}*$N{row},2),0)',
            fmt["money"],
        )

    workbook.close()
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Les données, préparées avant écriture
# --------------------------------------------------------------------------- #

class _ZonePage:
    """Une zone et la feuille qui la portera."""

    __slots__ = ("closed", "label", "lines", "passes", "sheet_name", "zone_code")

    def __init__(
        self, *, zone_code: str, label: str, sheet_name: str,
        lines: Sequence[ZonePassLine], passes: int, closed: bool,
    ) -> None:
        self.zone_code = zone_code
        self.label = label
        self.sheet_name = sheet_name
        self.lines = lines
        self.passes = passes
        self.closed = closed


def _zone_pages(payload: ConsolidationInput) -> list[_ZonePage]:
    """Une feuille par zone — **une seule**, même quand la zone est comptée deux fois.

    C'est le point de départ de la demande, et c'est aussi ce que le métier
    retient : à la fin, une ligne porte une quantité et une seule. Les deux
    passages restent visibles côte à côte dans deux colonnes, la quantité
    arbitrée dans une troisième, et la quantité retenue se déduit des trois par
    formule. Deux onglets par zone auraient rendu le classeur illisible et
    auraient surtout laissé croire qu'il reste deux chiffres à choisir.
    """
    pages: list[_ZonePage] = []
    taken: set[str] = set()
    for counts in payload.zones:
        zone = counts.zone
        pages.append(_ZonePage(
            zone_code=zone.code,
            label=zone.label or zone.code,
            sheet_name=_unique_sheet_name(f"{_ZONE_PREFIX}{zone.code}", taken),
            lines=zone_pass_lines(counts),
            passes=zone.passes,
            closed=zone.closed_at is not None,
        ))
    return pages


def _bom_rows(payload: ConsolidationInput) -> list[tuple[str, str, Decimal]]:
    """La nomenclature **telle que le moteur la lit**, pas telle qu'elle est stockée.

    Versions périmées écartées, doublons de versions fusionnés, composants
    exclus retirés : c'est l'index de la campagne qui répond, et non une seconde
    lecture des liens. Un classeur qui afficherait les liens bruts montrerait
    une structure dont le journal, juste à côté, ne se sert pas.
    """
    bom = payload.bom
    return [
        (parent, child, qty)
        for parent in sorted(bom.parents)
        for child, qty in bom.direct_children(parent)
    ]


def _explosion_rows(
    payload: ConsolidationInput,
) -> tuple[list[tuple[str, str, Decimal]], list[str], bool]:
    """L'éclatement à plat : ce qu'une unité de chaque composé consomme.

    Le tableur ne sait pas descendre une structure à plusieurs niveaux ; c'est
    donc fait ici, une fois, avec la mécanique du moteur — niveaux fantômes
    traversés, profondeur maximale de la campagne respectée. La feuille est un
    **instantané de la structure** : corriger une quantité de comptage
    recalcule tout, corriger une nomenclature ne recalcule rien. La feuille
    « Lisez-moi » le dit, parce que c'est la seule chose que ce classeur ne
    suit pas.

    :returns: les lignes, les composés dont la structure boucle, et si le
        plafond a tronqué la feuille.
    """
    bom = payload.bom
    rows: list[tuple[str, str, Decimal]] = []
    cycles: list[str] = []
    for parent in sorted(bom.parents):
        try:
            unit = bom.unit_explosion(parent)
        except BomCycleError:
            cycles.append(parent)
            continue
        for child in sorted(unit):
            if len(rows) >= MAX_EXPLOSION_ROWS:
                return rows, cycles, True
            rows.append((parent, child, unit[child]))
    return rows, cycles, False


# --------------------------------------------------------------------------- #
# Écriture
# --------------------------------------------------------------------------- #

def _formats(workbook: Any) -> dict[str, Any]:
    return {
        "header": workbook.add_format({
            "bold": True, "bg_color": "#1E293B", "font_color": "#FFFFFF",
            "border": 1, "border_color": "#334155", "valign": "vcenter",
            "text_wrap": True,
        }),
        "title": workbook.add_format({"bold": True, "font_size": 14}),
        "section": workbook.add_format({
            "bold": True, "font_color": "#0F172A", "bottom": 1,
            "bottom_color": "#CBD5E1",
        }),
        "label": workbook.add_format({"bold": True, "font_color": "#475569"}),
        "note": workbook.add_format({"font_color": "#475569", "text_wrap": True}),
        "warn": workbook.add_format({"bold": True, "font_color": "#B45309"}),
        "qty": workbook.add_format({"num_format": "#,##0.######"}),
        "money": workbook.add_format({"num_format": "#,##0.00 €"}),
        # Les cases qu'on est invité à corriger, et elles seules.
        "input": workbook.add_format({
            "num_format": "#,##0.######", "bg_color": "#FEFCE8",
            "border": 1, "border_color": "#E2E8F0",
        }),
    }


def _grid(workbook: Any, fmt: dict[str, Any], name: str, headers: Sequence[str]) -> Any:
    sheet = workbook.add_worksheet(name)
    sheet.freeze_panes(1, 0)
    for column, header in enumerate(headers):
        sheet.write_string(0, column, header, fmt["header"])
        sheet.set_column(column, column, max(11.0, min(46.0, len(header) * 1.15 + 4)))
    return sheet


def _write_zone(
    workbook: Any, fmt: dict[str, Any], page: _ZonePage, items: Mapping[str, Item]
) -> None:
    """La feuille d'une zone : deux comptages saisis, la quantité retenue calculée.

    Les colonnes de saisie sont teintées. Ce n'est pas de la décoration : c'est
    la seule chose qui distingue, sur ce document, ce qu'on peut corriger de ce
    qui se déduit — et écraser la colonne « Quantité retenue » par un chiffre
    tapé à la main ferait taire la règle sans que personne ne le voie.
    """
    sheet = _grid(workbook, fmt, page.sheet_name, (
        "Référence", "Désignation", "Section", "Unité", "Comptage n°1",
        "Comptage n°2", "Quantité arbitrée", "Quantité retenue", "État",
    ))
    for r in range(1, len(page.lines) + SPARE_ROWS_PER_ZONE + 1):
        row = r + 1
        if r <= len(page.lines):
            line = page.lines[r - 1]
            item = items.get(line.item_number)
            sheet.write_string(r, 0, line.item_number)
            sheet.write_string(r, 1, item.name if item else "")
            sheet.write_string(r, 2, str(line.section))
            sheet.write_string(r, 3, item.unit if item else "")
            for col, qty in (
                (4, line.qty_pass_1), (5, line.qty_pass_2), (6, line.qty_arbitrated),
            ):
                # Une case **vide** dit « ce passage ne porte pas la
                # référence » ; un zéro dit « on a cherché, il n'y avait
                # rien ». Écrire zéro dans les deux cas transformerait tous les
                # comptages simples en divergences à arbitrer.
                if qty is None:
                    sheet.write_blank(r, col, None, fmt["input"])
                else:
                    sheet.write_number(r, col, float(qty), fmt["input"])
        else:
            for col in range(7):
                sheet.write_blank(r, col, None, fmt["input"] if col >= 4 else None)

        sheet.write_formula(
            r, 7,
            f'=IF($A{row}="","",'
            f'IF($G{row}<>"",$G{row},'
            f'IF($E{row}="",$F{row},'
            f'IF($F{row}="",$E{row},'
            f'IF($E{row}=$F{row},$F{row},'
            f'IF({_TOLERANCE_NAME}<=0,"",'
            f"IF(ABS($F{row}-$E{row})/MAX(ABS($E{row}),ABS($F{row}))"
            f'<={_TOLERANCE_NAME},$F{row},"")))))))',
            fmt["qty"],
        )
        sheet.write_formula(
            r, 8,
            f'=IF($A{row}="","",'
            f'IF($G{row}<>"","{_ARBITRATED}",'
            f'IF(OR($E{row}="",$F{row}=""),"{_SINGLE_PASS}",'
            f'IF($H{row}="","{_PENDING}","{_AGREED}"))))',
        )


def _write_readme(
    sheet: Any,
    fmt: dict[str, Any],
    *,
    campaign_code: str,
    campaign_label: str,
    count_date: dt.date,
    generic_key: str,
    provenance: Mapping[str, Any],
    tolerance: Decimal,
    zones: Sequence[_ZonePage],
    cycles: Sequence[str],
    truncated: bool,
    controls: Sequence[tuple[str, str]],
) -> int:
    """La première feuille, et la seule qui se lit en entier.

    :returns: l'indice de la ligne portant la tolérance d'arbitrage, pour que le
        nom défini qui la désigne dans les formules pointe sur la bonne case.
    """
    r = 0
    sheet.write_string(r, 0, "Consolidation GENERIQUE — classeur de repli", fmt["title"])
    r += 2
    sheet.write_string(
        r, 0,
        "Ce classeur ne montre pas un résultat : il le recalcule. Corrigez une "
        "quantité sur la feuille d'une zone, la feuille « Data » et le journal "
        "consolidé suivent immédiatement. Il existe pour le jour où le journal "
        "doit partir sans l'application ; l'application reste la source de "
        "vérité tant qu'elle répond.",
        fmt["note"],
    )
    r += 2

    sheet.write_string(r, 0, "Campagne", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for label, value in (
        ("Code", campaign_code),
        ("Libellé", campaign_label),
        ("Date de comptage", count_date.isoformat()),
        ("Emplacement couvert", generic_key),
        *[(str(k), _text(v)) for k, v in provenance.items()],
    ):
        sheet.write_string(r, 0, label, fmt["label"])
        sheet.write_string(r, 1, value)
        r += 1

    r += 1
    sheet.write_string(r, 0, "Règle d'arbitrage", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    tolerance_row = r
    sheet.write_string(r, 0, "Tolérance d'écart entre les deux comptages", fmt["label"])
    sheet.write_number(r, 1, float(tolerance), fmt["qty"])
    r += 1
    sheet.write_string(r, 0, "", fmt["label"])
    sheet.write_string(
        r, 1,
        "Écart relatif en deçà duquel le comptage n°2 est retenu sans "
        "arbitrage. Zéro exige l'égalité stricte. Modifier cette case "
        "recalcule toutes les zones.",
        fmt["note"],
    )
    r += 2

    sheet.write_string(r, 0, "Quantité retenue, dans l'ordre", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for label, text in (
        ("1.", "Une quantité arbitrée, s'il y en a une : elle tranche."),
        ("2.", "Les deux comptages s'accordent (ou à la tolérance près) : "
               "le comptage n°2, le plus tardif."),
        ("3.", "Un seul passage porte la référence : celui-là, et la colonne "
               "« État » le dit."),
        ("4.", "Sinon la ligne reste vide et attend un arbitrage : elle ne "
               "compte nulle part tant que personne n'a tranché."),
    ):
        sheet.write_string(r, 0, label, fmt["label"])
        sheet.write_string(r, 1, text, fmt["note"])
        r += 1
    r += 1

    sheet.write_string(r, 0, "Les trois sections", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for section, text in SECTION_HELP:
        sheet.write_string(r, 0, str(section), fmt["label"])
        sheet.write_string(r, 1, text, fmt["note"])
        r += 1
    r += 1

    sheet.write_string(r, 0, "Contrôles — recalculés en direct", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for label, formula in controls:
        sheet.write_string(r, 0, label, fmt["label"])
        sheet.write_formula(r, 1, formula, fmt["qty"])
        r += 1
    r += 1

    sheet.write_string(r, 0, "Les zones du classeur", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for page in zones:
        sheet.write_string(r, 0, page.sheet_name, fmt["label"])
        sheet.write_string(
            r, 1,
            f"{page.label} — {len(page.lines)} ligne(s), "
            f"{page.passes} comptage(s) attendu(s), "
            + ("zone terminée" if page.closed else "zone en cours"),
        )
        r += 1
    if not zones:
        sheet.write_string(r, 0, "—", fmt["label"])
        sheet.write_string(r, 1, "Aucune zone n'est encore définie.", fmt["note"])
        r += 1
    r += 1

    sheet.write_string(r, 0, "Ce que ce classeur ne suit pas", fmt["section"])
    sheet.write_string(r, 1, "", fmt["section"])
    r += 1
    for label, text in (
        ("Éclatement",
         "La feuille « Éclatement » est un instantané de la structure des "
         "nomenclatures, aplatie à la génération. Modifier la feuille "
         "« Nomenclatures » ne la met pas à jour : c'est la seule chose de ce "
         "classeur qui ne se recalcule pas."),
        ("Lignes ajoutées",
         f"Chaque zone réserve {SPARE_ROWS_PER_ZONE} lignes libres déjà "
         "rattachées à « Data » : écrivez dedans et le total suit. Au-delà, "
         "insérer une ligne dans une zone oblige à la recopier dans « Data », "
         "sinon sa quantité ne compte nulle part."),
        ("Articles inconnus",
         "Le journal porte une ligne par article du référentiel. Une référence "
         "comptée qui n'y figure pas n'a pas de ligne — comme dans "
         "l'application. La colonne « Au référentiel » de la feuille « Data » "
         "et le contrôle ci-dessus la montrent."),
    ):
        sheet.write_string(r, 0, label, fmt["label"])
        sheet.write_string(r, 1, text, fmt["note"])
        r += 1

    if cycles:
        r += 1
        sheet.write_string(r, 0, "Nomenclatures qui bouclent", fmt["warn"])
        sheet.write_string(
            r, 1,
            "Non éclatées, donc absentes du journal : "
            + ", ".join(cycles[:40])
            + (" …" if len(cycles) > 40 else ""),
            fmt["warn"],
        )
        r += 1
    if truncated:
        r += 1
        sheet.write_string(r, 0, "Éclatement tronqué", fmt["warn"])
        sheet.write_string(
            r, 1,
            f"La feuille « Éclatement » a atteint {MAX_EXPLOSION_ROWS} lignes et "
            "a été arrêtée là. Les composés suivants n'y figurent pas, et leur "
            "WIP ne compte donc pas dans ce classeur.",
            fmt["warn"],
        )
        r += 1

    return tolerance_row


# --------------------------------------------------------------------------- #
# Petites choses
# --------------------------------------------------------------------------- #

def _flag(value: bool) -> str:
    return _YES if value else _NO


def _name(items: Mapping[str, Item], number: str) -> str:
    item = items.get(number)
    return item.name if item else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def _sheet(name: str) -> str:
    """Un nom de feuille cité, prêt à entrer dans une formule.

    Toujours entre apostrophes, y compris quand ce ne serait pas nécessaire :
    un code de zone peut porter un espace, et une règle uniforme ne laisse pas
    le cas particulier se glisser entre les mailles.
    """
    return "'" + name.replace("'", "''") + "'"


def _column(name: str, column: str, rows: int) -> str:
    """La plage d'une colonne de données, en-tête exclu.

    ``max(rows, 1)`` : une grille vide donnerait ``$D$2:$D$1``, une plage
    inversée que le tableur refuse — et l'export d'une campagne sans zone doit
    produire un fichier ouvrable, pas une erreur.
    """
    return f"{_sheet(name)}!${column}$2:${column}${1 + max(rows, 1)}"


def _range(name: str, first: str, last: str, rows: int) -> str:
    return f"{_sheet(name)}!${first}$2:${last}${1 + max(rows, 1)}"


def _unique_sheet_name(name: str, taken: set[str]) -> str:
    """31 caractères, aucun des caractères interdits, et jamais deux fois le même.

    Deux zones dont les codes ne diffèrent qu'au-delà du trente-et-unième
    caractère existent ; le classeur refuserait de se construire, ou pire,
    écraserait la première.
    """
    cleaned = "".join("-" if ch in "[]:*?/\\'" else ch for ch in name).strip()
    base = cleaned[:31] or "Zone"
    candidate, suffix = base, 2
    while candidate.casefold() in taken:
        tail = f"~{suffix}"
        candidate = base[: 31 - len(tail)] + tail
        suffix += 1
    taken.add(candidate.casefold())
    return candidate
