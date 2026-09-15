"""Le produit fabriqué d'une référence.

Une table à part plutôt qu'une colonne sur `item`, et pour la raison qui a déjà
valu aux portefeuilles la même forme : le référentiel articles **gèle à l'entrée
en comptage**. Sa place naturelle est bien une colonne d'`item`, et c'est là
qu'elle ira ; sur les campagnes déjà gelées — celles précisément qu'on analyse
aujourd'hui — elle serait arrivée trop tard pour servir.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.models import ItemProduct
from ._base import _Base, _NullContext

__all__ = ["ProductRepository"]


class ProductRepository(_Base):
    """Lecture et écriture du rattachement article → produit fabriqué."""

    def list(self, campaign_id: str) -> list[ItemProduct]:
        rows = self._fetch_all(
            "SELECT campaign_id, item_number, product FROM item_product "
            "WHERE campaign_id = %s ORDER BY product, item_number",
            (campaign_id,),
        )
        return [
            ItemProduct(
                campaign_id=str(r["campaign_id"]),
                item_number=r["item_number"],
                product=r["product"],
            )
            for r in rows
        ]

    def by_item(self, campaign_id: str) -> dict[str, str]:
        """Le produit de chaque référence, sous la forme que les jointures veulent.

        Un dictionnaire et non une liste : la vue Écarts pose la question pour
        chacune de ses cinq cents lignes, et une liste y répondrait en la
        parcourant à chaque fois.
        """
        rows = self._fetch_all(
            "SELECT item_number, product FROM item_product WHERE campaign_id = %s",
            (campaign_id,),
        )
        return {r["item_number"]: r["product"] for r in rows}

    def counts_by_product(self, campaign_id: str) -> list[dict[str, Any]]:
        """Combien de références par produit — ce que l'écran résume en tête."""
        return self._fetch_all(
            "SELECT product, count(*) AS items FROM item_product "
            "WHERE campaign_id = %s GROUP BY product ORDER BY count(*) DESC, product",
            (campaign_id,),
        )

    def upsert(
        self,
        campaign_id: str,
        rows: Sequence[ItemProduct],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Rattacher les références que *rows* cite, et **elles seules**.

        Une fusion entre les références — un fichier de trente lignes ne dit
        rien des quatre cent cinquante autres — et un remplacement sur chacune,
        puisqu'une référence n'appartient qu'à un produit : la recharger la
        déplace.

        Un produit **vide** détache la référence. C'est la seule façon de défaire
        un rattachement depuis le même fichier qui les pose, et la ligne dit
        alors « celle-ci n'est rattachée à rien » plutôt que de ne rien dire.
        """
        attached = [r for r in rows if r.product]
        detached = [r.item_number for r in rows if not r.product]
        if not attached and not detached:
            return 0

        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection:
            if detached:
                self._execute(
                    "DELETE FROM item_product "
                    "WHERE campaign_id = %s AND item_number = ANY(%s)",
                    (campaign_id, detached),
                    conn=connection,
                )
            self._execute_many(
                "INSERT INTO item_product "
                "(campaign_id, item_number, product, updated_by, updated_at) "
                "VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (campaign_id, item_number) DO UPDATE SET "
                "product = EXCLUDED.product, updated_by = EXCLUDED.updated_by, "
                "updated_at = now()",
                [(campaign_id, r.item_number, r.product, actor) for r in attached],
                conn=connection,
            )
        return len(attached)

    def clear(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> int:
        """Tout détacher — le geste « je repars de zéro », demandé explicitement."""
        return self._execute(
            "DELETE FROM item_product WHERE campaign_id = %s",
            (campaign_id,),
            conn=conn,
        )
