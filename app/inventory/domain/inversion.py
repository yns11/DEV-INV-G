"""La signature d'une inversion de références au comptage.

Le cas
------
Deux pièces du même assemblage se ressemblent, voisinent sur la même étagère, et
l'une est comptée à la place de l'autre. L'inventaire rend alors deux anomalies :
un excédent franc sur la première, un manque du même ordre sur la seconde. Prises
séparément — et c'est ainsi que tous les écrans les montrent — ce sont deux
problèmes à investiguer, chacun coûtant un aller-retour en magasin. Prises
ensemble, c'est une seule erreur, et elle se corrige sans bouger de son bureau.

Rien ne les rapprochait. Ni la catégorie, qui est trop large ; ni le programme,
qui dit pour quel marché la pièce est produite ; ni l'emplacement, puisque les
deux références sont justement au même endroit et que l'écart par emplacement
les sépare aussi bien que l'écart par référence. Le **produit fabriqué** est la
seule découpe qui les met côte à côte.

Ce que ce module décide, et ce qu'il ne décide pas
-------------------------------------------------
Il **signale**, il ne conclut pas. Deux écarts qui se compensent sur un produit
peuvent aussi bien être deux erreurs indépendantes qui tombent par hasard du bon
côté — c'est d'autant plus probable que le produit porte de références. La
sortie est donc un indice, destiné au dossier envoyé au modèle et à l'œil
humain ; aucune quantité n'est corrigée ici, et aucune cause n'est posée.

Pur, et sans base de données : c'est ce qui permet de le vérifier sur des
chiffres écrits à la main plutôt que sur une campagne entière.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = ["Compensation", "offsetting_groups", "OFFSET_TOLERANCE"]

#: Jusqu'où le net doit rester sous le brut pour qu'on parle de compensation.
#:
#: 0,2 — le solde vaut au plus un cinquième de ce qui a bougé. Une inversion
#: parfaite donne zéro ; en pratique elle s'accompagne d'un écart réel sur l'une
#: des deux références, et exiger zéro n'aurait signalé que le cas d'école. Plus
#: haut, deux écarts de même ordre et de sens opposés se croisent trop souvent
#: par hasard, et l'indice devient du bruit.
OFFSET_TOLERANCE = Decimal("0.2")


@dataclass(frozen=True, slots=True)
class Compensation:
    """Un produit fabriqué dont les écarts s'annulent à peu près."""

    product: str
    #: Les références en cause, du plus gros écart absolu au plus petit.
    item_numbers: tuple[str, ...]
    #: Somme des valeurs absolues — ce qui a bougé.
    gross_qty: Decimal
    #: Somme signée — ce qui reste une fois les sens opposés compensés.
    net_qty: Decimal

    @property
    def ratio(self) -> Decimal:
        """Le net rapporté au brut. Zéro : compensation parfaite."""
        return abs(self.net_qty) / self.gross_qty if self.gross_qty else Decimal(0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "produitFabrique": self.product,
            "references": list(self.item_numbers),
            "ecartBrutQte": float(self.gross_qty),
            "ecartNetQte": float(self.net_qty),
            "tauxCompensation": round(float(1 - self.ratio), 4),
        }


def offsetting_groups(
    lines: Sequence[Any],
    products: Mapping[str, str],
    *,
    tolerance: Decimal = OFFSET_TOLERANCE,
) -> list[Compensation]:
    """Les produits dont les écarts en quantité se compensent.

    *lines* porte des objets à ``item_number`` et ``variance_qty`` — les lignes
    d'écart du moteur. *products* rattache une référence à son produit fabriqué ;
    une référence absente, ou rattachée à rien, est ignorée.

    Deux conditions :

    **Au moins deux références.** Une seule ne se compense avec rien.

    **Le net faible devant le brut**, au sens de *tolerance*.

    Une troisième — exiger les deux sens — a été écrite puis retirée : elle ne
    pouvait jamais rien retirer. Des quantités de même signe donnent un net égal
    au brut, donc un rapport de 1, et la tolérance les écarte déjà toutes. Elle
    se lisait pourtant comme une garde utile, ce qui est la pire sorte de code
    mort : celle qu'on croit protectrice.

    Triées par quantité brute décroissante : la première ligne est celle qui
    mérite le coup d'œil, et un dossier envoyé au modèle a une taille limitée.
    """
    by_product: dict[str, list[tuple[str, Decimal]]] = {}
    for line in lines:
        product = (products.get(line.item_number) or "").strip()
        if not product:
            continue
        qty = _as_decimal(line.variance_qty)
        if qty:
            by_product.setdefault(product, []).append((line.item_number, qty))

    out: list[Compensation] = []
    for product, entries in by_product.items():
        if len(entries) < 2:
            continue
        gross = sum((abs(q) for _, q in entries), Decimal(0))
        net = sum((q for _, q in entries), Decimal(0))
        if not gross or abs(net) / gross > tolerance:
            continue
        ranked = sorted(entries, key=lambda e: abs(e[1]), reverse=True)
        out.append(
            Compensation(
                product=product,
                item_numbers=tuple(number for number, _ in ranked),
                gross_qty=gross,
                net_qty=net,
            )
        )
    out.sort(key=lambda c: c.gross_qty, reverse=True)
    return out


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))
