"""What a counting sheet may be printed as, and when.

A counting sheet is three different documents depending on what is already
known, and offering the wrong one produces paper nobody can use:

* **sans références** — a blank grid. Only meaningful for a free-entry zone,
  where nothing is known in advance. Offering it for a zone whose article list
  *is* known would throw that list away and ask the counter to rewrite it.
* **sans quantités** — the article list, quantities left empty. This is the
  sheet handed to a counter. It cannot exist for a free-entry zone: there is no
  list to print.
* **avec quantités** — the record of what came back. It cannot exist before the
  count has started, because there is nothing to record.

The rules live here, in the pure layer, so the API, the screen and the tests all
read the same matrix instead of each re-deriving it.
"""

from __future__ import annotations

from enum import StrEnum

from .enums import CampaignStatus

__all__ = [
    "PrintMode",
    "BLANK_ROWS_PER_SECTION",
    "available_print_modes",
    "print_refusal",
]


class PrintMode(StrEnum):
    """The three documents a sheet can be printed as."""

    #: No pre-printed article at all — a grid of empty rows to be filled by hand.
    BLANK = "blank"
    #: The article list, with the quantity column left empty.
    LIST = "list"
    #: The article list with the counted quantities — the record.
    FILLED = "filled"


#: Extra empty rows appended per section on a sheet handed to a counter, so an
#: article nobody listed can be written down instead of being remembered. Sized
#: from what actually turns up: the line side is where surprises happen, the two
#: WIP sections much less so.
BLANK_ROWS_PER_SECTION = {"LINE_SIDE": 5, "WIP": 3, "WIP_OK": 2}


def available_print_modes(
    *, free_entry: bool, status: CampaignStatus
) -> tuple[PrintMode, ...]:
    """The modes offered for one zone, in the order they make sense.

    :param free_entry: the zone carries no pre-printed article list.
    :param status: the campaign's phase. Quantities only exist once counting has
        started, so the record is not offered before then.
    """
    modes = [PrintMode.BLANK] if free_entry else [PrintMode.LIST]
    if status is not CampaignStatus.PREPARATION:
        modes.append(PrintMode.FILLED)
    return tuple(modes)


def print_refusal(
    mode: PrintMode, *, free_entry: bool, status: CampaignStatus
) -> str | None:
    """Why *mode* is refused for this zone, or ``None`` when it is allowed.

    The message is the one shown to the user, so it says what is missing rather
    than that something is forbidden.
    """
    if mode in available_print_modes(free_entry=free_entry, status=status):
        return None
    if mode is PrintMode.FILLED:
        return (
            "Aucune quantité n'a encore été comptée : le relevé rempli n'existe "
            "qu'à partir de la phase de comptage."
        )
    if mode is PrintMode.BLANK:
        return (
            "Cette zone a une liste d'articles pré-imprimée. Imprimez-la plutôt "
            "sans quantités : la feuille vierge la ferait réécrire à la main."
        )
    return (
        "Cette zone est en saisie libre : elle n'a aucune liste d'articles à "
        "imprimer. Choisissez la feuille vierge et son nombre de lignes."
    )
