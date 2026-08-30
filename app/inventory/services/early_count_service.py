"""Les comptages avancés : un journal ERP, son périmètre, son scellement.

Compter certains emplacements à J-1 ou J-2 pour alléger le jour J, sans éclater
preuves, écarts et analyses entre plusieurs campagnes.

Ce que ce module tient, et pourquoi
-----------------------------------
**Le journal ERP *est* le précomptage.** Une version antérieure interposait un
« lot » entre le journal et le scellement. Le métier a tranché : un précomptage
couvre exactement un journal, qui couvre un ou plusieurs emplacements. Le lot
n'apportait qu'un regroupement dont personne n'avait besoin, plus deux champs —
la date du comptage et le scellement — qui appartiennent au journal.

**Déclarer le périmètre vaut scellement.** Dire quels emplacements ce journal
couvre, c'est dire lesquels sont comptés et ne bougeront plus : il n'y avait
aucune décision entre les deux, seulement des clics. Le geste unique pose la
référence dans la foulée.

**Le journal porte sa propre référence.** La colonne « Stock ERP »
(``OnHandQuantity``) donne le stock d'avant comptage, ligne à ligne. Aucun
chargement séparé : le fichier qui apporte le comptage apporte aussi ce contre
quoi il se compare. Et sa colonne « Date de comptage » donne la date du relevé,
qui date la référence — elle n'est plus retapée.

**La référence d'un emplacement scellé est celle de son précomptage.**
:attr:`~inventory.domain.models.VarianceLine.variance_qty` documente déjà la
règle : le snapshot gelé est *ce contre quoi la campagne a été comptée*. Elle
vaut pour le jour J sur un emplacement ordinaire, et pour T0 sur un emplacement
précompté. Même règle, deux dates. Sans elle, poster le journal ayant réaligné
l'ERP sur le physique, l'écart d'un emplacement précompté serait **nul** dans le
cas nominal et le résultat de son inventaire disparaîtrait.

**Un réimport remplace et met à jour.** Recharger le journal, ou en charger un
autre qui touche un emplacement déjà scellé, recalcule la référence et rescelle.
C'est la règle métier, et elle est la bonne : la dernière lecture de l'ERP est la
plus juste, et une preuve qu'on ne peut plus corriger n'est pas une preuve, c'est
une impasse.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from typing import Any

from ..db import new_id
from ..domain.enums import AuditAction, DataSource, LabelResolution
from ..domain.models import (
    BookStockLine,
    Campaign,
    CountJournalLine,
    LabelDecision,
    LocationKey,
)
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["EarlyCountService"]


class EarlyCountService:
    """Déclarer le périmètre d'un journal, le sceller, traiter ses étiquettes."""

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
        """Déclarer les emplacements du journal, les sceller, poser la référence.

        Un seul geste, et c'est le point de la révision. Le postage du journal
        n'est pas exigé : un journal de précomptage se charge une fois posté et
        validé dans l'ERP — il y en a peu, et ils n'ont pas l'urgence du jour J.
        Une garde qui ne se déclenche jamais est une garde qu'on ne sait pas
        maintenir.
        """
        ctx = self.ctx
        ctx.guard(campaign, "early_counts")
        journal = self._erp_journal(campaign, erp_journal_id)
        buffer_key = campaign.config.buffer_key
        if any(key == buffer_key for key in keys):
            raise ValidationError(
                f"L'emplacement tampon {buffer_key} ne se compte pas : il est "
                "virtuel, et l'ERP n'y crée aucun journal. Ses lignes sont "
                "conservées pour la traçabilité.",
                location=str(buffer_key),
            )
        if not keys:
            raise ValidationError(
                "Un périmètre vide ne scelle rien. Pour retirer le périmètre "
                "d'un journal, descellez-le."
            )
        # Un emplacement n'appartient qu'à un journal, et la base le tient déjà :
        # `erp_journal_scope_location_uq`. Mais un index unique ne sait pas dire
        # *qui* possède déjà l'emplacement — il remonte une UniqueViolation
        # brute, donc un 500 devant lequel il n'y a rien à faire. La liste
        # proposée exclut déjà ces emplacements ; ce refus est là pour l'appel
        # qui ne passe pas par elle, et il nomme le journal à desceller.
        owners = self.scope_owners(campaign.id)
        requested = set(keys)
        taken = sorted(
            (
                (key, owner)
                for key, owner in owners.items()
                if key in requested and owner != journal.journal_number
            ),
            key=lambda pair: str(pair[0]),
        )
        if taken:
            names = ", ".join(f"{key} (journal {owner})" for key, owner in taken)
            raise ConflictError(
                f"{len(taken)} emplacement(s) appartiennent déjà au périmètre "
                f"d'un autre journal : {names}. Descellez ce journal-là pour "
                "les lui reprendre.",
                locations=[str(key) for key, _ in taken],
            )

        with ctx.db.transaction() as conn:
            count = ctx.erp_journals.set_scope(
                campaign.id, erp_journal_id, keys, actor=ctx.actor, conn=conn
            )
            ctx.journals.ensure_journals(campaign.id, list(keys), conn=conn)
            reference = self._reference_lines(campaign, journal, keys, conn=conn)
            ctx.book_stock.replace_for_journal(
                campaign.id, erp_journal_id, reference, conn=conn
            )
            touched, counted = self._counted_lines(campaign, keys, conn=conn)
            ctx.journals.replace_imported_lines(
                campaign.id, touched, counted, conn=conn
            )
            ctx.journals.seal(
                campaign.id,
                [(k.warehouse_id, k.location_id) for k in keys],
                actor=ctx.actor,
                conn=conn,
            )
            # Les lignes de passage de ce journal avaient créé, à l'import, un
            # journal de comptage par emplacement touché — y compris ceux que
            # l'utilisateur vient précisément de ne pas sélectionner. Déclarer
            # le périmètre est le moment où l'on sait lesquels : ils s'en vont.
            dropped = ctx.journals.delete_pass_through_journals(
                campaign.id, journal.journal_number, keys, conn=conn
            )
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.FREEZE,
                entity_type="erp_journal",
                entity_id=erp_journal_id,
                summary=(
                    f"Journal {journal.journal_number} : {count} emplacement(s) "
                    f"déclarés et scellés, {len(reference)} ligne(s) de "
                    f"référence, {len(dropped)} journal(aux) de passage retiré(s)."
                ),
                after={
                    "locations": [str(k) for k in keys],
                    "referenceLines": len(reference),
                    "countedOn": (
                        journal.counted_on.isoformat() if journal.counted_on else None
                    ),
                    "passThroughJournalsRemoved": dropped,
                },
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return count

    def unseal(self, campaign: Campaign, erp_journal_id: str, *, reason: str) -> int:
        """Desceller le journal : ses emplacements rejoignent le comptage général.

        Motif obligatoire : le descellement annule une preuve datée, et un geste
        qui annule une preuve sans dire pourquoi est une porte dérobée.

        Le périmètre part avec le scellement. Sans périmètre, le journal n'a
        plus d'emplacement à couvrir, donc plus rien à sceller ; redéclarer est
        le geste qui rescelle.
        """
        ctx = self.ctx
        ctx.guard(campaign, "early_counts")
        if not reason.strip():
            raise ValidationError(
                "Le descellement demande un motif : il annule une preuve datée."
            )
        journal = self._erp_journal(campaign, erp_journal_id)
        keys = list(journal.scope)
        with ctx.db.transaction() as conn:
            ctx.book_stock.replace_for_journal(
                campaign.id, erp_journal_id, [], conn=conn
            )
            ctx.journals.unseal(
                campaign.id,
                [(k.warehouse_id, k.location_id) for k in keys],
                actor=ctx.actor,
                conn=conn,
            )
            ctx.erp_journals.unseal(campaign.id, erp_journal_id, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="erp_journal",
                entity_id=erp_journal_id,
                summary=(
                    f"Journal {journal.journal_number} descellé : "
                    f"{len(keys)} emplacement(s) rendus au comptage général. "
                    f"Motif : {reason.strip()}"
                ),
                before={"locations": [str(k) for k in keys]},
                after={"reason": reason.strip()},
                conn=conn,
            )
        ctx.forget_progress(campaign.id)
        return len(keys)

    def reseal_after_import(self, campaign: Campaign) -> int:
        """Recalculer la référence des journaux déjà scellés, après un import.

        Appelée par l'import des lignes de journaux. Un réimport apporte la
        lecture la plus fraîche de l'ERP ; laisser la référence sur celle de la
        veille reviendrait à mesurer contre un chiffre que l'ERP ne tient plus.

        Silencieuse sur un journal sans périmètre : il n'a rien à rescellez, et
        ce n'est pas une anomalie mais l'état normal d'un journal qui vient
        d'arriver.
        """
        ctx = self.ctx
        # Gardée pour elle-même, bien que son appelant garde déjà : une
        # écriture qui compte sur la garde de qui l'appelle devient non gardée
        # le jour où un routeur l'appelle directement, et rien ne le dirait.
        ctx.guard(campaign, "early_counts")
        resealed = 0
        with ctx.db.transaction() as conn:
            for journal in ctx.erp_journals.list(campaign.id, conn=conn):
                if not journal.scope_declared or not journal.scope:
                    continue
                reference = self._reference_lines(
                    campaign, journal, journal.scope, conn=conn
                )
                ctx.book_stock.replace_for_journal(
                    campaign.id, journal.id, reference, conn=conn
                )
                touched, counted = self._counted_lines(
                    campaign, journal.scope, conn=conn
                )
                ctx.journals.replace_imported_lines(
                    campaign.id, touched, counted, conn=conn
                )
                ctx.journals.seal(
                    campaign.id,
                    [(k.warehouse_id, k.location_id) for k in journal.scope],
                    actor=ctx.actor,
                    conn=conn,
                )
                resealed += 1
        return resealed

    def scope_owners(self, campaign_id: str) -> dict[LocationKey, str]:
        """Quel journal possède chaque emplacement scellé, par numéro de journal.

        Un emplacement n'appartient au périmètre que d'un seul journal — index
        unique de la migration 025. C'est cette propriété qui décide **qui le
        compte** : le journal qui le possède, et lui seul.

        Les lignes des autres journaux sur cet emplacement existent : un journal
        ERP porte des lignes sur des emplacements qu'il ne couvre pas, pour
        matérialiser un déplacement. Elles restent dans ``erp_journal_line`` —
        c'est la trace, et c'est ce que le contrôle par étiquette relit — mais
        elles ne comptent pas. Sans cette règle, la quantité comptée d'un
        emplacement scellé prenait celle du dernier journal passé dessus tandis
        que sa référence restait celle de son propriétaire : deux journaux dans
        un même écart, et rien pour le dire.
        """
        return {
            key: journal.journal_number
            for journal in self.ctx.erp_journals.list(campaign_id)
            if journal.scope_declared
            for key in journal.scope
        }

    def declared_journal_numbers(self, campaign_id: str) -> set[str]:
        """Les journaux dont on sait ce qu'ils couvrent.

        Pour les autres — un journal qui vient d'arriver, un journal du comptage
        général qui n'a pas de périmètre — on ne sait rien, et présumer serait
        pire que de laisser entrer.
        """
        return {
            journal.journal_number
            for journal in self.ctx.erp_journals.list(campaign_id)
            if journal.scope_declared and journal.scope
        }

    def counting_filter(
        self, campaign_id: str, *, disabled: Collection[LocationKey] = ()
    ) -> Callable[[Any], bool]:
        """Le tri de l'import : cette ligne compte-t-elle son emplacement ?

        Le tri se fait **ligne par ligne**, pas emplacement par emplacement. Un
        même fichier apporte les lignes du propriétaire de l'emplacement et
        celles des journaux qui n'ont fait qu'y passer ; écarter la clé entière
        écarterait aussi le comptage de son propriétaire, et l'emplacement
        scellé se retrouverait sans quantité comptée.

        Trois cas, et le troisième est le seul permissif : emplacement
        désactivé, non ; emplacement déclaré, seul son journal ; emplacement
        libre, tout journal dont on ne connaît pas encore le périmètre.
        """
        excluded = set(disabled)
        owners = self.scope_owners(campaign_id)
        declared = self.declared_journal_numbers(campaign_id)

        def counts(line: Any) -> bool:
            key = LocationKey(
                warehouse_id=line.warehouse_id, location_id=line.location_id
            )
            if key in excluded:
                return False
            owner = owners.get(key)
            if owner is not None:
                return owner == line.journal_number
            return line.journal_number not in declared

        return counts

    # ---------------------------------------------------------------- lectures

    def list_journals(self, campaign_id: str) -> list[dict[str, Any]]:
        """Les journaux ERP importés, avec leur périmètre et leur scellement.

        ``scopeDeclared`` à faux est la seule chose à traiter : tant qu'il l'est,
        les lignes du journal ne produisent aucune référence et ses emplacements
        restent au comptage général.
        """
        return [
            {
                **journal.model_dump(mode="json", exclude={"scope"}),
                "scope": [
                    {"warehouseId": k.warehouse_id, "locationId": k.location_id}
                    for k in journal.scope
                ],
                "scopeDeclared": journal.scope_declared,
                "isSealed": journal.is_sealed,
                "warehouses": sorted(journal.warehouses),
            }
            for journal in self.ctx.erp_journals.list(campaign_id)
        ]

    # -------------------------------------------------------------- étiquettes

    def label_alerts(self, campaign_id: str) -> list[dict[str, Any]]:
        """Les étiquettes d'un emplacement scellé comptées dans un autre journal.

        Le seul contrôle qui descende au grain de l'étiquette, et celui qui
        rattrape ce que la dérive ne voit pas : une pièce sortie d'un
        emplacement scellé sans aucune transaction ERP laisse une dérive nulle,
        mais si elle est re-scannée ailleurs — précomptage voisin ou comptage du
        jour J — son étiquette apparaît dans un second journal.

        Chaque alerte porte l'issue qu'on lui a donnée, ou aucune.
        """
        sealed = [
            LocationKey(warehouse_id=warehouse, location_id=location)
            for warehouse, location in sorted(
                self.ctx.journals.sealed_keys(campaign_id)
            )
        ]
        decided = {
            (d.label_id, d.item_number): d
            for d in self.ctx.label_decisions.list(campaign_id)
        }
        alerts = []
        for row in self.ctx.erp_journals.labels_counted_elsewhere(
            campaign_id, sealed
        ):
            decision = decided.get((row["label_id"], row["item_number"]))
            alerts.append({
                "labelId": row["label_id"],
                "itemNumber": row["item_number"],
                "sealedWarehouseId": row["sealed_warehouse_id"],
                "sealedLocationId": row["sealed_location_id"],
                "otherWarehouseId": row["other_warehouse_id"],
                "otherLocationId": row["other_location_id"],
                "otherJournalNumber": row["other_journal_number"],
                "otherQtyCounted": float(row["other_qty_counted"] or 0),
                "decision": str(decision.decision) if decision else None,
                "comment": decision.comment if decision else "",
                "decidedBy": decision.decided_by if decision else "",
                "decidedAt": (
                    decision.decided_at.isoformat()
                    if decision and decision.decided_at
                    else None
                ),
            })
        return alerts

    def labels_recounted_in_place(self, campaign_id: str) -> list[dict[str, Any]]:
        """Les emplacements scellés qu'un second journal a recomptés sur place.

        Le pendant de :meth:`label_alerts`, et ce qui explique pourquoi cette
        liste-là s'est vidée. Deux journaux passés sur le même emplacement n'y
        ont jamais eu leur place — la pièce n'a pas bougé — mais ils y étaient,
        et les en retirer sans le dire cacherait un fait réel : deux comptages
        du même emplacement, dont un seul est retenu.
        """
        sealed = [
            LocationKey(warehouse_id=warehouse, location_id=location)
            for warehouse, location in sorted(
                self.ctx.journals.sealed_keys(campaign_id)
            )
        ]
        return [
            {
                "sealedWarehouseId": row["sealed_warehouse_id"],
                "sealedLocationId": row["sealed_location_id"],
                "ownerJournalNumber": row["owner_journal_number"],
                "otherJournalNumber": row["other_journal_number"],
                "labelCount": int(row["label_count"]),
            }
            for row in self.ctx.erp_journals.labels_recounted_in_place(
                campaign_id, sealed
            )
        ]

    def decide_label(
        self,
        campaign: Campaign,
        *,
        label_id: str,
        item_number: str,
        decision: LabelResolution,
        sealed: LocationKey,
        other: LocationKey,
        comment: str = "",
    ) -> LabelDecision:
        """Dire où est la pièce, et en tirer les conséquences sur les quantités.

        Trois issues, et chacune agit :

        * ``KEEP_NEW`` — elle est au nouvel emplacement. L'étiquette sort de
          l'agrégation de l'emplacement scellé, qui perd la quantité.
        * ``KEEP_SEALED`` — elle n'a pas bougé. C'est la ligne de l'autre
          journal qui est l'erreur, et c'est elle qui sort.
        * ``RECOUNT`` — on ne tranche pas sur pièce. Rien n'est exclu, et
          l'emplacement scellé rejoint la liste de ceux à desceller et rescanner.

        L'effet passe par l'agrégation, qui est rejouée ici : une décision qui
        ne changerait les chiffres qu'au prochain import serait une décision
        qu'on croit prise et qui ne l'est pas.
        """
        ctx = self.ctx
        ctx.guard(campaign, "early_counts")
        if not label_id.strip():
            raise ValidationError("Une décision porte sur une étiquette nommée.")
        record = LabelDecision(
            id=new_id(),
            campaign_id=campaign.id,
            label_id=label_id,
            item_number=item_number,
            decision=decision,
            sealed_warehouse_id=sealed.warehouse_id,
            sealed_location_id=sealed.location_id,
            other_warehouse_id=other.warehouse_id,
            other_location_id=other.location_id,
            comment=comment,
            decided_by=ctx.actor,
        )
        with ctx.db.transaction() as conn:
            ctx.label_decisions.decide(record, conn=conn)
            ctx.record(
                campaign_id=campaign.id,
                action=AuditAction.UPDATE,
                entity_type="early_count_label",
                entity_id=record.id,
                summary=(
                    f"Étiquette {label_id} ({item_number}) : "
                    f"{_DECISION_LABELS[decision]}."
                ),
                after={
                    "decision": str(decision),
                    "sealed": str(sealed),
                    "other": str(other),
                    "comment": comment,
                },
                conn=conn,
            )
        # La décision retire l'étiquette d'un côté ou de l'autre : la référence
        # des emplacements scellés se recalcule dans la foulée.
        self.reseal_after_import(campaign)
        return record

    def locations_to_rescan(self, campaign_id: str) -> list[dict[str, Any]]:
        """Les emplacements scellés dont une étiquette est en question.

        Ceux que ``RECOUNT`` désigne : on n'a pas voulu trancher sur pièce, et
        la façon d'en sortir est d'aller recompter. La liste expose l'ancien
        emplacement — celui qui est scellé — parce que c'est lui qu'il faut
        desceller pour que le comptage du jour J le reprenne.
        """
        journals = {
            (k.warehouse_id, k.location_id): journal
            for journal in self.ctx.erp_journals.list(campaign_id)
            for k in journal.scope
        }
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for decision in self.ctx.label_decisions.list(campaign_id):
            if decision.decision is not LabelResolution.RECOUNT:
                continue
            key = (decision.sealed_warehouse_id, decision.sealed_location_id)
            journal = journals.get(key)
            entry = grouped.setdefault(key, {
                "warehouseId": key[0],
                "locationId": key[1],
                "journalNumber": journal.journal_number if journal else "",
                "erpJournalId": journal.id if journal else None,
                "isSealed": bool(journal and journal.is_sealed),
                "labels": [],
            })
            entry["labels"].append({
                "labelId": decision.label_id,
                "itemNumber": decision.item_number,
                "otherWarehouseId": decision.other_warehouse_id,
                "otherLocationId": decision.other_location_id,
                "comment": decision.comment,
                "decidedBy": decision.decided_by,
            })
        return sorted(
            grouped.values(), key=lambda e: (e["warehouseId"], e["locationId"])
        )

    # ----------------------------------------------------------------- interne

    def _aggregate(
        self,
        campaign: Campaign,
        keys: Sequence[LocationKey],
        *,
        conn: Any = None,
    ) -> list[tuple[LocationKey, dict[str, Any]]]:
        """Les lignes du périmètre, agrégées par emplacement et article.

        Une seule lecture pour la référence *et* le comptage, et c'est la raison
        d'être de cette fonction : les deux nombres d'un même écart doivent
        venir de la même requête, sur la même connexion, avec les mêmes
        étiquettes exclues. Les avoir calculés séparément est exactement ce qui
        produisait une référence tirée d'un journal et un comptage tiré d'un
        autre.
        """
        wanted = set(keys)
        excluded = {
            (d.label_id, d.item_number)
            for d in self.ctx.label_decisions.list(campaign.id, conn=conn)
            if d.excluded_from_sealed
        }
        rows: list[tuple[LocationKey, dict[str, Any]]] = []
        for row in self.ctx.erp_journals.aggregate_in_scope(
            campaign.id, excluded_labels=excluded, conn=conn
        ):
            key = LocationKey(
                warehouse_id=row["warehouse_id"], location_id=row["location_id"]
            )
            if key in wanted:
                rows.append((key, row))
        return rows

    def _counted_lines(
        self,
        campaign: Campaign,
        keys: Sequence[LocationKey],
        *,
        conn: Any = None,
    ) -> tuple[list[str], list[CountJournalLine]]:
        """Le comptage des emplacements du périmètre, relu depuis leur journal.

        Déclarer ne posait que la référence, et le comptage restait celui que
        l'import avait écrit — c'est-à-dire, quand plusieurs journaux touchaient
        l'emplacement avant qu'aucun ne soit déclaré, la somme de leurs lignes.
        Le scellement affichait alors un écart entre le stock d'un journal et le
        comptage de plusieurs.

        Le recalculer ici rend l'ordre des gestes indifférent : importer puis
        déclarer, ou déclarer puis réimporter, donnent le même comptage.
        """
        journals = {j.key: j for j in self.ctx.journals.list(campaign.id, conn=conn)}
        touched = [journals[key].id for key in set(keys) if key in journals]
        lines = [
            CountJournalLine(
                id=new_id(),
                journal_id=journals[key].id,
                campaign_id=campaign.id,
                item_number=row["item_number"],
                qty_imported=row["qty_counted"],
                unit=row["unit"] or "PCE",
                source=DataSource.ERP_IMPORT,
                updated_by=self.ctx.actor,
                qty_on_hand=row["qty_on_hand"],
                erp_journal_number=row["journal_number"],
                label_count=row["label_count"],
            )
            for key, row in self._aggregate(campaign, keys, conn=conn)
            if key in journals
        ]
        return touched, lines

    def _reference_lines(
        self,
        campaign: Campaign,
        journal: Any,
        keys: Sequence[LocationKey],
        *,
        conn: Any = None,
    ) -> list[BookStockLine]:
        """`ERP@T0` pour les emplacements du journal, lu dans le journal lui-même.

        Les étiquettes qu'une décision a fait sortir de l'emplacement scellé ne
        comptent pas : c'est tout l'effet de ``KEEP_NEW``.

        Lue sur **la même connexion** que l'écriture qui l'appelle. Le périmètre
        vient d'être déclaré dans la transaction en cours : une autre connexion
        du pool ne le verrait pas, l'agrégation ne ramènerait rien, et le
        scellement poserait une référence vide sans que rien ne le signale.
        """
        prices = {
            number: item.std_price
            for number, item in self.ctx.referentials.items_by_number(
                campaign.id
            ).items()
        }
        lines: list[BookStockLine] = []
        for key, row in self._aggregate(campaign, keys, conn=conn):
            lines.append(
                BookStockLine(
                    campaign_id=campaign.id,
                    item_number=row["item_number"],
                    warehouse_id=key.warehouse_id,
                    location_id=key.location_id,
                    qty=row["qty_on_hand"],
                    unit=row["unit"] or "PCE",
                    unit_cost=prices.get(row["item_number"], 0),
                    reference_date=journal.counted_on,
                    erp_journal_id=journal.id,
                )
            )
        return lines

    def _erp_journal(self, campaign: Campaign, erp_journal_id: str):
        for journal in self.ctx.erp_journals.list(campaign.id):
            if journal.id == erp_journal_id:
                return journal
        raise NotFoundError(
            "Journal ERP introuvable dans cette campagne.", journalId=erp_journal_id
        )


_DECISION_LABELS = {
    LabelResolution.KEEP_NEW: "placée au nouvel emplacement",
    LabelResolution.KEEP_SEALED: "retirée du nouvel emplacement",
    LabelResolution.RECOUNT: "signalée, emplacement à desceller et rescanner",
}
