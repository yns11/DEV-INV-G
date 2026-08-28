"""Campaign, journal and zone state machines.

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
    ZoneStatus,
)
from .models import ArbitrationLine, ControlFinding

__all__ = [
    "Editable",
    "CAMPAIGN_TRANSITIONS",
    "mutability_of",
    "passes_for",
    "assert_campaign_transition",
    "campaign_transition_blockers",
    "derive_zone_status",
    "zone_closure_blockers",
    "arbitration_required",
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
    #: Les comptages avancés : journaux ERP importés, périmètres déclarés, lots
    #: ouverts, clos, scellés, et les dérives tranchées le jour J.
    #:
    #: Aspect distinct de ``count_journals``, et pas par goût de la granularité.
    #: Les deux ouvrent et se ferment au même moment, mais ils n'attendent pas
    #: la même chose : un journal de comptage général se mesure contre le stock
    #: ERP chargé, un lot avancé porte sa propre référence dans la colonne
    #: « Stock ERP » de son journal. Partager l'aspect revenait à imposer au
    #: comptage avancé le prérequis de l'étape qu'il précède — voir
    #: :mod:`inventory.domain.sequence`.
    early_counts: bool
    #: The *structure* of a counting sheet: which articles it lists. Prepared
    #: before inventory day, which is the whole point of preparing paper.
    count_sheets: bool
    #: The *quantities* written on those sheets, and everything downstream of
    #: them — arbitration, consolidation, WIP reclassification. Separate from
    #: the structure because a sheet was fillable in preparation, which produced
    #: counts of a campaign that had not started.
    count_entries: bool
    adjustments: bool
    #: Human analysis (cause assignment, comments) on variances.
    analysis: bool
    #: Reading and refreshing the frozen backflush variance. Open for as long as
    #: the campaign is: the gold table is rebuilt nightly, so a past week's
    #: figure can move, and re-reading it is a legitimate correction. Closing the
    #: campaign is what makes it final — and it has to, or a variance a
    #: controller signed off could still change afterwards.
    backflush: bool = True
    #: The stock-flow reconciliation against an earlier campaign, and the three
    #: quantities it is given. Open in *every* status, closure included — the one
    #: aspect that is.
    #:
    #: The rule differs from the backflush above, and deliberately. The backflush
    #: enters the campaign's own variance, so a controller who signed off a
    #: figure must be sure it cannot move afterwards. This does not: it writes
    #: only into its own three tables, and reads the counted stock of two
    #: campaigns without touching either. Nothing it stores changes a variance,
    #: an IRA or a total anybody validated.
    #:
    #: And the useful moment is precisely the closed one. Comparing two
    #: inventories through the flows of the period between them is something one
    #: does once both are finished; freezing it at closure forbade the main use
    #: of the feature, which is how this was found — a chip that would not
    #: click, on a campaign where the comparison was the whole point.
    stock_flow: bool = True
    #: Les paramètres de campagne autres que les seuils — aujourd'hui le seul
    #: réglage « Accepter des formules dans les comptages ».
    #:
    #: Ouvert tant qu'on saisit, fermé ensuite, et **pas au même moment que les
    #: seuils**. Les seuils gèlent à l'entrée en comptage parce qu'ils décident
    #: ce qui sera signalé comme exception : les changer en cours de route
    #: changerait la liste sous les yeux de qui la traite. Ce réglage-là décide
    #: seulement de ce qu'un champ de saisie accepte, et le besoin apparaît
    #: précisément le jour de l'inventaire, devant la première feuille qui porte
    #: un calcul. Le geler avec les seuils l'aurait rendu inatteignable au seul
    #: moment où il sert.
    settings: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "thresholds": self.thresholds,
            "items": self.items,
            "boms": self.boms,
            "locations": self.locations,
            "bookStock": self.book_stock,
            "zones": self.zones,
            "countJournals": self.count_journals,
            "earlyCounts": self.early_counts,
            "countSheets": self.count_sheets,
            "countEntries": self.count_entries,
            "adjustments": self.adjustments,
            "analysis": self.analysis,
            "backflush": self.backflush,
            "stockFlow": self.stock_flow,
            "settings": self.settings,
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
        settings=True,
        thresholds=True,
        items=True,
        boms=True,
        locations=True,
        book_stock=False,
        zones=True,
        count_journals=False,
        early_counts=False,
        count_sheets=True,
        count_entries=False,
        adjustments=False,
        analysis=False,
    ),
    CampaignStatus.COUNTING: Editable(
        settings=True,
        thresholds=False,
        items=False,
        boms=False,
        locations=True,
        book_stock=True,
        zones=True,
        count_journals=True,
        early_counts=True,
        count_sheets=True,
        count_entries=True,
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
        early_counts=False,
        count_sheets=False,
        count_entries=False,
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
        early_counts=False,
        count_sheets=False,
        count_entries=False,
        adjustments=False,
        analysis=False,
        backflush=False,
        # Voir le champ : la réconciliation n'écrit rien qui entre dans les
        # chiffres de la campagne, et c'est une fois close qu'on la fait.
        stock_flow=True,
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
    unexplained_material: int = 0,
    unresolved_drift: int = 0,
    publication_done: bool = True,
) -> list[ControlFinding]:
    """Business preconditions that must hold before *target* can be entered.

    Returns the findings that *block* the transition (empty list == go ahead).
    Kept separate from :func:`assert_campaign_transition` so the UI can display
    a live "what is missing to move on" panel without attempting the move.

    Les lignes refusées à l'import n'y figurent pas
    ----------------------------------------------
    Elles ont bloqué la clôture un temps. C'était trop : un chargement laisse
    des lignes refusées pour des raisons que l'exploitant connaît et assume —
    un article sorti du référentiel, une ligne d'export corrompue à la source,
    un fichier partiel volontairement chargé. Exiger zéro refus rendait la
    clôture impossible sur un manque que personne n'avait le pouvoir de
    combler, et poussait à recharger un fichier pour faire taire un point plutôt
    que pour corriger quelque chose.

    Le constat reste affiché — la liste de contrôle le porte en « à regarder »,
    voir :mod:`inventory.domain.closure`. Ce qui change est le pouvoir d'arrêt,
    pas la visibilité.
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
                        "Le stock ERP n'a pas été chargé puis gelé : "
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
                        "postés ou forcés au stock ERP."
                    ),
                    entity_type="count_journal",
                    context={"pending": len(pending)},
                )
            )
        if unresolved_drift:
            blockers.append(
                ControlFinding(
                    code="EARLY_COUNT_DRIFT_UNRESOLVED",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{unresolved_drift} dérive(s) matérielle(s) sur des "
                        "emplacements scellés n'ont pas d'issue. Le stock ERP du "
                        "jour J ne dit pas la même chose que le physique posté "
                        "au précomptage : décidez, pour chacune, laquelle fait "
                        "foi — conserver le comptage avancé, ou recompter."
                    ),
                    entity_type="early_count_drift",
                    context={"unresolved": unresolved_drift},
                )
            )
        open_zones = [z for z in zone_statuses if z is not ZoneStatus.DONE]
        if open_zones:
            blockers.append(
                ControlFinding(
                    code="ZONES_NOT_DONE",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{len(open_zones)} zone(s) GENERIQUE ne sont pas "
                        "terminées : ouvrez-les et déclarez-les finies."
                    ),
                    entity_type="zone",
                    context={"open": len(open_zones)},
                )
            )

    if target is CampaignStatus.CLOSED:
        # La clôture est le seul geste irréversible du parcours : après elle,
        # plus rien ne se corrige. Elle n'exigeait pourtant rien de particulier
        # — le paramètre `blocking_controls` existait, personne ne le
        # remplissait — si bien qu'une campagne se clôturait avec ses écarts
        # matériels sans explication et ses imports amputés.
        if unexplained_material:
            blockers.append(
                ControlFinding(
                    code="MATERIAL_VARIANCES_UNEXPLAINED",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{unexplained_material} écart(s) matériel(s) n'ont ni "
                        "cause assignée ni acceptation explicite. Clôturer "
                        "figerait un écart que personne n'a expliqué, et c'est "
                        "précisément ce qu'un contrôle demandera six mois plus "
                        "tard."
                    ),
                    entity_type="variance_analysis",
                    context={"unexplained": unexplained_material},
                )
            )
        if not publication_done:
            blockers.append(
                ControlFinding(
                    code="PUBLICATION_NOT_DONE",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        "L'archive Delta de cette campagne n'a pas été publiée. "
                        "Clôturer maintenant scellerait un dossier dont la copie "
                        "opposable n'existe pas : la base opérationnelle est "
                        "vivante, l'archive est ce qui reste."
                    ),
                    entity_type="campaign",
                )
            )

    blockers.extend(
        f for f in blocking_controls if f.severity is ControlSeverity.BLOCKER
    )
    return blockers


# --------------------------------------------------------------------------- #
# Counting sheets
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Zone status
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


def derive_zone_status(*, counted_lines: int, closed: bool) -> ZoneStatus:
    """Où en est une zone : trois états, dont deux se déduisent.

    * ``DONE``        un humain l'a déclarée terminée ;
    * ``PENDING``     sinon, et aucune quantité n'a encore été relevée ;
    * ``IN_PROGRESS`` sinon.

    Les deux derniers se lisent dans les quantités elles-mêmes, donc ils ne
    peuvent pas mentir : il n'y a rien à faire avancer à la main, et une zone
    dont on saisit la première quantité passe « en cours » du seul fait qu'on
    l'a saisie.

    ``DONE`` reste une décision. La déduire de « toutes les lignes comptées »
    paraît plus pur et ne l'est pas : une ligne qu'on ne peut légitimement pas
    compter — l'article a disparu, l'emplacement est inaccessible — laisserait
    la zone ouverte pour toujours, et avec elle le passage de la campagne en
    analyse, qui exige que toutes les zones soient terminées.
    """
    if closed:
        return ZoneStatus.DONE
    return ZoneStatus.IN_PROGRESS if counted_lines > 0 else ZoneStatus.PENDING


def zone_closure_blockers(*, pending_arbitrations: int) -> str:
    """Ce qui empêche de déclarer une zone terminée, en clair. Vide = rien.

    Un écart non tranché entre les deux comptages est le seul refus : la
    consolidation ne saurait pas quelle quantité retenir, et fermer la zone
    reviendrait à promettre un chiffre qui n'existe pas encore.
    """
    if pending_arbitrations > 0:
        return (
            f"{pending_arbitrations} écart(s) entre les deux comptages ne sont "
            "pas tranchés. Arbitrez-les avant de terminer la zone : sans "
            "décision, la consolidation ne sait pas quelle quantité retenir."
        )
    return ""
