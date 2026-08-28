"""La campagne : son cycle de vie et ses seuils.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ...domain.enums import (
    CampaignStatus,
    ItemType,
)
from ...domain.models import (
    Campaign,
    CampaignConfig,
    Thresholds,
)
from ...errors import NotFoundError
from ._base import _Base

# --------------------------------------------------------------------------- #
# Campaign
# --------------------------------------------------------------------------- #

class CampaignRepository(_Base):
    """Campaigns, their configuration and their materiality thresholds."""

    _COLUMNS = (
        "id, code, label, count_date, status, config, referentials_frozen_at, "
        "book_stock_frozen_at, counting_frozen_at, closed_at, published_at, "
        "general_count_opened_at, journals_imported_at, "
        "cloned_from_code, "
        "engine_version, created_by, created_at, updated_at, row_version"
    )

    def list(
        self, *, include_closed: bool = True, limit: int = 100, offset: int = 0
    ) -> list[Campaign]:
        """Les campagnes, sans le total.

        ``page`` rend une paire parce qu'un écran qui pagine a besoin des deux.
        Un service qui cherche « la campagne précédente » n'a que faire du
        total, et itérer la paire lui donne silencieusement deux éléments dont
        le premier est une liste — ``other.id`` lève alors sur un ``list``, en
        production, sur trois écrans à la fois. Le nom dit désormais ce qu'on
        reçoit.
        """
        campaigns, _ = self.page(
            include_closed=include_closed, limit=limit, offset=offset
        )
        return campaigns

    def page(
        self, *, include_closed: bool = True, limit: int = 100, offset: int = 0
    ) -> tuple[list[Campaign], int]:
        """Une page de campagnes, et combien il y en a en tout.

        Le total n'est pas décoratif : la liste était bornée à cent sans le
        dire, si bien qu'après quelques années d'inventaires trimestriels les
        plus anciennes cessaient simplement d'apparaître. Aucun message, aucun
        bouton — elles n'existaient plus pour qui regardait l'écran, alors
        qu'elles étaient toujours en base.

        Pagination par décalage plutôt que par curseur : cette liste s'allonge
        de quelques lignes par an et se trie sur une date de comptage stable.
        La dérive qu'un curseur évite — une ligne insérée entre deux pages —
        suppose des écritures concurrentes fréquentes, ce qui n'est pas le cas
        ici, et coûterait un encodage de curseur que personne ne lirait.
        """
        clause = "" if include_closed else "AND status <> 'CLOSED'"
        total = self._fetch_one(
            f"SELECT count(*) AS n FROM campaign WHERE deleted_at IS NULL {clause}"
        )
        rows = self._fetch_all(
            f"SELECT {self._COLUMNS} FROM campaign "
            f"WHERE deleted_at IS NULL {clause} "
            "ORDER BY count_date DESC, created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return [self._to_model(r) for r in rows], int(total["n"]) if total else 0

    def get(self, campaign_id: str) -> Campaign:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM campaign WHERE id = %s AND deleted_at IS NULL",
            (campaign_id,),
        )
        if row is None:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)
        campaign = self._to_model(row)
        campaign.thresholds = self.list_thresholds(campaign_id)
        return campaign

    def get_by_code(self, code: str) -> Campaign | None:
        row = self._fetch_one(
            f"SELECT {self._COLUMNS} FROM campaign "
            "WHERE code = %s AND deleted_at IS NULL",
            (code.upper(),),
        )
        if row is None:
            return None
        campaign = self._to_model(row)
        campaign.thresholds = self.list_thresholds(campaign.id)
        return campaign

    def create(self, campaign: Campaign, *, conn: psycopg.Connection | None = None) -> Campaign:
        self._execute(
            "INSERT INTO campaign (id, code, label, count_date, status, config, "
            "cloned_from_code, engine_version, created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                campaign.id, campaign.code, campaign.label, campaign.count_date,
                str(campaign.status), Jsonb(campaign.config.model_dump(mode="json")),
                campaign.cloned_from_code, campaign.engine_version,
                campaign.created_by, campaign.created_at, campaign.created_at,
            ),
            conn=conn,
        )
        self.replace_thresholds(campaign.id, campaign.thresholds, conn=conn)
        return campaign

    def update_status(
        self,
        campaign_id: str,
        status: CampaignStatus,
        *,
        actor: str,
        timestamps: dict[str, dt.datetime] | None = None,
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Move a campaign to *status* and stamp the matching freeze timestamp."""
        sets = ["status = %s", "updated_by = %s", "updated_at = now()",
                "row_version = row_version + 1"]
        params: list[Any] = [str(status), actor]
        for column, value in (timestamps or {}).items():
            if column not in {
                "referentials_frozen_at", "book_stock_frozen_at",
                "counting_frozen_at", "closed_at", "published_at",
            }:
                raise ValueError(f"unexpected freeze column {column!r}")
            sets.append(f"{column} = %s")
            params.append(value)
        params.append(campaign_id)
        n = self._execute(
            f"UPDATE campaign SET {', '.join(sets)} WHERE id = %s AND deleted_at IS NULL",
            params,
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)

    def soft_delete(
        self, campaign_id: str, *, actor: str, conn: psycopg.Connection | None = None
    ) -> None:
        """Retire a campaign without destroying it.

        Everything the campaign carries — counts, journals, audit entries — stays
        on disk, because a campaign that was closed on figures somebody signed
        cannot be made to have never existed. The row simply stops being returned
        by :meth:`list` and :meth:`get`, which frees its code for reuse.
        """
        n = self._execute(
            "UPDATE campaign SET deleted_at = now(), updated_by = %s, "
            "updated_at = now(), row_version = row_version + 1 "
            "WHERE id = %s AND deleted_at IS NULL",
            (actor, campaign_id),
            conn=conn,
        )
        if n == 0:
            raise NotFoundError("Campagne introuvable.", campaignId=campaign_id)

    def set_cloned_from(
        self, campaign_id: str, source_code: str, *, conn: psycopg.Connection | None = None
    ) -> None:
        """Record which campaign this one was duplicated from."""
        self._execute(
            "UPDATE campaign SET cloned_from_code = %s, updated_at = now() "
            "WHERE id = %s",
            (source_code, campaign_id),
            conn=conn,
        )

    # -- thresholds ----------------------------------------------------------

    def list_thresholds(self, campaign_id: str) -> list[Thresholds]:
        rows = self._fetch_all(
            "SELECT item_type, value_abs_eur, qty_relative "
            "FROM threshold WHERE campaign_id = %s ORDER BY item_type",
            (campaign_id,),
        )
        return [
            Thresholds(
                item_type=ItemType(r["item_type"]),
                value_abs_eur=r["value_abs_eur"],
                qty_relative=r["qty_relative"],
            )
            for r in rows
        ]

    def update_config(
        self,
        campaign_id: str,
        config: CampaignConfig,
        *,
        actor: str = "system",
        conn: psycopg.Connection | None = None,
    ) -> None:
        """Réécrit la configuration d'une campagne.

        Le bloc entier, jamais une clé : la colonne est un JSONB, et une
        écriture partielle laisserait la moitié des réglages à la merci de
        l'ordre des requêtes. L'appelant charge, modifie, renvoie.
        """
        self._execute(
            "UPDATE campaign SET config = %s, updated_by = %s, updated_at = now(), "
            "row_version = row_version + 1 WHERE id = %s AND deleted_at IS NULL",
            (Jsonb(config.model_dump(mode="json")), actor, campaign_id),
            conn=conn,
        )

    def replace_thresholds(
        self,
        campaign_id: str,
        thresholds: Sequence[Thresholds],
        *,
        actor: str = "system",
        conn: psycopg.Connection | None = None,
    ) -> None:
        self._execute_many(
            # `qty_abs_floor` and `ira_tolerance` are no longer written: they
            # were two dials nobody turned, and the columns keep their defaults
            # rather than being dropped, so an older campaign stays readable.
            "INSERT INTO threshold (campaign_id, item_type, value_abs_eur, "
            "qty_relative, updated_by, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (campaign_id, item_type) DO UPDATE SET "
            "value_abs_eur = EXCLUDED.value_abs_eur, "
            "qty_relative = EXCLUDED.qty_relative, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            [
                (campaign_id, str(t.item_type), t.value_abs_eur, t.qty_relative, actor)
                for t in thresholds
            ],
            conn=conn,
        )

    # -- mapping -------------------------------------------------------------

    @staticmethod
    def _to_model(row: dict[str, Any]) -> Campaign:
        config = row.get("config") or {}
        if isinstance(config, str):
            config = json.loads(config)
        return Campaign(
            id=str(row["id"]),
            code=row["code"],
            label=row["label"],
            count_date=row["count_date"],
            status=CampaignStatus(row["status"]),
            config=CampaignConfig(**config) if config else CampaignConfig(),
            referentials_frozen_at=row["referentials_frozen_at"],
            book_stock_frozen_at=row["book_stock_frozen_at"],
            counting_frozen_at=row["counting_frozen_at"],
            closed_at=row["closed_at"],
            published_at=row["published_at"],
            general_count_opened_at=row.get("general_count_opened_at"),
            journals_imported_at=row.get("journals_imported_at"),
            cloned_from_code=row["cloned_from_code"],
            engine_version=row["engine_version"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
