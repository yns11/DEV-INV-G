"""The order in which a campaign is built, enforced rather than remembered.

:mod:`~inventory.domain.workflow` answers *when* something may be written — the
phase it belongs to. This module answers *after what*: within a phase, the steps
still have an order, and doing them out of order produces work that looks done
and is not.

Three real incidents motivated it, all from the same afternoon:

* quantities were entered on GENERIQUE sheets while the campaign was still in
  preparation — counts of a stock nobody had frozen a reference for;
* the consolidated journal was generated from those quantities, which made a
  campaign that had not started look half-counted;
* every counting journal was posted before the ERP stock was even loaded, so
  the postings had nothing to be compared against.

None of the three raised anything. They cannot happen now: the prerequisite is
checked at the same choke point as the freeze, so the API refuses them and the
interface can grey them out with the same sentence.

The rules read as the process does:

    Préparation   articles → nomenclatures, feuilles → pilotage
    Comptage      stock ERP → journaux, GENERIQUE

Two facts decide everything — what has been loaded, and what has been frozen —
so the whole thing stays a pure function of :class:`Progress`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Progress", "PREREQUISITES", "blocking_reason", "unlocked_aspects"]


@dataclass(frozen=True, slots=True)
class Progress:
    """What the campaign already holds. Counts, not opinions."""

    #: Articles in the campaign's referential.
    items: int = 0
    #: GENERIQUE zones, each of which carries its printable sheets.
    zones: int = 0
    #: Lines of the ERP stock snapshot.
    book_stock_lines: int = 0
    #: Whether that snapshot has been frozen.
    book_stock_frozen: bool = False


@dataclass(frozen=True, slots=True)
class _Requirement:
    """One prerequisite, and the sentence shown when it is not met."""

    #: Attribute of :class:`Progress` that must be truthy.
    fact: str
    reason: str

    def unmet(self, progress: Progress) -> bool:
        return not getattr(progress, self.fact)


_ITEMS = _Requirement(
    "items",
    "Chargez d'abord le référentiel articles : tout le reste s'y rattache.",
)
_ZONES = _Requirement(
    "zones",
    "Créez d'abord les feuilles de comptage : il n'y a rien à affecter tant "
    "qu'aucune zone n'existe.",
)
_BOOK_STOCK = _Requirement(
    "book_stock_lines",
    "Chargez d'abord le stock ERP : un comptage sans référence à laquelle le "
    "comparer ne mesure rien.",
)
_BOOK_STOCK_FROZEN = _Requirement(
    "book_stock_frozen",
    "Gelez d'abord le stock ERP : poster un comptage contre une référence qui "
    "peut encore bouger rend l'écart irreproductible.",
)

#: Prerequisites per editable aspect. An aspect absent from this map has none —
#: which is the honest default: a step nobody has a reason to order should not
#: be ordered.
PREREQUISITES: dict[str, tuple[_Requirement, ...]] = {
    # --- Préparation ------------------------------------------------------
    "boms": (_ITEMS,),
    "zones": (_ITEMS,),
    "count_sheets": (_ITEMS,),
    # Managers, perimeters and thresholds all ride on this aspect: they assign
    # people to zones and money to article types, so both must already exist.
    "thresholds": (_ITEMS, _ZONES),
    # --- Comptage ---------------------------------------------------------
    "count_journals": (_BOOK_STOCK,),
    "count_entries": (_BOOK_STOCK,),
    # L'écart backflush est rattaché aux articles de la campagne : sans
    # référentiel, il n'y a rien à quoi le rattacher, et la lecture ramènerait
    # toute l'usine.
    "backflush": (_ITEMS,),
    # La réconciliation part du stock *compté* d'une campagne antérieure et le
    # compare au stock compté de celle-ci : les deux se lisent par article.
    "stock_flow": (_ITEMS,),
    # Posting is the irreversible one, and the only one that needs the snapshot
    # to have stopped moving.
    "post_journal": (_BOOK_STOCK, _BOOK_STOCK_FROZEN),
}


def blocking_reason(aspect: str, progress: Progress) -> str | None:
    """Why *aspect* cannot be written yet, or ``None`` when it can.

    Returns the first unmet prerequisite rather than a list: the steps are
    ordered, so the first gap is the one to close, and naming three at once
    would only obscure which to do next.
    """
    for requirement in PREREQUISITES.get(aspect, ()):
        if requirement.unmet(progress):
            return requirement.reason
    return None


def unlocked_aspects(progress: Progress) -> dict[str, bool]:
    """Every gated aspect, and whether its prerequisites are met.

    Sent to the interface so a locked step is greyed out with its reason rather
    than offered and then refused — the two must agree, and they agree because
    they are the same function.
    """
    return {aspect: blocking_reason(aspect, progress) is None for aspect in PREREQUISITES}
