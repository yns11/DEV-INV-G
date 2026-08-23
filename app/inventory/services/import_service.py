"""Generic import pipeline shared by every grid.

One code path handles file uploads, clipboard pastes and typed rows, so the
validation rules, the duplicate detection and the audit trail cannot diverge
between them.

Every import produces an :class:`ImportOutcome` that the UI renders as a
before/after summary: how many rows arrived, how many were accepted, exactly
which ones were rejected and why. Nothing is ever loaded blind.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

from ..db import new_id
from ..domain.enums import (
    AuditAction,
    DataSource,
    JournalKind,
    JournalStatus,
    LocationStatus,
    LocationType,
)
from ..domain.models import (
    Campaign,
    CountJournalLine,
    CountSheetLine,
    LocationKey,
    Warehouse,
    Zone,
)
from ..domain.workflow import passes_for
from ..errors import ConflictError, ValidationError
from ..ingest import (
    GridContract,
    ParseResult,
    PreparedSheetRow,
    RowError,
    map_adjustments,
    map_backflush,
    map_bom_links,
    map_book_stock,
    map_count_sheets,
    map_items,
    map_journal_lines,
    map_locations,
)
from .context import ServiceContext, utcnow
from .import_batches import (
    ImportBatches,
    ImportOutcome,
    _hash_of,
    _source_of,
)
from .import_parsing import (
    ImportParser,
    InputMode,
    _base_outcome,
    _require_period,
)

log = logging.getLogger(__name__)

__all__ = [
    "ImportOutcome", "ImportService", "InputMode", "monday_of",
    "suggested_period",
]



class ImportService:
    """Parses, validates and persists bulk data for every grid."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx
        #: La provenance et l'idempotence, tenues à côté plutôt que dedans :
        #: elles accompagnent les six importeurs sans appartenir à aucun.
        self.batches = ImportBatches(ctx)
        #: La lecture d'une entrée, tenue à côté aussi : elle ne écrit rien, et
        #: les six importeurs s'en servent tous de la même façon.
        self.parser = ImportParser(ctx)

    # ---------------------------------------------------------------- parsing

    def _retire_stale_locations(
        self,
        campaign: Campaign,
        stale: Sequence[LocationKey],
        *,
        outcome: ImportOutcome,
        conn: Any,
    ) -> tuple[int, set[LocationKey]]:
        """Close the locations a new ERP snapshot no longer knows about.

        Returns how many journals were removed, and the locations kept back.

        A journal nobody has opened is a leftover and goes with its location. A
        journal that carries a line, or that somebody has already posted, is
        *work*: reloading the snapshot is not a decision to throw it away. Those
        locations stay active and the import says so — an emplacement counted
        under a snapshot that no longer lists it is exactly the sort of thing
        that has to be looked at, not cleaned up in silence.
        """
        ctx = self.ctx
        if not stale:
            return 0, set()

        untouched = ctx.journals.untouched_journal_keys(campaign.id, stale, conn=conn)
        existing_journals = ctx.journals.journal_keys(campaign.id, stale, conn=conn)
        kept = {
            k for k in stale
            if (k.warehouse_id, k.location_id) in existing_journals - untouched
        }
        # GENERIQUE ne porte pas de ligne de journal : son comptage vit dans les
        # feuilles. Le juger sur ses lignes de journal le déclarerait vierge
        # alors qu'une zone entière y a été comptée, et le rechargement d'un
        # snapshot emporterait tout ce travail sans le dire.
        generic = campaign.config.generic_key
        if generic in stale and ctx.sheets.count_counted_lines(campaign.id, conn=conn):
            kept.add(generic)
        removable = [
            k for k in stale
            if (k.warehouse_id, k.location_id) in untouched and k not in kept
        ]

        removed = ctx.journals.delete_journals_for_locations(
            campaign.id, removable, conn=conn
        )
        # L'emplacement suit son journal : le désactiver alors qu'un comptage y
        # est encore ouvert le ferait disparaître des écrans où ce comptage doit
        # rester visible.
        closing = [k for k in stale if k not in kept]
        if closing:
            ctx.referentials.set_location_status(
                campaign.id, closing, LocationStatus.DISABLED,
                actor=ctx.actor, conn=conn,
            )

        outcome.details["locationsRetired"] = len(closing)
        outcome.details["journalsRemoved"] = removed
        if kept:
            outcome.details["locationsKept"] = sorted(
                f"{k.warehouse_id} / {k.location_id}" for k in kept
            )[:50]
            outcome.warnings.append(
                RowError(
                    line=0,
                    column="",
                    value="",
                    message=(
                        f"{len(kept)} emplacement(s) absents du nouveau stock ERP "
                        "portent déjà un comptage : leur journal est conservé. "
                        "Vérifiez-les avant la clôture."
                    ),
                )
            )
        return removed, kept

    # ---------------------------------------------------------------- parsing

    def parse(self, *args: Any, **kwargs: Any) -> tuple[GridContract, ParseResult]:
        """Lit une entrée — voir :class:`ImportParser`.

        Le travail est à côté ; le point d'entrée reste ici parce que c'est
        celui que l'API et les contrôles connaissent, et qu'une façade d'une
        ligne coûte moins qu'un renommage de vingt-six appels.
        """
        return self.parser.parse(*args, **kwargs)

    def preview(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Essai à blanc — voir :class:`ImportParser`."""
        return self.parser.preview(*args, **kwargs)

    # -------------------------------------------------------------- importers

    def import_items(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "items")
        _, parsed = self.parser.parse("items", **kwargs)
        outcome = _base_outcome("items", parsed)
        outcome.storage_path = self.batches.archive(campaign, "items", kwargs)
        if not parsed.rows:
            return outcome

        source = _source_of(kwargs.get("mode", "file"))
        items, errors = map_items(campaign.id, parsed.rows, source=source)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(items)
        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_items(items, actor=ctx.actor, conn=conn)
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "items", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="item",
                summary=f"Import de {len(items)} article(s)",
                after={"accepted": len(items), "rejected": outcome.rows_rejected},
                conn=conn,
            )
        return outcome

    def import_boms(
        self,
        campaign: Campaign,
        *,
        replace: bool = False,
        allow_partial: bool = False,
        **kwargs: Any,
    ) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "boms")
        _, parsed = self.parser.parse("boms", **kwargs)
        outcome = _base_outcome("boms", parsed)
        outcome.storage_path = self.batches.archive(campaign, "boms", kwargs)
        if not parsed.rows:
            return outcome

        links, errors = map_bom_links(campaign.id, parsed.rows)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(links)
        if replace:
            # Seul le mode remplacement est concerné : un chargement qui
            # complète n'efface rien, et trois lignes refusées sur quatre mille
            # y sont trois lignes manquantes, pas trois lignes supprimées.
            self.batches.refuse_if_partial(
                outcome, accepted=len(links), allow_partial=allow_partial,
                what="Cette nomenclature",
            )
        with ctx.db.transaction() as conn:
            if replace:
                removed = ctx.referentials.clear_bom(
                    campaign.id, actor=ctx.actor, conn=conn
                )
                outcome.details["replacedLinks"] = removed
            ctx.referentials.upsert_bom_links(links, actor=ctx.actor, conn=conn)
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "boms", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="bom_link",
                summary=f"Import de {len(links)} lien(s) de nomenclature",
                after={"accepted": len(links), "replaced": replace},
                conn=conn,
            )

        # A cycle makes every explosion undefined, so it is reported immediately
        # rather than at consolidation time on the day of the inventory.
        from ..domain.bom import BomIndex

        cycles = BomIndex(ctx.referentials.list_bom_links(campaign.id)).find_cycles()
        if cycles:
            outcome.details["bomCycles"] = [" → ".join(c) for c in cycles[:10]]
            outcome.warnings.append(
                RowError(0, "parent_item", None,
                         f"{len(cycles)} cycle(s) détecté(s) dans la nomenclature. "
                         "L'éclatement du WIP sera bloqué tant qu'ils subsistent.")
            )
        return outcome

    def import_adjustments(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "adjustments")
        _, parsed = self.parser.parse("adjustments", **kwargs)
        outcome = _base_outcome("adjustments", parsed)
        outcome.storage_path = self.batches.archive(campaign, "adjustments", kwargs)
        if not parsed.rows:
            return outcome

        lines, errors = map_adjustments(
            campaign.id, parsed.rows,
            source=_source_of(kwargs.get("mode", "file")), id_factory=new_id,
        )
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        outcome.rows_accepted = len(lines)
        with ctx.db.transaction() as conn:
            ctx.adjustments.upsert(lines, actor=ctx.actor, conn=conn)
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "adjustments", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="adjustment_line",
                summary=f"Import de {len(lines)} mouvement(s) de stock",
                after={"accepted": len(lines)},
                conn=conn,
            )
        return outcome

    def import_locations(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        ctx = self.ctx
        ctx.guard(campaign, "locations")
        _, parsed = self.parser.parse("locations", **kwargs)
        outcome = _base_outcome("locations", parsed)
        outcome.storage_path = self.batches.archive(campaign, "locations", kwargs)
        if not parsed.rows:
            return outcome

        locations, errors = map_locations(campaign.id, parsed.rows)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        warehouses = {
            l.warehouse_id: Warehouse(
                campaign_id=campaign.id, warehouse_id=l.warehouse_id, type=l.type
            )
            for l in locations
        }
        outcome.rows_accepted = len(locations)
        with ctx.db.transaction() as conn:
            ctx.referentials.upsert_warehouses(
                warehouses.values(), actor=ctx.actor, conn=conn
            )
            ctx.referentials.upsert_locations(locations, actor=ctx.actor, conn=conn)
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "locations", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="location",
                summary=f"Import de {len(locations)} emplacement(s)",
                conn=conn,
            )
        return outcome

    # ------------------------------------------------------------ book stock

    def import_book_stock(
        self, campaign: Campaign, *, allow_partial: bool = False, **kwargs: Any
    ) -> ImportOutcome:
        """Load the ERP snapshot and derive the location referential from it.

        Three things happen in one transaction, because they only make sense
        together:

        1. the snapshot replaces any previous one (a photograph is not merged);
        2. every warehouse/location pair it contains is added to the referential,
           **preserving** the ACTIVE/DISABLED decisions already made;
        3. one PENDING counting journal is created per active location.
        """
        ctx = self.ctx
        ctx.guard(campaign, "book_stock")
        if campaign.book_stock_frozen_at is not None:
            raise ConflictError(
                "Le stock ERP est gelé pour cette campagne. Créez une nouvelle "
                "campagne si un nouveau snapshot est nécessaire.",
                frozenAt=campaign.book_stock_frozen_at.isoformat(),
            )

        _, parsed = self.parser.parse("book_stock", **kwargs)
        outcome = _base_outcome("book_stock", parsed)
        outcome.storage_path = self.batches.archive(campaign, "book_stock", kwargs)
        if not parsed.rows:
            return outcome

        items = ctx.referentials.items_by_number(campaign.id)
        lines, errors = map_book_stock(campaign.id, parsed.rows, items=items)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)
        if not lines:
            return outcome

        existing = ctx.referentials.locations_by_key(campaign.id)
        generic_key = campaign.config.generic_key
        discovered: dict[LocationKey, Any] = {}
        warehouses: dict[str, Warehouse] = {}

        from ..domain.models import Location

        for line in lines:
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in existing or key in discovered:
                continue
            # A warehouse whose name contains VRAC, or the GENERIQUE location
            # itself, is a bulk location: it is counted by manual entry (INVV)
            # rather than by scanning labels (INVE).
            is_bulk = "VRAC" in key.warehouse_id or key == generic_key
            discovered[key] = Location(
                campaign_id=campaign.id,
                warehouse_id=key.warehouse_id,
                location_id=key.location_id,
                type=LocationType.BULK if is_bulk else LocationType.LABEL,
                status=LocationStatus.ACTIVE,
                source=DataSource.SYSTEM,
            )
            warehouses.setdefault(
                key.warehouse_id,
                Warehouse(
                    campaign_id=campaign.id,
                    warehouse_id=key.warehouse_id,
                    type=LocationType.BULK if is_bulk else LocationType.LABEL,
                ),
            )

        # Un snapshot *remplace* le précédent. Les emplacements que seul l'ancien
        # connaissait n'ont donc plus de raison d'être — et sans cela leurs
        # journaux restaient dans la liste, s'ajoutant aux nouveaux : la
        # couverture comptait des emplacements qui n'existent plus, et personne
        # ne pouvait dire lesquels étaient à compter.
        #
        # Seuls les emplacements *nés d'un snapshot* sont retirés. Celui qu'on a
        # déclaré à la main reste : quelqu'un a décidé qu'il existait, et un
        # chargement ERP n'est pas un avis sur cette décision.
        snapshot_keys = {
            LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
            for l in lines
        }
        stale = [
            key
            for key, location in existing.items()
            if key not in snapshot_keys
            and location.source is DataSource.SYSTEM
            and location.status is LocationStatus.ACTIVE
        ]

        # Le pire cas du rapport d'audit : un snapshot amputé qui se présente
        # comme complet. Chaque article manquant produit ensuite un écart de
        # 100 % contre un stock que l'ERP n'a jamais annoncé nul.
        self.batches.refuse_if_partial(
            outcome, accepted=len(lines), allow_partial=allow_partial,
            what="Le stock ERP",
        )

        batch_id = new_id()
        with ctx.db.transaction() as conn:
            ctx.book_stock.replace(campaign.id, lines, batch_id=batch_id, conn=conn)
            removed, kept = self._retire_stale_locations(
                campaign, stale, outcome=outcome, conn=conn
            )
            if warehouses:
                ctx.referentials.upsert_warehouses(
                    warehouses.values(), actor=ctx.actor, conn=conn
                )
            if discovered:
                ctx.referentials.upsert_locations(
                    discovered.values(), actor=ctx.actor, conn=conn
                )
            retired = set(stale) - kept
            active_keys = [
                key
                for key, location in {
                    **existing, **discovered
                }.items()
                if location.status is LocationStatus.ACTIVE and key not in retired
            ]
            created = ctx.journals.ensure_journals(
                campaign.id,
                active_keys,
                kinds={
                    k: (JournalKind.INVV
                        if ({**existing, **discovered})[k].type is LocationType.BULK
                        else JournalKind.INVE)
                    for k in active_keys
                },
                actor=ctx.actor,
                conn=conn,
            )
            ctx.imports.create(
                campaign_id=campaign.id,
                target="book_stock",
                # `_origin_of` plutôt que le nom du fichier : une lecture ERP
                # n'en a pas, et la colonne restait vide — c'est-à-dire que
                # l'historique ne disait pas d'où venait le stock, ce qui est
                # précisément le seul travail de cet historique.
                filename=self.batches.origin_of("book_stock", kwargs),
                content_hash=_hash_of(kwargs),
                storage_path=outcome.storage_path,
                rows_received=outcome.rows_received,
                rows_accepted=len(lines),
                rows_rejected=outcome.rows_rejected,
                report=outcome.as_dict(),
                imported_by=ctx.actor,
                # Le même identifiant que celui gravé dans les lignes de stock
                # juste au-dessus : c'est ce qui rend « d'où vient cette
                # quantité » interrogeable.
                batch_id=batch_id,
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="book_stock",
                summary=(
                    f"Stock ERP chargé : {len(lines)} lignes, "
                    f"{len(discovered)} nouvel(s) emplacement(s), "
                    f"{created} journal(aux) créé(s), "
                    f"{removed} journal(aux) retiré(s)."
                ),
                after={
                    "lines": len(lines),
                    "newLocations": len(discovered),
                    "journalsCreated": created,
                    "journalsRemoved": removed,
                },
                conn=conn,
            )

        outcome.batch_id = batch_id
        outcome.rows_accepted = len(lines)
        # Fusionner, et non remplacer : le retrait des emplacements périmés a
        # déjà écrit ce qu'il a fait, et c'est précisément ce que l'utilisateur
        # doit lire après un rechargement.
        outcome.details.update({
            "newLocations": len(discovered),
            "totalLocations": len(existing) + len(discovered),
            "journalsCreated": created,
            "warehouses": sorted({l.warehouse_id for l in lines}),
        })
        return outcome

    def freeze_book_stock(self, campaign: Campaign) -> Campaign:
        """Lock the snapshot. From here on, variances are reproducible."""
        ctx = self.ctx
        ctx.guard(campaign, "book_stock")
        if ctx.book_stock.count(campaign.id) == 0:
            raise ValidationError(
                "Impossible de geler un stock ERP vide : chargez d'abord le "
                "snapshot ERP."
            )
        with ctx.db.transaction() as conn:
            ctx.campaigns.update_status(
                campaign.id,
                campaign.status,
                actor=ctx.actor,
                timestamps={"book_stock_frozen_at": utcnow()},
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.FREEZE,
                entity_type="book_stock",
                summary="Stock ERP gelé",
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return ctx.campaigns.get(campaign.id)

    # ------------------------------------------------------------- backflush

    def import_backflush(
        self,
        campaign: Campaign,
        *,
        period_start: dt.date | None = None,
        period_end: dt.date | None = None,
        allow_partial: bool = False,
        **kwargs: Any,
    ) -> ImportOutcome:
        """Freeze the backflush variance of one period onto the campaign.

        Read once and written here rather than queried on every display. The gold
        table is rebuilt in full every night — a nomenclature correction, a
        movement booked late, a standard cost updated — so the variance of a week
        already past can move. Reading live would mean the same campaign consulted
        a fortnight apart shows two figures, and a residual variance a controller
        signed off could no longer be reproduced.

        Refreshing stays possible for as long as the campaign is open, and the
        freeze matrix stops it at closure. Each refresh replaces the whole read:
        an article whose variance has gone must disappear, not keep an old figure
        under new bounds.
        """
        ctx = self.ctx
        ctx.guard(campaign, "backflush")

        start, end = _require_period(period_start, period_end)
        _, parsed = self.parser.parse(
            "backflush", period_start=start, period_end=end, **kwargs
        )
        outcome = _base_outcome("backflush", parsed)
        outcome.storage_path = self.batches.archive(campaign, "backflush", kwargs)

        # Même règle que les flux de la comparaison : la table couvre toute
        # l'usine, et un article exclu du périmètre n'a pas d'écart à porter.
        items = ctx.referentials.items_in_scope(campaign.id)
        lines, errors = map_backflush(
            campaign.id, parsed.rows,
            period_start=start, period_end=end, items=items,
        )
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        # `replace` : l'écart de la période remplace le précédent. Un article
        # refusé disparaît donc de l'écart au lieu d'y manquer, et la
        # consommation qu'il portait cesse d'exister pour l'analyse.
        self.batches.refuse_if_partial(
            outcome, accepted=len(lines), allow_partial=allow_partial,
            what="L'écart backflush",
        )

        batch_id = new_id()
        with ctx.db.transaction() as conn:
            written = ctx.backflush.replace(
                campaign.id, lines, batch_id=batch_id, conn=conn
            )
            # Les lignes portaient un identifiant de lot dont aucune ligne
            # d'`import_batch` n'existait : l'écran d'historique ne montrait pas
            # ce chargement, et la pièce archivée n'était rattachée à rien.
            ctx.imports.create(
                campaign_id=campaign.id,
                target="backflush",
                filename=self.batches.origin_of("backflush", kwargs),
                content_hash=_hash_of(kwargs),
                storage_path=outcome.storage_path,
                rows_received=outcome.rows_received,
                rows_accepted=written,
                rows_rejected=outcome.rows_rejected,
                report=outcome.as_dict(),
                imported_by=ctx.actor,
                batch_id=batch_id,
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="backflush",
                summary=(
                    f"Écart backflush figé : {written} article(s), "
                    f"du {start:%d/%m/%Y} au {end:%d/%m/%Y} (exclu)"
                ),
                after={
                    "items": written,
                    "periodStart": start.isoformat(),
                    "periodEnd": end.isoformat(),
                },
                conn=conn,
            )

        outcome.batch_id = batch_id
        outcome.rows_accepted = written
        # The two halves are reported separately from the net: forty of
        # under-consumption against thirty-eight of over-consumption does not
        # read like two, and the summary is where that distinction survives.
        outcome.details.update({
            "periodStart": start.isoformat(),
            "periodEnd": end.isoformat(),
            "weeks": (end - start).days // 7,
            "netQty": float(sum(line.net_qty for line in lines)),
            "underConsumed": float(sum(line.under_consumed_qty for line in lines)),
            "overConsumed": float(sum(line.over_consumed_qty for line in lines)),
            "outOfScope": max(0, len(parsed.rows) - written - len(errors)),
        })
        return outcome

    # -------------------------------------------------------- count journals

    def import_journal_lines(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        """Load or refresh the ERP counting-journal lines.

        Behaviour required by the specification:

        * reloading replaces the imported values but never a manual correction;
        * a journal present in the file but absent from the referential is
          created — unless its location is disabled, in which case the lines are
          rejected with an explicit message rather than silently dropped;
        * a journal whose lines are all flagged posted becomes ``POSTED``.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        _, parsed = self.parser.parse("count_journal_lines", **kwargs)
        outcome = _base_outcome("count_journal_lines", parsed)
        outcome.storage_path = self.batches.archive(campaign, "count_journal_lines", kwargs)
        if not parsed.rows:
            return outcome

        imported, errors, warnings = map_journal_lines(parsed.rows)
        outcome.errors.extend(errors)
        outcome.warnings.extend(warnings)
        outcome.rows_rejected += len(errors)
        if not imported:
            return outcome

        locations = ctx.referentials.locations_by_key(campaign.id)
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}

        keys_in_file = {
            LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id)
            for l in imported
        }
        disabled = {
            k for k in keys_in_file
            if k in locations and locations[k].status is LocationStatus.DISABLED
        }
        to_create = [
            k for k in keys_in_file
            if k not in journals and k not in disabled
        ]

        if disabled:
            for line_no, line in enumerate(imported, start=2):
                key = LocationKey(
                    warehouse_id=line.warehouse_id, location_id=line.location_id
                )
                if key in disabled:
                    outcome.warnings.append(
                        RowError(
                            line_no, "location_id", str(key),
                            f"L'emplacement {key} est désactivé : la ligne est "
                            "ignorée. Réactivez-le pour l'inclure.",
                        )
                    )

        with ctx.db.transaction() as conn:
            if to_create:
                kinds = {}
                for key in to_create:
                    matching = next(
                        (l for l in imported
                         if l.warehouse_id == key.warehouse_id
                         and l.location_id == key.location_id),
                        None,
                    )
                    kinds[key] = matching.kind if matching else JournalKind.INVV
                ctx.journals.ensure_journals(
                    campaign.id, to_create, kinds=kinds,
                    auto_created=True, actor=ctx.actor, conn=conn,
                )
            # Read back through the *same* connection: the journals just created
            # are not visible to another pooled connection until this
            # transaction commits, and their lines would be dropped.
            journals = {j.key: j for j in ctx.journals.list(campaign.id, conn=conn)}

            lines: list[CountJournalLine] = []
            posted_flags: dict[str, list[bool]] = {}
            journal_numbers: dict[str, str] = {}
            for line in imported:
                key = LocationKey(
                    warehouse_id=line.warehouse_id, location_id=line.location_id
                )
                if key in disabled:
                    continue
                journal = journals.get(key)
                if journal is None:  # pragma: no cover - defensive
                    continue
                lines.append(
                    CountJournalLine(
                        id=new_id(),
                        journal_id=journal.id,
                        campaign_id=campaign.id,
                        item_number=line.item_number,
                        qty_imported=line.qty,
                        unit=line.unit,
                        source=DataSource.ERP_IMPORT,
                        updated_by=ctx.actor,
                    )
                )
                posted_flags.setdefault(journal.id, []).append(line.is_posted)
                if line.journal_number:
                    journal_numbers[journal.id] = line.journal_number

            touched = sorted(posted_flags)
            ctx.journals.replace_imported_lines(
                campaign.id, touched, lines, conn=conn
            )

            fully_posted = [jid for jid, flags in posted_flags.items() if all(flags)]
            partially = [
                jid for jid, flags in posted_flags.items()
                if any(flags) and not all(flags)
            ]
            in_progress = [
                jid for jid, flags in posted_flags.items() if not any(flags)
            ]
            if fully_posted:
                ctx.journals.set_status(
                    campaign.id, fully_posted, JournalStatus.POSTED,
                    actor=ctx.actor, posted_at=utcnow(), conn=conn,
                )
            for group in (partially, in_progress):
                running = [
                    jid for jid in group
                    if journals and _journal_by_id(journals, jid) is not None
                    and _journal_by_id(journals, jid).status is not JournalStatus.POSTED
                ]
                if running:
                    ctx.journals.set_status(
                        campaign.id, running, JournalStatus.IN_PROGRESS,
                        actor=ctx.actor, conn=conn,
                    )

            outcome.rows_accepted = len(lines)
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "count_journal_lines", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="count_journal",
                summary=(
                    f"Import de {len(lines)} ligne(s) de comptage sur "
                    f"{len(touched)} journal(aux) ; {len(to_create)} journal(aux) "
                    f"créé(s) automatiquement ; {len(fully_posted)} posté(s)."
                ),
                after={
                    "lines": len(lines),
                    "journalsTouched": len(touched),
                    "journalsCreated": len(to_create),
                    "journalsPosted": len(fully_posted),
                    "disabledLocationsSkipped": sorted(str(k) for k in disabled),
                },
                conn=conn,
            )

        outcome.details = {
            "journalsTouched": len(touched),
            "journalsCreated": len(to_create),
            "journalsPosted": len(fully_posted),
            "journalsInProgress": len(partially) + len(in_progress),
            "disabledLocationsSkipped": sorted(str(k) for k in disabled),
        }
        return outcome

    # ----------------------------------------------------------- count sheets

    def import_count_sheets(self, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
        """Load the ``[feuille, article, section]`` list the sheets will carry.

        This is the preparation-time counterpart of printing: it decides *what*
        each zone will be asked to count, months before anybody counts it.

        Four rules, each of which exists because its opposite lost data once:

        * a sheet code nobody has seen creates its zone (and the zone's passes);
          a known code is **completed**, never recreated — reloading a corrected
          file must not wipe a list somebody has been curating;
        * an article absent from the campaign's referential is a row error, not
          an article created on the fly (see :func:`map_count_sheets`);
        * sections go through the same vocabulary as the client-side paste, so a
          file accepted on one side is accepted on the other;
        * lines land on **every** counting pass of the zone, with empty
          quantities. Pre-filling only pass 1 would leave the second counter
          blind and turn the arbitration into a comparison against nothing.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_sheets")
        _, parsed = self.parser.parse("count_sheets", **kwargs)
        outcome = _base_outcome("count_sheets", parsed)
        outcome.storage_path = self.batches.archive(campaign, "count_sheets", kwargs)
        if not parsed.rows:
            return outcome

        items = ctx.referentials.items_by_number(campaign.id)
        if not items:
            raise ValidationError(
                "Le référentiel articles de cette campagne est vide : chargez-le "
                "avant les feuilles de comptage. Un import de feuilles ne crée "
                "jamais d'article."
            )

        prepared, errors = map_count_sheets(parsed.rows, items=items)
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)
        if not prepared:
            return outcome

        by_code: dict[str, list[PreparedSheetRow]] = {}
        for row in prepared:
            by_code.setdefault(row.sheet_code, []).append(row)

        source = _source_of(kwargs.get("mode", "file"))
        created_zones: list[str] = []
        completed_zones: list[str] = []
        lines_created = 0

        with ctx.db.transaction() as conn:
            # Read back through the transaction's own connection: the zones
            # created below are invisible to another pooled connection until it
            # commits, and their sheets would be looked up on an empty set.
            zones = {z.code: z for z in ctx.sheets.list_zones(campaign.id, conn=conn)}
            next_order = max((z.display_order for z in zones.values()), default=0)

            for offset, code in enumerate(sorted(by_code), start=1):
                rows = by_code[code]
                zone = zones.get(code)
                if zone is None:
                    zone = Zone(
                        id=new_id(),
                        campaign_id=campaign.id,
                        code=code,
                        display_order=next_order + offset,
                        passes=campaign.config.generic_passes,
                    )
                    ctx.sheets.create_zone(zone, actor=ctx.actor, conn=conn)
                    ctx.sheets.ensure_sheets(
                        campaign.id, zone.id, passes_for(zone.passes),
                        actor=ctx.actor, conn=conn,
                    )
                    created_zones.append(code)
                else:
                    completed_zones.append(code)
                    if zone.free_entry:
                        # A zone that receives a pre-printed list is no longer a
                        # free-entry sheet, whatever it was created as.
                        ctx.sheets.update_zones(
                            campaign.id, [zone.id], actor=ctx.actor,
                            free_entry=False, conn=conn,
                        )

                for sheet in ctx.sheets.list_sheets(
                    campaign.id, zone_id=zone.id, conn=conn
                ):
                    existing = ctx.sheets.list_sheet_lines(sheet.id, conn=conn)
                    known = {(l.item_number, l.section) for l in existing}
                    order = max((l.display_order for l in existing), default=-1)
                    new_lines: list[CountSheetLine] = []
                    for row in rows:
                        if row.key in known:
                            continue
                        known.add(row.key)
                        order += 1
                        new_lines.append(
                            CountSheetLine(
                                id=new_id(),
                                sheet_id=sheet.id,
                                campaign_id=campaign.id,
                                item_number=row.item_number,
                                section=row.section,
                                # Both quantities left unset: a prepared line is
                                # not a counted line, and a blank cell is not a
                                # zero anywhere in this application.
                                unit=row.unit,
                                source=source,
                                display_order=order,
                            )
                        )
                    if new_lines:
                        lines_created += ctx.sheets.upsert_sheet_lines(
                            new_lines, actor=ctx.actor, conn=conn
                        )

            outcome.rows_accepted = len(prepared)
            outcome.details = {
                "zonesCreated": created_zones,
                "zonesCompleted": sorted(set(completed_zones)),
                "sheetLinesCreated": lines_created,
            }
            outcome.batch_id = self.batches.record_batch(
                campaign.id, "count_sheets", outcome, conn=conn, **kwargs
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="count_sheet_line",
                summary=(
                    f"Import de {len(prepared)} ligne(s) de feuille sur "
                    f"{len(by_code)} feuille(s) ; {len(created_zones)} zone(s) "
                    f"créée(s) ; {lines_created} ligne(s) pré-imprimée(s)."
                ),
                after=outcome.details,
                conn=conn,
            )
        return outcome

    # --------------------------------------------------------------- helpers

def _journal_by_id(journals: dict[LocationKey, Any], journal_id: str) -> Any:
    for journal in journals.values():
        if journal.id == journal_id:
            return journal
    return None


def monday_of(day: dt.date) -> dt.date:
    """The Monday of *day*'s ISO week."""
    return day - dt.timedelta(days=day.weekday())


def suggested_period(
    count_date: dt.date, *, previous: dt.date | None = None, weeks: int = 13
) -> tuple[dt.date, dt.date]:
    """A period the screen can propose, and that a user can override.

    The end is the Monday of the counting week, *excluded*: the week in which the
    count happens is cut in two by the count itself, and charging a whole week's
    production against the days that preceded it would overstate the variance on
    every article produced that week.

    The start is the previous campaign's counting week when there is one — that
    is the period nobody has looked at yet — and a quarter otherwise, which is
    long enough to be worth reading and short enough to stay explainable.
    """
    end = monday_of(count_date)
    if previous is not None:
        start = monday_of(previous)
        if start < end:
            return start, end
    return end - dt.timedelta(weeks=weeks), end
