"""Control engine — the guard-rails the Excel process never had.

Each control is a small, independently testable function returning
:class:`~inventory.domain.models.ControlFinding` objects. They are cheap enough
to run on every write, which is what makes the "exception first" principle of
the specification real: the user is shown what is wrong *while* they work, not
in a post-mortem three weeks later.

Severity contract
-----------------
``BLOCKER``  prevents a phase transition (and, for some, prevents posting).
``WARNING``  must be looked at but does not stop the campaign.
``INFO``     contextual, useful in the audit trail.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .bom import BomIndex
from .enums import (
    ControlSeverity,
    ItemType,
    JournalStatus,
    LocationStatus,
    SheetPass,
)
from .models import (
    BomLink,
    BookStockLine,
    Campaign,
    ControlFinding,
    CountJournal,
    CountJournalLine,
    CountSheet,
    CountSheetLine,
    FindingGroup,
    Item,
    Location,
    LocationKey,
    VarianceLine,
    Zone,
)
from .variance import is_material

__all__ = [
    "CONTROL_LABELS",
    "group_findings",
    "check_items",
    "check_referentials",
    "check_book_stock",
    "check_stock_import",
    "check_journals",
    "check_variances",
    "check_zones",
    "run_all_controls",
    "summarise",
]


# --------------------------------------------------------------------------- #
# Referentials
# --------------------------------------------------------------------------- #

def check_referentials(
    *,
    items: Mapping[str, Item],
    bom_links: Sequence[BomLink],
    bom_index: BomIndex | None = None,
) -> list[ControlFinding]:
    """Structural integrity of the article and BOM referentials."""
    findings: list[ControlFinding] = []

    # -- BOM edges pointing at unknown articles -------------------------------
    missing_parents: set[str] = set()
    missing_children: set[str] = set()
    for link in bom_links:
        if link.parent_item not in items:
            missing_parents.add(link.parent_item)
        if link.child_item not in items:
            missing_children.add(link.child_item)

    for parent in sorted(missing_parents):
        findings.append(
            ControlFinding(
                code="BOM_PARENT_UNKNOWN",
                severity=ControlSeverity.WARNING,
                message=(
                    f"La nomenclature référence l'assemblage {parent}, absent du "
                    "référentiel articles."
                ),
                entity_type="bom",
                item_number=parent,
            )
        )
    for child in sorted(missing_children):
        findings.append(
            ControlFinding(
                code="BOM_CHILD_UNKNOWN",
                severity=ControlSeverity.WARNING,
                message=(
                    f"La nomenclature référence le composant {child}, absent du "
                    "référentiel articles."
                ),
                entity_type="bom",
                item_number=child,
            )
        )

    # -- cycles ----------------------------------------------------------------
    index = bom_index or BomIndex(bom_links)
    for cycle in index.find_cycles():
        findings.append(
            ControlFinding(
                code="BOM_CYCLE",
                severity=ControlSeverity.BLOCKER,
                message="Cycle de nomenclature : " + " → ".join(cycle),
                entity_type="bom",
                context={"cycle": cycle},
            )
        )

    # -- assemblies without a usable structure --------------------------------
    #
    # Two different situations, and they used to produce the same alert. An
    # assembly the ERP has no recipe for at all is a referential gap somebody
    # must fill. One whose recipes are all retired is a decision somebody
    # already made — it still cannot be exploded, but the answer is to reinstate
    # a version, not to write one. Reporting both as "aucune nomenclature"
    # buried the first under the second.
    for item in items.values():
        if not item.is_assembly or item.excluded_everywhere:
            continue
        # « Ignoré en nomenclature » est une décision, pas un oubli : l'article
        # ne sera jamais éclaté, donc lui réclamer une structure revient à
        # signaler comme manquant ce que quelqu'un a explicitement retiré.
        if item.excluded_from_bom:
            continue
        if index.has_bom(item.item_number):
            continue
        kind = "produit fini" if item.item_type is ItemType.FINISHED else "semi-fini"
        if index.retired_only(item.item_number):
            findings.append(
                ControlFinding(
                    code="ASSEMBLY_BOM_RETIRED",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{item.item_number} ({kind}) n'a que des versions de "
                        "nomenclature inactives : il ne pourra pas être éclaté "
                        "s'il est compté en WIP."
                    ),
                    entity_type="item",
                    item_number=item.item_number,
                )
            )
            continue
        findings.append(
            ControlFinding(
                code="ASSEMBLY_WITHOUT_BOM",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{item.item_number} est déclaré {kind} mais n'a aucune "
                    "nomenclature : il ne pourra pas être éclaté s'il est "
                    "compté en WIP."
                ),
                entity_type="item",
                item_number=item.item_number,
            )
        )

    return findings


def check_items(*, items: Mapping[str, Item]) -> list[ControlFinding]:
    """Defects of the article referential itself.

    Kept apart from :func:`check_referentials`, which answers "can a bill of
    materials be exploded?". A missing standard price has nothing to do with a
    structure — it was showing up under « santé des nomenclatures », where
    whoever is repairing a BOM cannot act on it and whoever owns prices never
    looks.
    """
    findings: list[ControlFinding] = []
    unpriced = sorted(
        i.item_number
        for i in items.values()
        if i.std_price == 0 and not i.excluded_everywhere
    )
    # Un constat par article, et non un seul qui en compterait cent cinq : c'est
    # la liste elle-même qu'on va relire pour aller chercher les prix, et un
    # compte sans les références ne dit à personne par où commencer.
    for item_number in unpriced:
        findings.append(
            ControlFinding(
                code="ITEMS_WITHOUT_PRICE",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{item_number} a un prix standard nul : ses écarts seront "
                    "valorisés à 0 € et disparaîtront des analyses en valeur."
                ),
                entity_type="item",
                item_number=item_number,
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Book stock snapshot
# --------------------------------------------------------------------------- #

def check_book_stock(
    *,
    book_stock: Sequence[BookStockLine],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
) -> list[ControlFinding]:
    """Coherence of the frozen ERP snapshot."""
    findings: list[ControlFinding] = []

    if not book_stock:
        return [
            ControlFinding(
                code="BOOK_STOCK_EMPTY",
                severity=ControlSeverity.BLOCKER,
                message="Le stock ERP est vide : aucun écart ne pourra être calculé.",
                entity_type="book_stock",
            )
        ]

    unknown_items = sorted({
        line.item_number for line in book_stock if line.item_number not in items
    })
    for item_number in unknown_items:
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_UNKNOWN_ITEM",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{item_number} porte du stock ERP mais est absent du "
                    "référentiel de la campagne."
                ),
                entity_type="book_stock",
                item_number=item_number,
            )
        )

    # Duplicate (item, warehouse, location) triples would double-count the book.
    duplicates = [
        key
        for key, n in Counter(
            (l.item_number, l.warehouse_id, l.location_id) for l in book_stock
        ).items()
        if n > 1
    ]
    for item_number, warehouse_id, location_id in sorted(duplicates):
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_DUPLICATE_KEY",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{item_number} apparaît plusieurs fois en {warehouse_id} / "
                    f"{location_id} dans le stock ERP ; les quantités ont été sommées."
                ),
                entity_type="book_stock",
                item_number=item_number,
                warehouse_id=warehouse_id,
                location_id=location_id,
            )
        )

    # Unit drift between the snapshot and the referential silently changes the
    # meaning of every quantity compared afterwards.
    for line in book_stock:
        item = items.get(line.item_number)
        if item and line.unit and item.unit and line.unit != item.unit:
            findings.append(
                ControlFinding(
                    code="UNIT_MISMATCH",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{line.item_number} : unité {line.unit} dans le stock ERP "
                        f"contre {item.unit} dans le référentiel."
                    ),
                    entity_type="book_stock",
                    item_number=line.item_number,
                    warehouse_id=line.warehouse_id,
                    location_id=line.location_id,
                    context={"snapshot": line.unit, "referential": item.unit},
                )
            )

    negatives = [l for l in book_stock if l.qty < 0]
    if negatives:
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_NEGATIVE",
                severity=ControlSeverity.INFO,
                message=(
                    f"{len(negatives)} ligne(s) de stock ERP sont négatives — "
                    "généralement une consommation antérieure à la réception."
                ),
                entity_type="book_stock",
                context={"count": len(negatives)},
            )
        )

    if locations:
        orphans = sorted({
            f"{l.warehouse_id} / {l.location_id}"
            for l in book_stock
            if LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
            not in locations
        })
        if orphans:
            findings.append(
                ControlFinding(
                    code="BOOK_STOCK_UNKNOWN_LOCATION",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"{len(orphans)} emplacement(s) du stock ERP ne figurent pas "
                        "dans le référentiel emplacements."
                    ),
                    entity_type="location",
                    context={"sample": orphans[:20]},
                )
            )
    return findings


def check_stock_import(*, report: Mapping[str, Any] | None) -> list[ControlFinding]:
    """Ce que le dernier chargement de stock ERP n'a **pas** chargé.

    Un chargement de stock remplace l'ensemble existant : une seule ligne
    refusée annulait donc toute l'écriture. Les deux motifs de refus les plus
    fréquents ne venaient pourtant pas du fichier — une référence absente du
    référentiel, un article exclu du périmètre — si bien que plus le dossier
    était en retard, moins le stock était chargeable, et le seul geste proposé
    (« corrigez le fichier ») portait sur le seul document qui n'avait rien de
    faux.

    Ces lignes sont désormais écartées, et le chargement aboutit. Écarter en
    silence serait la même faute sous un autre nom : ce qui n'est pas entré doit
    se lire quelque part, et ce quelque part doit survivre au rapport d'import,
    qui disparaît dès qu'on quitte l'écran. C'est ici.

    **Le rapport du lot est la source, et sa durée de vie est la bonne.** Il est
    remplacé au chargement suivant : recharger après avoir complété le
    référentiel fait disparaître le constat, ce qui est exactement le
    comportement attendu d'un contrôle — il décrit l'état actuel, pas
    l'historique des tentatives.

    ``report`` est le rapport du **dernier** chargement de stock, ou ``None``
    quand il n'y en a jamais eu. Un rapport antérieur au découpage de ce champ
    ne porte aucune des clés lues ici : il ne produit alors aucun constat plutôt
    que de faire échouer la lecture des contrôles.
    """
    if not report:
        return []
    return _left_out(
        report,
        prefix="unknown",
        code="BOOK_STOCK_UNKNOWN_ITEM",
        severity=ControlSeverity.WARNING,
        why=(
            "est absent du référentiel de la campagne : son stock ERP n'a pas "
            "été chargé, et aucun écart ne sera calculé sur cette référence. "
            "Complétez le référentiel articles puis rechargez le stock."
        ),
    ) + _left_out(
        report,
        prefix="outOfScope",
        code="BOOK_STOCK_OUT_OF_SCOPE",
        severity=ControlSeverity.INFO,
        why=(
            "est exclu du périmètre : son stock ERP n'a pas été chargé, ce qui "
            "est le comportement voulu. Levez l'exclusion sur la grille Articles "
            "puis rechargez le stock pour l'inventorier."
        ),
    )


def _left_out(
    report: Mapping[str, Any],
    *,
    prefix: str,
    code: str,
    severity: ControlSeverity,
    why: str,
) -> list[ControlFinding]:
    """Un constat par référence écartée, plus le compte de ce qui n'est pas nommé.

    Les deux motifs — référence inconnue, article exclu — se lisent dans le
    rapport sous le même triplet de clés, à leur préfixe près. Les deux appelants
    nomment leur code en toutes lettres : un code construit à l'exécution
    n'apparaîtrait dans aucune relecture du fichier, et c'est par cette relecture
    que la suite vérifie qu'aucun constat ne s'affiche sans titre français.
    """
    named = [str(ref) for ref in report.get(f"{prefix}ItemNumbers") or []]
    total = int(report.get(f"{prefix}Items") or 0)
    lines = int(report.get(f"{prefix}Lines") or 0)
    if not total and not named:
        return []

    findings = [
        ControlFinding(
            code=code,
            severity=severity,
            message=f"{item_number} {why}",
            entity_type="book_stock",
            item_number=item_number,
        )
        for item_number in named
    ]

    # Le rapport ne nomme qu'un échantillon : un fichier ERP chargé contre un
    # référentiel vide en produirait des dizaines de milliers, et le rapport
    # deviendrait une copie du fichier. Le total, lui, n'est pas tronqué — et
    # sans cette ligne, une liste tronquée se lirait comme complète.
    if total > len(named):
        findings.append(
            ControlFinding(
                code=code,
                severity=severity,
                message=(
                    f"{total} références au total dans ce cas, sur {lines} "
                    f"ligne(s) de stock ; les {len(named)} premières sont "
                    "détaillées ci-dessus."
                ),
                entity_type="book_stock",
                context={"total": total, "named": len(named), "lines": lines},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Counting journals
# --------------------------------------------------------------------------- #

def check_journals(
    *,
    journals: Sequence[CountJournal],
    lines_by_journal: Mapping[str, Sequence[CountJournalLine]],
    items: Mapping[str, Item],
    locations: Mapping[LocationKey, Location] | None = None,
) -> list[ControlFinding]:
    """Coherence of the counting journals and their lines."""
    findings: list[ControlFinding] = []

    seen_keys: Counter[tuple[str, str]] = Counter()
    for journal in journals:
        seen_keys[(journal.warehouse_id, journal.location_id)] += 1
        lines = lines_by_journal.get(journal.id, ())

        # A journal on a disabled location must not exist at all.
        if locations is not None:
            loc = locations.get(journal.key)
            if loc is not None and loc.status is LocationStatus.DISABLED:
                findings.append(
                    ControlFinding(
                        code="JOURNAL_ON_DISABLED_LOCATION",
                        severity=ControlSeverity.BLOCKER,
                        message=(
                            f"Le journal {journal.key} porte sur un emplacement "
                            "désactivé : il doit être supprimé ou l'emplacement "
                            "réactivé."
                        ),
                        entity_type="count_journal",
                        entity_id=journal.id,
                        warehouse_id=journal.warehouse_id,
                        location_id=journal.location_id,
                    )
                )

        if journal.status is JournalStatus.POSTED and not lines:
            findings.append(
                ControlFinding(
                    code="POSTED_JOURNAL_EMPTY",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"Le journal {journal.key} est posté mais ne contient aucune "
                        "ligne : l'emplacement sera compté à zéro."
                    ),
                    entity_type="count_journal",
                    entity_id=journal.id,
                    warehouse_id=journal.warehouse_id,
                    location_id=journal.location_id,
                )
            )

        item_counts: Counter[str] = Counter()
        for line in lines:
            item_counts[line.item_number] += 1
            item = items.get(line.item_number)
            if item is None:
                findings.append(
                    ControlFinding(
                        code="JOURNAL_UNKNOWN_ITEM",
                        severity=ControlSeverity.WARNING,
                        message=(
                            f"{line.item_number} est compté dans {journal.key} mais "
                            "absent du référentiel articles."
                        ),
                        entity_type="count_journal_line",
                        entity_id=line.id,
                        item_number=line.item_number,
                        warehouse_id=journal.warehouse_id,
                        location_id=journal.location_id,
                    )
                )
            else:
                if item.excluded_everywhere:
                    findings.append(
                        ControlFinding(
                            code="EXCLUDED_ITEM_COUNTED",
                            severity=ControlSeverity.WARNING,
                            message=(
                                f"{line.item_number} est exclu du périmètre mais "
                                f"apparaît dans le journal {journal.key}."
                            ),
                            entity_type="count_journal_line",
                            entity_id=line.id,
                            item_number=line.item_number,
                        )
                    )
                if line.unit and item.unit and line.unit != item.unit:
                    findings.append(
                        ControlFinding(
                            code="UNIT_MISMATCH",
                            severity=ControlSeverity.WARNING,
                            message=(
                                f"{line.item_number} : unité comptée {line.unit} "
                                f"contre {item.unit} au référentiel."
                            ),
                            entity_type="count_journal_line",
                            entity_id=line.id,
                            item_number=line.item_number,
                            context={"counted": line.unit, "referential": item.unit},
                        )
                    )
            if line.qty < 0:
                findings.append(
                    ControlFinding(
                        code="NEGATIVE_COUNT",
                        severity=ControlSeverity.BLOCKER,
                        message=(
                            f"{line.item_number} est compté négativement "
                            f"({line.qty}) dans {journal.key} : un comptage physique "
                            "ne peut pas être négatif."
                        ),
                        entity_type="count_journal_line",
                        entity_id=line.id,
                        item_number=line.item_number,
                    )
                )

        for item_number, n in item_counts.items():
            if n > 1:
                findings.append(
                    ControlFinding(
                        code="DUPLICATE_COUNT_LINE",
                        severity=ControlSeverity.WARNING,
                        message=(
                            f"{item_number} apparaît {n} fois dans le journal "
                            f"{journal.key} ; les quantités seront sommées."
                        ),
                        entity_type="count_journal",
                        entity_id=journal.id,
                        item_number=item_number,
                        context={"occurrences": n},
                    )
                )

    for (wh, loc), n in seen_keys.items():
        if n > 1:
            findings.append(
                ControlFinding(
                    code="DUPLICATE_JOURNAL",
                    severity=ControlSeverity.BLOCKER,
                    message=(
                        f"{n} journaux existent pour {wh} / {loc} ; il doit y en avoir "
                        "exactement un par emplacement actif."
                    ),
                    entity_type="count_journal",
                    warehouse_id=wh,
                    location_id=loc,
                    context={"occurrences": n},
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# Variances
# --------------------------------------------------------------------------- #

def check_variances(
    *, campaign: Campaign, variances: Sequence[VarianceLine]
) -> list[ControlFinding]:
    """Findings derived from the reconciled variances."""
    findings: list[ControlFinding] = []

    # Un constat par couple, et non un seul qui en compterait dix-sept. Le
    # nombre dit l'ampleur, mais ce qu'on va faire ensuite — aller voir dans
    # l'allée si les palettes y sont encore — demande la liste, article et
    # emplacement. Le regroupement les ramène à une ligne à l'écran de toute
    # façon, et « voir plus » les rouvre toutes.
    for line in variances:
        if not (line.book_only and line.book_qty != 0):
            continue
        findings.append(
            ControlFinding(
                code="BOOK_STOCK_NOT_COUNTED",
                severity=ControlSeverity.BLOCKER,
                message=(
                    f"{line.item_number} porte {line.book_qty} en stock ERP "
                    f"({line.book_value:,.0f} €) en {line.warehouse_id} / "
                    f"{line.location_id} sans aucun comptage. Sera soldé à zéro si "
                    "l'inventaire est clôturé en l'état."
                ),
                entity_type="variance",
                item_number=line.item_number,
                warehouse_id=line.warehouse_id,
                location_id=line.location_id,
                context={"bookQty": str(line.book_qty),
                         "bookValue": str(line.book_value)},
            )
        )

    for line in variances:
        if not (line.counted_only and line.counted_qty != 0):
            continue
        findings.append(
            ControlFinding(
                code="COUNTED_WITHOUT_BOOK_STOCK",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{line.item_number} a été compté à {line.counted_qty} en "
                    f"{line.warehouse_id} / {line.location_id} alors que l'ERP n'y "
                    "voyait aucun stock."
                ),
                entity_type="variance",
                item_number=line.item_number,
                warehouse_id=line.warehouse_id,
                location_id=line.location_id,
                context={"countedQty": str(line.counted_qty)},
            )
        )

    for line in variances:
        thresholds = campaign.threshold_for(line.item_type)
        if not is_material(line, thresholds):
            continue
        findings.append(
            ControlFinding(
                code="MATERIAL_VARIANCE",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{line.item_number} : écart de {line.variance_qty} {line.unit} "
                    f"({line.variance_value:,.0f} €) au-delà des seuils "
                    f"{line.item_type}."
                ),
                entity_type="variance",
                item_number=line.item_number,
                warehouse_id=line.warehouse_id,
                location_id=line.location_id,
                context={
                    "varianceQty": str(line.variance_qty),
                    "varianceValue": str(line.variance_value),
                    "bookQty": str(line.book_qty),
                },
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# GENERIQUE zones
# --------------------------------------------------------------------------- #

def check_zones(
    *,
    zones: Sequence[Zone],
    sheets: Sequence[CountSheet],
    lines_by_sheet: Mapping[str, Sequence[CountSheetLine]] | None = None,
    items: Mapping[str, Item] | None = None,
) -> list[ControlFinding]:
    """Preparation controls on the GENERIQUE zones.

    Trois défauts. Les deux premiers sont bon marché à corriger en préparation
    et chers à découvrir le matin du comptage :

    * a zone whose sheets carry no pre-printed article list — **unless** it is
      declared as a free-entry sheet. That distinction is the whole reason
      :attr:`Zone.free_entry` exists: a deliberately blank sheet and a sheet
      somebody forgot to prepare look exactly alike otherwise, and flagging both
      teaches people to ignore the warning;
    * a zone missing one of the sheets its own ``passes`` requires — a second
      counter with no sheet is a second count that will not happen.

    Le troisième se découvre après coup : une **quantité comptée sur un article
    exclu du périmètre**. Elle ne produit aucun écart — un article exclu est
    hors du calcul, c'est ce que l'exclusion veut dire — et c'est précisément
    pour cela qu'elle doit se lire quelque part. Sans ce constat, quelqu'un
    aurait compté une zone et ses quantités n'apparaîtraient nulle part, sans un
    mot : ou bien l'exclusion est une erreur et il faut la lever, ou bien le
    comptage l'est et il faut le retirer, mais les deux se décident en le
    sachant.

    Le contrôle jumeau existait déjà pour les **journaux** (voir
    :func:`check_journals`) ; les feuilles GENERIQUE ne l'avaient pas, alors
    que ce sont elles qu'on remplit à la main et donc elles qui portent le plus
    volontiers un article que le référentiel a mis hors périmètre depuis.

    :param items: le référentiel de la campagne. Sans lui, seuls les deux
        premiers contrôles tournent — un appelant qui n'a pas chargé les
        articles ne doit pas payer leur chargement pour un troisième constat.
    """
    lines_by_sheet = lines_by_sheet or {}
    items = items or {}
    findings: list[ControlFinding] = []
    sheets_by_zone: dict[str, list[CountSheet]] = defaultdict(list)
    for sheet in sheets:
        sheets_by_zone[sheet.zone_id].append(sheet)

    # Une ligne par article, pas par feuille : le même article exclu compté sur
    # les deux passages d'une zone est un seul fait à trancher, et l'annoncer
    # deux fois pousse le reste des constats hors de l'écran.
    zone_of = {s.id: s.zone_id for s in sheets}
    excluded_counted: dict[str, set[str]] = defaultdict(set)
    for sheet_id, lines in lines_by_sheet.items():
        for line in lines:
            item = items.get(line.item_number)
            if item is None or not item.excluded_everywhere or not line.is_counted:
                continue
            excluded_counted[line.item_number].add(zone_of.get(sheet_id, ""))

    zone_code = {z.id: z.code for z in zones}
    for item_number, zone_ids in sorted(excluded_counted.items()):
        where = ", ".join(sorted(zone_code.get(z, z) for z in zone_ids if z))
        findings.append(
            ControlFinding(
                code="EXCLUDED_ITEM_COUNTED",
                severity=ControlSeverity.WARNING,
                message=(
                    f"{item_number} est exclu du périmètre mais a été compté"
                    + (f" en zone {where}" if where else "")
                    + " : sa quantité n'entre dans aucun écart. Levez "
                    "l'exclusion sur la grille Articles si l'article doit être "
                    "inventorié, ou retirez la ligne de la feuille."
                ),
                entity_type="count_sheet_line",
                item_number=item_number,
                context={"zones": sorted(z for z in zone_ids if z)},
            )
        )

    for zone in zones:
        zone_sheets = sheets_by_zone.get(zone.id, [])
        expected = {SheetPass.PASS_1, SheetPass.PASS_2} if zone.passes >= 2 else {
            SheetPass.PASS_1
        }
        missing = expected - {s.pass_no for s in zone_sheets}
        if missing:
            findings.append(
                ControlFinding(
                    code="ZONE_MISSING_SHEET",
                    severity=ControlSeverity.WARNING,
                    message=(
                        f"Zone {zone.code} : la feuille de comptage "
                        f"{', '.join(sorted(str(p) for p in missing))} manque alors "
                        f"que la zone demande {zone.passes} comptage(s)."
                    ),
                    entity_type="zone",
                    entity_id=zone.id,
                    context={"passes": zone.passes},
                )
            )

        if zone.free_entry:
            continue
        if any(lines_by_sheet.get(s.id) for s in zone_sheets):
            continue
        findings.append(
            ControlFinding(
                code="ZONE_WITHOUT_LINES",
                severity=ControlSeverity.WARNING,
                message=(
                    f"Zone {zone.code} : aucune ligne pré-imprimée. Chargez sa "
                    "liste d'articles, ou déclarez-la en saisie libre si le "
                    "compteur doit écrire ce qu'il trouve."
                ),
                entity_type="zone",
                entity_id=zone.id,
                context={"remedy": "import_count_sheets_or_free_entry"},
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_all_controls(
    *,
    campaign: Campaign,
    items: Mapping[str, Item] | None = None,
    bom_links: Sequence[BomLink] = (),
    bom_index: BomIndex | None = None,
    book_stock: Sequence[BookStockLine] = (),
    locations: Mapping[LocationKey, Location] | None = None,
    journals: Sequence[CountJournal] = (),
    lines_by_journal: Mapping[str, Sequence[CountJournalLine]] | None = None,
    zones: Sequence[Zone] = (),
    sheets: Sequence[CountSheet] = (),
    lines_by_sheet: Mapping[str, Sequence[CountSheetLine]] | None = None,
    variances: Sequence[VarianceLine] = (),
) -> list[ControlFinding]:
    """Run every control applicable to the data that was supplied.

    Each block is optional: passing only what a screen has loaded keeps the call
    cheap, and the caller decides how much context to pay for.
    """
    items = items or {}
    findings: list[ControlFinding] = []
    if items or bom_links:
        findings += check_referentials(
            items=items, bom_links=bom_links, bom_index=bom_index
        )
    if book_stock:
        findings += check_book_stock(
            book_stock=book_stock, items=items, locations=locations
        )
    if journals:
        findings += check_journals(
            journals=journals,
            lines_by_journal=lines_by_journal or {},
            items=items,
            locations=locations,
        )
    if zones:
        findings += check_zones(
            zones=zones, sheets=sheets, lines_by_sheet=lines_by_sheet, items=items
        )
    if variances:
        findings += check_variances(campaign=campaign, variances=variances)
    return findings


#: What each control is *about*, in one short phrase.
#:
#: The finding's own message names the article, the quantity or the location —
#: that is what makes it actionable, and it is also what makes forty of them
#: unreadable. Grouping needs a title that stays the same across the forty, and
#: the code is already that category; this table is only its French name. It is
#: deliberately not a second copy of the message: the message is the detail line
#: shown underneath.
CONTROL_LABELS: dict[str, str] = {
    "ARBITRATION_PENDING": "Écarts entre les deux comptages, en attente d'arbitrage",
    "ASSEMBLY_BOM_RETIRED": "Assemblages dont toutes les versions de nomenclature sont inactives",
    "ASSEMBLY_WITHOUT_BOM": "Assemblages sans aucune nomenclature",
    "BOM_CHILD_UNKNOWN": "Composants de nomenclature absents du référentiel articles",
    "BOM_CYCLE": "Cycles de nomenclature",
    "BOM_DEPTH_TRUNCATED": "Éclatements arrêtés à la profondeur maximale",
    "BOM_PARENT_UNKNOWN": "Assemblages de nomenclature absents du référentiel articles",
    "BOOK_STOCK_DUPLICATE_KEY": "Doublons dans le stock ERP",
    "BOOK_STOCK_EMPTY": "Stock ERP absent",
    "BOOK_STOCK_NEGATIVE": "Quantités négatives dans le stock ERP",
    "BOOK_STOCK_NOT_COUNTED": "Stock ERP jamais compté",
    "BOOK_STOCK_OUT_OF_SCOPE": "Stock ERP écarté : articles hors périmètre",
    "BOOK_STOCK_UNKNOWN_ITEM": "Stock ERP sur des articles hors référentiel",
    "BOOK_STOCK_UNKNOWN_LOCATION": "Stock ERP sur des emplacements inconnus",
    "COUNTED_WITHOUT_BOOK_STOCK": "Comptages sans stock ERP en face",
    "DUPLICATE_COUNT_LINE": "Références saisies plusieurs fois dans un journal",
    "DUPLICATE_JOURNAL": "Emplacements portant plusieurs journaux",
    "EXCLUDED_ITEM_COUNTED": "Articles exclus pourtant comptés",
    "FINISHED_IN_WIP_OK": "Produits finis comptés en WIP assemblé (indicatif)",
    "FINISHED_ON_LINE_SIDE": "Produits finis comptés en bord de ligne",
    "ITEMS_WITHOUT_PRICE": "Articles sans prix standard",
    "JOURNAL_ON_DISABLED_LOCATION": "Journaux ouverts sur un emplacement désactivé",
    "JOURNAL_UNKNOWN_ITEM": "Comptages sur des articles hors référentiel",
    "MATERIAL_VARIANCE": "Écarts au-delà des seuils",
    "NEGATIVE_COUNT": "Quantités comptées négatives",
    "NET_ZERO_CONSOLIDATION": "Sections qui se compensent exactement",
    "POSTED_JOURNAL_EMPTY": "Journaux postés sans aucune ligne",
    "SINGLE_PASS_ONLY": "Références comptées par une seule équipe",
    "UNCOUNTED_WITH_BOOK_STOCK": "Articles en stock ERP jamais comptés en GENERIQUE",
    "UNIT_MISMATCH": "Unités incohérentes avec le référentiel",
    "UNKNOWN_ITEM": "Comptages GENERIQUE hors référentiel articles",
    "WIP_OK_NOT_ASSEMBLY": "WIP assemblé déclaré sur un article qui n'en est pas un",
    "WIP_WITHOUT_BOM": "WIP comptés sans nomenclature exploitable",
    "ZONE_MISSING_SHEET": "Zones sans feuille de comptage",
    "ZONE_WITHOUT_LINES": "Zones sans ligne à compter",
}

#: Blockers first, then warnings: within a screen the order is the reading order.
_SEVERITY_RANK = {
    ControlSeverity.BLOCKER: 0,
    ControlSeverity.WARNING: 1,
    ControlSeverity.INFO: 2,
}


def group_findings(findings: Iterable[ControlFinding]) -> list[FindingGroup]:
    """One entry per control, carrying its occurrences.

    Fifty lines saying the same thing about fifty different articles is not
    fifty pieces of information — it is one, buried under its own repetitions,
    and it pushes everything else off the screen. What the reader needs first is
    *which* control fired and *how often*; the list of articles is the second
    question, and it belongs behind a « voir plus ».

    Grouping is by code rather than by message text: the messages differ, since
    each names its own article, and that difference is exactly what has to stop
    being shown forty times.

    Groups come back blockers first, then by size — the loudest control at the
    top — and the occurrences keep the order the engine produced them in, which
    is already sorted by article.
    """
    buckets: dict[str, list[ControlFinding]] = defaultdict(list)
    for finding in findings:
        buckets[finding.code].append(finding)

    groups = [
        FindingGroup(
            code=code,
            label=CONTROL_LABELS.get(code, code),
            # The worst of the bucket. A code whose severity depends on the case
            # must not be filed under the milder of the two.
            severity=min(
                (f.severity for f in occurrences),
                key=lambda s: _SEVERITY_RANK.get(s, 3),
            ),
            findings=list(occurrences),
        )
        for code, occurrences in buckets.items()
    ]
    groups.sort(
        key=lambda g: (_SEVERITY_RANK.get(g.severity, 3), -len(g.findings), g.code)
    )
    return groups


def summarise(findings: Iterable[ControlFinding]) -> dict[str, object]:
    """Counts per severity and per code, for the exception banner."""
    by_severity: Counter[str] = Counter()
    by_code: defaultdict[str, int] = defaultdict(int)
    for f in findings:
        by_severity[str(f.severity)] += 1
        by_code[f.code] += 1
    return {
        "total": sum(by_severity.values()),
        "bySeverity": dict(by_severity),
        "byCode": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
        "hasBlocker": by_severity.get(str(ControlSeverity.BLOCKER), 0) > 0,
    }
