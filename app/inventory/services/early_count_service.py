"""Les comptages avancés : un journal ERP, son périmètre, son scellement.

Compter certains emplacements à J-1 ou J-2 pour alléger le jour J.

Ce que ce module ne fait plus, et pourquoi
------------------------------------------
**Rien ne se calcule à partir d'un comptage avancé.** Le dispositif portait sa
propre référence — le stock ERP d'avant comptage, ``ERP@T0`` — contre laquelle
l'écart d'un emplacement scellé était mesuré. Cette référence n'existe plus.

La raison est dans l'ordre des faits : un journal de précomptage est **posté
dans l'ERP** avant que la photo du jour J ne soit prise, et cette photo l'a donc
déjà intégré. La correction de l'inventaire n'est pas perdue, elle est
enregistrée plus tôt — dans l'ERP, avant la campagne. Mesurer une seconde fois
contre un état antérieur revenait à compter deux fois la même correction, et
c'est ce qui rendait le dispositif si difficile à lire : deux axes qui se
ressemblaient, six gestes dont quatre ne changeaient aucun chiffre.

Ce qui reste, et à quoi cela sert
---------------------------------
**Déclarer le périmètre d'un journal scelle ses emplacements.** Le scellement
dit *lesquels sont comptés par ce journal-là*, et il décide encore de deux
choses réelles : quelles lignes comptent pour un emplacement — celles de son
propriétaire, jamais celles d'un journal de passage — et le statut de son
journal de comptage.

**Deux listes, en affichage seul.** La dérive — ``ERP@J − compté@T0`` — et les
étiquettes d'un emplacement scellé retrouvées ailleurs. Elles se regardent, elles
n'appellent aucune décision et ne bloquent rien : ce sont des indices sur ce qui
a bougé entre le précomptage et le jour J, pas des écarts à trancher.

**La référence est unique** : le stock ERP du jour J, gelé, pour tout article et
tout emplacement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Sequence
from typing import Any

from ..db import new_id
from ..domain.enums import (
    AuditAction,
    DataSource,
    JournalStatus,
)
from ..domain.models import (
    Campaign,
    CountJournalLine,
    LocationKey,
)
from ..errors import ConflictError, NotFoundError, ValidationError
from .context import ServiceContext, utcnow

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
        # Le gel du stock ERP ferme la fenêtre du précomptage, et c'est la
        # définition même du précomptage : *avant* la référence générale. Après
        # le gel, l'emplacement a déjà la sienne — celle du jour J —, le journal
        # du jour apporte son comptage par l'import, et il n'y a rien à sceller.
        # Déclarer quand même écrivait une seconde référence sur des clés déjà
        # servies : l'écran remontait une violation d'unicité, c'est-à-dire un
        # 500 sur un geste que l'application proposait elle-même.
        if campaign.book_stock_frozen_at is not None:
            raise ConflictError(
                "Le stock ERP est gelé : il n'y a plus de précomptage à "
                "déclarer. Un journal du jour J n'a rien à sceller — sa "
                "référence est le stock ERP gelé, et son comptage est entré "
                "par l'import. Pour reprendre un emplacement déjà scellé, "
                "descellez son journal.",
                frozenAt=campaign.book_stock_frozen_at.isoformat(),
            )
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
            touched, counted = self._counted_lines(campaign, keys, conn=conn)
            ctx.journals.replace_imported_lines(
                campaign.id, touched, counted, conn=conn
            )
            # **Sceller, c'est déclarer compté.** Le statut du journal de
            # comptage suit, comme il suit à l'import : seuls les journaux
            # `IN_PROGRESS` et `POSTED` entrent dans les quantités comptées.
            # Un emplacement resté `PENDING` apportait donc sa référence au
            # stock ERP et **rien** au stock physique — un manquant fantôme de
            # la totalité de sa quantité, sur un emplacement dont le scellement
            # affirme précisément qu'il est compté.
            if touched:
                ctx.journals.set_status(
                    campaign.id,
                    touched,
                    JournalStatus.POSTED if journal.erp_posted
                    else JournalStatus.IN_PROGRESS,
                    actor=ctx.actor,
                    posted_at=utcnow() if journal.erp_posted else None,
                    conn=conn,
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
                    f"déclarés et scellés, {len(dropped)} journal(aux) de "
                    "passage retiré(s)."
                ),
                after={
                    "locations": [str(k) for k in keys],
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

    def journal_lines(
        self, campaign_id: str, erp_journal_id: str
    ) -> list[dict[str, Any]]:
        """Les lignes brutes d'un journal ERP, telles que l'ERP les a produites.

        Elles existaient en base sans qu'aucun écran ne les montre. C'est
        pourtant ce qu'on veut lire quand un périmètre surprend, quand une
        étiquette est signalée, ou simplement pour vérifier qu'un import a
        rapporté ce qu'on croit : l'application agrège vers l'emplacement, et
        l'agrégat ne dit pas d'où il vient.

        Chaque ligne porte son appartenance au périmètre déclaré. C'est la seule
        chose que l'application ajoute à la ligne, et c'est celle qui décide de
        tout : une ligne hors périmètre est conservée comme trace d'un
        déplacement, mais **elle ne compte pas** — sans le dire, la grille
        laisserait additionner des quantités que le journal ne retient pas.
        """
        journal = next(
            (
                j
                for j in self.ctx.erp_journals.list(campaign_id)
                if j.id == erp_journal_id
            ),
            None,
        )
        if journal is None:
            raise NotFoundError(
                "Journal ERP introuvable dans cette campagne.",
                erpJournalId=erp_journal_id,
            )
        scope = {(k.warehouse_id, k.location_id) for k in journal.scope}
        return [
            {
                **line.model_dump(mode="json"),
                "qtyOnHand": float(line.qty_on_hand),
                "qtyCounted": float(line.qty_counted),
                "varianceQty": float(line.variance_qty),
                "inScope": (line.warehouse_id, line.location_id) in scope,
            }
            for line in self.ctx.erp_journals.lines(campaign_id, erp_journal_id)
        ]

    # -------------------------------------------------------------- étiquettes

    def label_alerts(self, campaign_id: str) -> list[dict[str, Any]]:
        """Les étiquettes d'un emplacement scellé comptées dans un autre journal.

        Le seul indice qui descende au grain de l'étiquette, et celui qui montre
        ce que la dérive ne voit pas : une pièce sortie d'un emplacement scellé
        sans aucune transaction ERP laisse une dérive nulle, mais si elle est
        re-scannée ailleurs — précomptage voisin ou comptage du jour J — son
        étiquette apparaît dans un second journal.

        **En affichage seul.** La liste portait trois issues, dont chacune
        retirait une quantité d'un côté ou de l'autre. Aucune ne subsiste : rien
        ne se calcule à partir d'un comptage avancé, et une pièce comptée deux
        fois se règle sur le terrain, pas en excluant une ligne d'une
        agrégation.
        """
        sealed = [
            LocationKey(warehouse_id=warehouse, location_id=location)
            for warehouse, location in sorted(
                self.ctx.journals.sealed_keys(campaign_id)
            )
        ]
        return [
            {
                "labelId": row["label_id"],
                "itemNumber": row["item_number"],
                "sealedWarehouseId": row["sealed_warehouse_id"],
                "sealedLocationId": row["sealed_location_id"],
                "otherWarehouseId": row["other_warehouse_id"],
                "otherLocationId": row["other_location_id"],
                "otherJournalNumber": row["other_journal_number"],
                "otherQtyCounted": float(row["other_qty_counted"] or 0),
            }
            for row in self.ctx.erp_journals.labels_counted_elsewhere(
                campaign_id, sealed
            )
        ]

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

    # ----------------------------------------------------------------- interne

    def _aggregate(
        self,
        campaign: Campaign,
        keys: Sequence[LocationKey],
        *,
        conn: Any = None,
    ) -> list[tuple[LocationKey, dict[str, Any]]]:
        """Les lignes du périmètre, agrégées par emplacement et article.

        Sur la même connexion que l'écriture qui l'appelle : le périmètre vient
        d'être déclaré dans la transaction en cours, et une autre connexion du
        pool ne le verrait pas — l'agrégation ne ramènerait rien, et le
        scellement poserait un comptage vide sans que rien ne le signale.
        """
        wanted = set(keys)
        rows: list[tuple[LocationKey, dict[str, Any]]] = []
        for row in self.ctx.erp_journals.aggregate_in_scope(
            campaign.id, conn=conn
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

    def _erp_journal(self, campaign: Campaign, erp_journal_id: str):
        for journal in self.ctx.erp_journals.list(campaign.id):
            if journal.id == erp_journal_id:
                return journal
        raise NotFoundError(
            "Journal ERP introuvable dans cette campagne.", journalId=erp_journal_id
        )
