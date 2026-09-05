"""The GENERIQUE location: zones, printed sheets, arbitration, consolidation.

This is the module that replaces ``Compil GENERIQUE.xlsx`` end to end — the
40 zone tabs, the five Power Query steps and the manual copy/paste into the ERP.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.enums import (
    AuditAction,
    CampaignStatus,
    CountLineKind,
    CountSection,
    DataSource,
    SheetPass,
)
from ..domain.models import (
    Campaign,
    CountSheet,
    CountSheetLine,
    Zone,
)
from ..domain.printing import available_print_modes
from ..domain.sheet_layout import subsections_of
from ..domain.workflow import (
    derive_zone_status,
    passes_for,
    zone_closure_blockers,
)
from ..errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
    WorkflowError,
)
from .arbitration_service import refresh_zone_arbitrations
from .context import ServiceContext
from .manager_service import Perimeter

log = logging.getLogger(__name__)

__all__ = ["GenericService"]

class GenericService:
    """Use cases for the multi-zone GENERIQUE location."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ read

    def list_zones(
        self, campaign: Campaign, *, perimeter: Perimeter | None = None
    ) -> list[dict[str, Any]]:
        """Zones with their derived status and per-pass progress.

        :param perimeter: when given, only the zones assigned to that manager are
            returned. Filtering here rather than in the browser is the point: a
            client-side filter would still ship every zone of the site to every
            workstation, which is unacceptable the moment a contractor counts one.
        """
        ctx = self.ctx
        zones = ctx.sheets.list_zones(campaign.id)
        if perimeter is not None:
            zones = [z for z in zones if perimeter.covers_zone(z.id)]
        sheets = ctx.sheets.list_sheets(campaign.id)
        lines = ctx.sheets.lines_by_sheet(campaign.id)
        arbitrations = ctx.sheets.list_arbitrations(campaign.id)

        by_zone: dict[str, list[CountSheet]] = {}
        for sheet in sheets:
            by_zone.setdefault(sheet.zone_id, []).append(sheet)
        pending: dict[str, int] = {}
        for arb in arbitrations:
            if not arb.is_resolved and arb.qty_pass_1 != arb.qty_pass_2:
                pending[arb.zone_id] = pending.get(arb.zone_id, 0) + 1

        out: list[dict[str, Any]] = []
        for zone in zones:
            zone_sheets = by_zone.get(zone.id, [])
            counted = sum(
                1
                for sheet in zone_sheets
                for line in lines.get(sheet.id, [])
                if line.has_entry
            )
            status = derive_zone_status(
                counted_lines=counted, closed=zone.closed_at is not None
            )
            out.append({
                **zone.model_dump(mode="json"),
                "status": str(status),
                "pendingArbitrations": pending.get(zone.id, 0),
                # Which of the three printable documents this zone can produce
                # right now. Derived server-side so the screen never re-implements
                # the matrix and drifts from what the endpoint will accept.
                "printModes": [
                    str(m)
                    for m in available_print_modes(
                        free_entry=zone.free_entry, status=campaign.status
                    )
                ],
                "sheets": [
                    {
                        **sheet.model_dump(mode="json"),
                        "lineCount": len(lines.get(sheet.id, [])),
                        "countedLines": sum(
                            1 for l in lines.get(sheet.id, []) if l.has_entry
                        ),
                        # What a second, multi-sheet scan must not overwrite
                        # without being told to.
                        "correctedLines": sum(
                            1 for l in lines.get(sheet.id, []) if l.was_ai_corrected
                        ),
                    }
                    for sheet in sorted(zone_sheets, key=lambda s: str(s.pass_no))
                ],
            })
        return out

    def get_sheet(self, campaign: Campaign, sheet_id: str) -> dict[str, Any]:
        """One sheet's content, ready for the grid.

        A pass-2 sheet also carries the pass-1 quantity of each line. Having it
        on screen is what turns encoding into a check — the encoder sees the
        disagreement as they type it, instead of meeting it later in a list of
        arbitrations detached from the paper. It is a screen-only column: the
        printed sheet must never show the first count, or the second one stops
        being independent.
        """
        ctx = self.ctx
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")
        items = ctx.referentials.items_by_number(campaign.id)
        lines = ctx.sheets.list_sheet_lines(sheet_id)
        pass_1 = self._pass_1_quantities(campaign, sheet)
        return {
            "sheet": sheet.model_dump(mode="json"),
            "lines": [
                {
                    **line.model_dump(mode="json"),
                    # La quantité comptée, toujours — une case vide vaut zéro.
                    "qty": float(line.qty),
                    # Ce qui reste vrai de l'ancien booléen : quelqu'un a-t-il
                    # touché cette ligne. Sert à l'avancement, jamais au stock.
                    "hasEntry": line.has_entry,
                    "name": items[line.item_number].name
                    if line.item_number in items else "",
                    "known": line.item_number in items,
                    "qtyPass1": pass_1.get((line.item_number, line.section)),
                }
                for line in lines
            ],
        }

    def _pass_1_quantities(
        self, campaign: Campaign, sheet: CountSheet
    ) -> dict[tuple[str, CountSection], float]:
        """Pass-1 quantities per (item, section), for a pass-2 sheet only."""
        if sheet.pass_no is not SheetPass.PASS_2:
            return {}
        ctx = self.ctx
        first = next(
            (
                s
                for s in ctx.sheets.list_sheets(campaign.id, zone_id=sheet.zone_id)
                if s.pass_no is SheetPass.PASS_1
            ),
            None,
        )
        if first is None:
            return {}
        totals: dict[tuple[str, CountSection], float] = {}
        for line in ctx.sheets.list_sheet_lines(first.id):
            if line.line_kind is not CountLineKind.ARTICLE:
                continue
            key = (line.item_number, line.section)
            # A sheet may list the same article twice (two pallets); the
            # comparison is against the zone's total, as the arbitration is.
            totals[key] = totals.get(key, 0.0) + float(line.qty)
        return totals

    # ----------------------------------------------------------------- zones

    def create_zone(
        self,
        campaign: Campaign,
        *,
        code: str,
        label: str = "",
        sector: str = "",
        display_order: int = 0,
        passes: int | None = None,
        free_entry: bool = True,
        manager_code: str = "",
    ) -> Zone:
        """Create a zone and its counting sheets.

        Allowed in both PREPARATION and COUNTING: preparation is precisely when
        one decides what to count, and a physical area nobody had listed is
        routinely discovered on the day of the inventory.

        :param free_entry: this endpoint creates a zone with **no** pre-printed
            article list, which is the definition of a free-entry sheet — the
            counter writes down what they find. Defaulting to ``True`` is what
            keeps the interface from presenting a deliberate blank sheet as an
            unprepared one. Loading a list through the ``count_sheets`` import
            clears the flag.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        existing = {z.code for z in ctx.sheets.list_zones(campaign.id)}
        zone = Zone(
            id=new_id(),
            campaign_id=campaign.id,
            code=code,
            label=label,
            sector=sector,
            display_order=display_order,
            passes=campaign.config.generic_passes if passes is None else passes,
            free_entry=free_entry,
            manager_code=manager_code,
        )
        if zone.code in existing:
            raise ConflictError(
                f"Une zone « {zone.code} » existe déjà dans cette campagne.",
                code=zone.code,
            )
        # Une zone sans ses feuilles n'est pas une demi-zone : c'est une zone
        # que rien ne permet de compter, et que l'écran présente pourtant comme
        # prête. Les trois écritures tiennent ou tombent ensemble.
        with ctx.db.transaction() as conn:
            ctx.sheets.create_zone(zone, actor=ctx.actor, conn=conn)
            ctx.sheets.ensure_sheets(
                campaign.id, zone.id, passes_for(zone.passes),
                actor=ctx.actor, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.CREATE,
                entity_type="zone",
                entity_id=zone.id,
                summary=f"Création de la zone {zone.code}",
                after=zone.model_dump(mode="json"),
                conn=conn,
            )
        # A zone is what unlocks the pilotage steps; the counts move with it.
        ctx.forget_progress(campaign.id)
        return zone

    def set_zone_passes(
        self, campaign: Campaign, zone_ids: Sequence[str], passes: int
    ) -> dict[str, Any]:
        """Set how many independent counts a selection of zones requires.

        Dropping to one count **deletes** the second sheet, so the operation is
        refused when that sheet already carries a quantity: bringing a zone back
        to a single count after the fact would erase a real count. The refusal
        names the zones concerned, because "some zone somewhere" is not
        actionable on inventory day.

        Raising back to two recreates the second sheet, empty.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if passes not in (1, 2):
            raise ValidationError(
                "Le nombre de comptages doit être 1 ou 2.", passes=passes
            )
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)
        targets = [z for z in zone_ids if zones[z].passes != passes]
        if not targets:
            return {"updated": 0, "sheetsRemoved": 0, "sheetsCreated": 0}

        removed = created = 0
        if passes == 1:
            counted = ctx.sheets.zones_with_counted_pass(
                campaign.id, targets, SheetPass.PASS_2
            )
            if counted:
                codes = sorted(zones[z].code for z in counted)
                raise ConflictError(
                    "Impossible de ramener à un seul comptage : le comptage n°2 "
                    f"porte déjà des quantités saisies sur {', '.join(codes)}. "
                    "Effacez ces quantités si le second comptage doit être "
                    "abandonné.",
                    zones=codes,
                )

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, targets, actor=ctx.actor, passes=passes, conn=conn
            )
            if passes == 1:
                removed = ctx.sheets.delete_sheets_for_pass(
                    campaign.id, targets, SheetPass.PASS_2, conn=conn
                )
                ctx.sheets.delete_arbitrations(campaign.id, targets, conn=conn)
            else:
                for zone_id in targets:
                    created += ctx.sheets.ensure_sheets(
                        campaign.id, zone_id, passes_for(2),
                        actor=ctx.actor, conn=conn,
                    )
                    self._mirror_document(campaign, zone_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) passée(s) à {passes} comptage(s) "
                    f"({removed} feuille(s) supprimée(s), {created} créée(s))."
                ),
                after={
                    "passes": passes,
                    "zones": sorted(zones[z].code for z in targets),
                },
                conn=conn,
            )
        return {"updated": updated, "sheetsRemoved": removed, "sheetsCreated": created}

    def set_zone_negative(
        self, campaign: Campaign, zone_ids: Sequence[str], allowed: bool
    ) -> int:
        """Allow — or forbid again — negative counted quantities on a selection.

        Carried by the zone rather than the sheet: both passes of one area must
        obey the same rule, otherwise the arbitration compares two counts that
        were not allowed the same values.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        unknown = [z for z in zone_ids if z not in zones]
        if unknown:
            raise NotFoundError("Zone(s) introuvable(s).", zoneIds=unknown)

        with ctx.db.transaction() as conn:
            updated = ctx.sheets.update_zones(
                campaign.id, list(zone_ids), actor=ctx.actor,
                allow_negative=allowed, conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="zone",
                summary=(
                    f"{updated} zone(s) : quantités négatives "
                    f"{'autorisées' if allowed else 'interdites'}"
                ),
                after={
                    "allowNegative": allowed,
                    "zones": sorted(zones[z].code for z in zone_ids),
                },
                conn=conn,
            )
        return updated

    def set_section_labels(
        self, campaign: Campaign, zone_id: str, labels: dict[str, str]
    ) -> dict[str, str]:
        """Le texte imprimé en tête de chaque section d'une zone.

        Un texte vide **efface** la personnalisation au lieu d'imprimer une
        bannière vide : c'est ce que veut dire un champ qu'on vide, et une
        section sans titre laisserait le compteur sans la règle sous laquelle il
        compte.

        Posé sur la zone et non sur la feuille : les deux passages sont le même
        document imprimé deux fois, et les voir diverger n'aurait aucun sens.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == zone_id), None
        )
        if zone is None:
            raise NotFoundError("Zone introuvable.", zoneId=zone_id)

        kept = {
            str(_section(code)): text.strip()
            for code, text in labels.items()
            if text.strip()
        }
        ctx.sheets.set_section_labels(
            campaign.id, zone_id, kept, actor=ctx.actor
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="zone",
            entity_id=zone_id,
            summary=(
                f"En-têtes de section de la zone {zone.code} : "
                f"{len(kept)} personnalisé(s)"
            ),
            before={"sectionLabels": zone.section_labels},
            after={"sectionLabels": kept},
        )
        return kept

    def delete_zones(
        self, campaign: Campaign, zone_ids: Sequence[str]
    ) -> dict[str, int]:
        """Retire des zones et leurs feuilles de comptage, une ou tout un lot.

        **Réservé à la préparation**, et c'est plus strict que la matrice de gel
        ne l'exige : les zones restent modifiables en phase de comptage, mais
        leurs feuilles y portent alors des quantités relevées sur le terrain, et
        une feuille supprimée emporte ses lignes — donc un comptage que personne
        ne refera. Préparer du papier est une activité de préparation ; en jeter
        le jour J n'en est pas une.

        **Les feuilles partent avec la zone.** La zone est retirée
        logiquement — son histoire reste au dossier — mais ses feuilles sont
        supprimées pour de bon. Les laisser produirait des feuilles orphelines :
        les listes par zone ne les montreraient plus, la liste à plat de toutes
        les lignes si, et la campagne compterait des articles rattachés à une
        zone qui n'existe plus.
        """
        ctx = self.ctx
        ctx.guard(campaign, "zones")
        if campaign.status is not CampaignStatus.PREPARATION:
            raise ValidationError(
                "Les zones ne se suppriment qu'en préparation. Depuis le passage "
                "en comptage, leurs feuilles portent des quantités relevées.",
                status=str(campaign.status),
            )

        unique = list(dict.fromkeys(i for i in zone_ids if i))
        if not unique:
            raise ValidationError("Aucune zone transmise.")

        known = {zone.id: zone for zone in ctx.sheets.list_zones(campaign.id)}
        missing = [i for i in unique if i not in known]
        if missing:
            raise ValidationError(
                f"{len(missing)} zone(s) introuvables dans cette campagne, dont "
                f"{missing[0]}.",
                missing=missing[:20],
            )

        doomed = [
            sheet.id
            for sheet in ctx.sheets.list_sheets(campaign.id)
            if sheet.zone_id in known and sheet.zone_id in set(unique)
        ]
        with ctx.db.transaction() as conn:
            sheets = ctx.sheets.delete_sheets(campaign.id, doomed, conn=conn)
            for zone_id in unique:
                ctx.sheets.delete_zone(campaign.id, zone_id, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.DELETE,
                entity_type="zone",
                summary=(
                    f"Suppression de {len(unique)} zone(s) et de leurs "
                    f"{sheets} feuille(s) de comptage"
                ),
                before={"codes": [known[i].code for i in unique][:50]},
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return {"zones": len(unique), "sheets": sheets}

    # ---------------------------------------------------------------- sheets

    def set_zone_closed(
        self, campaign: Campaign, zone_id: str, *, closed: bool
    ) -> dict[str, Any]:
        """Déclare une zone terminée, ou la rouvre.

        La seule décision d'état du parcours de comptage. Elle remplace quatre
        transitions par feuille — en attente, comptage, encodage, terminée —
        qu'il fallait faire avancer à la main sans qu'aucune écriture n'en
        dépende : le papier partait au comptage que le bouton ait été cliqué ou
        non, et les quantités s'enregistraient dans tous les cas.

        Un écart non tranché refuse la clôture, et le dit : la consolidation ne
        saurait pas quelle quantité retenir, et fermer la zone reviendrait à
        promettre un chiffre qui n'existe pas encore. Rouvrir, en revanche, ne
        se refuse jamais — c'est le geste qui répare une clôture trop rapide.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == zone_id), None
        )
        if zone is None:
            raise NotFoundError("Zone introuvable dans cette campagne.")

        if closed:
            # L'arbitrage se calcule sur les quantités du moment : le rafraîchir
            # d'abord évite de refuser une clôture pour un écart déjà tranché,
            # ou de l'accepter alors qu'une saisie vient d'en créer un.
            refresh_zone_arbitrations(ctx, campaign, zone_id)
            pending = sum(
                1
                for a in ctx.sheets.list_arbitrations(campaign.id)
                if a.zone_id == zone_id
                and not a.is_resolved
                and a.qty_pass_1 != a.qty_pass_2
            )
            blocker = zone_closure_blockers(pending_arbitrations=pending)
            if blocker:
                raise WorkflowError(blocker, zone=zone.code, pending=pending)

        with ctx.db.transaction() as conn:
            ctx.sheets.set_zone_closed(
                campaign.id, zone_id, closed=closed, actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.STATUS_CHANGE,
                entity_type="zone",
                entity_id=zone_id,
                summary=(
                    f"Zone {zone.code} déclarée terminée."
                    if closed
                    else f"Zone {zone.code} rouverte."
                ),
                before={"closed": zone.closed_at is not None},
                after={"closed": closed},
                conn=conn,
            )
        return {"id": zone_id, "closed": closed}

    def upsert_sheet_lines(
        self,
        campaign: Campaign,
        sheet_id: str,
        rows: Sequence[dict[str, Any]],
        *,
        replace: bool = False,
        expected_version: int | None = None,
    ) -> int:
        """Create or update the lines of a sheet from grid edits or a paste.

        Two different permissions, because they are two different acts. *Which
        articles are on the sheet* is preparation work — pruning a list, fixing a
        section, adding the reference somebody forgot — and it stays open as long
        as the sheets themselves are editable. *What quantity was found* is the
        count itself, and it only opens when counting does. Guarding both under
        the stricter of the two is what made the preparation screen refuse to let
        anyone touch a list they were still building.

        ``expected_version`` est la version que l'écran avait sous les yeux. Un
        remplacement écrase l'ensemble : sans elle, deux personnes sur la même
        feuille s'effacent l'une l'autre en silence. Facultative parce que tous
        les appelants ne l'ont pas — une extraction IA écrit une feuille qu'elle
        vient de lire, et rien d'autre ne la touche — mais l'écran, lui, la
        transmet toujours.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")

        zone = next(
            (z for z in ctx.sheets.list_zones(campaign.id) if z.id == sheet.zone_id),
            None,
        )
        allow_negative = bool(zone and zone.allow_negative)

        existing = {l.id: l for l in ctx.sheets.list_sheet_lines(sheet_id)}
        if _touches_quantities(rows, existing):
            ctx.guard(campaign, "count_entries")

        # La sous-section se **dérive** des séparateurs quand la feuille entière
        # est réécrite : l'écran envoie l'ordre des lignes, et l'intertitre sous
        # lequel une ligne se trouve est une lecture de cet ordre, jamais une
        # saisie. Sur une écriture partielle — une quantité corrigée, une
        # extraction IA — l'ordre reçu ne dit rien du document, et la valeur déjà
        # en base est la bonne.
        derived = list(subsections_of(rows)) if replace else []

        lines: list[CountSheetLine] = []
        for order, row in enumerate(rows):
            line_id = str(row.get("id") or "") or new_id()
            previous = existing.get(line_id)
            kind = _line_kind(row.get("line_kind"))
            if kind is not CountLineKind.ARTICLE:
                # Un intertitre et une ligne vide ne portent ni article ni
                # quantité : les laisser passer par la construction ordinaire
                # ferait refuser « pas de référence » sur une ligne dont c'est
                # tout le propos.
                lines.append(CountSheetLine(
                    id=line_id, sheet_id=sheet_id, campaign_id=campaign.id,
                    item_number="", section=_section(row.get("section")),
                    line_kind=kind,
                    label=str(row.get("label") or "")
                    if kind is CountLineKind.SUBSECTION else "",
                    unit="", source=DataSource.MANUAL,
                    display_order=int(row.get("display_order") or order),
                ))
                continue
            # « 3*48+7 » plutôt que « 151 » : trois palettes de quarante-huit et
            # un fond de bac. La conversion a lieu ici et non dans le contrat
            # d'entrée parce que c'est ici qu'on connaît la campagne — donc le
            # réglage — et que le refus doit pouvoir nommer ce réglage.
            qty, formula = _quantity_of(row, campaign=campaign)
            if not allow_negative and qty is not None and qty < 0:
                # One does not find minus twenty screws in a bin: a negative is
                # a typo until a human says otherwise, zone by zone. Catching it
                # at the keyboard costs a second; catching it at the variance
                # meeting costs an afternoon.
                raise ValidationError(
                    f"Quantité négative refusée sur « {row.get('item_number')} » : "
                    f"la zone {zone.code if zone else ''} n'autorise pas les "
                    "quantités négatives. Activez-les sur cette zone si la "
                    "feuille sert à corriger un comptage déjà posté.",
                    itemNumber=row.get("item_number"),
                    qty=str(qty),
                    zoneId=sheet.zone_id,
                )
            # **La provenance appartient à la ligne, pas à la feuille.**
            # L'écran renvoie les cent lignes qu'il affiche, y compris les
            # quatre-vingt-dix-neuf que personne n'a touchées ; les marquer
            # toutes « saisie manuelle » effaçait la trace de la lecture IA sur
            # toute la feuille dès qu'une seule cellule était corrigée. On ne
            # peut plus alors dire quelle valeur a été relue par un humain — ce
            # qui est justement ce que la colonne existe pour dire.
            comment = str(row.get("comment") or "")
            untouched = (
                previous is not None
                and previous.qty_manual is None
                and qty == (previous.qty if previous.has_entry else None)
                and comment == previous.comment
            )
            lines.append(
                CountSheetLine(
                    id=line_id,
                    sheet_id=sheet_id,
                    campaign_id=campaign.id,
                    item_number=str(row.get("item_number") or ""),
                    section=_section(row.get("section")),
                    # A value typed by a human always lands in qty_manual so the
                    # AI reading it replaced stays visible next to it.
                    qty_imported=previous.qty_imported if previous else None,
                    qty_manual=None if untouched else qty,
                    unit=str(row.get("unit") or "PCE"),
                    source=(
                        previous.source if untouched and previous
                        else DataSource.MANUAL
                    ),
                    confidence=previous.confidence if previous else None,
                    qty_formula=previous.qty_formula if untouched and previous else formula,
                    comment=comment,
                    display_order=int(row.get("display_order") or order),
                    subsection=(
                        derived[order] if replace
                        else str(
                            row.get("subsection")
                            if row.get("subsection") is not None
                            else (previous.subsection if previous else "")
                        )
                    ),
                )
            )

        with ctx.db.transaction() as conn:
            if replace:
                # Le verrou est pris *dans* la transaction qui écrit. Le prendre
                # avant laisserait une fenêtre entre la prise et le
                # remplacement — c'est-à-dire exactement la course qu'il ferme.
                if expected_version is not None:
                    ctx.sheets.bump_sheet(
                        campaign.id,
                        sheet_id,
                        expected_version=expected_version,
                        actor=ctx.actor,
                        conn=conn,
                    )
                written = ctx.sheets.replace_sheet_lines(
                    sheet_id, lines, actor=ctx.actor, conn=conn
                )
                # Le document se décide sur le passage 1 et vaut pour les deux.
                # Une référence retirée, un intertitre renommé, deux lignes
                # échangées : sans cette ligne, le second compteur tenait une
                # feuille différente, et l'arbitrage comparait deux réponses à
                # deux questions.
                if sheet.pass_no is SheetPass.PASS_1:
                    self._mirror_document(campaign, sheet.zone_id, conn=conn)
            else:
                written = ctx.sheets.upsert_sheet_lines(
                    lines, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="count_sheet_line",
                entity_id=sheet_id,
                summary=f"{written} ligne(s) enregistrée(s) sur la feuille",
                after={"lines": written, "replace": replace},
                conn=conn,
            )

        # La comparaison entre les deux passages se recalcule ici, là où les
        # quantités changent.
        #
        # Elle ne se calculait qu'à la fermeture d'une zone. Entre-temps l'onglet
        # Arbitrages affirmait « les deux équipes ont trouvé les mêmes
        # quantités » — sur une zone où un désaccord attendait — et l'indicateur
        # « Arbitrages en attente » restait à zéro. L'écart n'apparaissait qu'au
        # refus de la clôture, c'est-à-dire au moment où l'on croyait avoir fini.
        #
        # Hors transaction : un arbitrage manqué se rattrape à la fermeture, qui
        # recalcule de toute façon, alors qu'un échec de recalcul ne doit jamais
        # faire perdre des quantités relevées à la main.
        if zone is not None and zone.passes > 1:
            try:
                refresh_zone_arbitrations(ctx, campaign, zone.id)
            except Exception:
                # Journalisé, jamais tu : l'échec ne remonte pas à l'écran
                # parce que la saisie, elle, est enregistrée — annoncer un
                # échec ferait ressaisir des quantités déjà en base.
                log.exception(
                    "Rafraîchissement des arbitrages impossible sur la zone %s",
                    zone.code,
                )
        return written

    def list_all_lines(
        self, campaign: Campaign, *, zone_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every counting-sheet line of the campaign, flat.

        Carries the zone on each line: without it the list is a pile of
        references with no way back to the paper, and correcting one means
        guessing which sheet it came from.

        **Le passage 1 seulement.** Les deux passages portent le même document —
        c'est ce que garantit :meth:`mirror_document` — et les afficher tous les
        deux doublait la liste sans rien y ajouter : quarante mille lignes au
        lieu de vingt mille, chaque référence deux fois, et le doute à chaque
        correction sur laquelle des deux on est en train de modifier. Une
        correction faite ici descend sur les deux feuilles.
        """
        ctx = self.ctx
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        sheets = {
            s.id: s
            for s in ctx.sheets.list_sheets(campaign.id)
            if s.pass_no is SheetPass.PASS_1
            and (zone_id is None or s.zone_id == zone_id)
        }
        items = ctx.referentials.items_by_number(campaign.id)
        out: list[dict[str, Any]] = []
        for sheet_id, lines in ctx.sheets.lines_by_sheet(campaign.id).items():
            sheet = sheets.get(sheet_id)
            if sheet is None:
                continue
            zone = zones.get(sheet.zone_id)
            for line in lines:
                out.append({
                    **line.model_dump(mode="json"),
                    # La quantité comptée, toujours — une case vide vaut zéro.
                    "qty": float(line.qty),
                    # Ce qui reste vrai de l'ancien booléen : quelqu'un a-t-il
                    # touché cette ligne. Sert à l'avancement, jamais au stock.
                    "hasEntry": line.has_entry,
                    "zoneId": sheet.zone_id,
                    "zoneCode": zone.code if zone else "",
                    "zoneLabel": zone.label if zone else "",
                    "name": items[line.item_number].name
                    if line.item_number in items else "",
                    "known": line.item_number in items,
                })
        out.sort(key=lambda r: (r["zoneCode"], r["display_order"]))
        return out

    def delete_sheet_lines(
        self, campaign: Campaign, line_ids: Sequence[str]
    ) -> int:
        """Remove a selection of lines.

        Structural, so it follows the sheets' own permission rather than the
        counting one: pruning a list of references is preparation work, and it
        is precisely what one does before printing.

        Every identifier is resolved against *this campaign's* lines before
        anything is written. That is two guarantees in one: an identifier the
        campaign does not own is refused rather than deleted, and one that is
        not an identifier at all — a row index sent by a client that mistook a
        blank row for a saved one — comes back as a clear refusal instead of a
        driver error five layers down.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        unique = list(dict.fromkeys(i for i in line_ids if i))
        if not unique:
            raise ValidationError("Aucune ligne transmise.")

        by_sheet = ctx.sheets.lines_by_sheet(campaign.id)
        known = {line.id for lines in by_sheet.values() for line in lines}
        missing = [i for i in unique if i not in known]
        if missing:
            raise ValidationError(
                f"{len(missing)} ligne(s) introuvables dans cette campagne, dont "
                f"« {missing[0]} ». Rechargez la liste avant de recommencer.",
                missing=missing[:20],
            )

        # La trace annonce « suppression de N lignes » : elle ne doit pas
        # survivre à un lot interrompu au milieu, sans quoi elle décrit un état
        # que la base n'a jamais eu.
        zones = {
            sheet.zone_id
            for sheet in ctx.sheets.list_sheets(campaign.id)
            if sheet.pass_no is SheetPass.PASS_1
            and any(l.id in set(unique) for l in by_sheet.get(sheet.id, ()))
        }
        with ctx.db.transaction() as conn:
            for line_id in unique:
                ctx.sheets.delete_sheet_line(
                    campaign.id, line_id, actor=ctx.actor, conn=conn
                )
            # Une ligne retirée du passage 1 l'est du document, donc des deux
            # feuilles. La laisser au passage 2 y ferait compter une référence
            # que le passage 1 ne demande plus — et remonter un désaccord.
            for zone_id in zones:
                self._mirror_document(campaign, zone_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.DELETE,
                entity_type="count_sheet_line",
                summary=f"Suppression de {len(unique)} ligne(s) de feuille",
                after={"lineIds": ", ".join(unique[:50])},
                conn=conn,
            )
        return len(unique)

    def delete_sheet_line(self, campaign: Campaign, line_id: str) -> None:
        """One line — the same path as a batch of one.

        Deleting by identifier alone would delete it wherever it lives, another
        campaign included, and would hand an unparsable identifier straight to
        the driver. Both are the batch's job to check, so this goes through it.
        """
        self.delete_sheet_lines(campaign, [line_id])

    # ---------------------------------------------------------- AI extraction

    # --------------------------------------------------------- consolidation

    # --------------------------------------------------------------- helpers

    def _mirror_document(
        self, campaign: Campaign, zone_id: str, *, conn: Any = None
    ) -> int:
        """Le passage 2 porte le **même document** que le passage 1.

        Mêmes références, mêmes intertitres, mêmes lignes vides, même ordre.
        C'est la définition du double comptage : deux équipes à qui l'on pose la
        même question, et dont on compare les réponses. Deux documents
        différents rendent la comparaison sans objet — un article présent d'un
        côté et absent de l'autre remonte en arbitrage comme un désaccord alors
        que personne n'a jamais été invité à le compter.

        La copie ne descendait qu'à la création de la feuille, et seulement pour
        y **ajouter** ce qui manquait. Une référence retirée, un intertitre
        renommé, deux lignes échangées : rien de tout cela ne passait, et les
        deux feuilles divergeaient dès la première correction.

        **Les quantités du passage 2 lui appartiennent.** Elles sont conservées
        ligne par ligne, appariées sur la clé — référence, section,
        sous-section. Les recopier serait cesser de compter deux fois ; les
        perdre à chaque correction de la feuille serait pire.
        """
        ctx = self.ctx
        sheets = {
            s.pass_no: s
            for s in ctx.sheets.list_sheets(campaign.id, zone_id=zone_id, conn=conn)
        }
        first, second = sheets.get(SheetPass.PASS_1), sheets.get(SheetPass.PASS_2)
        if first is None or second is None:
            return 0

        def key(line: CountSheetLine) -> tuple[Any, ...]:
            return (line.line_kind, line.item_number, line.section,
                    line.subsection, line.label)

        # Appariement par clé, dans l'ordre : deux lignes de mise en page
        # identiques — deux lignes vides — se distinguent par leur rang d'arrivée
        # et non par leur contenu, qui est vide des deux côtés.
        twins: dict[tuple[Any, ...], list[CountSheetLine]] = {}
        for line in ctx.sheets.list_sheet_lines(second.id, conn=conn):
            twins.setdefault(key(line), []).append(line)

        mirrored: list[CountSheetLine] = []
        for line in ctx.sheets.list_sheet_lines(first.id, conn=conn):
            same = twins.get(key(line))
            twin = same.pop(0) if same else None
            mirrored.append(CountSheetLine(
                id=twin.id if twin else new_id(),
                sheet_id=second.id,
                campaign_id=campaign.id,
                item_number=line.item_number,
                section=line.section,
                line_kind=line.line_kind,
                label=line.label,
                subsection=line.subsection,
                unit=line.unit,
                display_order=line.display_order,
                # Ce que le second passage a relevé, et rien d'autre.
                qty_imported=twin.qty_imported if twin else None,
                qty_manual=twin.qty_manual if twin else None,
                qty_formula=twin.qty_formula if twin else "",
                comment=twin.comment if twin else "",
                confidence=twin.confidence if twin else None,
                source=twin.source if twin else DataSource.SYSTEM,
            ))
        return ctx.sheets.replace_sheet_lines(
            second.id, mirrored, actor=ctx.actor, conn=conn
        )


def _line_kind(value: Any) -> CountLineKind:
    """Article, intertitre ou ligne vide — l'article par défaut.

    Le défaut compte : tout ce qui écrivait des lignes avant que la mise en page
    existe continue d'écrire des articles sans rien dire.
    """
    text = str(value or "").strip().upper()
    if text in CountLineKind.__members__:
        return CountLineKind[text]
    if text:
        raise ValidationError(
            f"Genre de ligne inconnu : {value!r}. Attendu ARTICLE, SUBSECTION "
            "ou SPACER.",
            lineKind=str(value),
        )
    return CountLineKind.ARTICLE


def _section(value: Any) -> CountSection:
    from ..domain.enums import legacy_section_alias

    if value in (None, ""):
        return CountSection.LINE_SIDE
    text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    if text in CountSection.__members__:
        return CountSection[text]
    resolved = legacy_section_alias(str(value))
    if resolved is None:
        raise ValidationError(
            f"Section inconnue : {value!r}. Attendu LINE_SIDE, WIP ou WIP_OK.",
            section=str(value),
        )
    return resolved


def _quantity_of(
    row: dict[str, Any], *, campaign: Campaign
) -> tuple[Decimal | None, str]:
    """La quantité d'une ligne, et l'opération qui l'a produite s'il y en a une.

    Une case vide reste vide **en base** : elle vaut zéro partout où l'on
    calcule un stock — c'est :attr:`CountSheetLine.qty` qui le dit — mais y
    écrire un zéro effacerait la distinction entre une ligne que personne n'a
    touchée et une ligne où quelqu'un a écrit « 0 ». C'est cette distinction-là
    qui fait l'avancement d'une zone.

    Le réglage de la campagne décide si « 3*48+7 » est une quantité ou une
    erreur, et le refus le nomme — c'est tout l'objet de
    :func:`~inventory.domain.formula.resolve_quantity`. L'erreur remontée porte
    la référence : sur une feuille de cent lignes, « quantité invalide » sans
    dire laquelle oblige à toutes les relire.
    """
    from ..domain.formula import FormulaError, resolve_quantity

    raw = row.get("qty")
    if raw in (None, ""):
        return None, ""
    try:
        return resolve_quantity(raw, allow_formulas=campaign.config.allow_formulas)
    except FormulaError as exc:
        raise ValidationError(
            f"Ligne « {row.get('item_number') or '?'} » : {exc}",
            itemNumber=row.get("item_number"),
            qty=str(raw),
        ) from exc


def _touches_quantities(
    rows: Sequence[dict[str, Any]], existing: dict[str, CountSheetLine]
) -> bool:
    """Whether this write changes any counted quantity.

    Only a *change* counts. A preparation screen re-saving a sheet sends back the
    quantities it was given, and treating that echo as a count would freeze the
    list the moment one line happened to carry a figure.

    Appelée **avant** que les opérations soient évaluées, et volontairement :
    c'est elle qui décide si la garde de phase s'applique, et une campagne dont
    les comptages sont gelés doit répondre « c'est gelé » plutôt que de discuter
    la syntaxe de ce qu'on tente d'y écrire. Une opération y est donc comparée
    telle qu'écrite, à celle que la ligne portait déjà.
    """
    for row in rows:
        qty = row.get("qty")
        previous = existing.get(str(row.get("id") or ""))
        before = previous.qty_manual if previous else None
        if qty in (None, ""):
            if before is not None:
                return True
            continue
        try:
            after = Decimal(str(qty))
        except (ArithmeticError, ValueError):
            written = str(qty).strip()
            if previous is None or previous.qty_formula != written:
                return True
            continue
        if after != before:
            return True
    return False
