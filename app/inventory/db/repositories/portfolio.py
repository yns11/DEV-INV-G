"""Le portefeuille : quelles références sont à qui.

Une table à part plutôt qu'une colonne sur `item`, et pour une raison qui n'est
pas l'élégance : le référentiel articles **gèle à l'entrée en comptage**, alors
que la répartition du travail, elle, bouge — quelqu'un tombe malade le matin du
jour J, l'analyse se répartit autrement trois semaines plus tard. Une colonne
sur `item` aurait rendu le portefeuille immodifiable au moment précis où il
sert, exactement comme les gestionnaires l'étaient avant la migration 031.

**Plusieurs personnes peuvent suivre la même référence** depuis la migration
034 : l'identité fait partie de la clé. Une colonne aurait suffi à dire « à qui
est cette référence » — c'était le premier choix — et elle décrivait mal
l'organisation, où un acheteur et un contrôleur de gestion suivent les mêmes
articles sans que l'un soit le propriétaire de l'autre.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ...domain.models import ItemPortfolio
from ._base import _Base, _NullContext

__all__ = ["PortfolioRepository"]


class PortfolioRepository(_Base):
    """Lecture et écriture des attributions d'articles."""

    def list(self, campaign_id: str) -> list[ItemPortfolio]:
        """Une ligne par couple référence / personne.

        Ordonné par référence d'abord : une référence suivie à deux voit ses
        deux propriétaires côte à côte, ce qui est la chose que le partage rend
        visible. L'ordre inverse — par personne — dispersait les deux lignes
        dans la grille et donnait à lire deux portefeuilles séparés là où il y
        a un partage.
        """
        rows = self._fetch_all(
            "SELECT campaign_id, item_number, actor FROM item_portfolio "
            "WHERE campaign_id = %s ORDER BY item_number, actor",
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
        """Combien de références chacun suit — ce que l'écran résume en tête.

        La somme de ces décomptes dépasse le nombre de références dès qu'une
        est partagée, et c'est exact : chacun en suit bien autant. C'est le
        décompte « sans propriétaire » qui doit, lui, raisonner en références
        distinctes — voir :meth:`assigned_items`.
        """
        return self._fetch_all(
            "SELECT actor, count(*) AS items FROM item_portfolio "
            "WHERE campaign_id = %s GROUP BY actor ORDER BY count(*) DESC, actor",
            (campaign_id,),
        )

    def assigned_items(self, campaign_id: str) -> int:
        """Combien de **références** ont au moins un propriétaire.

        Distinct de la hauteur de la table depuis que le partage existe :
        compter les lignes ferait passer une référence suivie à deux pour deux
        références couvertes, et l'écran annoncerait moins d'orphelines qu'il
        n'y en a — l'erreur qui rassure au lieu d'alerter.
        """
        row = self._fetch_one(
            "SELECT count(DISTINCT item_number) AS items FROM item_portfolio "
            "WHERE campaign_id = %s",
            (campaign_id,),
        )
        return int(row["items"]) if row else 0

    def upsert(
        self,
        campaign_id: str,
        rows: Sequence[ItemPortfolio],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Poser les propriétaires des références que *rows* cite, et **elles seules**.

        Deux portées, et c'est toute la règle.

        **Entre les références, une fusion.** Un fichier de trente références ne
        dit rien des quatre cent cinquante autres, et les effacer parce qu'il ne
        les mentionne pas ferait d'une correction ciblée une remise à zéro. Le
        tableau se charge en plusieurs fois, ce qui est la façon dont il se
        construit réellement.

        **Sur une référence citée, un remplacement.** Le fichier fait foi pour
        elle : ses lignes sont la liste complète de ceux qui la suivent. C'est ce
        qui permet de retirer *une* personne d'une référence partagée — on
        recharge la référence avec la liste voulue — là où une fusion pure ne
        saurait qu'ajouter, et obligerait à tout vider pour enlever quelqu'un.

        Une adresse **vide** est donc le cas limite et non une règle à part :
        une référence citée sans personne n'est plus à personne.

        La suppression et les insertions tiennent dans une seule transaction.
        Sans cela, un chargement interrompu au milieu laisserait des références
        sans propriétaire — et l'écran dirait « orpheline » d'une référence dont
        le tableau porte le nom de quelqu'un.
        """
        mentioned = list(dict.fromkeys(r.item_number for r in rows))
        if not mentioned:
            return 0

        owns_transaction = conn is None
        ctx = self.db.transaction() if owns_transaction else _NullContext(conn)
        with ctx as connection:
            self._execute(
                "DELETE FROM item_portfolio "
                "WHERE campaign_id = %s AND item_number = ANY(%s)",
                (campaign_id, mentioned),
                conn=connection,
            )
            assigned = [r for r in rows if r.actor]
            # `DO NOTHING` plutôt qu'une erreur : un tableur répète volontiers
            # la même paire, et deux fois « P-100 est à Anne » dit une seule
            # chose. Le rapport d'import, lui, les a déjà signalées en doublon.
            self._execute_many(
                "INSERT INTO item_portfolio "
                "(campaign_id, item_number, actor, updated_by, updated_at) "
                "VALUES (%s,%s,%s,%s, now()) "
                "ON CONFLICT (campaign_id, item_number, actor) DO NOTHING",
                [(campaign_id, r.item_number, r.actor, actor) for r in assigned],
                conn=connection,
            )
        return len(assigned)

    def clear(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> int:
        """Vider la table — le geste « je repars de zéro », demandé explicitement."""
        return self._execute(
            "DELETE FROM item_portfolio WHERE campaign_id = %s",
            (campaign_id,),
            conn=conn,
        )
