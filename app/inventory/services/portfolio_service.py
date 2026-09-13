"""Les portefeuilles : quelles références sont à qui, et « les miennes ».

Une campagne porte quatre à cinq cents références, et l'analyse se répartit
entre plusieurs personnes. L'application savait déjà répartir des
**emplacements** — c'est « mon périmètre », porté par les gestionnaires — et ne
savait pas répartir des **articles**. Ce n'est pas la même découpe : un acheteur
suit ses références partout où elles sont, quel que soit l'entrepôt qui les
range, et le périmètre d'un gestionnaire ne l'aide en rien.

Deux choses, et deux seulement
------------------------------
**Écrire le tableau** — par fichier, par collage ou à la main, comme toutes les
grilles de l'application, puisque c'est le même pipeline d'import.

**Le relire comme un filtre** — `mine`, qui rend les références de la personne
connectée. Les écrans s'en servent pour la bascule « uniquement mes
références ».

Ce que ce n'est pas
-------------------
**Une habilitation.** Un portefeuille filtre l'affichage et n'interdit rien :
chacun garde le droit d'agir sur toutes les références, comme un gestionnaire
garde celui d'agir hors de son périmètre. Le droit d'écrire vient d'être
déclaré gestionnaire, et de rien d'autre — voir :mod:`inventory.domain.access`.

**Un doublon du périmètre.** Les deux se cumulent sans se connaître : on peut
suivre des références dans un entrepôt qu'on ne pilote pas, et piloter un
entrepôt plein de références qui ne sont pas les siennes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.enums import AuditAction
from ..domain.models import Campaign
from ..ingest import map_portfolios
from .context import ServiceContext
from .import_batches import ImportOutcome
from .import_parsing import _base_outcome

__all__ = ["PortfolioService", "import_portfolios"]


class PortfolioService:
    """Lecture des portefeuilles, et du sien."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def overview(self, campaign: Campaign) -> dict[str, Any]:
        """Le tableau entier, et ce qu'il pèse par personne.

        Les deux ensemble parce que l'écran montre les deux : la grille des
        attributions, et au-dessus le décompte qui dit d'un coup d'œil si la
        répartition est complète ou si quelqu'un porte tout.
        """
        ctx = self.ctx
        rows = ctx.portfolios.list(campaign.id)
        known = ctx.referentials.items_by_number(campaign.id)
        return {
            "rows": [
                {
                    "itemNumber": p.item_number,
                    "actor": p.actor,
                    # Une référence attribuée mais absente du référentiel est le
                    # cas qu'on veut voir : le portefeuille se charge souvent
                    # avant les articles, et une coquille y reste invisible
                    # jusqu'au jour où le filtre ne rend rien.
                    "name": known[p.item_number].name if p.item_number in known else "",
                    "known": p.item_number in known,
                }
                for p in rows
            ],
            "byActor": [
                {"actor": row["actor"], "items": row["items"]}
                for row in ctx.portfolios.counts_by_actor(campaign.id)
            ],
            "unassigned": max(len(known) - len(rows), 0),
        }

    def mine(self, campaign: Campaign) -> frozenset[str]:
        """Les références de la personne connectée.

        Résolu **côté serveur**, à partir de l'identité que la plateforme
        transmet, comme « mon périmètre » : le navigateur ne nomme jamais
        personne, et ne peut donc pas demander le portefeuille d'un autre.
        """
        return self.ctx.portfolios.items_of(campaign.id, self.ctx.actor)


def import_portfolios(
    service: Any, campaign: Campaign, **kwargs: Any
) -> ImportOutcome:
    """Charger le tableau d'attribution — fichier, collage ou saisie.

    Gardé par ``managers``, comme les gestionnaires et leurs deux périmètres :
    c'est la même nature de décision — qui s'occupe de quoi — et elle bouge aux
    mêmes moments, jusqu'à la clôture comprise.

    **Une fusion, pas un remplacement.** Un fichier de trente références ne dit
    rien des quatre cent cinquante autres, et les effacer parce qu'il ne les
    mentionne pas ferait d'une correction ciblée une remise à zéro. Une adresse
    vide, elle, retire l'attribution : c'est ainsi qu'on en défait une depuis le
    même fichier qui les pose.

    Les références inconnues du référentiel ne sont **pas** refusées, et c'est
    voulu : le tableau se prépare souvent avant que les articles ne soient
    chargés. Elles sont comptées et nommées dans le rapport, et l'écran les
    montre — ce qui est ce qu'il faut pour repérer une coquille sans bloquer un
    chargement légitime.
    """
    ctx = service.ctx
    ctx.guard(campaign, "managers")
    _, parsed = service.parser.parse("portfolios", **kwargs)
    outcome = _base_outcome("portfolios", parsed)
    outcome.storage_path = service.batches.archive(campaign, "portfolios", kwargs)
    if not parsed.rows:
        return outcome

    rows, errors = map_portfolios(campaign.id, parsed.rows)
    outcome.errors.extend(errors)
    outcome.rows_rejected += len(errors)
    if not rows:
        return outcome

    known = set(ctx.referentials.items_by_number(campaign.id))
    unknown = sorted({r.item_number for r in rows if r.item_number not in known})
    outcome.rows_accepted = len(rows)
    outcome.details["unknownItems"] = len(unknown)
    outcome.details["unknownItemNumbers"] = unknown[:50]
    outcome.details["cleared"] = sum(1 for r in rows if not r.actor)
    outcome.details["actors"] = len({r.actor for r in rows if r.actor})

    with ctx.db.transaction() as conn:
        ctx.portfolios.upsert(campaign.id, rows, actor=ctx.actor, conn=conn)
        outcome.batch_id = service.batches.record_batch(
            campaign.id, "portfolios", outcome, conn=conn, **kwargs
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.IMPORT,
            entity_type="item_portfolio",
            summary=(
                f"Portefeuilles : {len(rows)} attribution(s) sur "
                f"{outcome.details['actors']} personne(s)"
            ),
            conn=conn,
        )
    return outcome


def clear_portfolios(ctx: ServiceContext, campaign: Campaign) -> int:
    """Tout retirer — le geste « je repars de zéro », demandé explicitement."""
    ctx.guard(campaign, "managers")
    with ctx.db.transaction() as conn:
        removed = ctx.portfolios.clear(campaign.id, conn=conn)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="item_portfolio",
            summary=f"Portefeuilles vidés : {removed} attribution(s) retirées",
            conn=conn,
        )
    return removed


def portfolio_filter(
    ctx: ServiceContext, campaign: Campaign, *, mine: bool
) -> frozenset[str] | None:
    """Les références à garder, ou ``None`` quand rien n'est filtré.

    ``None`` et l'ensemble vide disent deux choses différentes, et les
    confondre ferait mentir l'écran : sans filtre, on voit tout ; avec un
    filtre et aucun portefeuille, on ne voit **rien**, et l'interface doit
    pouvoir dire « aucune référence ne vous est attribuée » plutôt que de
    rendre la liste entière comme si la bascule n'existait pas.
    """
    if not mine:
        return None
    return PortfolioService(ctx).mine(campaign)


def kept_by(items: Sequence[Any], wanted: frozenset[str] | None, key: str) -> list[Any]:
    """Filtrer une liste de lignes sur *wanted*, en laissant tout passer si ``None``."""
    if wanted is None:
        return list(items)
    return [row for row in items if getattr(row, key, None) in wanted]
