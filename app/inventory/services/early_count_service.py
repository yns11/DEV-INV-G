"""Les comptages avancés : lots, périmètres, scellement.

Compter certains emplacements à J-1 ou J-2 pour alléger le jour J, sans éclater
preuves, écarts et analyses entre plusieurs campagnes.

Ce que ce module tient, et pourquoi
-----------------------------------
**Le journal porte sa propre référence.** La colonne « Stock ERP »
(``OnHandQuantity``) donne le stock d'avant comptage, ligne à ligne. Un lot
avancé n'a donc besoin d'aucun chargement de stock séparé : le fichier qui
apporte le comptage apporte aussi ce contre quoi il se compare. Cela supprime
d'un coup une table de référence, un séquencement fragile — charger avant de
compter — et le risque de l'oublier.

**La référence d'un emplacement scellé est celle de son précomptage.**
:attr:`~inventory.domain.models.VarianceLine.variance_qty` documente déjà la
règle : le snapshot gelé est *ce contre quoi la campagne a été comptée*. Elle
vaut pour le jour J sur un emplacement ordinaire, et pour T0 sur un emplacement
précompté. Même règle, deux dates. Sans elle, poster le journal ayant réaligné
l'ERP sur le physique, l'écart d'un emplacement précompté serait **nul** dans le
cas nominal et le résultat de son inventaire disparaîtrait.

**On ne scelle qu'un journal posté dans l'ERP.** Le réalignement est alors
acquis par construction, au lieu d'être diagnostiqué après coup depuis la forme
d'une dérive. C'est une précondition qui remplace une branche entière du
traitement.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction
from ..domain.models import BookStockLine, Campaign, EarlyCountBatch, LocationKey
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ServiceContext, utcnow

log = logging.getLogger(__name__)

__all__ = ["EarlyCountService"]


class EarlyCountService:
    """Créer, alimenter, clore et sceller un lot de comptage avancé."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # -------------------------------------------------------------- périmètres

    def propose_scope(
        self, campaign: Campaign, erp_journal_id: str
    ) -> list[dict[str, Any]]:
        """Les emplacements que ce journal ERP *pourrait* couvrir.

        Ceux de ses lignes, moins le tampon, moins ceux déjà alloués à un autre
        journal, classés par nombre de lignes décroissant.

        L'application propose, l'utilisateur tranche — et ce partage n'est pas
        de la prudence de façade : les emplacements des lignes ne suffisent pas
        à dire le périmètre, puisque certaines ne sont là que pour matérialiser
        un déplacement. Deviner produirait des références sur des emplacements
        que le journal ne couvre pas.
        """
        return self.ctx.erp_journals.candidate_locations(
            campaign.id, erp_journal_id, buffer_key=campaign.config.buffer_key
        )

    def declare_scope(
        self, campaign: Campaign, erp_journal_id: str, keys: Sequence[LocationKey]
    ) -> int:
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        journal = self._erp_journal(campaign, erp_journal_id)
        buffer_key = campaign.config.buffer_key
        if any(key == buffer_key for key in keys):
            raise ValidationError(
                f"L'emplacement tampon {buffer_key} ne se compte pas : il est "
                "virtuel, et l'ERP n'y crée aucun journal. Ses lignes sont "
                "conservées pour la traçabilité.",
                location=str(buffer_key),
            )
        with ctx.db.transaction() as conn:
            count = ctx.erp_journals.set_scope(
                campaign.id, erp_journal_id, keys, actor=ctx.actor, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="erp_journal",
                entity_id=erp_journal_id,
                summary=(
                    f"Périmètre du journal {journal.journal_number} : "
                    f"{count} emplacement(s)."
                ),
                after={"locations": [str(k) for k in keys]},
                conn=conn,
            )
        return count

    # ------------------------------------------------------------------- lots

    def list_batches(self, campaign_id: str) -> list[EarlyCountBatch]:
        return self.ctx.early_counts.list(campaign_id)

    def create_batch(
        self,
        campaign: Campaign,
        *,
        code: str,
        label: str = "",
        counted_on: dt.date | None = None,
        erp_journal_ids: Sequence[str],
    ) -> EarlyCountBatch:
        """Ouvrir un lot sur le périmètre déclaré d'un ou plusieurs journaux ERP.

        Le périmètre doit être déclaré : sans lui, la référence du lot serait
        assise sur des emplacements que le journal ne couvre pas.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        if not erp_journal_ids:
            raise ValidationError(
                "Un lot avancé porte sur au moins un journal ERP."
            )

        journals = [self._erp_journal(campaign, jid) for jid in erp_journal_ids]
        undeclared = [j.journal_number for j in journals if not j.scope_declared]
        if undeclared:
            raise ConflictError(
                "Le périmètre de ces journaux n'est pas déclaré : "
                f"{', '.join(undeclared)}. Sélectionnez leurs emplacements "
                "avant d'ouvrir le lot.",
                journals=undeclared,
            )

        keys = sorted(
            {key for journal in journals for key in journal.scope},
            key=lambda k: (k.warehouse_id, k.location_id),
        )
        if not keys:
            raise ValidationError(
                "Le périmètre déclaré de ces journaux est vide."
            )

        batch = EarlyCountBatch(
            id=new_id(),
            campaign_id=campaign.id,
            code=code,
            label=label,
            counted_on=counted_on,
            opened_at=utcnow(),
            opened_by=ctx.actor,
            locations=keys,
        )
        with ctx.db.transaction() as conn:
            ctx.early_counts.create(batch, conn=conn)
            # Le périmètre du lot *est* l'ensemble des journaux qui portent son
            # identifiant : il se pose ici, à l'ouverture, pas au scellement.
            ctx.journals.assign_batch(
                campaign.id,
                [(k.warehouse_id, k.location_id) for k in keys],
                batch_id=batch.id,
                actor=ctx.actor,
                conn=conn,
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.CREATE,
                entity_type="early_count_batch",
                entity_id=batch.id,
                summary=(
                    f"Lot avancé {batch.code} ouvert sur {len(keys)} emplacement(s), "
                    f"journaux {', '.join(j.journal_number for j in journals)}."
                ),
                after={
                    "locations": [str(k) for k in keys],
                    "journals": [j.journal_number for j in journals],
                },
                conn=conn,
            )
        return batch

    def close_batch(self, campaign: Campaign, batch_id: str) -> EarlyCountBatch:
        """Clore le lot : on cesse d'y ajouter, on peut encore corriger."""
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        batch = self._batch(campaign, batch_id)
        with ctx.db.transaction() as conn:
            ctx.early_counts.close(campaign.id, batch_id, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_batch",
                entity_id=batch_id,
                summary=f"Lot avancé {batch.code} clos.",
                conn=conn,
            )
        return self._batch(campaign, batch_id)

    def seal_batch(self, campaign: Campaign, batch_id: str) -> EarlyCountBatch:
        """Sceller le lot, et poser la référence de ses emplacements.

        Refusé si l'un des journaux ERP du périmètre n'est pas posté dans l'ERP.
        Poster réaligne l'ERP sur le physique compté ; n'accepter de sceller
        qu'un journal posté rend ce réalignement acquis, et supprime toute une
        branche du traitement des dérives.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        batch = self._batch(campaign, batch_id)
        if not batch.is_closed:
            raise ConflictError(
                f"Le lot {batch.code} n'est pas clos : clôturez-le avant de le "
                "sceller.",
                batchId=batch_id,
            )

        reference = self._reference_lines(campaign, batch)
        unposted = self._unposted_journals(campaign, batch)
        if unposted:
            raise ConflictError(
                "Ces journaux ne sont pas postés dans l'ERP : "
                f"{', '.join(unposted)}. Un comptage avancé ne se scelle qu'une "
                "fois posté — c'est le postage qui réaligne l'ERP sur le "
                "physique compté, et c'est ce réalignement que le scellement "
                "tient pour acquis.",
                journals=unposted,
            )

        with ctx.db.transaction() as conn:
            ctx.book_stock.replace_for_batch(
                campaign.id, batch_id, reference, conn=conn
            )
            ctx.journals.seal(
                campaign.id,
                [(k.warehouse_id, k.location_id) for k in batch.locations],
                actor=ctx.actor,
                conn=conn,
            )
            ctx.early_counts.seal(campaign.id, batch_id, actor=ctx.actor, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.FREEZE,
                entity_type="early_count_batch",
                entity_id=batch_id,
                summary=(
                    f"Lot avancé {batch.code} scellé : {len(batch.locations)} "
                    f"emplacement(s), {len(reference)} ligne(s) de référence."
                ),
                after={
                    "locations": [str(k) for k in batch.locations],
                    "referenceLines": len(reference),
                    "referenceDate": (
                        batch.counted_on.isoformat() if batch.counted_on else None
                    ),
                },
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return self._batch(campaign, batch_id)

    def unseal_batch(
        self, campaign: Campaign, batch_id: str, *, reason: str
    ) -> EarlyCountBatch:
        """Desceller — le geste qui rend un recomptage possible.

        Motif obligatoire : le descellement annule une preuve, et un geste qui
        annule une preuve sans dire pourquoi est une porte dérobée.
        """
        ctx = self.ctx
        ctx.guard(campaign, "count_journals")
        if not reason.strip():
            raise ValidationError(
                "Le descellement demande un motif : il annule une preuve datée."
            )
        batch = self._batch(campaign, batch_id)
        with ctx.db.transaction() as conn:
            ctx.book_stock.replace_for_batch(campaign.id, batch_id, [], conn=conn)
            ctx.journals.unseal(
                campaign.id,
                [(k.warehouse_id, k.location_id) for k in batch.locations],
                actor=ctx.actor,
                conn=conn,
            )
            ctx.early_counts.unseal(campaign.id, batch_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_batch",
                entity_id=batch_id,
                summary=f"Lot avancé {batch.code} descellé : {reason.strip()}",
                after={"reason": reason.strip()},
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return self._batch(campaign, batch_id)

    # ------------------------------------------------------------------ internes

    def _reference_lines(
        self, campaign: Campaign, batch: EarlyCountBatch
    ) -> list[BookStockLine]:
        """`ERP@T0` pour les emplacements du lot, lu dans les journaux eux-mêmes."""
        wanted = set(batch.locations)
        # Le prix standard du référentiel, comme le fait déjà `map_book_stock`
        # quand l'export n'en porte pas : c'est la même campagne, donc le même
        # prix, et la valorisation d'un lot avancé doit être celle de
        # l'inventaire.
        prices = {
            number: item.std_price
            for number, item in self.ctx.referentials.items_by_number(
                campaign.id
            ).items()
        }
        lines: list[BookStockLine] = []
        for row in self.ctx.erp_journals.aggregate_in_scope(campaign.id):
            key = LocationKey(
                warehouse_id=row["warehouse_id"], location_id=row["location_id"]
            )
            if key not in wanted:
                continue
            lines.append(
                BookStockLine(
                    campaign_id=campaign.id,
                    item_number=row["item_number"],
                    warehouse_id=key.warehouse_id,
                    location_id=key.location_id,
                    qty=row["qty_on_hand"],
                    unit=row["unit"] or "PCE",
                    unit_cost=prices.get(row["item_number"], 0),
                    reference_date=batch.counted_on,
                    early_batch_id=batch.id,
                )
            )
        return lines

    def _unposted_journals(
        self, campaign: Campaign, batch: EarlyCountBatch
    ) -> list[str]:
        wanted = set(batch.locations)
        return sorted(
            journal.journal_number
            for journal in self.ctx.erp_journals.list(campaign.id)
            if not journal.erp_posted and wanted & set(journal.scope)
        )

    def _erp_journal(self, campaign: Campaign, erp_journal_id: str):
        for journal in self.ctx.erp_journals.list(campaign.id):
            if journal.id == erp_journal_id:
                return journal
        raise NotFoundError(
            "Journal ERP introuvable dans cette campagne.", journalId=erp_journal_id
        )

    def _batch(self, campaign: Campaign, batch_id: str) -> EarlyCountBatch:
        for batch in self.ctx.early_counts.list(campaign.id):
            if batch.id == batch_id:
                return batch
        raise NotFoundError("Lot avancé introuvable.", batchId=batch_id)
