"""Relire une campagne à la forme d'une grille.

Une campagne porte déjà tout ce que ses grilles savent charger : son
référentiel, ses nomenclatures, son stock ERP, ses emplacements, ses feuilles,
ses journaux, ses ajustements. Les redonner **dans la forme du contrat** de la
grille — les mêmes colonnes, dans le même ordre — les rend relisables par la
même chaîne que tout le reste.

C'est ce qui permet à deux fonctions très différentes de partager une seule
définition :

* **l'export Excel** d'une grille écrit ces lignes dans un classeur ;
* **l'import « depuis une autre campagne »** les repasse par le parseur, avec
  la même validation, le même essai à blanc, les mêmes mappeurs, le même audit
  et la même grille modifiable ensuite.

Le second est un besoin réel et récurrent : le référentiel articles d'un
trimestre est celui du suivant à quelques lignes près, un stock ERP se rejoue
d'une campagne de contrôle à l'autre, et les journaux de comptage avancés d'une
campagne annulée n'ont aucune raison d'être ressaisis. La duplication de
campagne couvre le cas où l'on repart de zéro ; celui-ci couvre le cas — bien
plus fréquent — où la campagne existe déjà et où il ne manque qu'une grille.

**Ce n'est pas une porte dérobée.** Les lignes rentrent par le même point que
le fichier et le collage : un article absent du référentiel reste une erreur de
ligne, un article exclu du périmètre reste refusé, et un chargement qui
remplace refuse toujours d'écrire un ensemble amputé.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..domain.enums import CountLineKind, SheetPass
from ..domain.models import Campaign
from ..ingest import get_contract

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .context import ServiceContext

__all__ = ["SUPPORTED", "candidates", "grid_rows", "grid_dicts"]

#: Les grilles qu'une campagne sait redonner.
#:
#: Une grille absente d'ici ne propose pas l'import depuis une autre campagne —
#: et ``tests/test_import_campagne.py`` exige que la liste soit **la même** que
#: celle de l'export Excel. Les deux répondent à la même question : « que
#: sait-on ressortir de cette campagne ? ». Les laisser diverger produirait une
#: grille exportable qu'on ne saurait pas relire, ou l'inverse.
SUPPORTED: frozenset[str] = frozenset({
    "items",
    "boms",
    "book_stock",
    "locations",
    "zones",
    "count_sheets",
    "count_journal_lines",
    "adjustments",
})


def grid_dicts(
    ctx: ServiceContext, campaign: Campaign, key: str
) -> list[dict[str, Any]]:
    """Les mêmes lignes, mais nommées par les clés du contrat.

    Le parseur travaille sur des dictionnaires ; l'export sur des listes en
    ordre de colonnes. Une seule définition produit la liste, et c'est le
    contrat — la même source que les en-têtes du classeur — qui la nomme.
    """
    fields = [f.name for f in get_contract(key).fields]
    return [dict(zip(fields, row, strict=False)) for row in grid_rows(ctx, campaign, key)]


def grid_rows(
    ctx: ServiceContext, campaign: Campaign, key: str
) -> list[list[Any]]:
    """Une grille de cette campagne, colonne par colonne, dans l'ordre du contrat."""
    match key:
        case "items":
            return [
                [i.item_number, i.name, i.search_name, i.item_group,
                 i.lifecycle_state, str(i.item_type), i.category, i.program,
                 str(i.commonality), i.unit, float(i.std_price),
                 ",".join(sorted(str(e) for e in i.exclusions))]
                for i in ctx.referentials.list_items(campaign.id)
            ]
        case "boms":
            items = ctx.referentials.items_by_number(campaign.id)
            return [
                [l.parent_item,
                 items[l.parent_item].name if l.parent_item in items else "",
                 l.child_item, float(l.qty_per), l.unit]
                for l in ctx.referentials.list_bom_links(campaign.id)
            ]
        case "book_stock":
            return [
                [b.item_number, b.warehouse_id, b.location_id, float(b.qty),
                 b.unit, float(b.unit_cost)]
                for b in ctx.book_stock.list(campaign.id)
            ]
        case "locations":
            return [
                [l.warehouse_id, l.location_id, l.zone, str(l.type), str(l.status)]
                for l in ctx.referentials.list_locations(campaign.id)
            ]
        case "zones":
            return [
                [z.code, z.label, z.sector, z.display_order]
                for z in ctx.sheets.list_zones(campaign.id)
            ]
        case "count_sheets":
            # Pass 1 only: both passes carry the same article list by
            # construction, and exporting it twice would re-import as duplicates.
            zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
            lines_by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
            # Les intertitres et les lignes vides ne sortent pas : le fichier
            # porte la sous-section en colonne, et l'import repose les
            # séparateurs à partir d'elle. Les exporter en plus les
            # dédoublerait au rechargement.
            return [
                [zones[sheet.zone_id].code, line.item_number, str(line.section),
                 line.subsection, line.unit]
                for sheet in ctx.sheets.list_sheets(campaign.id)
                if sheet.pass_no is SheetPass.PASS_1 and sheet.zone_id in zones
                for line in lines_by_sheet.get(sheet.id, ())
                if line.line_kind is CountLineKind.ARTICLE
            ]
        case "count_journal_lines":
            # Les lignes brutes, telles que l'ERP les a produites, journal par
            # journal. C'est **le** cas que l'exploitation réclame : un lot de
            # précomptage rejoué d'une campagne à l'autre, ou repris après une
            # campagne annulée, plutôt que réexporté de l'ERP et rechargé.
            journals = {j.id: j for j in ctx.erp_journals.list(campaign.id)}
            return [
                [journal.journal_number, line.erp_line_number,
                 journal.counting_date.isoformat() if journal.counting_date else "",
                 line.site_id, line.warehouse_id, line.location_id,
                 line.label_id, line.serial_number, line.item_number,
                 float(line.qty_on_hand), float(line.qty_counted), line.unit,
                 line.inventory_status_id, journal.erp_posted,
                 journal.erp_posted_at.isoformat() if journal.erp_posted_at else "",
                 journal.description, str(journal.kind)]
                for journal in journals.values()
                for line in ctx.erp_journals.lines(campaign.id, journal.id)
            ]
        case "adjustments":
            return [
                [a.item_number,
                 a.physical_date.isoformat() if a.physical_date else "",
                 str(a.kind), a.journal_number, float(a.qty), a.unit,
                 float(a.value), a.warehouse_id, a.location_id, a.reason_code,
                 a.comment]
                for a in ctx.adjustments.list(campaign.id)
            ]
        case _:
            return []




def candidates(
    ctx: ServiceContext, campaign: Campaign, grid_key: str
) -> list[dict[str, Any]]:
    """Les campagnes dont on pourrait reprendre cette grille, et ce qu'elles portent.

    **Le décompte est l'information qui fait choisir.** Sans lui, l'écran offre
    une liste de codes et de dates : on désigne une campagne au jugé, on
    découvre qu'elle ne porte rien sur cette grille, on recommence. Avec lui,
    « INV-2026-06 · 4 128 articles » se choisit d'un coup d'œil, et une campagne
    à zéro se voit sans être ouverte.

    La campagne courante ne s'y trouve pas : se reprendre soi-même ne veut rien
    dire, et un chargement qui remplace s'effacerait avant de se réécrire.
    """
    counts = ctx.campaigns.grid_counts(grid_key)
    return [
        {
            "id": other.id,
            "code": other.code,
            "label": other.label,
            "status": str(other.status),
            "countDate": other.count_date.isoformat(),
            "createdAt": other.created_at.isoformat() if other.created_at else None,
            "createdBy": other.created_by,
            "rows": counts.get(other.id, 0),
        }
        # Assez large pour couvrir l'historique d'un site : c'est une liste
        # qu'on parcourt une fois, pas une page qu'on feuillette.
        for other in ctx.campaigns.list(limit=500)
        if other.id != campaign.id
    ]
