"""Les référentiels d'une campagne : articles, nomenclatures, stock ERP.

Ces onze opérations vivaient dans le routeur. Chacune y faisait la même chose
qu'un service — garder la phase, chercher, comparer avec l'existant, écrire,
enregistrer l'audit — mais dans une fonction dont la signature parlait de
requête HTTP et dont le corps ne pouvait s'exécuter qu'à travers une
application FastAPI.

Trois conséquences, et aucune n'est théorique.

**Un contrôle passait par le transport.** Vérifier qu'une exclusion sur un lot
d'articles refuse une référence inconnue demandait de construire un contrat
Pydantic pour poser deux références et une exclusion, pour une règle qui tient
en trois lignes. Les contrôles écrits ainsi vérifient le statut, rarement la
règle.

**La règle n'était appelable que par un navigateur.** L'assistant, un job, une
reprise en lot : tout ce qui n'entre pas par HTTP devait réimplémenter la
comparaison avec l'existant, ou passer outre — et écrire sans audit.

**Rien ne disait où était la limite.** Deux routeurs voisins, l'un déléguant,
l'autre non ; le prochain ajout suivait le voisin qu'on avait sous les yeux.

Ce qui **reste** au routeur est ce qui appartient vraiment à HTTP : lire les
paramètres, borner une page, et rendre en JSON. La sérialisation n'est pas de
la règle métier — ``float(qty_per)`` existe parce que JSON n'a pas de décimal,
pas parce que l'inventaire l'exige — et la faire descendre ici obligerait le
service à connaître la forme de l'écran.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.bom import BomIndex
from ..domain.controls import check_referentials, group_findings, summarise
from ..domain.enums import ExclusionScope
from ..domain.models import (
    BomLink,
    BookStockLine,
    Campaign,
    CountJournal,
    Item,
    Location,
    Warehouse,
)
from ..domain.variance import at_standard_price
from ..errors import NotFoundError, ValidationError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["BookStockView", "LocationView", "ReferentialService"]

#: Comment une exclusion se lit dans le journal d'audit et dans les messages.
EXCLUSION_LABELS = {
    ExclusionScope.GENERIC: "hors GENERIQUE",
    ExclusionScope.BOM: "ignoré en nomenclature",
    ExclusionScope.ALL: "hors périmètre",
}


def describe_exclusions(scopes: set[ExclusionScope]) -> str:
    if not scopes:
        return "aucune exclusion"
    return ", ".join(EXCLUSION_LABELS[s] for s in sorted(scopes, key=str))


@dataclass(frozen=True, slots=True)
class BookStockView:
    """Le stock ERP tel qu'on vient le lire, et ce que ses grosses lignes pèsent.

    ``top_share`` vaut ``None`` sans filtre, et ce n'est pas un oubli : « 100 % »
    se lirait comme un résultat alors que ce serait une tautologie.
    """

    lines: list[BookStockLine]
    total_value: float
    top_share: float | None


@dataclass(frozen=True, slots=True)
class LocationView:
    """Un emplacement, et le journal de comptage qui lui correspond — ou rien.

    L'appariement se fait ici parce qu'il demande deux dépôts. Le laisser à
    l'écran obligerait chaque écran à le refaire, et l'un d'eux le ferait
    autrement.
    """

    location: Location
    journal: CountJournal | None


class ReferentialService:
    """Articles, nomenclatures, stock ERP et emplacements d'une campagne.

    Les lectures rendent des modèles du domaine, jamais des dictionnaires : un
    service qui rend déjà du JSON ne se réutilise que par ce qui produit du
    JSON, ce qui est exactement la dépendance qu'on retire ici.
    """

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    # ---------------------------------------------------------------- articles

    def stocked_item_numbers(self, campaign_id: str) -> set[str]:
        """Les articles que la campagne s'attend vraiment à voir : feuilles ∪ journaux.

        Le référentiel porte le catalogue entier — des dizaines de milliers de
        références, dont la plupart n'ont pas été détenues depuis des années. Ce
        qu'on compte est un ensemble bien plus petit, et le seul pour lequel il
        vaille la peine de lire une désignation ou un lien de nomenclature. Les
        deux sources sont réunies plutôt que choisies : la répartition est une
        affaire de stockage, pas d'intérêt.
        """
        ctx = self.ctx
        return ctx.sheets.listed_item_numbers(campaign_id) | (
            ctx.journals.listed_item_numbers(campaign_id)
        )

    def list_items(
        self,
        campaign: Campaign,
        *,
        search: str | None = None,
        counted: bool = False,
    ) -> list[Item]:
        """Les articles de la campagne, filtrés ici et non dans le navigateur.

        Filtrer au serveur est ce qui permet au total de vouloir dire ce qu'il
        dit : une pagination dont le total compte des lignes que le filtre
        écarte annonce une suite qui n'existe pas.
        """
        items = self.ctx.referentials.list_items(campaign.id)
        if counted:
            stocked = self.stocked_item_numbers(campaign.id)
            items = [i for i in items if i.item_number in stocked]
        if search:
            needle = search.strip().upper()
            items = [
                i for i in items
                if needle in i.item_number or needle in i.name.upper()
                or needle in i.search_name.upper()
            ]
        return items

    def update_item(
        self, campaign: Campaign, item_number: str, changes: dict[str, Any]
    ) -> Item:
        """Corriger un article sans recharger le référentiel.

        Un référentiel arrive de l'ERP avec une désignation manquante ici, un
        type faux là. Avant, le seul remède était de réimporter tout le
        fichier — une correction d'un caractère coûtait le rechargement, et les
        gens ont cessé de corriger.
        """
        ctx = self.ctx
        ctx.guard(campaign, "items")
        current = ctx.referentials.get_item(campaign.id, item_number)
        if current is None:
            raise NotFoundError(f"Article « {item_number} » introuvable.")
        if not changes:
            raise ValidationError("Aucune modification transmise.")
        # `model_copy` ne revalide pas : l'ensemble doit donc arriver déjà
        # normalisé, sans quoi un `{ALL, GENERIC}` envoyé par un client serait
        # stocké tel quel et relu comme quelque chose que le sélecteur ne sait
        # pas représenter.
        if "exclusions" in changes:
            changes = {
                **changes,
                "exclusions": ExclusionScope.normalise(changes["exclusions"]),
            }
        updated = current.model_copy(update=changes)

        ctx.referentials.upsert_items([updated], actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action="UPDATE",
            entity_type="item",
            entity_id=item_number,
            summary=f"Modification de l'article {item_number}",
            before={k: str(getattr(current, k)) for k in changes},
            after={k: str(getattr(updated, k)) for k in changes},
        )
        return updated

    def set_item_exclusions(
        self,
        campaign: Campaign,
        item_numbers: Sequence[str],
        exclusions: Sequence[str] | set[ExclusionScope],
    ) -> dict[str, Any]:
        """Appliquer une exclusion à toute une sélection.

        Les exclusions viennent par familles, pas une référence à la fois : un
        programme parti du site, une gamme après-vente comptée ailleurs, des
        emballages que personne ne pèse. Le faire ligne à ligne est ce qui
        faisait abandonner à mi-chemin et laissait un référentiel à moitié
        vrai — pire que celui qui n'exclut rien, parce que les trous y sont
        invisibles.

        Une référence inconnue arrête tout le lot au lieu d'être ignorée : une
        sélection se fait sur ce qui est à l'écran, donc une référence que le
        serveur ne connaît pas signifie que les deux divergent, et appliquer le
        reste en silence masquerait justement cela.
        """
        ctx = self.ctx
        ctx.guard(campaign, "items")
        wanted = ExclusionScope.normalise(exclusions)
        numbers = list(
            dict.fromkeys(n.strip().upper() for n in item_numbers if n.strip())
        )
        if not numbers:
            raise ValidationError("Aucun article transmis.")

        known = ctx.referentials.items_by_number(campaign.id)
        missing = [n for n in numbers if n not in known]
        if missing:
            raise ValidationError(
                f"{len(missing)} article(s) hors référentiel, dont "
                f"« {missing[0]} ». Rechargez la liste avant de recommencer.",
                missing=missing[:20],
            )

        changed = [
            known[n].model_copy(update={"exclusions": set(wanted)})
            for n in numbers
            if known[n].exclusions != wanted
        ]
        if changed:
            ctx.referentials.upsert_items(changed, actor=ctx.actor)
            ctx.record(
                campaign_id=campaign.id,
                action="UPDATE",
                entity_type="item",
                entity_id="",
                summary=f"{len(changed)} article(s) : {describe_exclusions(wanted)}",
                after={
                    "exclusions": ",".join(sorted(str(e) for e in wanted)),
                    # Les références elles-mêmes, pour que la trace réponde à
                    # « lesquelles ? » sans rejouer la sélection — tronquées,
                    # parce qu'un lot peut porter le catalogue entier et qu'une
                    # ligne d'audit est lue par un humain.
                    "itemNumbers": ", ".join(i.item_number for i in changed[:50])
                    + (" …" if len(changed) > 50 else ""),
                },
            )
        return {
            "updated": len(changed),
            "unchanged": len(numbers) - len(changed),
            "exclusions": sorted(str(e) for e in wanted),
        }

    def delete_item(self, campaign: Campaign, item_number: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "items")
        ctx.referentials.delete_item(campaign.id, item_number, actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action="DELETE",
            entity_type="item",
            entity_id=item_number,
            summary=f"Suppression logique de l'article {item_number}",
        )

    # ----------------------------------------------------------- nomenclatures

    def list_bom_links(
        self,
        campaign: Campaign,
        *,
        parent: str | None = None,
        counted: bool = False,
    ) -> tuple[list[BomLink], dict[str, Item]]:
        """Les liens, et les articles qui les nomment.

        Un lien est conservé quand **l'une des deux** extrémités est stockée, et
        pas seulement l'assemblage : un assemblage trouvé sur une feuille est
        gardé parce qu'il sera éclaté, et un composant trouvé sur une feuille
        est gardé parce qu'un ``qty_per`` faux au-dessus de lui est exactement
        ce qui rendrait sa quantité comptée inexplicable.

        Les articles reviennent avec : une nomenclature lue en références seules
        oblige à ouvrir le référentiel à chaque ligne pour savoir de quoi il
        s'agit.
        """
        ctx = self.ctx
        links = ctx.referentials.list_bom_links(campaign.id)
        if parent:
            needle = parent.strip().upper()
            links = [l for l in links if l.parent_item == needle]
        if counted:
            stocked = self.stocked_item_numbers(campaign.id)
            links = [
                l for l in links
                if l.parent_item in stocked or l.child_item in stocked
            ]
        return links, ctx.referentials.items_by_number(campaign.id)

    def set_bom_activation(
        self,
        campaign: Campaign,
        links: Sequence[Any],
        active: bool,
    ) -> dict[str, int]:
        """Mettre un lot de liens en vigueur, ou les retirer.

        Un changement de version arrive comme un ensemble — c'est toute la
        recette d'un assemblage qui est remplacée, pas un de ses liens — donc
        c'est l'opération, et la faire lien par lien est la façon dont une
        moitié de version se retrouve en vigueur et l'autre retirée.
        """
        ctx = self.ctx
        ctx.guard(campaign, "boms")
        wanted = {(l.parent_item, l.child_item) for l in links}
        known = {
            (l.parent_item, l.child_item): l
            for l in ctx.referentials.list_bom_links(campaign.id)
        }
        missing = sorted(wanted - set(known))
        if missing:
            raise ValidationError(
                f"{len(missing)} lien(s) introuvables, dont "
                f"« {missing[0][0]} → {missing[0][1]} ». Rechargez la liste.",
                missing=[f"{p} → {c}" for p, c in missing[:20]],
            )

        changed = [
            known[key].model_copy(update={"active": active})
            for key in sorted(wanted)
            if known[key].active != active
        ]
        if changed:
            ctx.referentials.upsert_bom_links(changed, actor=ctx.actor)
            ctx.record(
                campaign_id=campaign.id,
                action="UPDATE",
                entity_type="bom_link",
                summary=(
                    f"{len(changed)} lien(s) "
                    f"{'remis en vigueur' if active else 'retirés'}"
                ),
                after={"active": str(active)},
            )
        return {"updated": len(changed), "unchanged": len(wanted) - len(changed)}

    def update_bom_link(
        self, campaign: Campaign, parent: str, child: str, changes: dict[str, Any]
    ) -> BomLink:
        """Corriger la quantité ou l'unité d'un lien.

        Un ``qty_per`` faux est invisible jusqu'à ce que la consolidation éclate
        un assemblage et produise un comptage de composant que personne ne sait
        expliquer. Le réparer doit coûter un champ, pas la réimportation de
        toute la structure.
        """
        ctx = self.ctx
        ctx.guard(campaign, "boms")
        parent_key, child_key = parent.strip().upper(), child.strip().upper()
        current = ctx.referentials.get_bom_link(campaign.id, parent_key, child_key)
        if current is None:
            raise NotFoundError(f"Lien « {parent_key} → {child_key} » introuvable.")
        if not changes:
            raise ValidationError("Aucune modification transmise.")
        updated = current.model_copy(update=changes)

        ctx.referentials.upsert_bom_links([updated], actor=ctx.actor)
        ctx.record(
            campaign_id=campaign.id,
            action="UPDATE",
            entity_type="bom_link",
            entity_id=f"{parent_key}/{child_key}",
            summary=f"Modification du lien {parent_key} → {child_key}",
            before={k: str(getattr(current, k)) for k in changes},
            after={k: str(getattr(updated, k)) for k in changes},
        )
        return updated

    def delete_bom_link(self, campaign: Campaign, parent: str, child: str) -> None:
        ctx = self.ctx
        ctx.guard(campaign, "boms")
        parent_key, child_key = parent.strip().upper(), child.strip().upper()
        removed = ctx.referentials.delete_bom_link(
            campaign.id, parent_key, child_key, actor=ctx.actor
        )
        if not removed:
            raise NotFoundError(f"Lien « {parent_key} → {child_key} » introuvable.")
        ctx.record(
            campaign_id=campaign.id,
            action="DELETE",
            entity_type="bom_link",
            entity_id=f"{parent_key}/{child_key}",
            summary=f"Suppression du lien {parent_key} → {child_key}",
        )

    def bom_health(self, campaign: Campaign) -> dict[str, Any]:
        """Cycles, liens orphelins et assemblages sans structure.

        Un défaut de nomenclature découvert le jour de l'inventaire coûte une
        après-midi ; découvert en préparation, dix minutes.
        """
        ctx = self.ctx
        items = ctx.referentials.items_by_number(campaign.id)
        links = ctx.referentials.list_bom_links(campaign.id)
        index = BomIndex(links)
        findings = check_referentials(items=items, bom_links=links, bom_index=index)
        return {
            "linkCount": len(index),
            "parentCount": len(index.parents),
            "cycles": [" → ".join(c) for c in index.find_cycles()],
            "summary": summarise(findings),
            "groups": [g.to_summary() for g in group_findings(findings)],
            "findings": [f.model_dump(mode="json") for f in findings],
        }

    # -------------------------------------------------------------- stock ERP

    def book_stock(self, campaign: Campaign, *, top: int | None = None) -> BookStockView:
        """L'instantané ERP, et le poids de ses plus grosses lignes.

        ``top=25`` garde les vingt-cinq triplets article / entrepôt /
        emplacement les plus lourds. La valeur du stock est concentrée — une
        poignée de lignes en portent l'essentiel — et ce sont celles qu'il vaut
        la peine de compter deux fois. ``top_share`` dit quelle part elles
        représentent vraiment, pour que l'affirmation soit mesurée plutôt que
        supposée.
        """
        # Au prix standard, comme les écarts et les KPI. La grille affichait
        # le coût porté par la ligne — celui de l'ERP au gel pour le snapshot,
        # le prix standard pour un emplacement précompté — et son total ne
        # tombait donc pas sur celui du carrousel, sur les mêmes lignes.
        lines = at_standard_price(
            self.ctx.book_stock.list(campaign.id),
            self.ctx.referentials.items_by_number(campaign.id),
        )
        total_value = sum(float(l.value) for l in lines)
        top_share: float | None = None
        if top is not None:
            lines = sorted(lines, key=lambda l: abs(float(l.value)), reverse=True)[:top]
            kept = sum(float(l.value) for l in lines)
            top_share = kept / total_value if total_value else 0.0
        return BookStockView(lines=lines, total_value=total_value, top_share=top_share)

    def locations(
        self, campaign: Campaign
    ) -> tuple[list[Warehouse], list[LocationView]]:
        """Entrepôts et emplacements, chacun sachant s'il porte déjà un journal.

        Sans le journal apparié, l'écran de préparation ne peut pas distinguer
        un emplacement qu'on a oublié d'ouvrir d'un emplacement dont le
        comptage est terminé.
        """
        ctx = self.ctx
        journals = {j.key: j for j in ctx.journals.list(campaign.id)}
        return (
            ctx.referentials.list_warehouses(campaign.id),
            [
                LocationView(location=location, journal=journals.get(location.key))
                for location in ctx.referentials.list_locations(campaign.id)
            ],
        )
