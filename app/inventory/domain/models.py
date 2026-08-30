"""Domain entities.

These models are pure data + invariants: they never touch the database, the
warehouse or HTTP. That separation is what lets the whole business logic
(BOM explosion, GENERIQUE consolidation, variance, controls) be unit-tested
without any Databricks dependency — the property the Excel tool never had.

Naming follows the business vocabulary of the specification (campagne, journal,
feuille de comptage, zone, écart) with English identifiers, and the *legacy*
labels are only ever seen at the import boundary.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AdjustmentKind,
    CampaignStatus,
    ControlSeverity,
    CountingStage,
    CountSection,
    DataSource,
    DriftResolution,
    ExclusionScope,
    FlowKind,
    FlowSource,
    ItemCommonality,
    ItemType,
    JournalKind,
    JournalStatus,
    LabelResolution,
    LocationStatus,
    LocationType,
    SheetPass,
)
from .quantities import ZERO, quantize_money, quantize_qty, to_decimal

log = logging.getLogger(__name__)

__all__ = [
    "DomainModel",
    "Qty",
    "Money",
    "normalise_key",
    "LocationKey",
    "Thresholds",
    "CampaignConfig",
    "Campaign",
    "Item",
    "in_perimeter",
    "BomLink",
    "Warehouse",
    "Location",
    "Manager",
    "BookStockLine",
    "CountJournal",
    "CountJournalLine",
    "erp_journal_numbers",
    "ErpJournal",
    "ErpJournalLine",
    "LabelDecision",
    "EarlyCountDrift",
    "Zone",
    "CountSheet",
    "CountSheetLine",
    "ArbitrationLine",
    "ConsolidatedLine",
    "WipBreakdown",
    "AdjustmentLine",
    "BackflushLine",
    "StockFlowRun",
    "StockFlowInput",
    "StockFlowErp",
    "StockFlowLine",
    "VarianceLine",
    "ControlFinding",
    "FindingGroup",
    "AuditEvent",
    "AssignableCause",
    "VarianceAnalysis",
]


# --------------------------------------------------------------------------- #
# Base types
# --------------------------------------------------------------------------- #

class DomainModel(BaseModel):
    """Common configuration for every domain entity."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        frozen=False,
        populate_by_name=True,
        # Un champ à valeur par défaut est *toujours* présent dans un
        # `model_dump`. Sans ce drapeau, le schéma de réponse le déclare
        # facultatif, et le client généré doit le tester à chaque lecture
        # alors qu'il ne manque jamais.
        json_schema_serialization_defaults_required=True,
    )


def _as_qty(value: Any) -> Decimal:
    return quantize_qty(to_decimal(value))


def _as_money(value: Any) -> Decimal:
    return quantize_money(to_decimal(value))


#: A quantity, stored with 6 decimals. Fields declare the type and rely on the
#: ``_as_qty`` validators above for normalisation.
Qty = Annotated[Decimal, "quantity, 6 decimals"]
#: A monetary amount in the campaign currency, stored with 2 decimals.
Money = Annotated[Decimal, "monetary amount, 2 decimals"]


_WHITESPACE_RE = re.compile(r"\s+")


def normalise_key(value: str | None) -> str:
    """Canonical form of a business key (warehouse, location, item number).

    Upper-cases, collapses internal whitespace and trims. The specification
    explicitly requires warehouses and locations to be upper-cased when the book
    stock is frozen; applying the same rule to *every* key removes the whole
    family of "``PAL B2S 01``" vs "``pal b2s  01``" duplicates that produced
    phantom variances in the legacy files.

    >>> normalise_key("  pal b2s   01 ")
    'PAL B2S 01'
    >>> normalise_key(None)
    ''
    """
    if value is None:
        return ""
    return _WHITESPACE_RE.sub(" ", value.strip()).upper()


class LocationKey(DomainModel):
    """Composite identity of a stock location.

    Two locations in different warehouses may share a name, so the *pair* is the
    key — never a concatenated string, per the modelling rules of the spec.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    warehouse_id: str
    location_id: str

    @field_validator("warehouse_id", "location_id", mode="before")
    @classmethod
    def _normalise(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.warehouse_id} / {self.location_id}"

    @property
    def is_blank(self) -> bool:
        return not self.warehouse_id and not self.location_id


# --------------------------------------------------------------------------- #
# Campaign
# --------------------------------------------------------------------------- #

class Thresholds(DomainModel):
    """Materiality thresholds for one :class:`~inventory.domain.enums.ItemType`.

    A variance is *material* when it breaches **all** the configured gates that
    apply (value AND relative quantity), which keeps the exception list short
    and actionable. ``None`` disables a gate.
    """

    item_type: ItemType
    #: Absolute variance value in EUR above which the line is an exception.
    value_abs_eur: Decimal = Field(default=Decimal("1000"))
    #: |Δqty| / book_qty above which the line is an exception (0.05 = 5 %).
    qty_relative: Decimal | None = Field(default=Decimal("0.02"))

    @field_validator("value_abs_eur", mode="before")
    @classmethod
    def _money(cls, v: Any) -> Decimal:
        return _as_money(v)

    @field_validator("qty_relative", mode="before")
    @classmethod
    def _ratio(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        d = to_decimal(v)
        if d < 0:
            raise ValueError("a tolerance ratio cannot be negative")
        return d


class CampaignConfig(DomainModel):
    """Frozen configuration of a campaign (part of the immutable dossier)."""

    generic_warehouse: str = "B06VRAC"
    generic_location: str = "GENERIQUE"
    #: Nombre de comptages indépendants exigés sur les zones GENERIQUE.
    #:
    #: **Deux au maximum, et pas trois.** Ce champ acceptait 3, mais rien
    #: derrière ne savait en faire quoi que ce soit : :class:`SheetPass` ne
    #: connaît que ``PASS_1`` et ``PASS_2``, une zone est bornée à 2, et
    #: :func:`passes_for` ramenait silencieusement à 2. Une campagne configurée
    #: à 3 affichait donc « 3 comptages » sur son écran de configuration et
    #: créait deux feuilles — un troisième comptage que tout le monde croyait
    #: exigé, que personne ne pouvait faire, et dont l'absence ne se signalait
    #: nulle part.
    generic_passes: int = Field(default=2, ge=1, le=2)

    @field_validator("generic_passes", mode="before")
    @classmethod
    def _at_most_two_counts(cls, value: Any) -> Any:
        """Ramène une valeur enregistrée à 3 sans faire échouer la campagne.

        Le champ a accepté 3 : des campagnes existantes peuvent le porter.
        Refuser de les charger reviendrait à les rendre inaccessibles pour une
        valeur qui n'a jamais rien produit de différent — 2 est ce que la base
        contient réellement, en feuilles comme en arbitrages.
        """
        if isinstance(value, int) and value > 2:
            log.warning(
                "Campagne configurée à %d comptages GENERIQUE : ramenée à 2, "
                "qui est ce que les feuilles et l'arbitrage savent porter.",
                value,
            )
            return 2
        return value
    #: Relative gap between pass 1 and pass 2 above which arbitration is
    #: mandatory rather than automatic (0 = any difference triggers arbitration).
    arbitration_tolerance: Decimal = Field(default=Decimal("0"))
    #: Maximum BOM explosion depth. Guards against pathological structures; a
    #: cycle is detected and reported regardless of this value.
    max_bom_depth: int = Field(default=10, ge=1, le=25)
    #: Currency of every monetary amount in the campaign.
    currency: str = "EUR"
    #: Les quantités peuvent-elles s'écrire comme des opérations ?
    #:
    #: Devant trois palettes de quarante-huit et un fond de bac de sept, un
    #: compteur écrit « 3*48+7 » — et c'est la bonne façon de compter : le
    #: calcul reste devant les yeux de qui relira, ce qu'un « 151 » nu ne permet
    #: plus. Activé, ces expressions sont évaluées à la saisie comme à la
    #: lecture d'un scan, et le texte d'origine est conservé à côté du résultat
    #: (:mod:`inventory.domain.formula`).
    #:
    #: Éteint par défaut, et le rester est un choix défendable : une usine qui
    #: veut que ses feuilles portent un nombre et un seul a raison de l'exiger.
    #: Ce qui ne l'était pas, c'est que le refus parlait d'une quantité
    #: illisible sans jamais dire qu'un réglage existait.
    allow_formulas: bool = False

    #: L'emplacement **tampon** de l'ERP, ``INV / 01``.
    #:
    #: Entièrement virtuel : aucun emplacement physique de l'usine ne lui
    #: correspond, et l'ERP n'y crée aucun journal de comptage. Il reçoit toute
    #: pièce qu'un comptage ne retrouve pas, et centralise ainsi les écarts du
    #: stock géré par lots.
    #:
    #: L'application le connaît pour trois raisons, et elles se tiennent : il ne
    #: doit jamais entrer dans le périmètre d'un journal ni dans un lot avancé,
    #: aucun contrôle de dérive ne s'y applique, et son emplacement se désactive
    #: après le chargement général. Cette désactivation n'est pas une commodité :
    #: une pièce introuvable produit un départ (ERP 1, compté 0) et une arrivée
    #: au tampon (ERP 0, compté 1) qui **se compensent exactement** à l'échelle
    #: de l'article. Le garder dans le périmètre effacerait donc la perte ; le
    #: retirer ne laisse que la ligne de départ, et la perte redevient un écart.
    buffer_warehouse: str = "INV"
    buffer_location: str = "01"

    @field_validator(
        "generic_warehouse", "generic_location",
        "buffer_warehouse", "buffer_location",
        mode="before",
    )
    @classmethod
    def _norm(cls, v: Any) -> str:
        return normalise_key(str(v))

    @property
    def generic_key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.generic_warehouse, location_id=self.generic_location
        )

    @property
    def buffer_key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.buffer_warehouse, location_id=self.buffer_location
        )


class Campaign(DomainModel):
    """An inventory campaign — the immutable dossier the whole app revolves around."""

    id: str
    code: str
    label: str
    count_date: dt.date
    status: CampaignStatus = CampaignStatus.PREPARATION
    config: CampaignConfig = Field(default_factory=CampaignConfig)
    thresholds: list[Thresholds] = Field(default_factory=list)

    #: Set when the referentials are frozen (entering COUNTING).
    referentials_frozen_at: dt.datetime | None = None
    #: Set when the book stock snapshot is taken and frozen.
    book_stock_frozen_at: dt.datetime | None = None
    #: Set when counting is closed (entering ANALYSIS).
    counting_frozen_at: dt.datetime | None = None
    closed_at: dt.datetime | None = None
    #: Fin de la dernière publication Delta réussie, posée par le job après
    #: son manifeste. ``None`` = jamais archivée, et la clôture le refuse : la
    #: base opérationnelle est vivante, l'archive est ce qui reste.
    published_at: dt.datetime | None = None
    #: Le jalon qui sépare les deux sous-phases du comptage : avant lui les lots
    #: avancés, après lui le comptage général.
    #:
    #: Un jalon et non un statut de campagne. ``COUNTING`` porte déjà exactement
    #: les droits qu'un comptage avancé demande — référentiels gelés, journaux et
    #: saisies ouverts — et un statut de plus traverserait les transitions, la
    #: matrice de gel, le contrat côté navigateur, la barre latérale et la table
    #: Delta pour aboutir à une ligne recopiée. La sous-phase se **déduit** donc,
    #: comme les deux premiers états d'une zone se déduisent de ses quantités.
    general_count_opened_at: dt.datetime | None = None
    #: Heure du dernier import de journaux ERP réussi. Le notebook est rejoué
    #: très régulièrement le jour J ; savoir de quand datent les chiffres qu'on
    #: regarde n'est pas un détail d'affichage, c'est ce qui dit s'il faut
    #: recharger avant de décider.
    journals_imported_at: dt.datetime | None = None

    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime | None = None
    #: Code of the campaign this one was duplicated from, if any.
    cloned_from_code: str | None = None
    #: Version of the calculation engine that produced the stored derived data.
    engine_version: str = "1.0.0"

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        code = normalise_key(str(v)).replace(" ", "-")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,49}", code):
            raise ValueError(
                "campaign code must be 3-50 chars of A-Z, 0-9, '.', '_' or '-'"
            )
        return code

    def threshold_for(self, item_type: ItemType) -> Thresholds:
        """Thresholds configured for *item_type*, or a permissive default."""
        for t in self.thresholds:
            if t.item_type is item_type:
                return t
        return Thresholds(item_type=item_type)

    @property
    def is_frozen(self) -> bool:
        return self.status is CampaignStatus.CLOSED

    @property
    def counting_stage(self) -> CountingStage:
        """Où en est le comptage : lots avancés, ou général.

        Dérivé du jalon, jamais stocké deux fois. Hors de la phase de comptage
        la question ne se pose pas, et la réponse le dit.
        """
        if self.status is not CampaignStatus.COUNTING:
            return CountingStage.NOT_COUNTING
        if self.general_count_opened_at is None:
            return CountingStage.EARLY
        return CountingStage.GENERAL


# --------------------------------------------------------------------------- #
# Referentials (snapshotted per campaign)
# --------------------------------------------------------------------------- #

class Item(DomainModel):
    """An article, as frozen for one campaign."""

    campaign_id: str
    item_number: str
    name: str = ""
    search_name: str = ""
    item_group: str = ""
    lifecycle_state: str = ""
    item_type: ItemType = ItemType.UNKNOWN
    #: Business family — MEL, STATOR, ONDULEUR, ROTOR, …
    category: str = ""
    #: Programme the article belongs to — M2BEV, M3, M4, M3GEN2, M2ERAD, …
    program: str = ""
    commonality: ItemCommonality = ItemCommonality.UNKNOWN
    unit: str = "PCE"
    #: Standard cost used to value quantities, in the campaign currency.
    std_price: Decimal = ZERO
    #: Facets of exclusion; empty set == fully in scope.
    exclusions: set[ExclusionScope] = Field(default_factory=set)
    source: DataSource = DataSource.FILE_IMPORT

    @field_validator("item_number", mode="before")
    @classmethod
    def _item_number(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("item_number is required")
        return key

    @field_validator("unit", "category", "program", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("std_price", mode="before")
    @classmethod
    def _price(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)

    @field_validator("exclusions", mode="before")
    @classmethod
    def _exclusions(cls, v: Any) -> set[ExclusionScope]:
        return ExclusionScope.normalise(v)

    # -- scope helpers --------------------------------------------------------
    @property
    def excluded_everywhere(self) -> bool:
        return ExclusionScope.ALL in self.exclusions

    @property
    def excluded_from_generic(self) -> bool:
        """Excluded from the GENERIQUE consolidation and its analysis."""
        return self.excluded_everywhere or ExclusionScope.GENERIC in self.exclusions

    @property
    def excluded_from_bom(self) -> bool:
        """Ignored when exploding a parent's bill of materials."""
        return self.excluded_everywhere or ExclusionScope.BOM in self.exclusions

    @property
    def is_assembly(self) -> bool:
        return self.item_type in (ItemType.SEMI_FINISHED, ItemType.FINISHED)

def in_perimeter(items: Mapping[str, Item]) -> dict[str, Item]:
    """The articles the campaign actually inventories.

    The rule of every ERP read, in one place. Those tables cover the whole
    plant, so being in the campaign's referential is necessary but not enough:
    an article deliberately left out of the perimeter must not come back
    through the quantities read on it, or its expected stock would be computed
    and shown as a variance nobody asked for.
    """
    return {number: item for number, item in items.items() if not item.excluded_everywhere}


class BomLink(DomainModel):
    """One parent → child edge of the bill of materials, frozen per campaign."""

    campaign_id: str
    parent_item: str
    child_item: str
    #: Quantity of *child* consumed by one *parent*.
    qty_per: Decimal
    unit: str = "PCE"
    #: 0 for the top level; kept to reproduce the ERP's effective BOM view.
    level: int = 1
    #: Whether this version of the recipe is the one in force.
    #:
    #: The ERP keeps every version of a bill of materials, active or not, and
    #: the campaign now loads them all — an assembly whose only recipe is
    #: retired *has* a structure, and reporting it as having none produced a
    #: page of alerts nobody could act on. Only the active versions are exploded
    #: though: adding a retired quantity to a live one would inflate the
    #: component count with parts the assembly no longer contains.
    active: bool = True

    @field_validator("parent_item", "child_item", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("BOM link requires both a parent and a child item")
        return key

    @field_validator("qty_per", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        d = _as_qty(v)
        if d <= 0:
            raise ValueError("qty_per must be strictly positive")
        return d

    @model_validator(mode="after")
    def _no_self_link(self) -> Self:
        if self.parent_item == self.child_item:
            raise ValueError(
                f"BOM link {self.parent_item} → itself is a one-node cycle"
            )
        return self


class Warehouse(DomainModel):
    """A warehouse of the site. The single-site assumption makes `site` noise."""

    campaign_id: str
    warehouse_id: str
    label: str = ""
    type: LocationType = LocationType.UNKNOWN
    status: LocationStatus = LocationStatus.ACTIVE

    @field_validator("warehouse_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("warehouse_id is required")
        return key


class Location(DomainModel):
    """A stock location inside a warehouse."""

    campaign_id: str
    warehouse_id: str
    location_id: str
    zone: str = ""
    type: LocationType = LocationType.UNKNOWN
    status: LocationStatus = LocationStatus.ACTIVE
    source: DataSource = DataSource.SYSTEM

    @field_validator("warehouse_id", "location_id", "zone", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @model_validator(mode="after")
    def _require_warehouse(self) -> Self:
        if not self.warehouse_id:
            raise ValueError("warehouse_id is required")
        return self

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def is_active(self) -> bool:
        return self.status is LocationStatus.ACTIVE


class Manager(DomainModel):
    """One of the campaign's managers (« gestionnaire ») and their identity.

    ``actor`` is the signed-in identity forwarded by the platform (an email).
    It carries two things that must not be confused.

    **The right to write.** Being declared here — and active — is what makes
    somebody a manager of the campaign rather than a reader of it. See
    :mod:`inventory.domain.access`.

    **A perimeter.** Warehouses and zones are assigned to a manager so each
    person can filter the screens down to their own work. Within the campaign
    that perimeter stays a *filter*: a manager may act outside it, because
    somebody has to cover for a colleague at 6 a.m. on inventory day.
    """

    campaign_id: str
    code: str
    label: str = ""
    actor: str = ""
    active: bool = True
    display_order: int = 0

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "").replace(" ", "_")
        if not key:
            raise ValueError("manager code is required")
        return key

    @field_validator("actor", mode="before")
    @classmethod
    def _actor(cls, v: Any) -> str:
        return str(v or "").strip().lower()


class BookStockLine(DomainModel):
    """One line of the frozen ERP book stock (``stock ERP``) snapshot."""

    campaign_id: str
    item_number: str
    warehouse_id: str
    location_id: str
    qty: Decimal = ZERO
    unit: str = "PCE"
    #: Unit cost captured at snapshot time; the campaign is valued with it.
    unit_cost: Decimal = ZERO
    #: La date à laquelle cette référence a été prise.
    #:
    #: Le jour J pour la plupart des lignes ; la date du précomptage pour les
    #: emplacements scellés, dont la référence est le stock ERP d'avant leur
    #: comptage. La règle est la même dans les deux cas, et c'est celle que
    #: :attr:`VarianceLine.variance_qty` documente déjà : la référence est *ce
    #: contre quoi la campagne a été comptée*. Elle s'applique simplement à deux
    #: dates dès qu'on précompte.
    #:
    #: D'où cette colonne : le total « stock ERP » d'une campagne qui précompte
    #: est composite, et un rapprochement avec un état ERP tiré à une date unique
    #: trouverait une différence que rien n'expliquerait.
    reference_date: dt.date | None = None
    #: Le lot avancé d'où vient cette référence, quand elle n'est pas du jour J.
    erp_journal_id: str | None = None

    @field_validator("item_number", "warehouse_id", "location_id", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("unit_cost", mode="before")
    @classmethod
    def _cost(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def value(self) -> Decimal:
        return quantize_money(self.qty * self.unit_cost)


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #

class CountJournal(DomainModel):
    """One ERP counting journal — exactly one per active warehouse+location."""

    id: str
    campaign_id: str
    warehouse_id: str
    location_id: str
    kind: JournalKind = JournalKind.INVV
    status: JournalStatus = JournalStatus.PENDING
    #: ERP journal number (``NPEM-522160``); empty until the journal exists.
    journal_number: str = ""
    description: str = ""
    posted_at: dt.datetime | None = None
    #: True when the journal was auto-created from an imported line whose
    #: location was absent from the book stock (book qty = 0, counted > 0).
    auto_created: bool = False
    updated_at: dt.datetime | None = None
    #: Le lot de comptage avancé auquel cet emplacement appartient, s'il y en a.
    erp_journal_id: str | None = None
    #: Quand le comptage de cet emplacement a été scellé, et par qui.
    #:
    #: **Le premier gel par objet du produit.** Jusqu'ici, tout ce que
    #: l'application gèle, elle le gèle par statut de campagne : la matrice de
    #: :mod:`inventory.domain.workflow` répond « peut-on écrire des journaux dans
    #: cette campagne ? », et tant qu'elle est en comptage la réponse vaut pour
    #: tous. Un comptage avancé posté doit pourtant cesser de bouger, sinon la
    #: preuve du 22 ne vaut rien le 24.
    #:
    #: La règle qui évite deux sources de vérité contradictoires : le scellement
    #: ne fait que **restreindre**. ``mutability_of`` est consulté en premier et
    #: garde le dernier mot pour interdire ; le scellement s'y ajoute et ne peut
    #: jamais rouvrir ce que la campagne a fermé.
    sealed_at: dt.datetime | None = None
    sealed_by: str = ""

    @field_validator("warehouse_id", "location_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def is_sealed(self) -> bool:
        return self.sealed_at is not None

    @property
    def is_complete(self) -> bool:
        """A journal contributes to progress once posted or book-enforced."""
        return self.status in (JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED)


class CountJournalLine(DomainModel):
    """One counted article inside a journal.

    The imported value and the manually corrected value are kept side by side —
    never overwritten — so that a reload of the ERP export can refresh
    ``qty_imported`` without destroying a human decision, and so that the audit
    trail shows exactly what a person changed.
    """

    id: str
    journal_id: str
    campaign_id: str
    item_number: str
    #: Value as received from the ERP export / file import.
    qty_imported: Decimal | None = None
    #: Value typed or pasted by a user; wins over ``qty_imported`` when set.
    qty_manual: Decimal | None = None
    unit: str = "PCE"
    source: DataSource = DataSource.ERP_IMPORT
    comment: str = ""
    updated_by: str | None = None
    updated_at: dt.datetime | None = None
    #: Le stock ERP **avant** comptage, agrégé depuis les lignes ERP du périmètre
    #: — la colonne ``OnHandQuantity`` de l'export, que l'export nomme
    #: « Stock ERP ». C'est la référence propre à cette ligne, et c'est elle qui
    #: rend un comptage avancé autonome : le journal apporte à la fois le
    #: comptage et ce contre quoi il se compare, sans chargement séparé.
    #:
    #: ``None`` n'est pas ``0``. ``None`` dit « aucune référence ERP connue » —
    #: une saisie manuelle, une ligne née d'un scan — quand ``0`` dit « l'ERP
    #: annonce zéro ». Les confondre ferait d'un article que l'ERP ignore un
    #: écart franc, alors qu'on n'en sait rien.
    qty_on_hand: Decimal | None = None
    #: Le numéro du journal ERP d'où cette ligne provient.
    erp_journal_number: str = ""
    #: Combien de lignes ERP — donc d'étiquettes — cette ligne agrège.
    label_count: int = 0

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty_imported", "qty_manual", "qty_on_hand", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def qty(self) -> Decimal:
        """Effective counted quantity: the manual value when present."""
        return self.qty_manual if self.qty_manual is not None else (
            self.qty_imported if self.qty_imported is not None else ZERO
        )

    @property
    def effective_source(self) -> DataSource:
        return DataSource.MANUAL if self.qty_manual is not None else self.source

    @property
    def is_overridden(self) -> bool:
        return self.qty_manual is not None and self.qty_manual != self.qty_imported


# --------------------------------------------------------------------------- #
# Le journal ERP, tel que l'ERP le produit
def erp_journal_numbers(lines: Sequence[CountJournalLine]) -> list[str]:
    """De quel(s) document(s) ERP vient le comptage de cet emplacement.

    Lu **dans les lignes**, jamais recopié. ``CountJournal`` porte bien un champ
    ``journal_number``, et trois écrans l'affichaient — la grille des journaux
    sous l'en-tête « N° ERP », l'export Excel, l'assistant. Rien ne l'écrit
    jamais : les journaux de comptage naissent d'un emplacement, pas d'un
    document, et le numéro arrive plus tard avec les lignes. La colonne était
    donc vide dans les trois, sans que rien ne le signale.

    Plusieurs valeurs quand plusieurs journaux ont alimenté l'emplacement. C'est
    un fait — pas une anomalie — et n'en garder qu'un le cacherait.
    """
    return sorted({line.erp_journal_number for line in lines if line.erp_journal_number})


# --------------------------------------------------------------------------- #
#
# Un objet **à côté** de :class:`CountJournal`, pas à sa place. ``CountJournal``
# reste un par (campagne, entrepôt, emplacement) : c'est l'unité de comptage, de
# progression et de gel de toute l'application. Le journal ERP, lui, tient à un
# entrepôt et couvre plusieurs emplacements — sur l'export réel du 13 juin 2026,
# 48 journaux sur 73 en couvrent plus d'un, jusqu'à 54 pour l'un d'eux.
#
# Deux grains, deux tables, et c'est délibéré.

class ErpJournal(DomainModel):
    """Un journal de comptage tel que l'ERP le tient, et son périmètre déclaré."""

    id: str
    campaign_id: str
    journal_number: str
    kind: JournalKind = JournalKind.INVV
    description: str = ""
    site_id: str = ""
    #: Le postage tel que l'en-tête ERP le déclare (``IsPosted``), distinct du
    #: statut de workflow d'un :class:`CountJournal` qu'un humain fait avancer.
    #:
    #: Poster un journal réaligne l'ERP sur le physique compté. L'application ne
    #: l'exige plus pour sceller : un journal de précomptage se charge une fois
    #: posté et validé dans l'ERP — il y en a peu, et ils n'ont pas l'urgence du
    #: jour J. Le cas du journal non posté ne se rencontre pas, et une garde qui
    #: ne se déclenche jamais est une garde qu'on ne sait pas maintenir.
    erp_posted: bool = False
    erp_posted_at: dt.datetime | None = None
    line_count: int = 0
    first_imported_at: dt.datetime | None = None
    last_imported_at: dt.datetime | None = None
    #: Les emplacements que ce journal couvre réellement, tels qu'un humain les
    #: a désignés.
    #:
    #: Ils ne se déduisent pas des lignes : certaines ne portent un autre
    #: entrepôt ou emplacement que pour matérialiser un déplacement — 1 932
    #: lignes sur 58 345 dans l'export analysé. Tant que le périmètre n'est pas
    #: déclaré, rien n'est calculable : ni la référence d'un emplacement, ni ce
    #: qui est une ligne de passage.
    scope: list[LocationKey] = Field(default_factory=list)
    scope_declared_at: dt.datetime | None = None
    scope_declared_by: str = ""
    #: La date du relevé physique, lue dans la colonne « Date de comptage » des
    #: lignes du journal — jamais retapée. C'est elle qui date la référence des
    #: emplacements scellés, donc l'inventaire de chacun d'eux.
    counted_on: dt.date | None = None
    #: Déclarer le périmètre **scelle**. Les deux gestes n'en font qu'un : dire
    #: quels emplacements ce journal couvre, c'est dire lesquels sont comptés et
    #: ne bougeront plus.
    sealed_at: dt.datetime | None = None
    sealed_by: str = ""

    @field_validator("journal_number", "site_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @property
    def scope_declared(self) -> bool:
        return self.scope_declared_at is not None

    @property
    def is_sealed(self) -> bool:
        return self.sealed_at is not None

    @property
    def warehouses(self) -> set[str]:
        return {key.warehouse_id for key in self.scope}

    def covers(self, key: LocationKey) -> bool:
        return key in self.scope


class ErpJournalLine(DomainModel):
    """Une ligne de journal ERP, au grain où l'ERP la produit.

    Conservée telle quelle — y compris les lignes hors périmètre et celles de
    l'emplacement tampon — parce que c'est la trace, et parce que le seul
    contrôle qui descende sous le grain de l'application la lit : une étiquette
    d'un emplacement scellé retrouvée comptée dans un autre journal.
    """

    id: str
    erp_journal_id: str
    campaign_id: str
    #: Numéro de ligne ERP. Absent de certains exports, et ce n'est pas une
    #: raison de refuser la ligne : ce serait perdre une quantité comptée pour
    #: une colonne technique.
    erp_line_number: int | None = None
    site_id: str = ""
    warehouse_id: str
    location_id: str = ""
    #: Étiquette logistique (``SILlabelID``) et numéro de série
    #: (``ItemSerialNumber``), **en texte et jamais autrement**. « 001609231 »
    #: perd trois caractères au premier passage par un entier, et une étiquette
    #: tronquée ne se rattache plus à rien.
    label_id: str = ""
    serial_number: str = ""
    item_number: str
    #: « Stock ERP » : la référence, avant comptage.
    qty_on_hand: Decimal = ZERO
    #: « Qté Comptée » : le physique relevé ou scanné.
    qty_counted: Decimal = ZERO
    unit: str = "PCE"
    inventory_status_id: str = ""

    @field_validator("item_number", "warehouse_id", "location_id", "unit",
                     "site_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("label_id", "serial_number", "inventory_status_id",
                     mode="before")
    @classmethod
    def _identifier(cls, v: Any) -> str:
        """Un identifiant se transporte, il ne se normalise pas.

        Ni majuscules, ni espaces recollés, ni conversion : seul l'entourage
        blanc part. Tout le reste appartient à l'ERP, y compris les zéros de
        tête, et le renvoyer autrement qu'il est arrivé casserait le
        rapprochement.
        """
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("qty_on_hand", "qty_counted", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def variance_qty(self) -> Decimal:
        """Qté Comptée − Stock ERP.

        Un écart de ligne n'est pas une anomalie de stock : un moins ici et un
        plus là-bas, c'est le déplacement d'une même pièce. Sur l'export du
        13 juin, 18 696 lignes portent une arrivée et 17 971 un départ.
        """
        return quantize_qty(self.qty_counted - self.qty_on_hand)


# --------------------------------------------------------------------------- #
# Comptages avancés
# --------------------------------------------------------------------------- #

class LabelDecision(DomainModel):
    """Où est la pièce, quand une étiquette scellée reparaît ailleurs.

    Le contrôle par étiquette rattrape ce que la dérive ne voit pas : une pièce
    sortie d'un emplacement scellé sans transaction ERP laisse une dérive nulle,
    mais si elle est re-scannée ailleurs, son étiquette apparaît dans un second
    journal. La question posée est alors simple et une seule personne peut y
    répondre — où est-elle réellement ?

    Trois réponses, et chacune a un effet mesurable sur les quantités :

    * :attr:`LabelResolution.KEEP_NEW` — elle est au nouvel emplacement, donc
      elle sort de l'agrégation de l'emplacement scellé ;
    * :attr:`LabelResolution.KEEP_SEALED` — elle n'a pas bougé, donc c'est la
      ligne de l'autre journal qui sort ;
    * :attr:`LabelResolution.RECOUNT` — on ne tranche pas sur pièce. Rien n'est
      exclu, et l'ancien emplacement rejoint la liste des emplacements à
      desceller et rescanner.

    Les emplacements sont figés à la décision. Un réimport qui déplacerait
    encore l'étiquette ne réécrit pas ce qu'un humain a constaté.
    """

    id: str
    campaign_id: str
    label_id: str
    item_number: str
    decision: LabelResolution
    sealed_warehouse_id: str = ""
    sealed_location_id: str = ""
    other_warehouse_id: str = ""
    other_location_id: str = ""
    comment: str = ""
    decided_at: dt.datetime | None = None
    decided_by: str = ""

    @field_validator("item_number", "sealed_warehouse_id", "sealed_location_id",
                     "other_warehouse_id", "other_location_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("label_id", mode="before")
    @classmethod
    def _label(cls, v: Any) -> str:
        # Jamais normalisée : « 001609231 » perd ses zéros de tête au premier
        # passage par autre chose qu'une chaîne, et une étiquette tronquée ne se
        # rattache plus à rien.
        return "" if v is None else str(v).strip()

    @property
    def excluded_from_sealed(self) -> bool:
        """L'étiquette quitte l'emplacement scellé."""
        return self.decision is LabelResolution.KEEP_NEW

    @property
    def excluded_from_other(self) -> bool:
        """L'étiquette reste où elle était ; l'autre ligne est l'erreur."""
        return self.decision is LabelResolution.KEEP_SEALED


class EarlyCountDrift(DomainModel):
    """L'écart entre le stock ERP du jour J et le physique posté au précomptage.

    Attendue nulle : l'emplacement a été balisé, et poster son journal a
    réaligné l'ERP sur le physique compté. Quand elle ne l'est pas, une seule
    question se pose — quelle quantité fait foi au jour J ? — et
    :class:`DriftResolution` en porte les deux réponses.

    Ce que cette dérive ne verra pas
    --------------------------------
    Elle se calcule entre deux lectures de l'ERP, donc elle ne voit que ce que
    l'ERP a appris. Une pièce sortie d'un emplacement scellé sans aucune
    transaction laisse une dérive nulle. Si elle est re-scannée ailleurs le jour
    J, c'est le contrôle par étiquette qui la rattrape ; sinon rien ne la voit,
    et la perte n'apparaîtra qu'à l'inventaire suivant.
    """

    id: str
    campaign_id: str
    erp_journal_id: str | None = None
    warehouse_id: str
    location_id: str
    item_number: str
    #: Le stock ERP d'avant le comptage avancé — la référence de l'emplacement.
    qty_erp_t0: Decimal = ZERO
    #: Compté + ajusté à T0.
    qty_physical_t0: Decimal = ZERO
    #: Le stock ERP du snapshot général, gelé le jour J.
    qty_erp_j: Decimal = ZERO
    drift_value: Decimal = ZERO
    is_material: bool = False
    resolution: DriftResolution | None = None
    cause_code: str = ""
    comment: str = ""
    resolved_at: dt.datetime | None = None
    resolved_by: str = ""

    @field_validator("item_number", "warehouse_id", "location_id", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty_erp_t0", "qty_physical_t0", "qty_erp_j", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("drift_value", mode="before")
    @classmethod
    def _value(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)

    @property
    def key(self) -> LocationKey:
        return LocationKey(
            warehouse_id=self.warehouse_id, location_id=self.location_id
        )

    @property
    def drift_qty(self) -> Decimal:
        """``ERP@J − physique@T0``, calculée et non stockée.

        Stocker la soustraction à côté de ses deux termes aurait ouvert la
        possibilité qu'ils cessent d'être d'accord.
        """
        return quantize_qty(self.qty_erp_j - self.qty_physical_t0)

    @property
    def is_resolved(self) -> bool:
        return self.resolution is not None

    @property
    def blocks_analysis(self) -> bool:
        """Une dérive matérielle sans issue arrête le passage en analyse."""
        return self.is_material and not self.is_resolved


class Zone(DomainModel):
    """A physical zone of the GENERIQUE location (line side, picking, lab, …).

    GENERIQUE is one ERP location but many physical areas; each is counted twice
    by two independent teams, then arbitrated.
    """

    id: str
    campaign_id: str
    code: str
    label: str = ""
    #: Quand un humain a déclaré la zone terminée, et qui. C'est la **seule**
    #: donnée d'état stockée du parcours de comptage : les deux autres statuts
    #: se déduisent des quantités relevées, et ne peuvent donc pas mentir.
    closed_at: dt.datetime | None = None
    closed_by: str = ""
    #: Free-text owner/sector, used for dispatching printed sheets.
    sector: str = ""
    display_order: int = 0
    #: Number of independent counts this zone requires. Two is the rule; one is
    #: the assumed exception for an area where a second team adds nothing.
    passes: int = Field(default=2, ge=1, le=2)
    #: True when the sheet is deliberately blank — the counter writes down what
    #: they find, there is no pre-printed article list. Distinguishing this from
    #: "the list was never prepared" is what stops the preparation controls from
    #: reporting a normal free-entry sheet as a defect.
    free_entry: bool = False
    #: Code of the manager (:class:`Manager`) this zone is assigned to; empty
    #: when nobody owns it yet.
    manager_code: str = ""
    #: Whether a negative counted quantity is accepted on this zone's sheets.
    #: Off by default: one does not find minus twenty screws in a bin, so a
    #: negative is almost always a typo, and catching it at the keyboard is far
    #: cheaper than explaining it at the variance meeting. Correction sheets are
    #: the legitimate exception, and they say so.
    allow_negative: bool = False

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v: Any) -> str:
        key = normalise_key(str(v) if v is not None else "")
        if not key:
            raise ValueError("zone code is required")
        return key


class CountSheet(DomainModel):
    """One printed counting sheet: a (zone, pass) pair."""

    id: str
    campaign_id: str
    zone_id: str
    pass_no: SheetPass
    counter_name: str = ""
    started_at: dt.datetime | None = None
    ended_at: dt.datetime | None = None
    #: UC volume path of the scanned sheet, when one was uploaded.
    evidence_path: str | None = None
    #: sha256 du fichier déposé. Le chemin dit *où*, l'empreinte dit *lequel* :
    #: un volume se modifie depuis l'espace de travail, et six mois plus tard
    #: c'est la seule façon de répondre autrement que par la confiance.
    #: ``None`` sur les feuilles scannées avant la migration 019.
    evidence_sha256: str | None = None
    #: Taille du fichier déposé, en octets.
    evidence_bytes: int | None = None
    #: Type MIME déduit du nom du fichier déposé.
    evidence_mime: str | None = None
    #: Mean confidence reported by the extraction model, in [0, 1].
    extraction_confidence: float | None = None
    updated_at: dt.datetime | None = None


class CountSheetLine(DomainModel):
    """One article counted on a sheet, within a section.

    ``section`` decides how the quantity is consolidated: as-is for
    ``LINE_SIDE`` and ``WIP_OK``, exploded through the BOM for ``WIP``.
    """

    id: str
    sheet_id: str
    campaign_id: str
    item_number: str
    section: CountSection = CountSection.LINE_SIDE
    #: Pre-printed / imported value.
    qty_imported: Decimal | None = None
    #: Value typed by the encoder, or corrected after an AI extraction.
    qty_manual: Decimal | None = None
    unit: str = "PCE"
    source: DataSource = DataSource.MANUAL
    #: Per-line confidence when the value came from ``SCAN_AI``.
    confidence: float | None = None
    #: L'opération écrite sur la feuille, quand la quantité en était une.
    #:
    #: « 3*48+7 » plutôt que « 151 » : c'est ce que le compteur a réellement
    #: noté, et c'est ce qui permet de recompter six mois plus tard. Vide dès
    #: que la saisie était déjà un nombre — garder « 151 » comme sa propre
    #: formule remplirait une colonne de doublons. Le résultat, lui, est dans
    #: `qty_manual` ou `qty_imported` comme n'importe quelle autre quantité :
    #: rien en aval n'a à connaître cette colonne pour calculer juste.
    qty_formula: str = ""
    comment: str = ""
    display_order: int = 0

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty_imported", "qty_manual", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def qty(self) -> Decimal:
        return self.qty_manual if self.qty_manual is not None else (
            self.qty_imported if self.qty_imported is not None else ZERO
        )

    @property
    def is_counted(self) -> bool:
        """A blank cell is *not* a zero: it means the line was not counted."""
        return self.qty_manual is not None or self.qty_imported is not None

    @property
    def was_ai_corrected(self) -> bool:
        """The model read this line and a human then typed over it.

        ``confidence`` survives a manual edit — the extraction is kept beside
        the correction, never replaced by it — so the two together are the
        record that somebody reviewed the machine. That is what a second,
        multi-sheet scan must not silently undo.
        """
        return self.confidence is not None and self.qty_manual is not None


class ArbitrationLine(DomainModel):
    """A discrepancy between pass 1 and pass 2, and the quantity retained."""

    id: str
    campaign_id: str
    zone_id: str
    item_number: str
    section: CountSection
    qty_pass_1: Decimal | None = None
    qty_pass_2: Decimal | None = None
    qty_arbitrated: Decimal | None = None
    decided_by: str | None = None
    decided_at: dt.datetime | None = None
    comment: str = ""

    @field_validator("qty_pass_1", "qty_pass_2", "qty_arbitrated", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal | None:
        if v is None or v == "":
            return None
        return _as_qty(v)

    @property
    def is_resolved(self) -> bool:
        """A human has *decided*, not merely been offered a value.

        Resolution is stamped by ``decided_at``, never by the presence of a
        quantity. Filling ``qty_arbitrated`` in bulk is a convenience — it saves
        typing the same figure forty times — and treating that convenience as a
        decision would post forty quantities nobody ever looked at.
        """
        return self.decided_at is not None

    @property
    def is_proposed(self) -> bool:
        """A quantity is on the table, waiting for someone to confirm or change it."""
        return self.qty_arbitrated is not None and self.decided_at is None

    @property
    def gap(self) -> Decimal:
        return (self.qty_pass_2 or ZERO) - (self.qty_pass_1 or ZERO)


class WipBreakdown(DomainModel):
    """Traceability of one exploded WIP assembly.

    Answers the specification's requirement to "see what the WIP is made of"
    instead of only its aggregated value.
    """

    parent_item: str
    parent_qty: Decimal
    child_item: str
    #: Cumulated quantity of *child* per one *parent*, across all BOM levels.
    qty_per_parent: Decimal
    child_qty: Decimal
    depth: int
    zone_code: str = ""


class ConsolidatedLine(DomainModel):
    """One line of the GENERIQUE consolidation, ready to post as an INVV journal."""

    campaign_id: str
    item_number: str
    qty: Decimal
    unit: str = "PCE"
    #: Split of the total by origin, for drill-down in the UI.
    qty_line_side: Decimal = ZERO
    qty_wip_ok: Decimal = ZERO
    qty_wip_exploded: Decimal = ZERO
    #: Zones that contributed, for the "who counted this" question.
    zone_codes: list[str] = Field(default_factory=list)

    @property
    def has_wip(self) -> bool:
        return self.qty_wip_exploded != 0


# --------------------------------------------------------------------------- #
# Adjustments & analysis
# --------------------------------------------------------------------------- #

class AdjustmentLine(DomainModel):
    """A stock movement recorded during the analysis phase.

    Covers both the movements generated by the counting journals and the manual
    adjustment journals posted afterwards — the ERP remains the master, the app
    mirrors the movements to keep the balance sheet live.
    """

    id: str
    campaign_id: str
    item_number: str
    warehouse_id: str = ""
    location_id: str = ""
    kind: AdjustmentKind = AdjustmentKind.ADJUSTMENT
    #: Signed quantity: positive = stock increase, negative = stock decrease.
    qty: Decimal = ZERO
    unit: str = "PCE"
    #: Signed value of the movement, as valued by the ERP.
    value: Decimal = ZERO
    journal_number: str = ""
    physical_date: dt.date | None = None
    reason_code: str = ""
    comment: str = ""
    source: DataSource = DataSource.FILE_IMPORT
    created_at: dt.datetime | None = None

    @field_validator("item_number", "warehouse_id", "location_id", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v: Any) -> Decimal:
        return _as_money(v if v not in (None, "") else 0)


# --------------------------------------------------------------------------- #
# Backflush
# --------------------------------------------------------------------------- #

class BackflushLine(DomainModel):
    """The backflush variance of one article, frozen on a campaign.

    Production does not book component issues line by line: they are deducted
    from the declared output according to the bill of materials. That deduction
    assumes real consumption equals theoretical consumption, and this figure is
    exactly the measure of that assumption:

        écart = consommation théorique − consommation réelle

    Its sign says which way the system stock drifted, and the sign is the whole
    point for an inventory. A **positive** backflush variance means the deduction
    took *less* than theory: the part left the store without the ERP recording
    it, so the system stock is overstated and the count will find less than the
    ERP claims. A negative one says the opposite.

    Which is why it enters the inventory formula with its sign changed — the two
    conventions are mirror images, and reconciling them anywhere but in one
    named property is how a sign error survives a review.
    """

    campaign_id: str
    item_number: str
    #: ISO Mondays. Start inclusive, end exclusive — the fact table's own grain.
    period_start: dt.date
    period_end: dt.date
    unit: str = "PCE"
    #: ``ecart_backflush_net``, in the backflush convention.
    net_qty: Decimal = ZERO
    #: The two halves of the net. They do not feed the recalculation — the net
    #: does — but they say what it is made of: 40 of under-consumption against
    #: 38 of over-consumption does not read like 2.
    under_consumed_qty: Decimal = ZERO
    over_consumed_qty: Decimal = ZERO
    theoretical_qty: Decimal = ZERO
    actual_qty: Decimal = ZERO
    parent_count: int = 0
    week_count: int = 0
    #: Freshness of the gold table when it was read, and when the read happened.
    #: Both are needed to replay a figure months later.
    source_loaded_at: dt.datetime | None = None
    refreshed_at: dt.datetime | None = None

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator(
        "net_qty", "under_consumed_qty", "over_consumed_qty",
        "theoretical_qty", "actual_qty", mode="before",
    )
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)

    @field_validator("parent_count", "week_count", mode="before")
    @classmethod
    def _count(cls, v: Any) -> int:
        return int(v) if v not in (None, "") else 0

    @property
    def inventory_share_qty(self) -> Decimal:
        """``part_backflush`` — the same figure, in the inventory convention.

        The inventory reads ``compté − ERP``; the backflush reads
        ``théorique − réel``. Changing the sign here, once, is what lets every
        caller downstream stay in a single convention.
        """
        return quantize_qty(-self.net_qty)


# --------------------------------------------------------------------------- #
# Stock-flow reconciliation between two campaigns
# --------------------------------------------------------------------------- #

class StockFlowRun(DomainModel):
    """One comparison of two campaigns, and the period it spans."""

    id: str
    #: The campaign whose counted stock we are trying to explain.
    campaign_id: str
    #: The earlier one, by *count date* — never by creation date. Two campaigns
    #: created in one order and counted in the other exist, and it is the count
    #: that bounds the period.
    baseline_campaign_id: str
    period_start: dt.date
    period_end: dt.date
    #: Distinguishes « no scrap » from « scrap not entered », which are two very
    #: different readings of the same zero.
    scrap_loaded: bool = False
    source_loaded_at: dt.datetime | None = None
    erp_refreshed_at: dt.datetime | None = None
    #: When each loaded step was last read from the ERP. Per step and not per
    #: run: the four reads hit four different tables, and one of them failing
    #: must not make the other three look stale.
    receipts_refreshed_at: dt.datetime | None = None
    shipments_refreshed_at: dt.datetime | None = None
    scrap_refreshed_at: dt.datetime | None = None
    created_by: str = ""
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class StockFlowInput(DomainModel):
    """One quantity the user loaded for the period."""

    run_id: str
    item_number: str
    kind: FlowKind
    #: Always positive; the direction is carried by :attr:`kind`.
    qty: Decimal = ZERO
    unit: str = "PCE"
    #: Read from the ERP, loaded from a file, or typed into the grid.
    source: FlowSource = FlowSource.MANUAL

    @field_validator("item_number", "unit", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return abs(_as_qty(v if v not in (None, "") else 0))


class StockFlowErp(DomainModel):
    """The two backflush measures of one article, frozen with the run."""

    run_id: str
    item_number: str
    #: Output declared for this article *as a parent*, de-duplicated by week.
    produced_qty: Decimal = ZERO
    #: Theoretical consumption of this article *as a component*.
    consumed_qty: Decimal = ZERO
    #: Read from the backflush table, or corrected by hand in the grid.
    source: FlowSource = FlowSource.ERP

    @field_validator("item_number", mode="before")
    @classmethod
    def _key(cls, v: Any) -> str:
        return normalise_key(str(v) if v is not None else "")

    @field_validator("produced_qty", "consumed_qty", mode="before")
    @classmethod
    def _qty(cls, v: Any) -> Decimal:
        return _as_qty(v if v not in (None, "") else 0)


class StockFlowLine(DomainModel):
    """One article's walk from the opening count to the closing one.

    Computed, never stored: every term is recoverable from the two campaigns,
    the loaded quantities and the frozen ERP snapshot, so the whole report can be
    rebuilt months later from the same inputs.
    """

    item_number: str
    name: str = ""
    unit: str = "PCE"
    unit_cost: Decimal = ZERO
    #: Stock of the earlier campaign — the opening balance. Which reading it is,
    #: physical or ERP, is the run's ``StockBasis`` and is reported with it.
    opening_qty: Decimal = ZERO
    received_qty: Decimal = ZERO
    produced_qty: Decimal = ZERO
    shipped_qty: Decimal = ZERO
    consumed_qty: Decimal = ZERO
    scrapped_qty: Decimal = ZERO
    #: Stock of the later campaign — what the chosen reading says turned up.
    closing_qty: Decimal = ZERO
    #: Whether each campaign has a figure for this article at all. A reference
    #: absent from one of the two readings is not a zero: it is a hole in the
    #: comparison, and reading it as a zero would produce a variance the size of
    #: the stock.
    has_opening: bool = False
    has_closing: bool = False

    @property
    def expected_qty(self) -> Decimal:
        """The whole chain, in the order the flows happen."""
        return quantize_qty(
            self.opening_qty
            + self.received_qty
            + self.produced_qty
            - self.shipped_qty
            - self.consumed_qty
            - self.scrapped_qty
        )

    @property
    def variance_qty(self) -> Decimal:
        """Counted minus expected — what none of the flows explains."""
        return quantize_qty(self.closing_qty - self.expected_qty)

    @property
    def variance_value(self) -> Decimal:
        return quantize_money(self.variance_qty * self.unit_cost)

    @property
    def abs_variance_value(self) -> Decimal:
        return abs(self.variance_value)

    @property
    def variance_ratio(self) -> Decimal | None:
        """Relative to the expected stock. ``None`` when nothing was expected.

        Returned as ``None`` rather than zero: an article expected at zero and
        found at twelve has an undefined ratio, not a nil one, and showing 0 %
        next to a real discrepancy is worse than showing nothing.
        """
        expected = self.expected_qty
        if expected == 0:
            return None
        return quantize_qty(self.variance_qty / expected)

    @property
    def is_complete(self) -> bool:
        """Whether both readings carry it — the comparison is only valid then."""
        return self.has_opening and self.has_closing


class VarianceLine(DomainModel):
    """A computed variance between book stock and counted stock.

    Produced by :mod:`inventory.domain.variance`; never stored as a source of
    truth, always recomputable from the frozen snapshot + counts + adjustments.
    """

    campaign_id: str
    item_number: str
    warehouse_id: str = ""
    location_id: str = ""
    item_type: ItemType = ItemType.UNKNOWN
    category: str = ""
    program: str = ""
    unit: str = "PCE"
    unit_cost: Decimal = ZERO

    book_qty: Decimal = ZERO
    counted_qty: Decimal = ZERO
    adjusted_qty: Decimal = ZERO
    #: ``ecart_backflush_net``, in the *backflush* convention (théorique − réel).
    #: Zero when the period holds no backflush line for this article, which is
    #: the honest reading: production did not touch it, so it has no such
    #: variance. :attr:`backflush_measured` distinguishes that from « not loaded ».
    backflush_qty: Decimal = ZERO
    backflush_measured: bool = False

    #: True when the article/location appears in a count but not in the book
    #: stock, or vice-versa — the two cases that used to disappear silently.
    counted_only: bool = False
    book_only: bool = False

    @property
    def book_value(self) -> Decimal:
        return quantize_money(self.book_qty * self.unit_cost)

    @property
    def physical_qty(self) -> Decimal:
        """The physical stock: counted, plus what moved after the count.

        An adjustment is a *real stock movement* recorded during the analysis
        phase — a late receipt, a recount, an issue booked afterwards. It
        therefore changes what is on the shelf, and the count alone stops being
        the current picture the moment one is posted.

        This is the reference the whole application reads. Everything else —
        KPIs, controls, aggregates, analytics, exports — goes through
        :attr:`variance_qty` below, so redefining the reference here changes them
        all at once rather than in twenty places that would drift.
        """
        return quantize_qty(self.counted_qty + self.adjusted_qty)

    @property
    def physical_value(self) -> Decimal:
        return quantize_money(self.physical_qty * self.unit_cost)

    @property
    def variance_qty(self) -> Decimal:
        """Physical minus book — *the* variance, adjustments included.

        The frozen ERP snapshot stays the reference on the other side: it is
        what the campaign was counted against, and moving it would make the
        variance irreproducible.
        """
        return quantize_qty(self.physical_qty - self.book_qty)

    @property
    def variance_value(self) -> Decimal:
        return quantize_money(self.variance_qty * self.unit_cost)

    @property
    def counted_variance_qty(self) -> Decimal:
        """The gap the count alone showed, before any adjustment.

        Kept because it answers a different question — « what did the count
        find? » rather than « where do we stand? » — and because the difference
        between the two is precisely what the adjustments did. Nothing steers on
        it; it is shown beside the variance when adjustments exist.
        """
        return quantize_qty(self.counted_qty - self.book_qty)

    @property
    def counted_variance_value(self) -> Decimal:
        return quantize_money(self.counted_variance_qty * self.unit_cost)

    @property
    def adjusted_value(self) -> Decimal:
        return quantize_money(self.adjusted_qty * self.unit_cost)

    @property
    def final_qty(self) -> Decimal:
        """Stock after inventory = book + variance = the physical stock."""
        return quantize_qty(self.book_qty + self.variance_qty)

    # -- backflush ---------------------------------------------------------- #
    #
    # Read against the *adjusted* variance, like everything else: the question is
    # « of the gap we are left with, how much does production explain? », and
    # asking it about a gap the adjustments have already moved would answer about
    # a state nobody is in any more.

    @property
    def backflush_share_qty(self) -> Decimal:
        """``part_backflush`` — the backflush figure, sign flipped once."""
        return quantize_qty(-self.backflush_qty)

    @property
    def backflush_share_value(self) -> Decimal:
        return quantize_money(self.backflush_share_qty * self.unit_cost)

    @property
    def unexplained_qty(self) -> Decimal:
        """What production does *not* explain — the figure to investigate.

        Named « inexpliqué » rather than « résiduel » : the word says what is
        missing — an explanation — instead of merely saying that something is
        left over, which is true of half the figures on the screen.
        """
        return quantize_qty(self.variance_qty - self.backflush_share_qty)

    @property
    def unexplained_value(self) -> Decimal:
        return quantize_money(self.unexplained_qty * self.unit_cost)

    @property
    def explanation_rate(self) -> Decimal | None:
        """How much of the variance the backflush removes, in [−∞, 1].

        A plain ``part / écart`` ratio is misleading: it passes 100 % as soon as
        the backflush over-explains, and has no readable sign when the two terms
        point opposite ways. Framing it as a *reduction of the gap* behaves:

            1  the backflush explains the variance exactly
            0  it brings nothing
           <0  taking it into account widens the gap instead of closing it

        That last case is a signal, not a defect of the formula, so it is
        returned as it stands rather than floored at zero. ``None`` when the
        variance is nil, because a share of nothing is undefined, not total.
        """
        gap = abs(self.variance_qty)
        if gap == 0:
            return None
        return quantize_qty(1 - abs(self.unexplained_qty) / gap)


class ControlFinding(DomainModel):
    """One finding produced by the control engine."""

    code: str
    severity: ControlSeverity
    message: str
    #: Business coordinates of the offending object, for deep-linking.
    entity_type: str = ""
    entity_id: str = ""
    item_number: str = ""
    warehouse_id: str = ""
    location_id: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class FindingGroup(DomainModel):
    """Every occurrence of one control, under a single title.

    The screen shows the label and the count, and opens the occurrences on
    demand. Carrying them here rather than re-fetching them keeps the two
    numbers — the one announced and the one listed — impossible to disagree.
    """

    code: str
    label: str
    #: The worst severity present among the occurrences.
    severity: ControlSeverity
    findings: list[ControlFinding] = Field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.findings)

    def to_summary(self) -> dict[str, Any]:
        """Wire shape: the title and how many, not the occurrences again.

        The occurrences already travel in the flat ``findings`` list the screen
        receives alongside; sending them a second time would double a payload
        that runs to thousands of lines, and hand the client two copies free to
        disagree. It filters that list by ``code`` to open a group.
        """
        return {
            "code": self.code,
            "label": self.label,
            "severity": str(self.severity),
            "count": self.count,
        }


class AssignableCause(DomainModel):
    """A standard root cause, from the site's referential."""

    code: str
    label: str
    family: str = ""
    description: str = ""
    display_order: int = 0
    active: bool = True


class VarianceAnalysis(DomainModel):
    """Human analysis attached to an article's variance."""

    id: str
    campaign_id: str
    item_number: str
    cause_code: str | None = None
    comment: str = ""
    analyst: str | None = None
    #: Set when the analyst confirms the residual is understood and accepted.
    accepted: bool = False
    #: Optional AI proposal kept separate from the human decision.
    ai_suggested_cause: str | None = None
    ai_confidence: float | None = None
    ai_rationale: str = ""
    updated_at: dt.datetime | None = None


class AuditEvent(DomainModel):
    """One immutable entry of the audit trail."""

    id: str
    campaign_id: str | None
    at: dt.datetime
    actor: str
    action: str
    entity_type: str
    entity_id: str = ""
    summary: str = ""
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    request_id: str | None = None
