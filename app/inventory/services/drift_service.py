"""La dérive d'un emplacement scellé, et ses deux issues.

::

    dérive = ERP@J − physique@T0

par article et par emplacement scellé. Attendue nulle : l'emplacement a été
balisé, et poster son journal a réaligné l'ERP sur le physique compté. Quand
elle ne l'est pas, une seule question se pose — *quelle quantité fait foi au
jour J ?* — et elle a deux réponses : conserver le comptage avancé, ou
recompter.

Deux, et pas quatre
-------------------
« Rejouer le postage » n'en est pas une : on ne scelle qu'un journal posté dans
l'ERP, donc le réalignement est acquis par construction plutôt que diagnostiqué
après coup. « Ajuster » non plus : un mouvement réel se saisit par le mécanisme
d'ajustement, qui a déjà son sens, sa table et sa place dans le calcul.

Ce que la dérive ne verra pas
-----------------------------
Elle se calcule entre deux lectures de l'ERP : elle ne voit donc que ce que
l'ERP a appris. Une pièce sortie d'un emplacement scellé sans aucune
transaction laisse une dérive nulle. Si elle est re-scannée ailleurs le jour J,
c'est le contrôle par étiquette qui la rattrape ; sinon rien ne la voit, et la
perte n'apparaîtra qu'à l'inventaire suivant. Aucun code ne rattrape ce
dernier cas — seul le balisage physique le fait.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal

from ..db import new_id
from ..domain.enums import AuditAction, DriftResolution, ItemType
from ..domain.models import BookStockLine, Campaign, EarlyCountDrift, LocationKey
from ..domain.quantities import ZERO, quantize_money, quantize_qty
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["DriftService"]


class DriftService:
    """Calculer, lister et trancher les dérives d'une campagne."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ calcul

    def record_general_load(
        self, campaign: Campaign, day_j_lines: Sequence[BookStockLine]
    ) -> int:
        """Confronter le stock ERP du jour J au physique posté au précomptage.

        Appelé par le chargement général, avec les lignes qu'il apportait. Les
        emplacements scellés ne les reçoivent pas — leur référence reste celle
        de leur précomptage — mais ces lignes disent tout de même ce que l'ERP
        pense d'eux le jour J, et c'est exactement ce qu'il faut confronter.

        Le rapprochement est une **jointure externe complète** sur
        ``(emplacement, article)``. Un article peut apparaître dans le stock du
        jour J sans avoir jamais été compté, ou en disparaître : une jointure
        interne perdrait précisément les deux cas qui méritent d'être vus.
        """
        ctx = self.ctx
        # Appelée depuis le chargement général, qui garde déjà — mais la garde
        # est reposée ici. Une écriture qui compte sur celle de son appelant
        # devient non gardée le jour où un routeur l'appelle directement, et
        # rien ne le signalerait.
        ctx.guard(campaign, "book_stock")
        sealed = {
            LocationKey(warehouse_id=warehouse, location_id=location)
            for warehouse, location in ctx.journals.sealed_keys(campaign.id)
        }
        if not sealed:
            return 0

        erp_j: dict[tuple[LocationKey, str], Decimal] = {}
        for line in day_j_lines:
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in sealed:
                slot = (key, line.item_number)
                erp_j[slot] = erp_j.get(slot, ZERO) + line.qty

        reference = {
            (LocationKey(warehouse_id=l.warehouse_id, location_id=l.location_id),
             l.item_number): l
            for l in ctx.book_stock.list(campaign.id)
            if LocationKey(
                warehouse_id=l.warehouse_id, location_id=l.location_id
            ) in sealed
        }
        physical = self._physical_at_t0(campaign, sealed)
        # La dérive nomme le journal qui a scellé l'emplacement : le jour J,
        # elle ne conteste pas « l'emplacement ATP / SOL », elle conteste le
        # précomptage que ce journal-là porte, avec sa date et son auteur.
        journals = {
            key: journal.id
            for journal in ctx.erp_journals.list(campaign.id)
            for key in journal.scope
        }

        drifts: list[EarlyCountDrift] = []
        for slot in sorted(
            set(erp_j) | set(reference) | set(physical),
            key=lambda s: (s[0].warehouse_id, s[0].location_id, s[1]),
        ):
            key, item_number = slot
            line = reference.get(slot)
            qty_erp_t0 = line.qty if line else ZERO
            qty_physical = physical.get(slot, ZERO)
            qty_erp_j = erp_j.get(slot, ZERO)
            drift_qty = quantize_qty(qty_erp_j - qty_physical)
            unit_cost = line.unit_cost if line else ZERO
            drifts.append(
                EarlyCountDrift(
                    id=new_id(),
                    campaign_id=campaign.id,
                    erp_journal_id=journals.get(key),
                    warehouse_id=key.warehouse_id,
                    location_id=key.location_id,
                    item_number=item_number,
                    qty_erp_t0=qty_erp_t0,
                    qty_physical_t0=qty_physical,
                    qty_erp_j=qty_erp_j,
                    drift_value=quantize_money(drift_qty * unit_cost),
                    is_material=self._is_material(campaign, drift_qty, unit_cost),
                )
            )

        with ctx.db.transaction() as conn:
            written = ctx.drifts.replace(campaign.id, drifts, conn=conn)
            material = sum(1 for d in drifts if d.is_material)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_drift",
                summary=(
                    f"Dérives recalculées sur {len(sealed)} emplacement(s) "
                    f"scellé(s) : {written} ligne(s), dont {material} matérielle(s)."
                ),
                after={"lines": written, "material": material},
                conn=conn,
            )
        return written

    def _physical_at_t0(
        self, campaign: Campaign, sealed: set[LocationKey]
    ) -> dict[tuple[LocationKey, str], Decimal]:
        """``compté@T0 + ajusté@T0`` sur les emplacements scellés."""
        out: dict[tuple[LocationKey, str], Decimal] = {}
        for row in self.ctx.journals.counted_quantities(campaign.id):
            key = LocationKey(
                warehouse_id=row["warehouse_id"], location_id=row["location_id"]
            )
            if key in sealed:
                slot = (key, row["item_number"])
                out[slot] = out.get(slot, ZERO) + (row["qty"] or ZERO)
        for line in self.ctx.adjustments.list(campaign.id):
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in sealed:
                slot = (key, line.item_number)
                out[slot] = out.get(slot, ZERO) + line.qty
        return out

    def _is_material(
        self, campaign: Campaign, drift_qty: Decimal, unit_cost: Decimal
    ) -> bool:
        """Les seuils de la campagne, et pas un réglage de plus.

        Un second réglage de matérialité finirait par contredire le premier, et
        l'exploitant aurait deux listes d'exceptions qui ne se recoupent pas.
        """
        if drift_qty == 0:
            return False
        thresholds = campaign.threshold_for(ItemType.UNKNOWN)
        return abs(drift_qty * unit_cost) >= thresholds.value_abs_eur

    # ------------------------------------------------------------------ lecture

    def list_drifts(self, campaign_id: str) -> list[EarlyCountDrift]:
        return self.ctx.drifts.list(campaign_id)

    def unresolved_material(self, campaign_id: str) -> int:
        return sum(
            1 for drift in self.ctx.drifts.list(campaign_id) if drift.blocks_analysis
        )

    # ------------------------------------------------------------------ issues

    def resolve(
        self,
        campaign: Campaign,
        drift_ids: Sequence[str],
        resolution: DriftResolution,
        *,
        cause_code: str = "",
        comment: str = "",
    ) -> int:
        """Trancher : quelle quantité fait foi au jour J ?

        ``KEEP_EARLY`` exige une cause. Cette issue laisse volontairement la
        campagne et l'ERP en désaccord de la valeur de la dérive — la campagne
        dit que l'emplacement porte le physique de T0, l'ERP dit autre chose, et
        aucun nouveau journal ne viendra les réaligner. Ce n'est pas un défaut,
        c'est le sens de la décision ; c'est ce qui la rend coûteuse, et ce qui
        justifie qu'on la nomme.
        """
        ctx = self.ctx
        ctx.guard(campaign, "early_counts")
        if not drift_ids:
            return 0
        if resolution is DriftResolution.KEEP_EARLY and not cause_code.strip():
            raise ValidationError(
                "Conserver le comptage avancé demande une cause : la campagne "
                "et l'ERP resteront en désaccord de la valeur de la dérive, et "
                "personne ne doit le découvrir plus tard.",
                resolution=str(resolution),
            )
        known = {drift.id for drift in ctx.drifts.list(campaign.id)}
        unknown = [d for d in drift_ids if d not in known]
        if unknown:
            raise NotFoundError(
                "Dérive introuvable dans cette campagne.", driftIds=unknown
            )

        with ctx.db.transaction() as conn:
            touched = ctx.drifts.resolve(
                campaign.id, drift_ids, resolution,
                cause_code=cause_code.strip(), comment=comment.strip(),
                actor=ctx.actor, resolved_at=utcnow(), conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_drift",
                summary=(
                    f"{touched} dérive(s) tranchée(s) : {resolution}"
                    + (f" ({cause_code.strip()})" if cause_code.strip() else "")
                ),
                after={
                    "resolution": str(resolution),
                    "causeCode": cause_code.strip(),
                    "comment": comment.strip(),
                    "driftIds": list(drift_ids),
                },
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return touched
