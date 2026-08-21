"""Reconciling two campaigns through the flows of the period between them.

Two inventories bracket a stretch of time. In between, an article's stock did
not move at random: it was received, produced, shipped, consumed and scrapped in
quantities the plant can put a number on. So the question this module answers is
a closed one — starting from the stock of the first inventory and applying the
period's flows, do we land on the stock of the second?

    stock attendu = stock initial (campagne initiale)
                  + réceptions          (chargées)
                  + production parent   (lue dans le backflush, dédoublonnée)
                  − expéditions         (chargées)
                  − consommation théo.  (lue dans le backflush)
                  − rebuts              (chargés, étape facultative)

What the gap between expected and counted measures is what none of those flows
explains. That is a different question from an inventory variance, and worth
keeping separate: an inventory variance compares a count to the ERP at one
instant, this compares two counts through everything that happened between them.

Three design decisions carry the weight.

**The earlier campaign is the earlier one by count date.** Never by creation
date. Campaigns created in one order and counted in the other exist, and it is
the count that bounds the period.

**Both ERP measures are frozen with the run.** The gold table is rebuilt every
night, so a past week can change; a report that cannot be replayed identically
does not survive its first review meeting.

**A reference missing from one of the two readings is not a zero.** It is a hole
in the comparison, and reading it as a zero would manufacture a variance the
size of the whole stock. Those lines are reported apart, never summed in.

**Which stock brackets the flows is chosen at read time.** Physique or ERP, on
each end independently, which makes four combinations — and each answers a
different question. Physique/physique measures what the plant actually lost;
ERP/ERP, what the system believes it lost; the two crossed pairs say where a
divergence between the two was born. None of it touches the run: the loaded
quantities and the frozen ERP snapshot are the same in all four.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction, FlowKind, FlowSource, StockBasis
from ..domain.models import (
    Campaign,
    Item,
    StockFlowErp,
    StockFlowInput,
    StockFlowLine,
    StockFlowRun,
    normalise_key,
)
from ..domain.quantities import ZERO, quantize_money, quantize_qty
from ..errors import InventoryError, NotFoundError, ValidationError
from .context import ServiceContext
from .import_service import ImportOutcome, monday_of

log = logging.getLogger(__name__)

__all__ = ["StockFlowService", "FLOW_LABELS", "BASIS_LABELS"]

#: How each loaded step names itself on screen and in the audit trail.
FLOW_LABELS = {
    FlowKind.RECEIPT: "réceptions",
    FlowKind.SHIPMENT: "expéditions",
    FlowKind.SCRAP: "rebuts",
}

#: How each reading names itself. « Physique » is the counted stock adjustments
#: included — the same word the rest of the application uses for it, because two
#: screens spending it on two different quantities is how a report gets misread.
BASIS_LABELS = {
    StockBasis.PHYSICAL: "Physique",
    StockBasis.BOOK: "ERP",
}

#: La même chose en toutes lettres, pour les titres et les phrases. Séparé et
#: non dérivé : « ERP » ne se met pas en minuscules au milieu d'une phrase, et
#: une seule forme ne peut pas servir de pastille *et* de titre d'axe.
BASIS_STOCK_LABELS = {
    StockBasis.PHYSICAL: "Stock physique",
    StockBasis.BOOK: "Stock ERP",
}

#: The sub-section each loaded step opens in. Declared here rather than on the
#: screen so the button that says « 0 article » and the tab that shows which
#: ones can never point at two different places.
_STEP_VIEWS = {
    FlowKind.RECEIPT: "receptions",
    FlowKind.SHIPMENT: "expeditions",
    FlowKind.SCRAP: "rebuts",
}

#: Which field of a movements row feeds each loaded step.
_FLOW_FIELDS = {
    FlowKind.RECEIPT: "reception",
    FlowKind.SHIPMENT: "expedition",
    FlowKind.SCRAP: "rebut",
}


class StockFlowService:
    """Compare two campaigns through the flows of the period between them."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------- runs

    def comparable_campaigns(self, campaign: Campaign) -> list[dict[str, Any]]:
        """The campaigns this one can be compared against.

        Only those counted *earlier*: the comparison walks forward through time,
        and offering a later campaign as a starting point would produce a period
        running backwards. Ordered by count date, most recent first — the
        previous inventory is the one people mean nine times out of ten.
        """
        earlier = [
            other for other in self.ctx.campaigns.list(limit=200)
            if other.id != campaign.id and other.count_date < campaign.count_date
        ]
        earlier.sort(key=lambda c: c.count_date, reverse=True)
        return [
            {
                "id": other.id,
                "code": other.code,
                "label": other.label,
                "countDate": other.count_date.isoformat(),
                "status": str(other.status),
                "weeks": (
                    monday_of(campaign.count_date) - monday_of(other.count_date)
                ).days // 7,
            }
            for other in earlier
        ]

    def open_run(self, campaign: Campaign, baseline_id: str) -> StockFlowRun:
        """Start — or re-open — the comparison with one earlier campaign.

        Idempotent on the pair: choosing the same baseline twice is somebody
        coming back to their comparison, not starting a second one. A second row
        would split the loaded quantities across two runs, of which the screen
        would only ever show one.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        baseline = self._baseline(campaign, baseline_id)

        start = monday_of(baseline.count_date)
        end = monday_of(campaign.count_date)
        if end <= start:
            raise ValidationError(
                f"« {baseline.code} » et « {campaign.code} » ont été comptées la "
                "même semaine : il n'y a pas de période entre les deux.",
                debut=baseline.count_date.isoformat(),
                fin=campaign.count_date.isoformat(),
            )

        run = ctx.stock_flow.upsert_run(
            StockFlowRun(
                id=new_id(),
                campaign_id=campaign.id,
                baseline_campaign_id=baseline.id,
                period_start=start,
                period_end=end,
            ),
            actor=ctx.actor,
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.CREATE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary=(
                f"Comparaison avec « {baseline.code} », "
                f"du {start:%d/%m/%Y} au {end:%d/%m/%Y} (exclu)"
            ),
            after={
                "baseline": baseline.code,
                "periodStart": start.isoformat(),
                "periodEnd": end.isoformat(),
            },
        )
        return run

    def list_runs(self, campaign: Campaign) -> list[dict[str, Any]]:
        codes = {c.id: c for c in self.ctx.campaigns.list(limit=200)}
        out = []
        for run in self.ctx.stock_flow.list_runs(campaign.id):
            baseline = codes.get(run.baseline_campaign_id)
            out.append({
                **_run_payload(run),
                "baselineCode": baseline.code if baseline else "",
                "baselineLabel": baseline.label if baseline else "",
            })
        return out

    def delete_run(self, campaign: Campaign, run_id: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        ctx.stock_flow.delete_run(run.id)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary="Comparaison supprimée",
        )

    # -------------------------------------------------------- loaded quantities

    def load_inputs(
        self, campaign: Campaign, run_id: str, kind: FlowKind, **kwargs: Any
    ) -> ImportOutcome:
        """Load one of the three steps: receipts, shipments or scrap.

        Scoped to its own step. The three loads are three separate gestures, and
        a user correcting their shipments must not lose the receipts they entered
        ten minutes earlier.
        """
        from ..ingest import map_stock_flow_inputs
        from .import_service import ImportService, _base_outcome

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        _, parsed = ImportService(ctx).parse("stock_flow", **kwargs)
        outcome = _base_outcome("stock_flow", parsed)
        items = self._in_scope(campaign)
        lines, errors = map_stock_flow_inputs(
            run.id, parsed.rows, kind=kind, items=items
        )
        outcome.errors.extend(errors)
        outcome.rows_rejected += len(errors)

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_inputs(run.id, kind, lines, conn=conn)
            if kind is FlowKind.SCRAP:
                ctx.stock_flow.mark_scrap_loaded(
                    run.id, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_input",
                entity_id=run.id,
                summary=f"{written} ligne(s) de {FLOW_LABELS[kind]} chargée(s)",
                after={"kind": str(kind), "rows": written},
                conn=conn,
            )

        outcome.rows_accepted = written
        outcome.details.update({
            "kind": str(kind),
            "totalQty": float(sum(line.qty for line in lines)),
            "outOfScope": max(0, len(parsed.rows) - written - len(errors)),
        })
        return outcome

    def save_inputs(
        self,
        campaign: Campaign,
        run_id: str,
        kind: FlowKind,
        rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Write back one step's grid, as edited on screen.

        A full replacement of that step and of nothing else: what the grid shows
        *is* the step, so a row deleted on screen has to disappear in the
        database too — merging would make deletion the one edit the grid cannot
        express. The other two steps are untouched.

        Everything written here is marked ``MANUAL``, including a row that came
        from the ERP and was left alone: once a human has passed over the grid
        and saved it, the whole step is their figure, and claiming otherwise for
        the rows they happened not to touch would be a distinction the screen
        cannot honestly draw.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        items = self._in_scope(campaign)

        lines: list[StockFlowInput] = []
        unknown: list[str] = []
        for row in rows:
            number = str(row.get("item_number") or row.get("itemNumber") or "").strip()
            if not number:
                continue
            line = StockFlowInput(
                run_id=run.id,
                item_number=number,
                kind=kind,
                qty=row.get("qty") or 0,
                unit=str(row.get("unit") or "PCE"),
                source=FlowSource.MANUAL,
            )
            if line.item_number not in items:
                unknown.append(line.item_number)
                continue
            lines.append(line)

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_inputs(run.id, kind, lines, conn=conn)
            if kind is FlowKind.SCRAP:
                ctx.stock_flow.mark_scrap_loaded(
                    run.id, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="stock_flow_input",
                entity_id=run.id,
                summary=f"{written} ligne(s) de {FLOW_LABELS[kind]} corrigée(s)",
                after={"kind": str(kind), "rows": written},
                conn=conn,
            )
        return {
            "kind": str(kind),
            "rows": written,
            "totalQty": float(sum(line.qty for line in lines)),
            # Une référence hors référentiel n'est pas rejetée en silence : elle
            # est nommée, parce que c'est presque toujours une faute de frappe.
            "unknown": unknown[:20],
            "unknownCount": len(unknown),
        }

    def save_erp(
        self, campaign: Campaign, run_id: str, rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Write back the production / theoretical-consumption grid.

        Same rule as the loaded steps: what the grid shows replaces the snapshot
        wholesale, and the result is marked as a human's figure. The next ERP
        read overwrites it — which is the intended way out of a correction that
        turned out to be wrong.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        items = self._in_scope(campaign)

        lines: list[StockFlowErp] = []
        unknown: list[str] = []
        for row in rows:
            number = str(row.get("item_number") or row.get("itemNumber") or "").strip()
            if not number:
                continue
            line = StockFlowErp(
                run_id=run.id,
                item_number=number,
                produced_qty=row.get("produced_qty") or row.get("producedQty") or 0,
                consumed_qty=row.get("consumed_qty") or row.get("consumedQty") or 0,
                source=FlowSource.MANUAL,
            )
            if line.item_number not in items:
                unknown.append(line.item_number)
                continue
            lines.append(line)

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_erp(run.id, lines, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="stock_flow_erp",
                entity_id=run.id,
                summary=f"{written} ligne(s) de production/consommation corrigée(s)",
                after={"rows": written},
                conn=conn,
            )
        return {
            "rows": written,
            "producedQty": float(sum(line.produced_qty for line in lines)),
            "consumedQty": float(sum(line.consumed_qty for line in lines)),
            "unknown": unknown[:20],
            "unknownCount": len(unknown),
        }

    def skip_scrap(self, campaign: Campaign, run_id: str) -> StockFlowRun:
        """Record that the scrap step was deliberately left out.

        Written down rather than inferred from an empty table: « no scrap » and
        « scrap not entered » are the same zero and two different readings of the
        report, and only one of them is a complete comparison.
        """
        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)
        with ctx.db.transaction() as conn:
            ctx.stock_flow.replace_inputs(run.id, FlowKind.SCRAP, [], conn=conn)
        ctx.stock_flow.mark_scrap_loaded(run.id, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="stock_flow_run",
            entity_id=run.id,
            summary="Étape rebuts ignorée volontairement",
        )
        return self._run(campaign, run_id)

    # ------------------------------------------------------------- ERP figures

    def refresh_movements(
        self, campaign: Campaign, run_id: str, kind: FlowKind
    ) -> dict[str, Any]:
        """Read one loaded step from the ERP instead of asking for a file.

        The three quantities the comparison used to demand by hand — receipts,
        shipments, scrap — are all recorded in the ERP already. Retyping what a
        warehouse already knows is where the legacy process produced most of its
        errors, and an inventory comparison is precisely where such an error is
        invisible: a wrong receipt total shifts every expected stock by the same
        amount and nothing on screen looks odd.

        The ERP signs these movements its own way — a return is a negative
        shipment, scrap leaves stock so it is negative. The step carries the
        direction, so the magnitude is what is stored; the net sign is reported
        back so a period whose returns outweigh its shipments is visible rather
        than silently flipped.
        """
        from ..ingest.erp import ErpReader, reading_from_mirror

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        reader = ErpReader()
        rows = reader.fetch_movements(
            kind,
            period_start=run.period_start,
            period_end=run.period_end,
            limit=ctx.settings.max_import_rows,
        )
        # Comme pour la production : on construit le modèle — donc on normalise
        # la référence — *avant* de la confronter au référentiel. Comparer la
        # chaîne brute de l'ERP à des clés normalisées écartait des lignes
        # parfaitement valides sans rien dire.
        items = self._in_scope(campaign)
        read = [
            StockFlowInput(
                run_id=run.id,
                item_number=row["item_number"],
                kind=kind,
                qty=row["qty"],
                unit=items[row["item_number"]].unit
                if row["item_number"] in items
                else "PCE",
                source=FlowSource.ERP,
            )
            for row in rows
        ]
        lines = [line for line in read if line.item_number in items]
        net = sum(Decimal(str(row["qty"])) for row in rows) if rows else ZERO

        now = dt.datetime.now(dt.UTC)
        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_inputs(run.id, kind, lines, conn=conn)
            ctx.stock_flow.mark_refreshed(
                run.id, kind, at=now, actor=ctx.actor, conn=conn
            )
            if kind is FlowKind.SCRAP:
                ctx.stock_flow.mark_scrap_loaded(
                    run.id, actor=ctx.actor, conn=conn
                )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_input",
                entity_id=run.id,
                summary=(
                    f"{written} ligne(s) de {FLOW_LABELS[kind]} lue(s) dans l'ERP"
                ),
                after={"kind": str(kind), "rows": written, "source": "ERP"},
                conn=conn,
            )

        return {
            "kind": str(kind),
            "label": FLOW_LABELS[kind].capitalize(),
            "items": written,
            "rowsRead": len(rows),
            "outOfScope": len(rows) - written,
            "totalQty": float(sum(line.qty for line in lines)),
            "netQty": float(quantize_qty(net)),
            "periodStart": run.period_start.isoformat(),
            "periodEnd": run.period_end.isoformat(),
            "source": reader.movements_source(kind),
            # Comme pour la production : une table vide ne se lit pas pareil
            # selon d'où elle vient. Dans le catalogue c'est une période sans
            # mouvement, dans le miroir c'est le plus souvent le job de
            # synchronisation qui n'a pas encore tourné.
            "mirror": reading_from_mirror(),
        }

    def refresh_all(self, campaign: Campaign, run_id: str) -> dict[str, Any]:
        """Read the five ERP measures in one gesture — and one round trip.

        They all sit on the same row of the movements table, so reading it four
        times and writing four transactions was four times the work for the same
        answer. One read, one transaction, and the whole thing either lands or
        leaves the previous figures intact.
        """
        from ..ingest.erp import ErpReader, reading_from_mirror

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        reader = ErpReader()
        try:
            rows = reader.fetch_all_flows(
                period_start=run.period_start,
                period_end=run.period_end,
                limit=ctx.settings.max_import_rows,
            )
        except InventoryError as exc:
            log.warning("Lecture ERP des flux impossible : %s", exc)
            return {
                "steps": [],
                "loaded": 0,
                "failed": 1,
                "error": str(exc),
                "source": reader.movements_source(FlowKind.RECEIPT),
                "mirror": reading_from_mirror(),
            }

        items = self._in_scope(campaign)
        inputs: dict[FlowKind, list[StockFlowInput]] = {
            kind: [] for kind in _FLOW_FIELDS
        }
        erp_lines: list[StockFlowErp] = []
        for row in rows:
            number = normalise_key(str(row["item_number"]))
            item = items.get(number)
            if item is None:
                continue
            for kind, field in _FLOW_FIELDS.items():
                if row[field]:
                    inputs[kind].append(StockFlowInput(
                        run_id=run.id, item_number=number, kind=kind,
                        qty=row[field], unit=item.unit, source=FlowSource.ERP,
                    ))
            if row["production"] or row["conso_theorique"]:
                erp_lines.append(StockFlowErp(
                    run_id=run.id, item_number=number,
                    produced_qty=row["production"],
                    consumed_qty=row["conso_theorique"],
                    source=FlowSource.ERP,
                ))

        now = dt.datetime.now(dt.UTC)
        steps: list[dict[str, Any]] = []
        with ctx.db.transaction() as conn:
            for kind, lines in inputs.items():
                written = ctx.stock_flow.replace_inputs(
                    run.id, kind, lines, conn=conn
                )
                ctx.stock_flow.mark_refreshed(
                    run.id, kind, at=now, actor=ctx.actor, conn=conn
                )
                steps.append({
                    "ok": True,
                    "kind": str(kind),
                    "label": FLOW_LABELS[kind].capitalize(),
                    "items": written,
                    "totalQty": float(sum(line.qty for line in lines)),
                    "netQty": float(quantize_qty(
                        sum((Decimal(str(r[_FLOW_FIELDS[kind]])) for r in rows), ZERO)
                    )),
                })
            ctx.stock_flow.mark_scrap_loaded(run.id, actor=ctx.actor, conn=conn)
            erp_written = ctx.stock_flow.replace_erp(run.id, erp_lines, conn=conn)
            ctx.stock_flow.mark_erp_refreshed(
                run.id, at=now, actor=ctx.actor, conn=conn
            )
            steps.append({
                "ok": True,
                "kind": "ERP",
                "label": "Production et consommation théorique",
                "items": erp_written,
                "producedQty": float(sum(e.produced_qty for e in erp_lines)),
                "consumedQty": float(sum(e.consumed_qty for e in erp_lines)),
            })
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_input",
                entity_id=run.id,
                summary=(
                    f"Flux de la période lus dans l'ERP : {len(rows)} référence(s) "
                    f"lue(s), {len(items)} au périmètre"
                ),
                after={"rowsRead": len(rows), "source": "ERP"},
                conn=conn,
            )

        return {
            "steps": steps,
            "loaded": len(steps),
            "failed": 0,
            "rowsRead": len(rows),
            "outOfScope": len(rows) - len({
                line.item_number
                for lines in inputs.values() for line in lines
            } | {e.item_number for e in erp_lines}),
            "periodStart": run.period_start.isoformat(),
            "periodEnd": run.period_end.isoformat(),
            "source": reader.movements_source(FlowKind.RECEIPT),
            "mirror": reading_from_mirror(),
        }

    def _in_scope(self, campaign: Campaign) -> dict[str, Item]:
        """Les articles pour lesquels un flux peut être chargé.

        Un article laissé hors du périmètre ne doit pas revenir par ses flux :
        son stock attendu serait calculé et affiché comme un écart que personne
        n'a demandé.
        """
        return self.ctx.referentials.items_in_scope(campaign.id)

    def refresh_erp(self, campaign: Campaign, run_id: str) -> dict[str, Any]:
        """Read production and theoretical consumption, and freeze them.

        Two columns of the same movements table the three loaded steps read, so
        the five flows of one comparison now come from one place. They used to be
        derived from the backflush fact table, where a parent's output is
        repeated on every component line and had to be collapsed by week before
        being summed; the silver table publishes both already consolidated.
        """
        from ..ingest.erp import ErpReader, reading_from_mirror

        ctx = self.ctx
        ctx.guard(campaign, "stock_flow")
        run = self._run(campaign, run_id)

        reader = ErpReader()
        rows = reader.fetch_stock_flow(
            period_start=run.period_start,
            period_end=run.period_end,
            limit=ctx.settings.max_import_rows,
        )
        # Le modèle est construit *avant* le filtre, pas après. La source écrit
        # ses identifiants comme l'ERP les lui donne, le référentiel les stocke
        # normalisés (majuscules, espaces réduits) : comparer la chaîne brute à
        # des clés normalisées écartait des lignes parfaitement valides, et
        # l'écran n'annonçait qu'un « 0 article » sans dire pourquoi. Tous les
        # autres imports construisent puis filtrent ; celui-ci faisait l'inverse.
        items = ctx.referentials.items_by_number(campaign.id)
        read = [
            StockFlowErp(
                run_id=run.id,
                item_number=row["item_number"],
                produced_qty=row["produced_qty"],
                consumed_qty=row["consumed_qty"],
            )
            for row in rows
        ]
        lines = [line for line in read if line.item_number in items]

        with ctx.db.transaction() as conn:
            written = ctx.stock_flow.replace_erp(run.id, lines, conn=conn)
            ctx.stock_flow.mark_erp_refreshed(
                run.id, at=dt.datetime.now(dt.UTC), actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.IMPORT,
                entity_type="stock_flow_erp",
                entity_id=run.id,
                summary=(
                    f"Production et consommation théorique figées : "
                    f"{written} article(s)"
                ),
                after={"items": written},
                conn=conn,
            )

        # Trois situations très différentes donnaient le même « 0 article » :
        # l'ERP n'a rien sur la période, il a répondu mais aucun de ses articles
        # n'est au référentiel de la campagne, ou tout s'est bien passé. Le
        # chiffre seul ne les distingue pas — d'où les deux compteurs, la période
        # réellement interrogée et la table lue, qui permettent de rejouer la
        # même requête à la main.
        return {
            "items": written,
            "rowsRead": len(rows),
            "outOfScope": len(rows) - written,
            "producedQty": float(sum(line.produced_qty for line in lines)),
            "consumedQty": float(sum(line.consumed_qty for line in lines)),
            "periodStart": run.period_start.isoformat(),
            "periodEnd": run.period_end.isoformat(),
            "source": reader.movements_source(FlowKind.RECEIPT),
            # Une table vide ne se lit pas pareil selon d'où elle vient : dans le
            # catalogue c'est une période sans production, dans le miroir c'est
            # souvent le job de synchronisation qui n'a pas encore tourné.
            "mirror": reading_from_mirror(),
        }

    # ------------------------------------------------------------ step details

    def step_rows(
        self, campaign: Campaign, run_id: str, kind: FlowKind
    ) -> list[dict[str, Any]]:
        """One loaded step, article by article, ready for an editable grid.

        The designation is joined in here rather than left to the screen: a grid
        of bare references is a grid nobody can proof-read, and the referential
        is already in memory for the scope check.
        """
        ctx = self.ctx
        run = self._run(campaign, run_id)
        items = ctx.referentials.items_by_number(campaign.id)
        return [
            {
                "itemNumber": entry.item_number,
                "name": items[entry.item_number].name
                if entry.item_number in items
                else "",
                "unit": entry.unit,
                "qty": float(entry.qty),
                "source": str(entry.source),
            }
            for entry in ctx.stock_flow.list_inputs(run.id)
            if entry.kind is kind
        ]

    def erp_rows(self, campaign: Campaign, run_id: str) -> list[dict[str, Any]]:
        """The frozen production / theoretical-consumption snapshot, as a grid."""
        ctx = self.ctx
        run = self._run(campaign, run_id)
        items = ctx.referentials.items_by_number(campaign.id)
        return [
            {
                "itemNumber": entry.item_number,
                "name": items[entry.item_number].name
                if entry.item_number in items
                else "",
                "unit": items[entry.item_number].unit
                if entry.item_number in items
                else "PCE",
                "producedQty": float(entry.produced_qty),
                "consumedQty": float(entry.consumed_qty),
                "source": str(entry.source),
            }
            for entry in ctx.stock_flow.list_erp(run.id)
        ]

    # ----------------------------------------------------------------- report

    def report(
        self,
        campaign: Campaign,
        run_id: str,
        *,
        opening_basis: StockBasis = StockBasis.PHYSICAL,
        closing_basis: StockBasis = StockBasis.PHYSICAL,
    ) -> dict[str, Any]:
        """The whole comparison: header, KPIs, aggregates and one line per article.

        The two bases pick which reading of each campaign brackets the flows.
        They are a parameter of the *reading*, not of the run: the frozen ERP
        snapshot and the loaded quantities do not move, so the four combinations
        are four views of one comparison rather than four comparisons — flip the
        pair and the report re-renders without anything being reloaded.
        """
        ctx = self.ctx
        run = self._run(campaign, run_id)
        baseline = ctx.campaigns.get(run.baseline_campaign_id)
        items = ctx.referentials.items_by_number(campaign.id)

        opening = _stock_by_item(ctx, baseline.id, opening_basis)
        closing = _stock_by_item(ctx, campaign.id, closing_basis)
        erp = {row.item_number: row for row in ctx.stock_flow.list_erp(run.id)}
        loaded: dict[FlowKind, dict[str, Decimal]] = {k: {} for k in FlowKind}
        sources: dict[FlowKind, set[FlowSource]] = {k: set() for k in FlowKind}
        for entry in ctx.stock_flow.list_inputs(run.id):
            loaded[entry.kind][entry.item_number] = entry.qty
            sources[entry.kind].add(entry.source)

        universe = (
            set(opening) | set(closing) | set(erp)
            | {item for step in loaded.values() for item in step}
        )
        lines: list[StockFlowLine] = []
        for item_number in sorted(universe):
            item = items.get(item_number)
            # An article the campaign excludes has no comparison to make: the
            # inventory deliberately does not hold it, so a variance on it would
            # be an artefact of the exclusion, not a finding.
            if item is not None and item.excluded_everywhere:
                continue
            flow = erp.get(item_number)
            lines.append(
                StockFlowLine(
                    item_number=item_number,
                    name=item.name if item else "",
                    unit=item.unit if item else "PCE",
                    unit_cost=item.std_price if item else ZERO,
                    opening_qty=opening.get(item_number, ZERO),
                    received_qty=loaded[FlowKind.RECEIPT].get(item_number, ZERO),
                    produced_qty=flow.produced_qty if flow else ZERO,
                    shipped_qty=loaded[FlowKind.SHIPMENT].get(item_number, ZERO),
                    consumed_qty=flow.consumed_qty if flow else ZERO,
                    scrapped_qty=loaded[FlowKind.SCRAP].get(item_number, ZERO),
                    closing_qty=closing.get(item_number, ZERO),
                    has_opening=item_number in opening,
                    has_closing=item_number in closing,
                )
            )

        return {
            "run": {
                **_run_payload(run),
                "baselineCode": baseline.code,
                "baselineLabel": baseline.label,
                "baselineCountDate": baseline.count_date.isoformat(),
                "campaignCode": campaign.code,
                "campaignCountDate": campaign.count_date.isoformat(),
            },
            "basis": {
                "opening": str(opening_basis),
                "closing": str(closing_basis),
                "openingLabel": BASIS_LABELS[opening_basis],
                "closingLabel": BASIS_LABELS[closing_basis],
                "openingStockLabel": BASIS_STOCK_LABELS[opening_basis],
                "closingStockLabel": BASIS_STOCK_LABELS[closing_basis],
                "label": (
                    f"{BASIS_LABELS[opening_basis]} {baseline.code} → "
                    f"{BASIS_LABELS[closing_basis]} {campaign.code}"
                ),
            },
            "steps": self._steps(run, loaded, erp, sources),
            "kpis": _kpis(lines),
            "chain": _chain(lines, closing_basis),
            "rows": [_line_payload(line) for line in lines],
        }

    def _steps(
        self,
        run: StockFlowRun,
        loaded: dict[FlowKind, dict[str, Decimal]],
        erp: dict[str, StockFlowErp],
        sources: dict[FlowKind, set[FlowSource]] | None = None,
    ) -> list[dict[str, Any]]:
        """What has been provided so far, step by step.

        The screen needs to distinguish three states, not two: not provided,
        provided and empty, provided with content. Only the scrap step can
        legitimately be « deliberately empty », and only because somebody said so.

        Each step also says **where its figures came from** and when the ERP was
        last read for it. Four steps that all display a number look equally solid;
        « lu dans l'ERP il y a deux minutes » and « corrigé à la main » are not,
        and the difference is what somebody defends six months later.
        """
        sources = sources or {}
        return [
            {
                "kind": str(kind),
                "label": FLOW_LABELS[kind].capitalize(),
                "items": len(loaded[kind]),
                "totalQty": float(sum(loaded[kind].values())),
                "loaded": bool(loaded[kind]) or (
                    kind is FlowKind.SCRAP and run.scrap_loaded
                ),
                "optional": kind is FlowKind.SCRAP,
                "sources": sorted(str(s) for s in sources.get(kind, set())),
                "refreshedAt": _iso({
                    FlowKind.RECEIPT: run.receipts_refreshed_at,
                    FlowKind.SHIPMENT: run.shipments_refreshed_at,
                    FlowKind.SCRAP: run.scrap_refreshed_at,
                }[kind]),
                "view": _STEP_VIEWS[kind],
            }
            for kind in (FlowKind.RECEIPT, FlowKind.SHIPMENT, FlowKind.SCRAP)
        ] + [
            {
                "kind": "ERP",
                "label": "Production et consommation théorique",
                "items": len(erp),
                "totalQty": float(sum(e.produced_qty for e in erp.values())),
                "loaded": bool(erp),
                "optional": False,
                "sources": sorted({str(e.source) for e in erp.values()}),
                "refreshedAt": _iso(run.erp_refreshed_at),
                "view": "production",
            }
        ]

    # ---------------------------------------------------------------- helpers

    def _run(self, campaign: Campaign, run_id: str) -> StockFlowRun:
        run = self.ctx.stock_flow.get_run(run_id)
        if run is None or run.campaign_id != campaign.id:
            raise NotFoundError(
                "Cette comparaison n'existe pas, ou appartient à une autre "
                "campagne.",
                runId=run_id,
            )
        return run

    def _baseline(self, campaign: Campaign, baseline_id: str) -> Campaign:
        baseline = self.ctx.campaigns.get(baseline_id)
        if baseline.count_date >= campaign.count_date:
            raise ValidationError(
                f"« {baseline.code} » a été comptée le "
                f"{baseline.count_date:%d/%m/%Y}, soit après « {campaign.code} » "
                f"({campaign.count_date:%d/%m/%Y}). Le stock initial est celui de "
                "la campagne la plus ancienne par date d'inventaire.",
                baseline=baseline.code,
            )
        return baseline


# --------------------------------------------------------------------------- #
# Computation
# --------------------------------------------------------------------------- #

def _stock_by_item(
    ctx: ServiceContext, campaign_id: str, basis: StockBasis
) -> dict[str, Decimal]:
    """One reading of a campaign's stock, collapsed to the article.

    Locations are collapsed on purpose: between two inventories a pallet moves,
    and comparing bin by bin would report every move as a variance. What the
    period's flows act on is the article's total.

    ``PHYSICAL`` adds the posted adjustments to the count, exactly as the
    inventory variance does — an adjustment is a stock movement, so a comparison
    that ignored it would start from a shelf nobody has seen since. An article
    that has an adjustment but was never counted still appears: it holds stock,
    and dropping it would silently shorten the comparison.
    """
    totals: dict[str, Decimal] = {}

    def add(item_number: str, qty: Any) -> None:
        value = qty if isinstance(qty, Decimal) else Decimal(str(qty))
        totals[item_number] = totals.get(item_number, ZERO) + value

    if basis is StockBasis.BOOK:
        for line in ctx.book_stock.list(campaign_id):
            add(line.item_number, line.qty)
    else:
        for row in ctx.journals.counted_quantities(campaign_id):
            add(row["item_number"], row["qty"])
        for adjustment in ctx.adjustments.list(campaign_id):
            add(adjustment.item_number, adjustment.qty)

    return {item: quantize_qty(value) for item, value in totals.items()}


def _kpis(lines: list[StockFlowLine]) -> dict[str, Any]:
    """Headline figures, computed over the articles both campaigns counted.

    The incomplete ones are counted and reported, never summed: an article
    missing from one of the two counts would contribute a variance the size of
    its whole stock, and a single such reference can dominate the total.
    """
    complete = [line for line in lines if line.is_complete]
    net = sum((line.variance_value for line in complete), ZERO)
    gross = sum((line.abs_variance_value for line in complete), ZERO)
    expected_value = sum(
        (quantize_money(line.expected_qty * line.unit_cost) for line in complete), ZERO
    )
    closing_value = sum(
        (quantize_money(line.closing_qty * line.unit_cost) for line in complete), ZERO
    )
    base = abs(expected_value)
    return {
        "lineCount": len(lines),
        "completeCount": len(complete),
        "incompleteCount": len(lines) - len(complete),
        "matchedCount": sum(1 for line in complete if line.variance_qty == 0),
        "expectedValue": float(expected_value),
        "closingValue": float(closing_value),
        "netVarianceValue": float(net),
        "grossVarianceValue": float(gross),
        # Same reading as the campaign's own reliability: offsets allowed for the
        # first, absolute errors for the second. `None` rather than 0 when there
        # is no base — « n/a » is the honest answer, 100 % is not.
        "netReliability": float(1 - abs(net) / base) if base else None,
        "grossReliability": float(1 - gross / base) if base else None,
    }


#: The chain, in the order the flows happen. Rendered as a waterfall by the
#: screen, which is the only shape in which six terms and a residual read as one
#: story rather than seven numbers.
#: Les libellés sont courts parce qu'ils sont écrits sous une barre de moins de
#: cent pixels : « Consommation théorique » y arrive tronqué, et un axe dont on
#: doit deviner les catégories ne se lit pas.
_CHAIN_STEPS = (
    ("opening", "Stock initial", "opening_qty", 1),
    ("received", "Réceptions", "received_qty", 1),
    ("produced", "Production", "produced_qty", 1),
    ("shipped", "Expéditions", "shipped_qty", -1),
    ("consumed", "Conso. théo.", "consumed_qty", -1),
    ("scrapped", "Rebuts", "scrapped_qty", -1),
)


def _chain(
    lines: list[StockFlowLine], closing_basis: StockBasis = StockBasis.PHYSICAL
) -> list[dict[str, Any]]:
    """The six terms, then the expected stock and the one actually found."""
    complete = [line for line in lines if line.is_complete]

    def total(attribute: str, sign: int) -> tuple[Decimal, Decimal]:
        qty = sum((getattr(line, attribute) for line in complete), ZERO)
        value = sum(
            (quantize_money(getattr(line, attribute) * line.unit_cost)
             for line in complete),
            ZERO,
        )
        return quantize_qty(qty * sign), quantize_money(value * sign)

    out = []
    for key, label, attribute, sign in _CHAIN_STEPS:
        qty, value = total(attribute, sign)
        out.append({
            "key": key, "label": label, "qty": float(qty), "value": float(value),
            "sign": sign, "terminal": False,
        })

    expected_qty = sum((line.expected_qty for line in complete), ZERO)
    expected_value = sum(
        (quantize_money(line.expected_qty * line.unit_cost) for line in complete), ZERO
    )
    closing_qty = sum((line.closing_qty for line in complete), ZERO)
    closing_value = sum(
        (quantize_money(line.closing_qty * line.unit_cost) for line in complete), ZERO
    )
    out.append({
        "key": "expected", "label": "Stock attendu",
        "qty": float(quantize_qty(expected_qty)),
        "value": float(quantize_money(expected_value)),
        "sign": 0, "terminal": True,
    })
    out.append({
        "key": "closing", "label": BASIS_STOCK_LABELS[closing_basis],
        "qty": float(quantize_qty(closing_qty)),
        "value": float(quantize_money(closing_value)),
        "sign": 0, "terminal": True,
    })
    return out


def _line_payload(line: StockFlowLine) -> dict[str, Any]:
    ratio = line.variance_ratio
    return {
        "itemNumber": line.item_number,
        "name": line.name,
        "unit": line.unit,
        "unitCost": float(line.unit_cost),
        "openingQty": float(line.opening_qty),
        "receivedQty": float(line.received_qty),
        "producedQty": float(line.produced_qty),
        "shippedQty": float(line.shipped_qty),
        "consumedQty": float(line.consumed_qty),
        "scrappedQty": float(line.scrapped_qty),
        "expectedQty": float(line.expected_qty),
        "closingQty": float(line.closing_qty),
        "varianceQty": float(line.variance_qty),
        "varianceValue": float(line.variance_value),
        "varianceRatio": None if ratio is None else float(ratio),
        "hasOpening": line.has_opening,
        "hasClosing": line.has_closing,
        "complete": line.is_complete,
    }


def _run_payload(run: StockFlowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "campaignId": run.campaign_id,
        "baselineCampaignId": run.baseline_campaign_id,
        "periodStart": run.period_start.isoformat(),
        "periodEnd": run.period_end.isoformat(),
        "weeks": (run.period_end - run.period_start).days // 7,
        "scrapLoaded": run.scrap_loaded,
        # `source_loaded_at` n'est plus alimenté : il portait la fraîcheur de la
        # table de faits du backflush, dont la comparaison ne dépend plus. La
        # colonne reste en base — une migration livrée ne se réécrit pas — mais
        # un champ toujours nul dans la réponse serait une promesse vide.
        "erpRefreshedAt": (
            run.erp_refreshed_at.isoformat() if run.erp_refreshed_at else None
        ),
        "receiptsRefreshedAt": _iso(run.receipts_refreshed_at),
        "shipmentsRefreshedAt": _iso(run.shipments_refreshed_at),
        "scrapRefreshedAt": _iso(run.scrap_refreshed_at),
    }


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None
