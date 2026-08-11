"""Column contracts for every importable grid.

The specification asks that, wherever a file can be uploaded, the user sees
*first* an empty, filterable grid whose header states exactly which columns are
expected and in which order. That grid and the parser must agree — so both are
generated from the single declaration below, and the frontend fetches it from
``GET /api/contracts``.

Each field declares:

* the canonical column name (what the header shows and what a file should use);
* accepted aliases, so an ERP export or an older workbook loads unchanged;
* the type, which drives coercion and the client-side input mask;
* whether it is required, and its default.

Adding a column is a one-line change here, and the grid, the parser, the
validation messages and the downloadable template all follow.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = ["FieldSpec", "GridContract", "CONTRACTS", "get_contract", "list_contracts"]

FieldType = Literal["string", "number", "integer", "date", "datetime", "boolean", "enum"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One column of an importable grid."""

    name: str
    label: str
    type: FieldType = "string"
    required: bool = False
    #: Alternative header spellings accepted on import (case/accent-insensitive).
    aliases: tuple[str, ...] = ()
    #: Allowed values when ``type == "enum"``.
    choices: tuple[str, ...] = ()
    default: Any = None
    help: str = ""
    #: Column width hint for the frontend grid, in pixels.
    width: int = 160

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "aliases": list(self.aliases),
            "choices": list(self.choices),
            "default": self.default,
            "help": self.help,
            "width": self.width,
        }


#: How the ERP spells "in force" in a ``statut`` column. Declared here, with the
#: contracts, because it is what the column *means* — the mappers and the
#: duplicate check both read it, and two definitions would drift.
ACTIVE_STATUSES = frozenset(
    {"actif", "active", "1", "true", "vrai", "o", "oui", "y", "yes"}
)


def is_active_status(value: Any) -> bool:
    """Whether a ``statut`` cell marks the row as in force.

    An empty cell counts as in force: a source that predates the column is a
    source of live recipes, and treating its rows as retired would silently
    empty every bill of materials.
    """
    if value is None or value == "":
        return True
    return str(value).strip().lower() in ACTIVE_STATUSES


@dataclass(frozen=True, slots=True)
class GridContract:
    """The full contract of one importable/editable grid."""

    key: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]
    #: Columns forming the natural key; duplicates on these are reported.
    natural_key: tuple[str, ...] = ()
    #: Rows the duplicate check applies to. ``None`` means all of them.
    #:
    #: A grid where several rows legitimately share a key needs the check
    #: narrowed rather than switched off: the bill of materials holds every
    #: version of a recipe, so the same pair appears once per retired version —
    #: normal — but twice in force is an anomaly worth naming. Reporting both
    #: buried the second under fifty of the first.
    duplicate_scope: Callable[[Mapping[str, Any]], bool] | None = None
    #: Short usage note rendered above the grid.
    hint: str = ""
    examples: tuple[dict[str, Any], ...] = field(default=())

    @property
    def headers(self) -> list[str]:
        return [f.name for f in self.fields]

    def field_by_name(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "hint": self.hint,
            "naturalKey": list(self.natural_key),
            "fields": [f.as_dict() for f in self.fields],
            "examples": [dict(e) for e in self.examples],
        }


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #

_ITEM_TYPES = ("COMPONENT", "SEMI_FINISHED", "FINISHED", "PACKAGING", "UNKNOWN")
_EXCLUSIONS = ("", "GENERIC", "BOM", "ALL")
_SECTIONS = ("LINE_SIDE", "WIP", "WIP_OK")

ITEMS = GridContract(
    key="items",
    title="Référentiel articles",
    description=(
        "Articles de la campagne : type, programme, catégorie, unité, prix standard "
        "et périmètre d'exclusion."
    ),
    hint=(
        "L'exclusion accepte GENERIC (hors GENERIQUE), BOM (ignoré dans les "
        "nomenclatures), ALL (hors périmètre complet), ou plusieurs valeurs séparées "
        "par une virgule."
    ),
    natural_key=("item_number",),
    fields=(
        FieldSpec("item_number", "Numéro d'article", required=True,
                  aliases=("numero d'article", "itemnumber", "reference", "ref",
                           "item", "article"), width=170),
        FieldSpec("name", "Nom du produit",
                  aliases=("nom du produit", "designation", "description",
                           "productname"), width=280),
        FieldSpec("search_name", "Nom de recherche",
                  aliases=("nom de recherche", "searchname"), width=200),
        FieldSpec("item_group", "Groupe d'articles",
                  aliases=("groupe d'articles", "itemgroup"), width=140),
        FieldSpec("lifecycle_state", "État du cycle de vie",
                  aliases=("etat du cycle de vie des produits", "lifecycle"),
                  width=150),
        FieldSpec("item_type", "Type produit", type="enum", choices=_ITEM_TYPES,
                  default="UNKNOWN",
                  aliases=("type produit", "type", "producttype"), width=150),
        FieldSpec("category", "Catégorie",
                  aliases=("categorie", "famille", "sous-produit", "sous produit"),
                  help="MEL, STATOR, ROTOR, ONDULEUR, …", width=140),
        FieldSpec("program", "Programme",
                  aliases=("programme", "program", "pgm"),
                  help="M2BEV, M3, M4, M3GEN2, M2ERAD… Vide = article commun.",
                  width=140),
        FieldSpec("commonality", "Spécificité", type="enum",
                  choices=("SPECIFIC", "COMMON", "UNKNOWN"), default="UNKNOWN",
                  aliases=("specificite", "commonality"), width=130),
        FieldSpec("unit", "Unité", default="PCE",
                  aliases=("unite", "unite de stock", "unit"), width=90),
        FieldSpec("std_price", "Prix standard (€)", type="number", default=0,
                  aliases=("prix", "prix std", "prix std 24", "std_price",
                           "unit cost", "unit price"), width=140),
        FieldSpec("exclusions", "Exclusion", type="string", default="",
                  choices=_EXCLUSIONS, aliases=("exclusion", "a exclure", "exclure"),
                  width=140),
    ),
    examples=(
        {"item_number": "mass-00040922", "name": "STATOR M4",
         "item_type": "SEMI_FINISHED", "category": "STATOR", "program": "M4",
         "unit": "PCE", "std_price": 312.5, "exclusions": ""},
    ),
)

BOMS = GridContract(
    key="boms",
    title="Nomenclatures (BOM)",
    description="Liens parent → composant utilisés pour éclater le WIP.",
    hint=(
        "Une ligne par couple assemblage/composant. La quantité est celle consommée "
        "par UNE unité de l'assemblage."
    ),
    natural_key=("parent_item", "child_item"),
    # Only the versions in force are checked. The ERP keeps every version of a
    # recipe, so a retired pair appearing three times is three versions of the
    # same link, not three mistakes — and reporting them drowned the one case
    # that matters: the same link declared twice, both in force, where the
    # explosion would have to pick one quantity and could not.
    duplicate_scope=lambda row: is_active_status(row.get("statut")),
    fields=(
        FieldSpec("parent_item", "Assemblage (parent)", required=True,
                  aliases=("ref_mere", "parent", "article/ressource"), width=180),
        FieldSpec("parent_name", "Désignation assemblage",
                  aliases=("desc_mere", "description parent"), width=240),
        FieldSpec("child_item", "Composant (enfant)", required=True,
                  aliases=("ref_fille", "child", "composant"), width=180),
        FieldSpec("qty_per", "Quantité par assemblage", type="number", required=True,
                  aliases=("quantite", "qty", "qty / bom", "quantite par"), width=180),
        FieldSpec("unit", "Unité", default="PCE",
                  aliases=("unite", "unit"), width=90),
        # The ERP holds every version of a recipe. All of them are loaded, and
        # only the ones in force are exploded — hence a column rather than a
        # filter at import: an assembly whose only recipe is retired has a
        # structure, and the screens have to be able to say so.
        FieldSpec("statut", "Statut", default="Actif",
                  aliases=("status", "actif", "etat", "état"), width=100),
    ),
    examples=(
        {"parent_item": "mass-00040922", "parent_name": "STATOR M4",
         "child_item": "P-00003759", "qty_per": 4.86, "unit": "KG",
         "statut": "Actif"},
    ),
)

BOOK_STOCK = GridContract(
    key="book_stock",
    title="Stock ERP (snapshot ERP)",
    description=(
        "Photographie du stock ERP prise juste avant le comptage. Entrepôts et "
        "emplacements sont normalisés en majuscules à l'import."
    ),
    hint=(
        "Export standard « Stock physique par emplacement ». La colonne Site est "
        "ignorée : le périmètre ne couvre qu'un seul site."
    ),
    natural_key=("item_number", "warehouse_id", "location_id"),
    fields=(
        FieldSpec("item_number", "Numéro d'article", required=True,
                  aliases=("numero d'article", "itemnumber", "reference"), width=170),
        FieldSpec("warehouse_id", "Entrepôt", required=True,
                  aliases=("entrepot", "warehouseid", "warehouse"), width=120),
        # Not required: a warehouse that does not use WMS location management
        # legitimately carries stock at warehouse level, with no location. The
        # (warehouse, location) pair remains the key either way.
        FieldSpec("location_id", "Emplacement",
                  aliases=("emplacement", "warehouselocationid", "location"),
                  help="Vide pour un stock géré au niveau de l'entrepôt.",
                  width=150),
        FieldSpec("qty", "Stock physique", type="number", required=True, default=0,
                  aliases=("stock physique", "quantite", "qty", "physical"),
                  width=150),
        FieldSpec("unit", "Unité", default="PCE",
                  aliases=("unite de stock", "unite", "unit"), width=90),
        FieldSpec("unit_cost", "Coût unitaire (€)", type="number", default=0,
                  aliases=("prix std 24", "cout unitaire", "prix", "unit cost"),
                  help="Si absent, le prix standard du référentiel articles est utilisé.",
                  width=150),
    ),
)

COUNT_JOURNAL_LINES = GridContract(
    key="count_journal_lines",
    title="Lignes de journaux de comptage (ERP)",
    description=(
        "Export OData des lignes de journaux INVE/INVV. Chaque rechargement "
        "remplace les valeurs importées sans effacer les corrections manuelles."
    ),
    hint=(
        "Structure identique à l'export OData standard. Un journal absent du "
        "référentiel des emplacements est créé automatiquement, sauf si "
        "l'emplacement est désactivé."
    ),
    natural_key=("journal_number", "item_number", "warehouse_id", "location_id"),
    fields=(
        FieldSpec("journal_number", "N° de journal",
                  aliases=("journalnumber", "numero", "journal"), width=150),
        FieldSpec("counting_date", "Date de comptage", type="datetime",
                  aliases=("countingdate", "date de comptage"), width=170),
        FieldSpec("item_number", "Numéro d'article", required=True,
                  aliases=("itemnumber", "numero d'article", "reference"), width=170),
        # Not required: some warehouses legitimately have a blank location, and
        # an ERP export occasionally drops the value on a single row. Both cases
        # are handled by the mapper, which infers from the journal number and
        # reports the correction instead of failing the whole import.
        FieldSpec("location_id", "Emplacement",
                  aliases=("warehouselocationid", "emplacement", "location"),
                  width=150),
        FieldSpec("warehouse_id", "Entrepôt", required=True,
                  aliases=("warehouseid", "entrepot", "warehouse"), width=120),
        FieldSpec("is_posted", "Posté", type="boolean", default=False,
                  aliases=("isposted", "poste", "posted"), width=100),
        FieldSpec("description", "Description",
                  aliases=("description",), width=220),
        FieldSpec("journal_name_id", "Type de journal", type="enum",
                  choices=("INVE", "INVV"), default="INVV",
                  aliases=("journalnameid", "type de journal"), width=140),
        FieldSpec("posted_date_time", "Date de postage", type="datetime",
                  aliases=("posteddatetime",), width=170),
        FieldSpec("counted_quantity", "Quantité comptée", type="number", required=True,
                  aliases=("countedquantity", "quantite comptee", "quantite"),
                  width=160),
        FieldSpec("unit", "Unité", default="PCE", aliases=("unite", "unit"), width=90),
    ),
)

COUNT_SHEETS = GridContract(
    key="count_sheets",
    title="Feuilles de comptage préparées",
    description=(
        "Contenu à pré-imprimer sur les feuilles GENERIQUE : une ligne par "
        "couple feuille / article, avec la section qui décide de la règle de "
        "consolidation."
    ),
    hint=(
        "Une feuille inconnue est créée avec ses passages ; une feuille connue "
        "est complétée, jamais recréée. Un article absent du référentiel est "
        "une erreur de ligne : l'import de feuilles n'étend jamais le "
        "référentiel. Sections : LINE_SIDE = bord de ligne (compté tel quel) · "
        "WIP = assemblage non déclaré (éclaté en nomenclature) · WIP_OK = "
        "assemblage déclaré (compté tel quel). Les anciens libellés « BDL », "
        "« MOM waiting » et « MOM OK » sont acceptés."
    ),
    # A same article legitimately appears twice on one sheet in two different
    # sections (line side *and* WIP): it is the trio that must be unique, not
    # the article.
    natural_key=("sheet_code", "item_number", "section"),
    fields=(
        FieldSpec("sheet_code", "Feuille", required=True,
                  aliases=("feuille", "zone", "code feuille", "code zone",
                           "sheet"), width=200),
        FieldSpec("item_number", "Numéro d'article", required=True,
                  aliases=("article", "reference", "ref", "item",
                           "numero d'article"), width=170),
        FieldSpec("section", "Section", type="enum", choices=_SECTIONS,
                  default="LINE_SIDE",
                  aliases=("source", "statut", "statut mom", "type"),
                  help="Vide = bord de ligne.", width=150),
        FieldSpec("unit", "Unité de comptage", default="PCE",
                  aliases=("unite de comptage", "unite", "unit"), width=140),
    ),
    examples=(
        {"sheet_code": "FI ASSY M3.1", "item_number": "P-00324093",
         "section": "LINE_SIDE", "unit": "PCE"},
        {"sheet_code": "FI ASSY M3.1", "item_number": "P-00324093",
         "section": "WIP", "unit": "PCE"},
    ),
)

ADJUSTMENTS = GridContract(
    key="adjustments",
    title="Mouvements & ajustements de stock",
    description=(
        "Mouvements générés par les journaux de comptage et journaux d'ajustement "
        "postés après analyse."
    ),
    hint=(
        "Quantité et valeur sont signées : négatif = diminution de stock. "
        "Format de l'export « Transactions de stock » de l'ERP."
    ),
    natural_key=("journal_number", "item_number", "warehouse_id", "location_id",
                 "physical_date"),
    fields=(
        FieldSpec("item_number", "Numéro d'article", required=True,
                  aliases=("numero d'article", "itemnumber", "reference"), width=170),
        FieldSpec("physical_date", "Date physique", type="date",
                  aliases=("date physique", "physicaldate"), width=140),
        FieldSpec("kind", "Nature", type="enum",
                  choices=("COUNT", "ADJUSTMENT", "RECOUNT", "OTHER"),
                  default="ADJUSTMENT",
                  aliases=("reference", "nature", "type"),
                  help="« Comptage » et « Ajustement de stock » sont reconnus.",
                  width=150),
        FieldSpec("journal_number", "N° de journal",
                  aliases=("numero", "journalnumber"), width=150),
        FieldSpec("qty", "Quantité", type="number", required=True, default=0,
                  aliases=("quantite", "qty"), width=130),
        FieldSpec("unit", "Unité", default="PCE", aliases=("unite", "unit"), width=90),
        FieldSpec("value", "Coût (€)", type="number", default=0,
                  aliases=("cout", "coût", "valeur", "cost"), width=130),
        FieldSpec("warehouse_id", "Entrepôt",
                  aliases=("entrepot", "warehouse"), width=120),
        FieldSpec("location_id", "Emplacement",
                  aliases=("emplacement", "location"), width=150),
        FieldSpec("reason_code", "Code motif",
                  aliases=("motif", "reason"), width=140),
        FieldSpec("comment", "Commentaire", aliases=("commentaire",), width=240),
    ),
)

ZONES = GridContract(
    key="zones",
    title="Zones de l'emplacement GENERIQUE",
    description=(
        "Zones physiques regroupées sous l'emplacement logique unique GENERIQUE."
    ),
    hint="Une feuille de comptage n°1 et n°2 est créée automatiquement par zone.",
    natural_key=("code",),
    fields=(
        FieldSpec("code", "Code zone", required=True,
                  aliases=("zone", "feuille", "code"), width=200),
        FieldSpec("label", "Libellé", aliases=("libelle", "designation"), width=280),
        FieldSpec("sector", "Secteur",
                  aliases=("secteur", "responsable"), width=180),
        FieldSpec("display_order", "Ordre", type="integer", default=0,
                  aliases=("ordre",), width=90),
    ),
)

LOCATIONS = GridContract(
    key="locations",
    title="Référentiel entrepôts / emplacements",
    description=(
        "Construit automatiquement à partir du stock ERP, complétable à la main. "
        "Un emplacement désactivé sort totalement du périmètre."
    ),
    hint=(
        "La combinaison entrepôt + emplacement est la clé : deux entrepôts "
        "différents peuvent porter le même nom d'emplacement."
    ),
    natural_key=("warehouse_id", "location_id"),
    fields=(
        FieldSpec("warehouse_id", "Entrepôt", required=True,
                  aliases=("entrepot", "warehouse"), width=130),
        FieldSpec("location_id", "Emplacement", required=True,
                  aliases=("emplacement", "location"), width=170),
        FieldSpec("zone", "Zone logistique",
                  aliases=("zone", "zone erp"), width=200),
        FieldSpec("type", "Type", type="enum", choices=("LABEL", "BULK", "UNKNOWN"),
                  default="UNKNOWN",
                  aliases=("type emplacement", "type inventaire"),
                  help="LABEL = étiquettes (scan, INVE) · BULK = vrac (INVV).",
                  width=130),
        FieldSpec("status", "Statut", type="enum", choices=("ACTIVE", "DISABLED"),
                  default="ACTIVE", aliases=("statut",), width=120),
    ),
)


CONTRACTS: dict[str, GridContract] = {
    c.key: c
    for c in (
        ITEMS,
        BOMS,
        BOOK_STOCK,
        COUNT_JOURNAL_LINES,
        COUNT_SHEETS,
        ADJUSTMENTS,
        ZONES,
        LOCATIONS,
    )
}


def get_contract(key: str) -> GridContract:
    """Look up a contract, raising a clear error for an unknown key."""
    try:
        return CONTRACTS[key]
    except KeyError:
        raise KeyError(
            f"unknown grid contract {key!r}; expected one of {sorted(CONTRACTS)}"
        ) from None


def list_contracts() -> list[dict[str, Any]]:
    """Every contract, ready to be served to the frontend."""
    return [c.as_dict() for c in CONTRACTS.values()]
