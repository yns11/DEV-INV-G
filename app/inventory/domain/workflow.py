"""Campaign, journal, sheet and zone state machines.

Everything the specification calls "gel" (freeze) is expressed here as a single
source of truth: :func:`mutability_of` answers "may I still edit X?" for every
editable aggregate, and every transition is guarded by explicit preconditions.

Encoding the rules once — instead of relying on people remembering not to touch
a tab — is what turns the Excel process into a controlled one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..errors import WorkflowError
from .enums import (
    CampaignStatus,
    ControlSeverity,
    JournalStatus,
    SheetPass,
    SheetStatus,
    ZoneStatus,
)
from .models import ArbitrationLine, ControlFinding, CountSheet

__all__ = [
    "Editable",
    "CAMPAIGN_TRANSITIONS",
    "mutability_of",
    "passes_for",
    "assert_campaign_transition",
    "campaign_transition_blockers",
    "next_sheet_status",
    "assert_sheet_transition",
    "derive_zone_status",
    "arbitration_required",
    "SHEET_TRANSITIONS",
]


# --------------------------------------------------------------------------- #
# Campaign lifecycle
# --------------------------------------------------------------------------- #

#: Allowed campaign transitions. The lifecycle is strictly forward: reopening a
#: closed campaign would break the "immutable dossier" guarantee, so it is not a
#: transition but a *new* campaign cloned from the old one.
CAMPAIGN_TRANSITIONS: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.PREPARATION: frozenset({CampaignStatus.COUNTING}),
    CampaignStatus.COUNTING: frozenset({CampaignStatus.ANALYSIS}),
    CampaignStatus.ANALYSIS: frozenset({CampaignStatus.CLOSED}),
    CampaignStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Editable:
    """What may be modified in a given campaign status.

    Read this as the machine-readable version of the "geler" instructions of
    the specification. The API layer consults it before every write, and the
    frontend consults the same payload to disable controls, so UI and backend
    can never disagree about what is frozen.
    """

    thresholds: bool
    items: bool
    boms: bool
    #: Locations/warehouses referential (built from the book stock).
    locations: bool
    book_stock: bool
    #: Creating new GENERIQUE zones and their printable sheets.
    zones: bool
    #: Counting journals and their lines.
    count_journals: bool
    #: Counting-sheet lines, statuses and arbitration.
    count_sheets: bool
    adjustments: bool
    #: Human analysis (cause assignment, comments) on variances.
    analysis: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "thresholds": self.thresholds,
            "items": self.items,
            "boms": self.boms,
            "locations": self.locations,
            "bookStock": self.book_stock,
            "zones": self.zones,
            "countJournals": self.count_journals,
            "countSheets": self.count_sheets,
            "adjustments": self.adjustments,
            "analysis": self.analysis,
        }


#: Per-status permission matrix.
#:
#: PREPARATION  thresholds + referentials + printable sheets are being built.
#: COUNTING     referentials are frozen, *except* that new GENERIQUE sheets may
#:              still be created (explicit requirement); the book stock is
#:              loaded then frozen; journals and sheets are the live objects.
#: ANALYSIS     everything from the counting phase is frozen; adjustments and
#:              human analysis are the only writable objects.
#: CLOSED       everything is frozen.
_EDITABILITY: dict[CampaignStatus, Editable] = {
    CampaignStatus.PREPARATION: Editable(
        thresholds=True,
        items=True,
        boms=True,
        locations=True,
        book_stock=False,
        zones=True,
        count_journals=False,
        count_sheets=True,
        adjustments=False,
        analysis=False,
    ),
    CampaignStatus.COUNTING: Editable(
        thresholds=False,
        items=False,
        boms=False,
        locations=True,
        book_stock=True,
        zones=True,
        count_journals=True,
        count_sheets=True,
        adjustments=False,
        analysis=False,
    ),
    CampaignStatus.ANALYSIS: Editable(
        thresholds=False,
        items=False,
        boms=False,
        locations=False,
        book_stock=False,
        zones=False,
        count_journals=False,
        count_sheets=False,
        adjustments=True,
        analysis=True,
    ),
    CampaignStatus.CLOSED: Editable(
        thresholds=False,
        items=False,
        boms=False,
        locations=False,
        book_stock=False,
        zones=False,
        count_journals=False,
        count_sheets=False,
        adjustments=False,
        analysis=False,
    ),
}


def mutability_of(status: CampaignStatus) -> Editable:
    """What is still writable while the campaign is in *status*."""
    return _EDITABILITY[status]


def assert_campaign_transition(
    current: CampaignStatus, target: CampaignStatus
) -> None:
    """Raise unless ``current → target`` is a legal transition.

    :raises WorkflowError: with the list of legal targets, so the API can render
        an actionable message rather than a generic 409.
    """
    allowed = CAMPAIGN_TRANSITIONS[current]
    if target not in allowed:
        raise WorkflowError(
            f"Transition {current} → {target} interdite.",
            current=str(current),
            target=str(target),
            allowed=sorted(str(s) for s in allowed),
        )


def campaign_transition_blockers(
    current: CampaignStatus,
    target: CampaignStatus,
    *,
    journal_statuses: Iterable[JournalStatus] = (),
    zone_statuses: Iterable[ZoneStatus] = (),
    book_stock_frozen: bool = False,
    blocking_controls: Sequence[ControlFinding] = (),
) -> list[ControlFinding]:
    """Business preconditions that must hold before *target* can be entered.

    Returns the findings that *block* the transition (empty list == go ahead).
    Kept separate from :func:`assert_campaign_transition` so the UI can display
    a live "what is missing to move on" panel without attempting the move.
    """
    blockers: list[ControlFinding] = []

    if target is CampaignStatus.COUNTING:
        # Nothing structural is required to start counting: the book stock is
        # loaded *during* the counting phase, right before the journals appear.
        pass

    if target is CampaignStatus.ANALYSIS:
        if not book_stock_frozen:
            blockers.append(
                ControlFinding(
                    code="BOOK_STOCK_NOT_FROZEN",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        "Le stock livre n'a pas été chargé puis gelé : "
                        "aucun écart ne peut être calculé."
                    ),
                    entity_type="campaign",
                )
            )
        pending = [s for s in journal_statuses if s not in (
            JournalStatus.POSTED, JournalStatus.BOOK_ENFORCED)]
        if pending:
            blockers.append(
                ControlFinding(
                    code="JOURNALS_NOT_POSTED",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{len(pending)} journal(aux) de comptage ne sont pas encore "
                        "postés ou forcés au stock livre."
                    ),
                    entity_type="count_journal",
                    context={"pending": len(pending)},
                )
            )
        open_zones = [z for z in zone_statuses if z is not ZoneStatus.DONE]
        if open_zones:
            blockers.append(
                ControlFinding(
                    code="ZONES_NOT_DONE",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{len(open_zones)} zone(s) GENERIQUE ne sont pas terminées "
                        "(comptage, encodage ou arbitrage en cours)."
                    ),
                    entity_type="zone",
                    context={"open": len(open_zones)},
                )
            )

    blockers.extend(
        f for f in blocking_controls if f.severity is ControlSeverity.BLOCKER
    )
    return blockers


# --------------------------------------------------------------------------- #
# Counting-sheet lifecycle
# --------------------------------------------------------------------------- #

#: PENDING → COUNTING → ENCODING → DONE, every step reversible one notch back so
#: an encoder can reopen a sheet to fix a typo (explicit requirement).
SHEET_TRANSITIONS: dict[SheetStatus, frozenset[SheetStatus]] = {
    SheetStatus.PENDING: frozenset({SheetStatus.COUNTING}),
    SheetStatus.COUNTING: frozenset({SheetStatus.ENCODING, SheetStatus.PENDING}),
    SheetStatus.ENCODING: frozenset({SheetStatus.DONE, SheetStatus.COUNTING}),
    SheetStatus.DONE: frozenset({SheetStatus.ENCODING}),
}

#: Sheet statuses that mean "pass 1 has produced usable data".
_PASS_1_READY = frozenset({SheetStatus.ENCODING, SheetStatus.DONE})


def passes_for(count: int) -> list[SheetPass]:
    """The sheets a zone requiring *count* independent counts must carry.

    Clamped to [1, 2] rather than trusted: the number reaches this function from
    a stored column, a campaign default and an HTTP payload, and a zone with
    zero sheets would be a zone nobody can count.

    >>> passes_for(1)
    [<SheetPass.PASS_1: 'PASS_1'>]
    >>> passes_for(2)
    [<SheetPass.PASS_1: 'PASS_1'>, <SheetPass.PASS_2: 'PASS_2'>]
    """
    order = [SheetPass.PASS_1, SheetPass.PASS_2]
    return order[: max(1, min(count, 2))]


def next_sheet_status(current: SheetStatus) -> SheetStatus | None:
    """The natural forward step, or ``None`` when already ``DONE``."""
    order = [
        SheetStatus.PENDING,
        SheetStatus.COUNTING,
        SheetStatus.ENCODING,
        SheetStatus.DONE,
    ]
    idx = order.index(current)
    return order[idx + 1] if idx + 1 < len(order) else None


def assert_sheet_transition(
    sheet: CountSheet,
    target: SheetStatus,
    *,
    pass_1_status: SheetStatus | None = None,
) -> None:
    """Guard a counting-sheet transition.

    Beyond the generic ordering, one cross-sheet rule from the specification is
    enforced: **pass 2 cannot start before pass 1 has been returned**. Two
    simultaneous counts are not independent counts — they are one count done
    twice by people who can see each other's sheet.

    :param pass_1_status: status of the pass-1 sheet of the same zone. Required
        when *sheet* is a pass-2 sheet moving to ``COUNTING``.
    """
    if target not in SHEET_TRANSITIONS[sheet.status]:
        raise WorkflowError(
            f"Transition de feuille {sheet.status} → {target} interdite.",
            current=str(sheet.status),
            target=str(target),
            allowed=sorted(str(s) for s in SHEET_TRANSITIONS[sheet.status]),
        )

    if (
        sheet.pass_no is SheetPass.PASS_2
        and target is SheetStatus.COUNTING
        and pass_1_status not in _PASS_1_READY
    ):
        raise WorkflowError(
            "Le comptage n°2 ne peut démarrer que lorsque le comptage n°1 est "
            "en cours d'encodage ou terminé.",
            pass_1_status=str(pass_1_status) if pass_1_status else None,
        )


# --------------------------------------------------------------------------- #
# Zone status derivation
# --------------------------------------------------------------------------- #

def arbitration_required(
    lines: Iterable[ArbitrationLine], *, tolerance: Decimal = Decimal("0")
) -> list[ArbitrationLine]:
    """Lines whose two passes disagree beyond *tolerance* and are unresolved.

    *tolerance* is a **relative** gap on the larger of the two passes; with the
    default of 0 any difference at all requires a human decision, which is the
    conservative WMS practice for a two-team blind count.
    """
    out: list[ArbitrationLine] = []
    for line in lines:
        if line.is_resolved:
            continue
        q1 = line.qty_pass_1 or Decimal(0)
        q2 = line.qty_pass_2 or Decimal(0)
        if q1 == q2:
            continue
        if tolerance > 0:
            base = max(abs(q1), abs(q2))
            if base > 0 and abs(q2 - q1) / base <= tolerance:
                continue
        out.append(line)
    return out


def derive_zone_status(
    sheets: Sequence[CountSheet],
    *,
    passes_required: int = 2,
    pending_arbitrations: int = 0,
) -> ZoneStatus:
    """Compute a zone's status from its sheets and its open arbitrations.

    The zone status is *derived*, never stored as an independent truth, so it
    can never drift from the sheets it summarises.

    Rules, in order:

    1. no sheet started            → ``PENDING``
    2. pass 1 not finished         → ``PASS_1_RUNNING``
    3. pass 2 not finished         → ``PASS_2_RUNNING``
    4. unresolved discrepancies    → ``ARBITRATION``
    5. otherwise                   → ``DONE``

    A single-pass zone (``passes_required=1``) skips steps 3 and 4.
    """
    by_pass: dict[SheetPass, CountSheet] = {}
    for sheet in sheets:
        # Keep the most advanced sheet if duplicates ever appear.
        current = by_pass.get(sheet.pass_no)
        if current is None or _sheet_rank(sheet.status) > _sheet_rank(current.status):
            by_pass[sheet.pass_no] = sheet

    p1 = by_pass.get(SheetPass.PASS_1)
    p2 = by_pass.get(SheetPass.PASS_2)

    if p1 is None or p1.status is SheetStatus.PENDING:
        if passes_required >= 2 and p2 is not None and p2.status is not SheetStatus.PENDING:
            # Defensive: pass 2 started without pass 1 — surface it as running.
            return ZoneStatus.PASS_2_RUNNING
        return ZoneStatus.PENDING

    if p1.status is not SheetStatus.DONE:
        return ZoneStatus.PASS_1_RUNNING

    if passes_required < 2:
        return ZoneStatus.DONE

    if p2 is None or p2.status is not SheetStatus.DONE:
        return ZoneStatus.PASS_2_RUNNING

    if pending_arbitrations > 0:
        return ZoneStatus.ARBITRATION

    return ZoneStatus.DONE


def _sheet_rank(status: SheetStatus) -> int:
    return {
        SheetStatus.PENDING: 0,
        SheetStatus.COUNTING: 1,
        SheetStatus.ENCODING: 2,
        SheetStatus.DONE: 3,
    }[status]
