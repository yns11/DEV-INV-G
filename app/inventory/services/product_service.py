"""Le produit fabriqué : à quel assemblage une référence appartient.

Trois découpes cohabitent dans l'application, et celle-ci est la troisième.

**Le périmètre** répartit des *emplacements* entre gestionnaires : quels journaux,
quelles zones sont à qui.

**Le portefeuille** répartit des *articles* entre personnes : qui suit quelle
référence, où qu'elle soit rangée.

**Le produit fabriqué** ne répartit rien du tout. Il dit ce que l'usine fait de
la référence, et il sert à *rapprocher* plutôt qu'à filtrer une charge de
travail : deux références du même produit dont les écarts se compensent à peu
près ne sont pas deux anomalies indépendantes, c'est la signature d'une
inversion au comptage. Ni la catégorie, ni le programme, ni l'emplacement ne
rapprochent ces deux lignes-là.

Pourquoi une table plutôt qu'une colonne du référentiel
--------------------------------------------------------
Sa place est dans `item`, et c'est là qu'elle ira. Mais le référentiel articles
**gèle à l'entrée en comptage** : sur les campagnes déjà gelées — celles
précisément qu'on analyse — une colonne d'`item` serait arrivée trop tard pour
servir. La table à part se charge quand on veut, y compris en pleine analyse.
"""

from __future__ import annotations

from typing import Any

from ..domain.enums import AuditAction
from ..domain.models import Campaign
from ..ingest import map_products
from .context import ServiceContext
from .import_batches import ImportOutcome
from .import_parsing import _base_outcome

__all__ = ["ProductService", "import_products", "clear_products"]


class ProductService:
    """Lecture du rattachement article → produit fabriqué."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def overview(self, campaign: Campaign) -> dict[str, Any]:
        """Le tableau entier, et ce qu'il pèse par produit.

        Les deux ensemble parce que l'écran montre les deux : la grille des
        rattachements, et au-dessus le décompte qui dit d'un coup d'œil combien
        de produits sont couverts et combien de références n'en ont aucun.
        """
        ctx = self.ctx
        rows = ctx.products.list(campaign.id)
        known = ctx.referentials.items_by_number(campaign.id)
        return {
            "rows": [
                {
                    "itemNumber": p.item_number,
                    "product": p.product,
                    # Une référence rattachée mais absente du référentiel est le
                    # cas qu'on veut voir : le tableau se charge souvent avant
                    # les articles, et une coquille y reste invisible jusqu'au
                    # jour où le filtre de la vue Écarts ne rend rien.
                    "name": known[p.item_number].name if p.item_number in known else "",
                    "known": p.item_number in known,
                }
                for p in rows
            ],
            "byProduct": [
                {"product": row["product"], "items": row["items"]}
                for row in ctx.products.counts_by_product(campaign.id)
            ],
            "unassigned": max(len(known) - len(rows), 0),
        }


def import_products(service: Any, campaign: Campaign, **kwargs: Any) -> ImportOutcome:
    """Charger le rattachement — fichier, collage ou saisie.

    Gardé par ``managers``, comme les gestionnaires, leurs périmètres et les
    portefeuilles : c'est la même nature de décision — une clé de lecture posée
    sur la campagne — et elle bouge aux mêmes moments, jusqu'à la clôture
    comprise. C'est aussi ce qui rend ce tableau chargeable sur une campagne
    dont le référentiel articles est déjà gelé, ce qui est sa raison d'être.

    **Une fusion entre les références, un remplacement sur chacune.** Un fichier
    de trente références ne dit rien des quatre cent cinquante autres. Mais une
    référence n'appartient qu'à un produit : la recharger la déplace. Un produit
    vide la détache.

    Les références inconnues du référentiel ne sont **pas** refusées : le tableau
    se prépare souvent avant que les articles ne soient chargés. Elles sont
    comptées et nommées dans le rapport, et l'écran les montre.
    """
    ctx = service.ctx
    ctx.guard(campaign, "managers")
    _, parsed = service.parser.parse("products", **kwargs)
    outcome = _base_outcome("products", parsed)
    outcome.storage_path = service.batches.archive(campaign, "products", kwargs)
    if not parsed.rows:
        return outcome

    rows, errors = map_products(campaign.id, parsed.rows)
    outcome.errors.extend(errors)
    outcome.rows_rejected += len(errors)
    if not rows:
        return outcome

    known = set(ctx.referentials.items_by_number(campaign.id))
    unknown = sorted({r.item_number for r in rows if r.item_number not in known})
    outcome.rows_accepted = len(rows)
    outcome.details["unknownItems"] = len(unknown)
    outcome.details["unknownItemNumbers"] = unknown[:50]
    outcome.details["detached"] = sum(1 for r in rows if not r.product)
    outcome.details["products"] = len({r.product for r in rows if r.product})

    with ctx.db.transaction() as conn:
        ctx.products.upsert(campaign.id, rows, actor=ctx.actor, conn=conn)
        outcome.batch_id = service.batches.record_batch(
            campaign.id, "products", outcome, conn=conn, **kwargs
        )
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.IMPORT,
            entity_type="item_product",
            summary=(
                f"Produits fabriqués : {len(rows)} référence(s) sur "
                f"{outcome.details['products']} produit(s)"
            ),
            conn=conn,
        )
    return outcome


def clear_products(ctx: ServiceContext, campaign: Campaign) -> int:
    """Tout détacher — le geste « je repars de zéro », demandé explicitement."""
    ctx.guard(campaign, "managers")
    with ctx.db.transaction() as conn:
        removed = ctx.products.clear(campaign.id, conn=conn)
        ctx.record(
            campaign_id=campaign.id,
            action=AuditAction.DELETE,
            entity_type="item_product",
            summary=f"Produits fabriqués vidés : {removed} rattachement(s) retirés",
            conn=conn,
        )
    return removed
