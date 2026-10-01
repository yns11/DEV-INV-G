"""La décision humaine sur un écart : sa cause, et ce qu'on en dit.

Un écart chiffré ne vaut que par ce qu'on en conclut. « −412 pièces » est une
mesure ; « −412 pièces, transfert non saisi entre deux bacs » est une décision,
et c'est elle qui alimente le plan d'action de la campagne suivante. Les deux
vivent dans `variance_analysis`, dans des colonnes séparées de la proposition
que le modèle a pu faire — une suggestion n'est jamais écrite à la place d'une
décision.

Pourquoi un module à part
-------------------------
La cause se pose depuis **deux** écrans maintenant : la vue Écarts, où l'on
traite ligne à ligne en regardant les chiffres, et la vue Causes, où l'on solde
ce qui reste. Le geste est le même ; seul l'endroit d'où on le fait change. Le
loger dans le service d'analyse le mettait au milieu du calcul des écarts, avec
lequel il n'a rien en commun sinon la table qu'il lit.

Un lot, et pourquoi il n'est pas N appels
-----------------------------------------
« Vingt lignes, même cause » est le geste réel de la fin d'analyse. Vingt appels
donneraient vingt transactions, vingt lignes d'audit et un échec possible au
douzième — la moitié du lot posée, l'autre non, et rien pour dire où ça s'est
arrêté. Ici : une transaction, une ligne d'audit qui nomme le lot.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction
from ..domain.models import Campaign, VarianceAnalysis
from .context import ServiceContext

__all__ = ["CauseService"]


class CauseService:
    """Poser une cause et un commentaire sur un écart, ou sur un lot."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def causes(self) -> list[Any]:
        """Le référentiel des causes standard, commun à toutes les campagnes."""
        return self.ctx.analysis.list_causes()

    def save(
        self,
        campaign: Campaign,
        *,
        item_number: str,
        cause_code: str | None,
        comment: str = "",
        accepted: bool = False,
    ) -> VarianceAnalysis:
        """Une ligne, telle que le formulaire la montre.

        Ce qui est écrit est exactement ce qui est à l'écran, commentaire vidé
        compris : le champ était pré-rempli avec la valeur courante, donc
        l'effacer est un geste et non un oubli.

        Les colonnes de la proposition IA sont recopiées telles quelles. Les
        écraser ferait disparaître ce que le modèle avait dit au moment où
        quelqu'un a tranché — c'est-à-dire la seule trace permettant, plus tard,
        de savoir si la décision suivait la proposition ou s'en écartait.
        """
        ctx = self.ctx
        ctx.guard(campaign, "analysis")
        previous = self._existing(campaign).get(item_number)
        analysis = self._build(
            campaign, item_number, cause_code, comment, accepted, previous
        )
        ctx.analysis.upsert_analysis(analysis, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.UPDATE,
            entity_type="variance_analysis",
            entity_id=analysis.id,
            summary=f"{item_number} : cause {cause_code or '—'}",
            before=previous.model_dump(mode="json") if previous else None,
            after=analysis.model_dump(mode="json"),
        )
        return analysis

    def save_many(
        self,
        campaign: Campaign,
        *,
        item_numbers: Sequence[str],
        cause_code: str | None,
        comment: str = "",
    ) -> int:
        """La même cause sur un lot de lignes, en une fois.

        **Un commentaire vide ne vide rien.** Le formulaire du lot s'ouvre à
        blanc — il ne peut pas montrer vingt commentaires différents — donc son
        champ vide veut dire « je n'en parle pas », et non « efface-les ». La
        règle est l'inverse de celle de :meth:`save`, et c'est la forme du
        formulaire qui la dicte : là, le champ montre ce qu'il va remplacer ;
        ici, il ne montre rien.

        Les lignes déjà porteuses de la cause demandée sont réécrites comme les
        autres plutôt que sautées : leur `updated_at` et leur analyste changent,
        ce qui est exact — quelqu'un vient bien de confirmer ce choix-là.
        """
        ctx = self.ctx
        ctx.guard(campaign, "analysis")
        wanted = list(dict.fromkeys(n for n in item_numbers if n))
        if not wanted:
            return 0

        existing = self._existing(campaign)
        with ctx.db.transaction() as conn:
            for item_number in wanted:
                previous = existing.get(item_number)
                analysis = self._build(
                    campaign,
                    item_number,
                    cause_code,
                    comment or (previous.comment if previous else ""),
                    cause_code is not None,
                    previous,
                )
                ctx.analysis.upsert_analysis(analysis, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="variance_analysis",
                summary=(
                    f"{len(wanted)} écart(s) : cause {cause_code or '—'} "
                    "affectée en lot"
                ),
                conn=conn,
            )
        return len(wanted)

    # -- interne ------------------------------------------------------------

    def _existing(self, campaign: Campaign) -> dict[str, VarianceAnalysis]:
        return {
            a.item_number: a for a in self.ctx.analysis.list_analyses(campaign.id)
        }

    def _build(
        self,
        campaign: Campaign,
        item_number: str,
        cause_code: str | None,
        comment: str,
        accepted: bool,
        previous: VarianceAnalysis | None,
    ) -> VarianceAnalysis:
        return VarianceAnalysis(
            id=previous.id if previous else new_id(),
            campaign_id=campaign.id,
            item_number=item_number,
            cause_code=cause_code,
            comment=comment,
            analyst=self.ctx.actor,
            accepted=accepted,
            ai_suggested_cause=previous.ai_suggested_cause if previous else None,
            ai_confidence=previous.ai_confidence if previous else None,
            ai_rationale=previous.ai_rationale if previous else "",
        )
