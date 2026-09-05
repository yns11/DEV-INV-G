"""L'arbitrage : comparer les deux comptages, et trancher.

Extrait de :mod:`~inventory.services.generic_service`, pour deux raisons qui
n'en font qu'une.

**La comparaison doit se recalculer là où les quantités changent**, et les
quantités d'une feuille changent en cinq endroits : la saisie à l'écran, la
lecture d'un scan, la lecture d'une pile, l'import d'une liste, et le
reclassement d'un WIP sans nomenclature. Tant que le recalcul était une méthode
de ``GenericService``, seuls les deux premiers l'appelaient — les trois autres
vivent dans d'autres services, et rien ne disait qu'il leur manquait quelque
chose. :func:`refresh_after_sheet_writes` est le geste que tous partagent, et
``tests/test_arbitrage_recalcul.py`` refuse désormais qu'une écriture de lignes
de feuille l'oublie.

**Et ``generic_service`` touchait son plafond de lignes.** Un service qui ne
peut plus grandir sans qu'un contrôle le refuse est un service qui demande
qu'on lui retire quelque chose plutôt qu'on lui ajoute.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from ..db import new_id
from ..domain import Campaign, ZoneCounts, build_arbitration_lines
from ..domain.enums import AuditAction
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext

__all__ = [
    "ArbitrationService",
    "refresh_zone_arbitrations",
    "refresh_after_sheet_writes",
]

log = logging.getLogger(__name__)


#: Ce qu'un arbitrage tranché en lot écrit dans son commentaire. La trace dit
#: *par quelle règle* la quantité a été retenue : « 12 » sans rien d'autre ne se
#: relit pas six mois plus tard.
BULK_COMMENT = "Validé en lot depuis l'écran d'arbitrage."


# --------------------------------------------------------------------------- #
# Le recalcul, partagé par tout ce qui écrit des lignes de feuille
# --------------------------------------------------------------------------- #

def refresh_zone_arbitrations(
    ctx: ServiceContext, campaign: Campaign, zone_id: str
) -> int:
    """Recalculer la comparaison n°1 / n°2 d'une zone. Renvoie le nombre de lignes.

    Sans garde de phase ni écriture d'audit : ce n'est pas une commande de
    l'utilisateur mais la conséquence mécanique d'une écriture qui, elle, a déjà
    été autorisée et tracée. L'appeler depuis un service qui vient d'écrire ne
    doit rien redemander.

    **Le recalcul ne regarde pas le statut de la zone.** Une zone déclarée
    terminée dont on corrige ensuite une quantité doit voir son arbitrage
    rouvert comme les autres : c'est même le cas où l'oubli coûte le plus cher,
    puisque plus rien en aval ne redemande la question — la consolidation prend
    une décision que personne n'a revue.

    Lecture et écriture dans la même transaction : entre les deux, une décision
    prise ailleurs serait écrasée par l'état lu avant elle.
    """
    zone = next(
        (z for z in ctx.sheets.list_zones(campaign.id) if z.id == zone_id), None
    )
    # Une zone à un seul passage n'a rien à comparer : il n'y a pas de second
    # avis, et en fabriquer un bloquerait la consolidation sur une décision que
    # personne ne peut prendre.
    if zone is None or zone.passes < 2:
        return 0
    with ctx.db.transaction() as conn:
        counts = ZoneCounts(
            zone=zone,
            sheets=ctx.sheets.list_sheets(campaign.id, zone_id=zone_id, conn=conn),
            lines_by_sheet=ctx.sheets.lines_by_sheet(campaign.id, conn=conn),
            arbitrations=ctx.sheets.list_arbitrations(
                campaign.id, zone_id=zone_id, conn=conn
            ),
        )
        lines = build_arbitration_lines(
            counts, campaign_id=campaign.id, id_factory=new_id
        )
        if lines:
            ctx.sheets.upsert_arbitrations(lines, conn=conn)
    return len(lines)


def refresh_after_sheet_writes(
    ctx: ServiceContext, campaign: Campaign, sheet_ids: Sequence[str]
) -> None:
    """Recalculer les zones des feuilles qu'on vient d'écrire.

    Le point d'entrée des appelants qui ne connaissent que des feuilles — un
    scan, une pile, un import, un reclassement.

    **Rien ne remonte à l'écran.** Un échec de recalcul ne doit jamais faire
    croire qu'une saisie a échoué : les quantités, elles, sont enregistrées, et
    annoncer un échec ferait ressaisir ce qui est déjà en base. Le manque se
    rattrape au prochain enregistrement, ou à la fermeture de la zone.
    """
    wanted = {s for s in sheet_ids if s}
    if not wanted:
        return
    zone_ids: list[str] = []
    for sheet in ctx.sheets.list_sheets(campaign.id):
        if sheet.id in wanted and sheet.zone_id not in zone_ids:
            zone_ids.append(sheet.zone_id)
    for zone_id in zone_ids:
        try:
            refresh_zone_arbitrations(ctx, campaign, zone_id)
        except Exception:
            log.exception(
                "Rafraîchissement des arbitrages impossible sur la zone %s", zone_id
            )


# --------------------------------------------------------------------------- #
# Le service
# --------------------------------------------------------------------------- #

class ArbitrationService:
    """Les écarts entre les deux comptages d'une zone, et les décisions prises."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------- lecture

    def refresh(self, campaign: Campaign, zone_id: str) -> list[dict[str, Any]]:
        """Recalculer puis renvoyer la comparaison d'une zone.

        Les décisions humaines encore valables sont conservées : recalculer la
        comparaison ne doit jamais effacer un arbitrage que quelqu'un a pris.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        refresh_zone_arbitrations(ctx, campaign, zone_id)
        return self.list(campaign, zone_id)

    def list(
        self,
        campaign: Campaign,
        zone_id: str | None = None,
        *,
        divergent_only: bool = False,
    ) -> list[dict[str, Any]]:
        """La comparaison, enrichie de quoi la lire et la trancher.

        :param divergent_only: ne garder que les lignes où les deux comptages ne
            disent pas la même chose. C'est ce que l'écran demande : une ligne
            sur laquelle les deux équipes s'accordent n'appelle aucune décision,
            et sur une zone de quatre cents références elle enterre les neuf qui
            en appellent une.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        zones = {z.id: z for z in ctx.sheets.list_zones(campaign.id)}
        tolerance = campaign.config.arbitration_tolerance
        out: list[dict[str, Any]] = []
        for line in ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id):
            q1, q2 = line.qty_pass_1, line.qty_pass_2
            divergent = q1 != q2
            if divergent and tolerance > 0 and q1 is not None and q2 is not None:
                base = max(abs(q1), abs(q2))
                divergent = base == 0 or abs(q2 - q1) / base > tolerance
            if divergent_only and not divergent:
                continue
            zone = zones.get(line.zone_id)
            item = items.get(line.item_number)
            out.append({
                **line.model_dump(mode="json"),
                "name": item.name if item else "",
                "zoneCode": zone.code if zone else "",
                "zoneLabel": zone.label if zone else "",
                "gap": float(line.gap),
                "divergent": divergent,
                "needsDecision": divergent and not line.is_resolved,
                "isProposed": line.is_proposed,
                "unitCost": float(item.std_price) if item else 0.0,
                "gapValue": float(line.gap * item.std_price) if item else 0.0,
            })
        out.sort(key=lambda r: (not r["needsDecision"], -abs(r["gapValue"])))
        return out

    # ------------------------------------------------------------ décisions

    def decide(
        self,
        campaign: Campaign,
        arbitration_id: str,
        qty: Decimal,
        *,
        comment: str = "",
    ) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        if qty < 0:
            raise ValidationError("Une quantité arbitrée ne peut pas être négative.")
        ctx.sheets.decide_arbitration(
            arbitration_id, qty, actor=ctx.actor, comment=comment
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.ARBITRATE,
            entity_type="arbitration",
            entity_id=arbitration_id,
            summary=f"Arbitrage : quantité retenue {qty}",
            after={"qty": str(qty), "comment": comment},
        )

    def decide_many(
        self,
        campaign: Campaign,
        zone_id: str,
        decisions: Mapping[str, Decimal],
    ) -> dict[str, int]:
        """Valider d'un geste les quantités que l'écran affiche.

        **Le lot valide ce qui est à l'écran, pas ce que le serveur recalcule.**
        Il a d'abord fonctionné autrement : le client disait « le n°2 partout »
        ou « valide les propositions », et le serveur allait rechercher la
        quantité lui-même. Les deux formes échouaient au même endroit — une
        quantité tapée dans le champ, ou posée là par un bouton de
        pré-remplissage local, n'existait pas côté serveur, si bien que « Valider
        tout » annonçait des lignes non tranchées en montrant à l'utilisateur
        les quantités qu'il croyait valider. Ce sont maintenant ces
        quantités-là, et elles seules, qui remontent.

        Une ligne déjà tranchée n'est pas retouchée : un lot ne défait pas un
        jugement pris une par une. Une ligne dont la quantité est absente ou
        négative reste ouverte, et le compte le dit — plutôt que de laisser
        croire que la zone est arbitrée.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_entries")
        open_lines = {
            line.id: line
            for line in ctx.sheets.list_arbitrations(campaign.id, zone_id=zone_id)
        }
        unknown = [i for i in decisions if i not in open_lines]
        if unknown:
            raise NotFoundError(
                "Arbitrage introuvable dans cette zone.", arbitrationIds=unknown
            )

        decided = 0
        skipped = 0
        for arbitration_id, line in open_lines.items():
            if line.is_resolved or line.qty_pass_1 == line.qty_pass_2:
                continue
            qty = decisions.get(arbitration_id)
            if qty is None or qty < 0:
                skipped += 1
                continue
            ctx.sheets.decide_arbitration(
                arbitration_id, qty, actor=ctx.actor, comment=BULK_COMMENT
            )
            decided += 1

        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.ARBITRATE,
            entity_type="zone",
            entity_id=zone_id,
            summary=f"{decided} arbitrage(s) validé(s) en lot",
            after={"decided": decided, "skipped": skipped},
        )
        return {"decided": decided, "skipped": skipped}
