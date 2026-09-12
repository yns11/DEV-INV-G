"""La dérive d'un emplacement scellé : un indice, pas un écart.

::

    dérive = ERP@J − compté@T0

par article et par emplacement scellé.

Ce qu'elle dit
--------------
Attendue nulle, et pour une raison précise : un journal de précomptage est
**posté dans l'ERP** avant que la photo du jour J ne soit prise, et cette photo
l'a donc déjà intégré. Ce qui subsiste après ce réalignement n'est pas un écart
d'inventaire — celui-là se mesure ailleurs, contre la référence unique du jour
J — mais ce qui a bougé entre les deux dates : une sortie, une réception, une
correction saisie entre-temps.

Ce qu'elle n'appelle pas
------------------------
**Aucune décision, et aucun blocage.** Elle se regarde. Le dispositif proposait
autrefois de trancher — conserver le comptage avancé, ou recompter — parce
qu'il portait sa propre référence, ``ERP@T0``, contre laquelle un emplacement
scellé était mesuré. Cette référence n'existe plus : mesurer une seconde fois
contre un état antérieur revenait à compter deux fois la même correction. Il
n'y a donc plus rien à arbitrer, et une dérive matérielle n'arrête plus le
passage en analyse.

Ce que la dérive ne verra pas
-----------------------------
Elle se calcule entre deux lectures de l'ERP : elle ne voit donc que ce que
l'ERP a appris. Une pièce sortie d'un emplacement scellé sans aucune
transaction laisse une dérive nulle. Si elle est re-scannée ailleurs le jour J,
c'est le contrôle par étiquette qui la montre ; sinon rien ne la voit, et la
perte n'apparaîtra qu'à l'inventaire suivant. Aucun code ne rattrape ce dernier
cas — seul le balisage physique le fait.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal

from ..db import new_id
from ..domain.enums import AuditAction
from ..domain.models import BookStockLine, Campaign, EarlyCountDrift, LocationKey
from ..domain.quantities import ZERO, quantize_money, quantize_qty
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["DriftService"]


class DriftService:
    """Calculer et lister les dérives d'une campagne."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------ calcul

    def record_general_load(
        self, campaign: Campaign, day_j_lines: Sequence[BookStockLine]
    ) -> int:
        """Confronter le stock ERP du jour J à ce que le précomptage avait compté.

        Appelé par le chargement général, avec les lignes qu'il apportait. Ces
        lignes sont désormais la référence de tout emplacement, scellé ou non ;
        pour un emplacement scellé, elles disent en plus ce que l'ERP pense de
        lui après que son précomptage y a été posté, et c'est exactement ce
        qu'il faut confronter au comptage de ce précomptage.

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
        costs: dict[tuple[LocationKey, str], Decimal] = {}
        for line in day_j_lines:
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in sealed:
                slot = (key, line.item_number)
                erp_j[slot] = erp_j.get(slot, ZERO) + line.qty
                costs.setdefault(slot, line.unit_cost)
        counted = self._counted_at_t0(campaign.id, sealed)
        # La dérive nomme le journal qui a scellé l'emplacement : le jour J,
        # elle ne montre pas « l'emplacement ATP / SOL », elle montre le
        # précomptage que ce journal-là porte, avec sa date et son auteur.
        journals = {
            key: journal.id
            for journal in ctx.erp_journals.list(campaign.id)
            for key in journal.scope
        }

        drifts: list[EarlyCountDrift] = []
        for slot in sorted(
            set(erp_j) | set(counted),
            key=lambda s: (s[0].warehouse_id, s[0].location_id, s[1]),
        ):
            key, item_number = slot
            qty_counted = counted.get(slot, ZERO)
            qty_erp_j = erp_j.get(slot, ZERO)
            drift_qty = quantize_qty(qty_erp_j - qty_counted)
            drifts.append(
                EarlyCountDrift(
                    id=new_id(),
                    campaign_id=campaign.id,
                    erp_journal_id=journals.get(key),
                    warehouse_id=key.warehouse_id,
                    location_id=key.location_id,
                    item_number=item_number,
                    qty_counted_t0=qty_counted,
                    qty_erp_j=qty_erp_j,
                    drift_value=quantize_money(
                        drift_qty * costs.get(slot, ZERO)
                    ),
                )
            )

        with ctx.db.transaction() as conn:
            written = ctx.drifts.replace(campaign.id, drifts, conn=conn)
            non_nulles = sum(1 for d in drifts if d.drift_qty != 0)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_drift",
                summary=(
                    f"Dérives recalculées sur {len(sealed)} emplacement(s) "
                    f"scellé(s) : {written} ligne(s), dont {non_nulles} non nulle(s)."
                ),
                after={"lines": written, "nonZero": non_nulles},
                conn=conn,
            )
        return written

    def _counted_at_t0(
        self, campaign_id: str, sealed: set[LocationKey]
    ) -> dict[tuple[LocationKey, str], Decimal]:
        """``compté@T0`` sur les emplacements scellés.

        Compté, et rien d'autre. L'ajustement des précomptages a disparu avec la
        référence qu'il corrigeait : ajouter ici les ajustements de la campagne
        — qui portent sur le jour J — reviendrait à corriger un terme par une
        correction destinée à l'autre.
        """
        out: dict[tuple[LocationKey, str], Decimal] = {}
        for row in self.ctx.journals.counted_quantities(campaign_id):
            key = LocationKey(
                warehouse_id=row["warehouse_id"], location_id=row["location_id"]
            )
            if key in sealed:
                slot = (key, row["item_number"])
                out[slot] = out.get(slot, ZERO) + (row["qty"] or ZERO)
        return out

    # ------------------------------------------------------------------ lecture

    def list_drifts(self, campaign_id: str) -> list[EarlyCountDrift]:
        """Les dérives à regarder — celles qui ne sont pas nulles.

        Une dérive nulle est le cas **normal** : l'emplacement était balisé, et
        poster son journal a réaligné l'ERP sur le comptage. La ligne n'est donc
        pas une information, c'est l'absence d'information — et sur un
        précomptage de cinquante emplacements à trois cents références, les
        quelques lignes qui apprennent quelque chose se perdaient au milieu de
        milliers de zéros.

        Le calcul, lui, les produit et les conserve toutes : c'est la trace que
        la confrontation a bien eu lieu sur chaque ligne.
        """
        return [d for d in self.ctx.drifts.list(campaign_id) if d.drift_qty != 0]
