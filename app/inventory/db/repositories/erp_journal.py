"""Les journaux ERP, leur périmètre déclaré et leurs lignes brutes.

Voir :mod:`inventory.db.repositories` pour les trois règles que tous les dépôts
appliquent.

Un journal ERP n'est pas un emplacement. Il tient à un entrepôt et couvre
plusieurs emplacements — sur l'export du 13 juin 2026, 48 journaux sur 73 en
couvrent plus d'un, jusqu'à 54 pour l'un d'eux. Ses lignes sont conservées au
grain où l'ERP les produit, une par étiquette, quand
:class:`~inventory.domain.models.CountJournalLine` garde celui sur lequel tout
le reste de l'application est écrit : emplacement plus article.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Sequence
from typing import Any

import psycopg

from ...domain.enums import JournalKind
from ...domain.models import (
    ErpJournal,
    ErpJournalLine,
    LocationKey,
)
from ._base import _Base, _NullContext, new_id

__all__ = ["ErpJournalRepository"]

#: Un emplacement vrac se compte **en quantité**, pas en lots étiquetés.
#:
#: Les lignes d'un journal ``INVV`` portent toutes la même étiquette générique —
#: littéralement « VRAC » dans l'export. Ce n'est pas l'identité d'une palette :
#: c'est un remplissage de colonne. Le contrôle par étiquette la lisait pourtant
#: comme une identité, et deux emplacements vrac quelconques se retrouvaient
#: donc « la même étiquette comptée aux deux endroits » — quatre cents lignes de
#: faux doublons, à trancher une par une, devant lesquelles il n'y a rien à
#: faire.
#:
#: La règle porte sur le **type de journal**, pas sur la valeur de l'étiquette :
#: c'est ce que le métier dit — un journal vrac ne compte pas des lots — et non
#: une chaîne de caractères qui pourrait changer au prochain export.
_NO_LABEL_KIND = "INVV"


#: Une ligne qui porte une **étiquette identifiante**, et le journal qui la dit.
#:
#: Une seule définition pour les deux contrôles et pour les deux côtés de
#: chacun — l'emplacement scellé comme l'autre. Écrite quatre fois, la règle
#: aurait divergé au premier ajout : il a suffi d'oublier les journaux vrac d'un
#: seul côté pour que le contrôle continue de les lire de l'autre.
_LABELLED_LINES = f"""
    SELECT l.label_id, l.warehouse_id, l.location_id, l.item_number,
           l.erp_journal_id, l.qty_counted, j.journal_number
    FROM erp_journal_line l
    JOIN erp_journal j
      ON j.id = l.erp_journal_id AND j.campaign_id = l.campaign_id
     AND j.deleted_at IS NULL
     AND j.kind <> '{_NO_LABEL_KIND}'
    WHERE l.campaign_id = %(cid)s
      AND l.label_id <> ''
      AND l.qty_counted <> 0
"""

#: Les lignes qui *portent la preuve* d'un emplacement scellé.
#:
#: Celles de son journal propriétaire, et d'aucun autre : la jointure sur
#: ``erp_journal_scope`` est ce qui distingue le comptage retenu d'une simple
#: ligne de passage. Sans elle, la ligne de passage d'un troisième journal
#: servait de point de départ, et la même paire ressortait autant de fois que de
#: journaux ayant touché l'emplacement.
_SEALED_EVIDENCE = """
    SELECT e.*
    FROM etiquetee e
    JOIN erp_journal_scope sc
      ON sc.campaign_id = %(cid)s
     AND sc.erp_journal_id = e.erp_journal_id
     AND sc.warehouse_id = e.warehouse_id
     AND sc.location_id = e.location_id
    WHERE (e.warehouse_id, e.location_id) IN (
            SELECT * FROM unnest(%(wh)s::text[], %(loc)s::text[])
    )
"""


class ErpJournalRepository(_Base):
    """Les journaux ERP d'une campagne."""

    _COLUMNS = (
        "id, campaign_id, journal_number, kind, description, site_id, "
        "erp_posted, erp_posted_at, line_count, first_imported_at, "
        "last_imported_at, scope_declared_at, scope_declared_by, "
        "counted_on, sealed_at, sealed_by"
    )

    _LINE_COLUMNS = (
        "id, erp_journal_id, campaign_id, erp_line_number, site_id, warehouse_id, "
        "location_id, label_id, serial_number, item_number, qty_on_hand, "
        "qty_counted, unit, inventory_status_id"
    )

    # ------------------------------------------------------------------ lecture

    def list(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> list[ErpJournal]:
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM erp_journal "
            "WHERE campaign_id = %s AND deleted_at IS NULL "
            "ORDER BY journal_number",
            (campaign_id,),
            conn=conn,
        )
        scopes = self.scopes(campaign_id, conn=conn)
        return [self._journal(row, scopes.get(str(row["id"]), [])) for row in rows]

    def get_by_number(
        self, campaign_id: str, number: str, *,
        conn: psycopg.Connection | None = None,
    ) -> ErpJournal | None:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM erp_journal "
            "WHERE campaign_id = %s AND journal_number = %s AND deleted_at IS NULL",
            (campaign_id, number),
            conn=conn,
        )
        if row is None:
            return None
        scopes = self.scopes(campaign_id, conn=conn)
        return self._journal(row, scopes.get(str(row["id"]), []))

    def scopes(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> dict[str, list[LocationKey]]:
        rows = self._fetch_all(
            "SELECT erp_journal_id, warehouse_id, location_id FROM erp_journal_scope "
            "WHERE campaign_id = %s ORDER BY warehouse_id, location_id",
            (campaign_id,),
            conn=conn,
        )
        out: dict[str, list[LocationKey]] = {}
        for row in rows:
            out.setdefault(str(row["erp_journal_id"]), []).append(
                LocationKey(
                    warehouse_id=row["warehouse_id"], location_id=row["location_id"]
                )
            )
        return out

    def lines(
        self, campaign_id: str, erp_journal_id: str, *,
        conn: psycopg.Connection | None = None,
    ) -> list[ErpJournalLine]:
        rows = self._fetch_all(
            f"SELECT {self._LINE_COLUMNS} FROM erp_journal_line "
            "WHERE campaign_id = %s AND erp_journal_id = %s "
            "ORDER BY erp_line_number NULLS LAST, item_number",
            (campaign_id, erp_journal_id),
            conn=conn,
        )
        return [self._line(row) for row in rows]

    # ------------------------------------------------------------------ écriture

    def upsert_journal(
        self,
        campaign_id: str,
        *,
        journal_number: str,
        kind: JournalKind,
        description: str = "",
        site_id: str = "",
        erp_posted: bool = False,
        erp_posted_at: dt.datetime | None = None,
        line_count: int = 0,
        counted_on: dt.date | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Enregistrer ou rafraîchir l'en-tête d'un journal ERP, et rendre son id.

        Le périmètre déclaré et le scellement ne sont **jamais** touchés ici :
        un réimport rafraîchit les faits que l'ERP annonce — postage, nombre de
        lignes, date de comptage — pas la décision qu'un humain a prise.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "INSERT INTO erp_journal (id, campaign_id, journal_number, kind, "
                "description, site_id, erp_posted, erp_posted_at, line_count, "
                "counted_on) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (campaign_id, journal_number) "
                "WHERE deleted_at IS NULL DO UPDATE SET "
                "kind = EXCLUDED.kind, description = EXCLUDED.description, "
                "site_id = EXCLUDED.site_id, erp_posted = EXCLUDED.erp_posted, "
                "erp_posted_at = EXCLUDED.erp_posted_at, "
                "line_count = EXCLUDED.line_count, "
                # `COALESCE` dans cet ordre : un export qui omet la colonne ne
                # doit pas effacer la date qu'un export précédent portait.
                "counted_on = COALESCE(EXCLUDED.counted_on, erp_journal.counted_on), "
                "last_imported_at = now() "
                "RETURNING id",
                (new_id(), campaign_id, journal_number, str(kind), description,
                 site_id, erp_posted, erp_posted_at, line_count, counted_on),
            )
            return str(cur.fetchone()["id"])

    def replace_lines(
        self,
        campaign_id: str,
        erp_journal_id: str,
        lines: Sequence[ErpJournalLine],
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Remplacer les lignes **de ce journal**, et d'aucun autre.

        C'est la traduction en une phrase de la règle « l'application
        n'additionne jamais plusieurs photographies, sauf pour les comptages
        avancés » : chaque import remplace ce qu'il apporte, et laisse
        intact ce qu'il n'apporte pas. Les journaux d'un lot avancé, absents de
        la fenêtre de dates du jour J, survivent donc d'eux-mêmes — sans
        exception à écrire, ce qui est la meilleure façon de ne pas l'oublier.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_line "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            if not lines:
                return 0
            cur.executemany(
                "INSERT INTO erp_journal_line (id, erp_journal_id, campaign_id, "
                "erp_line_number, site_id, warehouse_id, location_id, label_id, "
                "serial_number, item_number, qty_on_hand, qty_counted, unit, "
                "inventory_status_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (line.id or new_id(), erp_journal_id, campaign_id,
                     line.erp_line_number, line.site_id, line.warehouse_id,
                     line.location_id, line.label_id, line.serial_number,
                     line.item_number, line.qty_on_hand, line.qty_counted,
                     line.unit, line.inventory_status_id)
                    for line in lines
                ],
            )
            return len(lines)

    def set_scope(
        self,
        campaign_id: str,
        erp_journal_id: str,
        keys: Sequence[LocationKey],
        *,
        actor: str,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Déclarer les emplacements que ce journal couvre, **et les sceller**.

        Les deux gestes n'en font qu'un. Dire quels emplacements ce journal
        couvre, c'est dire lesquels sont comptés et ne bougeront plus : il n'y
        avait aucune décision entre les deux, seulement des clics.

        Un emplacement n'appartient au périmètre que d'un seul journal — c'est
        un index unique de la migration 025, pas une vérification faite ici :
        une déclaration qui empiéterait sur un autre journal est refusée par la
        base, même si le calcul qui a produit la proposition se trompait.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_scope "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            if keys:
                cur.executemany(
                    "INSERT INTO erp_journal_scope "
                    "(erp_journal_id, campaign_id, warehouse_id, location_id) "
                    "VALUES (%s,%s,%s,%s)",
                    [
                        (erp_journal_id, campaign_id, k.warehouse_id, k.location_id)
                        for k in keys
                    ],
                )
            cur.execute(
                "UPDATE erp_journal SET scope_declared_at = now(), "
                "scope_declared_by = %s, sealed_at = now(), sealed_by = %s "
                "WHERE campaign_id = %s AND id = %s",
                (actor, actor, campaign_id, erp_journal_id),
            )
            return len(keys)

    def unseal(
        self,
        campaign_id: str,
        erp_journal_id: str,
        *,
        conn: psycopg.Connection | None = None,
    ) -> int:
        """Retirer le périmètre et le scellement du journal.

        Le périmètre part avec le scellement, et c'est voulu : sans périmètre,
        le journal n'a plus d'emplacement à couvrir, donc plus rien à sceller.
        Redéclarer est le geste qui rescelle.
        """
        owns = conn is None
        ctx = self.db.transaction() if owns else _NullContext(conn)
        with ctx as connection, connection.cursor() as cur:
            cur.execute(
                "DELETE FROM erp_journal_scope "
                "WHERE campaign_id = %s AND erp_journal_id = %s",
                (campaign_id, erp_journal_id),
            )
            cur.execute(
                "UPDATE erp_journal SET scope_declared_at = NULL, "
                "scope_declared_by = '', sealed_at = NULL, sealed_by = '' "
                "WHERE campaign_id = %s AND id = %s",
                (campaign_id, erp_journal_id),
            )
            return cur.rowcount

    def touch_import(
        self, campaign_id: str, *, conn: psycopg.Connection | None = None
    ) -> None:
        """Horodater le dernier import de journaux réussi sur la campagne."""
        self._execute(
            "UPDATE campaign SET journals_imported_at = now() WHERE id = %s",
            (campaign_id,),
            conn=conn,
        )

    # ------------------------------------------------------------- propositions

    def candidate_locations(
        self,
        campaign_id: str,
        erp_journal_id: str,
        *,
        buffer_key: LocationKey,
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Les emplacements que ce journal *pourrait* couvrir.

        Ceux de ses lignes, moins le tampon, moins ceux déjà alloués à un autre
        journal. Classés par nombre de lignes décroissant : le périmètre réel
        arrive en tête, ce qui rend la sélection courte quand elle est évidente.

        L'application propose, l'utilisateur tranche. Les emplacements des lignes
        ne suffisent pas à dire le périmètre — certaines ne sont là que pour
        matérialiser un déplacement, 1 932 lignes sur 58 345 dans l'export
        analysé — et deviner à leur place produirait des références fausses sur
        des emplacements que le journal ne couvre pas.
        """
        return self._fetch_all(
            """
            SELECT l.warehouse_id,
                   l.location_id,
                   count(*)                             AS line_count,
                   count(DISTINCT l.item_number)        AS item_count,
                   sum(l.qty_on_hand)                   AS qty_on_hand,
                   sum(l.qty_counted)                   AS qty_counted
            FROM erp_journal_line l
            WHERE l.campaign_id = %(cid)s
              AND l.erp_journal_id = %(jid)s
              AND l.location_id <> ''
              AND NOT (l.warehouse_id = %(bwh)s AND l.location_id = %(bloc)s)
              AND NOT EXISTS (
                    SELECT 1 FROM erp_journal_scope s
                    WHERE s.campaign_id = l.campaign_id
                      AND s.warehouse_id = l.warehouse_id
                      AND s.location_id = l.location_id
                      AND s.erp_journal_id <> l.erp_journal_id
              )
            GROUP BY l.warehouse_id, l.location_id
            ORDER BY line_count DESC, l.warehouse_id, l.location_id
            """,
            {
                "cid": campaign_id, "jid": erp_journal_id,
                "bwh": buffer_key.warehouse_id, "bloc": buffer_key.location_id,
            },
            conn=conn,
        )

    # ------------------------------------------------------------- agrégations

    def aggregate_in_scope(
        self,
        campaign_id: str,
        *,
        excluded_labels: Collection[tuple[str, str]] = (),
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Référence et comptage par (entrepôt, emplacement, article), dans le périmètre.

        C'est ici que le journal devient autonome : ``qty_on_hand`` agrège la
        colonne « Stock ERP » des lignes, c'est-à-dire le stock d'avant comptage,
        et un lot avancé n'a donc besoin d'aucun chargement séparé.

        La jointure sur le périmètre déclaré n'est pas décorative : sans elle,
        une ligne de passage créerait une référence sur un emplacement que le
        journal ne couvre pas.

        ``excluded_labels`` porte les étiquettes qu'une décision a fait sortir de
        leur emplacement scellé — la pièce est ailleurs, quelqu'un est allé
        voir. Les exclure ici plutôt qu'après coup est ce qui fait que la
        référence, le comptage et l'écart racontent la même histoire.
        """
        return self._fetch_all(
            """
            SELECT l.warehouse_id,
                   l.location_id,
                   l.item_number,
                   max(l.unit)                   AS unit,
                   max(j.journal_number)         AS journal_number,
                   sum(l.qty_on_hand)            AS qty_on_hand,
                   sum(l.qty_counted)            AS qty_counted,
                   count(*)                      AS label_count,
                   bool_and(j.erp_posted)        AS erp_posted
            FROM erp_journal_line l
            JOIN erp_journal j
              ON j.id = l.erp_journal_id AND j.campaign_id = l.campaign_id
            JOIN erp_journal_scope s
              ON s.erp_journal_id = l.erp_journal_id
             AND s.campaign_id = l.campaign_id
             AND s.warehouse_id = l.warehouse_id
             AND s.location_id = l.location_id
            WHERE l.campaign_id = %(cid)s AND j.deleted_at IS NULL
              AND (l.label_id, l.item_number) <> ALL (
                    SELECT * FROM unnest(%(labels)s::text[], %(items)s::text[])
              )
            GROUP BY l.warehouse_id, l.location_id, l.item_number
            ORDER BY l.warehouse_id, l.location_id, l.item_number
            """,
            {
                "cid": campaign_id,
                "labels": [label for label, _ in excluded_labels],
                "items": [item for _, item in excluded_labels],
            },
            conn=conn,
        )

    def labels_counted_elsewhere(
        self,
        campaign_id: str,
        sealed: Sequence[LocationKey],
        *,
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Les étiquettes d'un emplacement scellé retrouvées **ailleurs**.

        Le seul contrôle du dispositif qui descende au grain de l'étiquette, et
        celui qui rattrape ce que la dérive ne voit pas : une pièce sortie d'un
        emplacement scellé sans aucune transaction ERP laisse une dérive nulle,
        mais si elle est re-scannée ailleurs, son étiquette apparaît dans un
        second journal.

        Deux restrictions, et chacune corrige une liste qui ne voulait rien dire.

        **Ailleurs veut dire un autre emplacement.** La condition ne portait que
        sur le journal : deux journaux passant sur le même emplacement scellé
        remplissaient l'écran de lignes « ATP / SF1 comptée aussi en ATP / SF1 ».
        La pièce n'a pas bougé, et les trois issues proposées — la mettre au
        nouvel emplacement, l'en enlever, la rescanner — n'ont aucun sens quand
        il n'y a pas de nouvel emplacement. Ce cas-là est un second passage sur
        le même emplacement : :meth:`labels_recounted_in_place` le dit.

        **Le point de vue est celui du journal propriétaire.** Sans cela, la
        ligne de l'autre journal servait à son tour de départ et la même paire
        ressortait deux fois, une par sens. Un emplacement scellé appartient à un
        seul journal ; c'est le sien qui porte la preuve.
        """
        if not sealed:
            return []
        return self._fetch_all(
            f"""
            WITH etiquetee AS ({_LABELLED_LINES}),
                 scelle AS ({_SEALED_EVIDENCE})
            SELECT s.label_id,
                   s.item_number,
                   s.warehouse_id            AS sealed_warehouse_id,
                   s.location_id             AS sealed_location_id,
                   o.warehouse_id            AS other_warehouse_id,
                   o.location_id             AS other_location_id,
                   o.journal_number          AS other_journal_number,
                   o.qty_counted             AS other_qty_counted
            FROM scelle s
            JOIN etiquetee o
              ON o.label_id = s.label_id
             AND o.erp_journal_id <> s.erp_journal_id
             AND (o.warehouse_id, o.location_id)
                 <> (s.warehouse_id, s.location_id)
            ORDER BY s.label_id, o.warehouse_id, o.location_id
            """,
            {
                "cid": campaign_id,
                "wh": [k.warehouse_id for k in sealed],
                "loc": [k.location_id for k in sealed],
            },
            conn=conn,
        )

    def labels_recounted_in_place(
        self,
        campaign_id: str,
        sealed: Sequence[LocationKey],
        *,
        conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        """Les emplacements scellés qu'un second journal a recomptés **sur place**.

        Ce n'est pas un déplacement — l'étiquette est là où elle doit être — et
        ça n'a donc rien à faire dans la liste des étiquettes comptées ailleurs,
        qu'elle noyait. C'est autre chose, et qui mérite d'être dit : deux
        journaux ont compté le même emplacement, parfois avec des quantités
        différentes, et seul celui qui le possède est retenu.

        Résumé par (emplacement scellé, autre journal) : la liste ligne à ligne
        n'apprendrait rien de plus, et un emplacement à cinq cents étiquettes en
        produirait cinq cents.
        """
        if not sealed:
            return []
        return self._fetch_all(
            f"""
            WITH etiquetee AS ({_LABELLED_LINES}),
                 scelle AS ({_SEALED_EVIDENCE})
            SELECT s.warehouse_id              AS sealed_warehouse_id,
                   s.location_id               AS sealed_location_id,
                   max(s.journal_number)       AS owner_journal_number,
                   o.journal_number            AS other_journal_number,
                   count(DISTINCT o.label_id)  AS label_count
            FROM scelle s
            JOIN etiquetee o
              ON o.label_id = s.label_id
             AND o.erp_journal_id <> s.erp_journal_id
             AND o.warehouse_id = s.warehouse_id
             AND o.location_id = s.location_id
            GROUP BY s.warehouse_id, s.location_id, o.journal_number
            ORDER BY s.warehouse_id, s.location_id, o.journal_number
            """,
            {
                "cid": campaign_id,
                "wh": [k.warehouse_id for k in sealed],
                "loc": [k.location_id for k in sealed],
            },
            conn=conn,
        )

    # ------------------------------------------------------------------ mapping

    @staticmethod
    def _journal(row: dict[str, Any], scope: list[LocationKey]) -> ErpJournal:
        return ErpJournal(
            id=str(row["id"]),
            campaign_id=str(row["campaign_id"]),
            journal_number=row["journal_number"],
            kind=JournalKind(row["kind"]),
            description=row["description"],
            site_id=row["site_id"],
            erp_posted=row["erp_posted"],
            erp_posted_at=row["erp_posted_at"],
            line_count=int(row["line_count"] or 0),
            first_imported_at=row["first_imported_at"],
            last_imported_at=row["last_imported_at"],
            scope=scope,
            scope_declared_at=row["scope_declared_at"],
            scope_declared_by=row["scope_declared_by"] or "",
            counted_on=row["counted_on"],
            sealed_at=row["sealed_at"],
            sealed_by=row["sealed_by"] or "",
        )

    @staticmethod
    def _line(row: dict[str, Any]) -> ErpJournalLine:
        return ErpJournalLine(
            id=str(row["id"]),
            erp_journal_id=str(row["erp_journal_id"]),
            campaign_id=str(row["campaign_id"]),
            erp_line_number=row["erp_line_number"],
            site_id=row["site_id"],
            warehouse_id=row["warehouse_id"],
            location_id=row["location_id"],
            label_id=row["label_id"],
            serial_number=row["serial_number"],
            item_number=row["item_number"],
            qty_on_hand=row["qty_on_hand"],
            qty_counted=row["qty_counted"],
            unit=row["unit"],
            inventory_status_id=row["inventory_status_id"],
        )
