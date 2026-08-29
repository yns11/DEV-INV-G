"""Controlled vocabularies of the inventory domain.

Every enum value is a stable string: it is what gets persisted in Lakebase,
published to Delta and rendered in the UI. Renaming a value is a migration,
never a refactor.

Vocabulary note — the legacy Excel tool used the MOM (Manufacturing Operations
Management) wording ``MOM waiting`` / ``Eclaté``. The specification requires
those to be surfaced as **WIP** (work in progress). The enum members therefore
carry the new business wording; :func:`legacy_section_alias` maps the historical
labels found in old files onto them so that archived workbooks stay importable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

__all__ = [
    "CampaignStatus",
    "ItemType",
    "ItemCommonality",
    "ExclusionScope",
    "LocationType",
    "LocationStatus",
    "JournalKind",
    "JournalStatus",
    "SheetPass",
    "ZoneStatus",
    "CountSection",
    "DataSource",
    "AdjustmentKind",
    "CountingStage",
    "DriftResolution",
    "LabelResolution",
    "FlowKind",
    "FlowSource",
    "StockBasis",
    "ControlSeverity",
    "AuditAction",
    "legacy_section_alias",
]


class CampaignStatus(StrEnum):
    """Lifecycle of a campaign — PREPARATION → COUNTING → ANALYSIS → CLOSED."""

    PREPARATION = "PREPARATION"
    COUNTING = "COUNTING"
    ANALYSIS = "ANALYSIS"
    CLOSED = "CLOSED"


class ItemType(StrEnum):
    """Nature of an article. Thresholds are configured per type."""

    COMPONENT = "COMPONENT"          # composant / BOP (bought-out part)
    SEMI_FINISHED = "SEMI_FINISHED"  # semi-fini
    FINISHED = "FINISHED"            # produit fini
    PACKAGING = "PACKAGING"          # emballage / conditionnement
    UNKNOWN = "UNKNOWN"              # present in a count but absent from the referential


class ItemCommonality(StrEnum):
    """Whether an article is dedicated to one programme or shared."""

    SPECIFIC = "SPECIFIC"
    COMMON = "COMMON"
    UNKNOWN = "UNKNOWN"


class ExclusionScope(StrEnum):
    """Three-level exclusion required by the specification.

    ``NONE``     the article participates everywhere;
    ``GENERIC``  excluded from the GENERIQUE consolidation and its analysis only;
    ``BOM``      ignored when exploding a parent's bill of materials;
    ``ALL``      excluded from every count and every analysis.

    ``GENERIC`` and ``BOM`` are independent facets, so they are stored as a set
    of scopes rather than a single value (see :class:`~inventory.domain.models.Item`).
    """

    NONE = "NONE"
    GENERIC = "GENERIC"
    BOM = "BOM"
    ALL = "ALL"

    @classmethod
    def normalise(cls, values: Any) -> set[ExclusionScope]:
        """The set an article really carries, from whatever was asked for.

        ``NONE`` is the absence of an exclusion rather than a fourth one, so it
        drops out; and ``ALL`` already means both facets, so it replaces them
        instead of sitting beside them. Without that last rule the same
        intention could be stored three ways — ``{ALL}``, ``{ALL, GENERIC}``,
        ``{ALL, GENERIC, BOM}`` — and a screen listing the raw set would say
        three different things about three identical articles.
        """
        if values is None:
            return set()
        if isinstance(values, (str, ExclusionScope)):
            values = [values]
        out = {cls(str(v).upper()) for v in values}
        out.discard(cls.NONE)
        return {cls.ALL} if cls.ALL in out else out


class LocationType(StrEnum):
    """How a location is counted."""

    LABEL = "LABEL"   # entrepôt à étiquettes → journal INVE généré par scan
    BULK = "BULK"     # entrepôt VRAC        → journal INVV saisi/consolidé
    UNKNOWN = "UNKNOWN"


class LocationStatus(StrEnum):
    """Active locations are in scope; disabled ones are fully ignored.

    A disabled location contributes neither quantity nor value to any KPI, and
    no journal is created for it.
    """

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class JournalKind(StrEnum):
    """ERP counting-journal name (``JournalNameId`` in the OData export)."""

    INVE = "INVE"  # inventaire par étiquette (scan)
    INVV = "INVV"  # inventaire vrac (saisie / consolidation)


class JournalStatus(StrEnum):
    """Progress of one counting journal (one per active warehouse+location).

    ``BOOK_ENFORCED`` covers locations inventoried separately *before* the book
    stock snapshot: the user forces the counted quantity to equal the book
    quantity, which closes the journal with a null variance by construction.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    POSTED = "POSTED"
    BOOK_ENFORCED = "BOOK_ENFORCED"


class SheetPass(StrEnum):
    """Which of the two independent counts a sheet materialises."""

    PASS_1 = "PASS_1"
    PASS_2 = "PASS_2"


class ZoneStatus(StrEnum):
    """Où en est une zone de l'emplacement GENERIQUE. Trois états, pas plus.

    Une feuille de comptage n'a **pas** d'état propre. Elle en a eu quatre —
    en attente, comptage en cours, encodage en cours, terminée — que quelqu'un
    devait faire avancer à la main, deux fois par zone, sans qu'aucune écriture
    n'en dépende : le papier partait au comptage que le bouton ait été cliqué ou
    non, et les quantités s'enregistraient dans tous les cas. Quatre clics par
    zone pour tenir à jour une donnée que personne ne lisait.

    Ce qui reste :

    * ``PENDING``     aucune quantité relevée dans la zone ;
    * ``IN_PROGRESS`` des quantités sont là, la zone n'est pas déclarée close ;
    * ``DONE``        un humain a déclaré la zone terminée.

    Les deux premiers se **déduisent** des quantités : ils ne peuvent donc pas
    mentir. Le troisième est la seule décision humaine du parcours, et elle est
    nécessaire — dérivé de « toutes les lignes comptées », il condamnerait une
    campagne entière pour une ligne qu'on ne peut légitimement pas compter.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class CountSection(StrEnum):
    """Section of a GENERIQUE counting sheet — drives the consolidation rule.

    * ``LINE_SIDE``  — "Composants en bord de ligne" (BDL): counted as-is.
    * ``WIP``        — legacy "Statut MOM: Waiting for decision / on progress":
      the assembly is *not* a valid stock unit yet, so it is exploded into its
      bill of materials.
    * ``WIP_OK``     — legacy "Statut MOM: OK": the assembly is declared in the
      ERP and counted as one assembled unit.
    """

    LINE_SIDE = "LINE_SIDE"
    WIP = "WIP"
    WIP_OK = "WIP_OK"


class DataSource(StrEnum):
    """Provenance of a quantity — always kept next to the value it produced."""

    ERP_IMPORT = "ERP_IMPORT"        # OData / Excel export of the counting journals
    FILE_IMPORT = "FILE_IMPORT"      # user-uploaded csv/xlsx matching a grid contract
    MANUAL = "MANUAL"                # typed or pasted in the app
    SCAN_AI = "SCAN_AI"              # extracted from a scanned sheet by the LLM
    CONSOLIDATION = "CONSOLIDATION"  # produced by the GENERIQUE consolidation engine
    ARBITRATION = "ARBITRATION"      # decided by a human during pass-1/pass-2 arbitration
    SYSTEM = "SYSTEM"                # derived by the application (e.g. book enforcement)


class AdjustmentKind(StrEnum):
    """Nature of a stock movement fed into the analysis phase."""

    COUNT = "COUNT"              # mouvement généré par un journal de comptage
    ADJUSTMENT = "ADJUSTMENT"    # journal d'ajustement saisi après analyse
    RECOUNT = "RECOUNT"          # recomptage post-inventaire
    OTHER = "OTHER"


class CountingStage(StrEnum):
    """Sous-phase du comptage, **déduite** du jalon de la campagne.

    Ce n'est pas un statut de campagne, et c'est délibéré : ``COUNTING`` porte
    déjà exactement les droits qu'un comptage avancé demande, si bien qu'un
    statut supplémentaire aurait traversé les transitions, la matrice de gel, le
    contrat côté navigateur et la table Delta pour recopier une ligne.
    """

    #: La campagne n'est pas en phase de comptage : la question ne se pose pas.
    NOT_COUNTING = "NOT_COUNTING"
    #: Avant l'ouverture du comptage général : on charge et scelle des lots.
    EARLY = "EARLY"
    #: Après : le stock ERP général est chargé, le reste se compte.
    GENERAL = "GENERAL"


class DriftResolution(StrEnum):
    """Ce qu'un exploitant décide d'une dérive matérielle.

    Une dérive est l'écart entre le stock ERP du jour J et le physique posté au
    précomptage, sur un emplacement scellé. Elle est attendue nulle ; quand elle
    ne l'est pas, **une seule question se pose** : quelle quantité fait foi au
    jour J ?

    Deux réponses, et pas quatre. « Rejouer le postage » n'en est pas une : un
    journal de précomptage se charge une fois posté et validé dans l'ERP, si
    bien que le réalignement est acquis en pratique plutôt que diagnostiqué
    après coup. « Ajuster » non plus : un mouvement réel se saisit par le mécanisme
    d'ajustement, qui a déjà son sens, sa table et sa place dans le calcul —
    en faire une issue de la dérive aurait dupliqué une fonction et forcé à
    choisir entre deux gestes qui ne s'excluent pas.
    """

    #: Le comptage avancé fait foi. Cause et commentaire obligatoires : la
    #: campagne et l'ERP restent alors en désaccord de la valeur de la dérive,
    #: et personne ne doit le découvrir plus tard.
    KEEP_EARLY = "KEEP_EARLY"
    #: L'emplacement est descellé et rejoint le comptage général ; sa référence
    #: redevient le stock ERP du jour J.
    RECOUNT = "RECOUNT"


class LabelResolution(StrEnum):
    """Où est la pièce, quand une étiquette scellée reparaît ailleurs.

    Le contrôle par étiquette est le seul du dispositif qui descende sous le
    grain « emplacement + article », et le seul qui rattrape une pièce sortie
    d'un emplacement scellé **sans aucune transaction ERP** : la dérive, elle,
    reste nulle dans ce cas, faute d'avoir quoi que ce soit à comparer.

    La question n'a pas de réponse calculable. Deux journaux affirment chacun
    détenir la même étiquette ; seul quelqu'un qui va voir peut trancher.
    """

    #: Elle est au nouvel emplacement : l'étiquette sort de l'agrégation de
    #: l'emplacement scellé, qui perd la quantité correspondante.
    KEEP_NEW = "KEEP_NEW"
    #: Elle n'a pas bougé : c'est la ligne de l'autre journal qui est l'erreur,
    #: et c'est elle qui sort.
    KEEP_SEALED = "KEEP_SEALED"
    #: On ne tranche pas sur pièce. Rien n'est exclu, et l'emplacement scellé
    #: rejoint la liste de ceux à desceller et rescanner.
    RECOUNT = "RECOUNT"


class FlowKind(StrEnum):
    """A quantity the user loads to explain the period between two campaigns.

    Only the three the application cannot derive on its own. Production and
    theoretical consumption are read from the backflush fact table instead —
    asking somebody to retype what a warehouse already knows is how the legacy
    process introduced most of its errors.

    The sign lives here, not in the quantity: a shipment is stored positive and
    subtracted by the calculation. Letting it be typed negative would mean an
    expedition entered the wrong way round is added to the stock, and nothing on
    screen would show it.
    """

    RECEIPT = "RECEIPT"      # réceptions : entrées en stock sur la période
    SHIPMENT = "SHIPMENT"    # expéditions : sorties vers le client
    SCRAP = "SCRAP"          # rebuts : étape facultative


class FlowSource(StrEnum):
    """Where one quantity of a comparison came from.

    Not decoration: a figure read from the ERP is rejouable by re-running its
    query, a figure typed into the grid is somebody's judgement, and a
    contested number is defended differently in each case. Without this, the
    two are indistinguishable the moment the screen is refreshed.
    """

    ERP = "ERP"
    FILE = "FILE"
    MANUAL = "MANUAL"


class StockBasis(StrEnum):
    """Which reading of a campaign's stock a comparison brackets itself with.

    Two readings coexist in every campaign and neither is the other's draft:
    the ERP said one thing at the freeze, the shelf said another. Comparing two
    campaigns therefore has four legitimate forms, and which one is wanted
    depends on the question — physique/physique measures what the plant lost,
    ERP/ERP measures what the system thinks it lost, and the two crossed pairs
    isolate where a divergence was born.

    ``PHYSICAL`` is the counted stock plus the adjustments posted after it, the
    same definition the inventory variance uses. Anything else would make one
    screen's « physique » mean something different from another's.
    """

    PHYSICAL = "PHYSICAL"
    BOOK = "BOOK"


class ControlSeverity(StrEnum):
    """Severity of a control finding. ``BLOCKER`` prevents phase transitions."""

    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    INFO = "INFO"


class AuditAction(StrEnum):
    """Verbs recorded in the immutable audit trail."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    STATUS_CHANGE = "STATUS_CHANGE"
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    FREEZE = "FREEZE"
    CONSOLIDATE = "CONSOLIDATE"
    ARBITRATE = "ARBITRATE"
    LOGIN = "LOGIN"


# --------------------------------------------------------------------------- #
# Legacy vocabulary bridge
# --------------------------------------------------------------------------- #

#: Historical section labels found in ``Compil GENERIQUE.xlsx`` and its Power
#: Query steps, mapped onto the current vocabulary. Keys are compared after
#: upper-casing and squeezing whitespace.
_LEGACY_SECTIONS: dict[str, CountSection] = {
    "BDL": CountSection.LINE_SIDE,
    "COMPOSANTS EN BORD DE LIGNE": CountSection.LINE_SIDE,
    "BORD DE LIGNE": CountSection.LINE_SIDE,
    "LINE_SIDE": CountSection.LINE_SIDE,
    "MOM_WAITING": CountSection.WIP,
    "MOM WAITING": CountSection.WIP,
    "STATUT MOM: WAITING FOR DECISION / ON PROGRESS": CountSection.WIP,
    "ECLATE": CountSection.WIP,
    "ECLATEE": CountSection.WIP,
    "WIP": CountSection.WIP,
    "MOM_OK": CountSection.WIP_OK,
    "MOM OK": CountSection.WIP_OK,
    "STATUT MOM: OK": CountSection.WIP_OK,
    "WIP_OK": CountSection.WIP_OK,
    # Ce que l'application affiche et exporte. Un tableau relu depuis Excel
    # porte le libellé, pas le code : sans ces entrées, un export réimporté
    # échouait sur « section inconnue » — sur des lignes que l'outil venait
    # lui-même d'écrire. Les deux orthographes, accentuée ou non, parce que la
    # comparaison ne dépouille pas les accents.
    "WIP (À ÉCLATER)": CountSection.WIP,
    "WIP (A ECLATER)": CountSection.WIP,
    "WIP ASSEMBLÉ": CountSection.WIP_OK,
    "WIP ASSEMBLE": CountSection.WIP_OK,
}


def legacy_section_alias(label: str | None) -> CountSection | None:
    """Resolve a historical section label onto a :class:`CountSection`.

    Returns ``None`` when the label is unknown, so callers can raise a precise
    validation error instead of silently mis-classifying a counted quantity —
    the exact failure mode that made the Excel tool untrustworthy.

    >>> legacy_section_alias("MOM waiting")
    <CountSection.WIP: 'WIP'>
    >>> legacy_section_alias("bdl")
    <CountSection.LINE_SIDE: 'LINE_SIDE'>
    >>> legacy_section_alias("???") is None
    True
    """
    if not label:
        return None
    key = " ".join(label.strip().upper().split())
    return _LEGACY_SECTIONS.get(key)
