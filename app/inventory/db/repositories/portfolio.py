"""Le portefeuille : quelles références sont à qui.

Une table à part plutôt qu'une colonne sur `item`, et pour une raison qui n'est
pas l'élégance : le référentiel articles **gèle à l'entrée en comptage**, alors
que la répartition du travail, elle, bouge — quelqu'un tombe malade le matin du
jour J, l'analyse se répartit autrement trois semaines plus tard. Une colonne
sur `item` aurait rendu le portefeuille immodifiable au moment précis où il
sert, exactement comme les gestionnaires l'étaient avant la migration 031.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.models import ItemPortfolio
from ._base import _Base

__all__ = ["PortfolioRepository"]


class PortfolioRepository(_Base):
    """Lecture et écriture des attributions d'articles."""

    def list(self, campaign_id: str) -> list[ItemPortfolio]:
        rows = self._fetch_all(
            "SELECT campaign_id, item_number, actor FROM item_portfolio "
            "WHERE campaign_id = %s ORDER BY actor, item_number",
            (campaign_id,),
        )
        return [
            ItemPortfolio(
                campaign_id=str(r["campaign_id"]),
                item_number=r["item_number"],
                actor=r["actor"],
            )
            for r in rows
        ]

    def items_of(self, campaign_id: str, actor: str) -> frozenset[str]:
        """Les références de *actor*, sous la forme que les filtres attendent.

        Un ensemble et non une liste : les trois écrans qui s'en servent posent
        la même question des centaines de fois — « celle-ci est-elle à moi ? » —
        et une liste y répondrait en la parcourant à chaque fois.
        """
        rows = self._fetch_all(
            "SELECT item_number FROM item_portfolio "
            "WHERE campaign_id = %s AND actor = %s",
            (campaign_id, (actor or "").strip().lower()),
        )
        return frozenset(r["item_number"] for r in rows)

    def counts_by_actor(self, campaign_id: str) -> list[dict[str, Any]]:
        """Combien de références chacun suit — ce que l'écran résume en tête."""
        return self._fetch_all(
            "SELECT actor, count(*) AS items FROM item_portfolio "
            "WHERE campaign_id = %s GROUP BY actor ORDER BY count(*) DESC, actor",
            (campaign_id,),
        )

    def upsert(
        self,
        campaign_id: str,
        rows: Sequence[ItemPortfolio],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Poser les attributions que *rows* porte, et **elles seules**.

        Une fusion, pas un remplacement. Un fichier de trente références ne dit
        rien des quatre cent cinquante autres, et les effacer parce qu'il ne les
        mentionne pas ferait d'une correction ciblée une remise à zéro. La
        grille se charge en plusieurs fois, un portefeuille à la fois, ce qui est
        la façon dont ce tableau se construit réellement.

        Une adresse **vide** retire l'attribution : c'est la seule façon de le
        faire depuis le même fichier qui les pose, et la ligne dit alors « cette
        référence n'est plus à personne » plutôt que de ne rien dire.
        """
        assigned = [r for r in rows if r.actor]
        cleared = [r.item_number for r in rows if not r.actor]
        written = 0
        if assigned:
            written += self._execute_many(
                "INSERT INTO item_portfolio "
                "(campaign_id, item_number, actor, updated_by, updated_at) "
                "VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (campaign_id, item_number) DO UPDATE SET "
                "actor = EXCLUDED.actor, updated_by = EXCLUDED.updated_by, "
                "updated_at = now()",
                [(campaign_id, r.item_number, r.actor, actor) for r in assigned],
                conn=conn,
            )
        if cleared:
            written += self._execute(
                "DELETE FROM item_portfolio "
                "WHERE campaign_id = %s AND item_number = ANY(%s)",
                (campaign_id, cleared),
                conn=conn,
            )
        return written

    def clear(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> int:
        """Vider la table — le geste « je repars de zéro », demandé explicitement."""
        return self._execute(
            "DELETE FROM item_portfolio WHERE campaign_id = %s",
            (campaign_id,),
            conn=conn,
        )
