"""Une page, et le nombre de lignes qu'elle ne montre pas.

Trois listes de référence — articles, nomenclatures, lignes de feuilles —
renvoyaient chacune leur contenu d'une façon différente. Les articles
paginaient déjà correctement ; les deux autres partaient entières, sans
plafond ni total. Une nomenclature de cinquante mille liens traversait alors le
réseau, était rendue en JSON puis triée dans le navigateur, pour qu'on en lise
trente lignes.

Le total est la partie qui compte. Une liste tronquée sans lui est
indistinguable d'une liste complète : l'écran ne peut pas dire « il y en a
d'autres », donc il ne le dit pas, et personne ne sait ce qui manque.

``render`` n'est appliqué qu'à la page. Le détail a son prix — une ligne de
nomenclature va chercher deux désignations au référentiel — et le payer sur des
lignes qu'on jette ensuite est exactement ce que la pagination doit éviter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

#: Ce qu'une grille accepte de recevoir d'un coup. Au-delà, ce n'est plus une
#: lecture : c'est un export, et l'export a son propre bouton.
MAX_PAGE = 20_000


def page(
    rows: Sequence[T],
    *,
    offset: int,
    limit: int,
    render: Callable[[T], dict[str, Any]],
) -> dict[str, Any]:
    """La tranche demandée, et combien il y en a en tout.

    ``total`` compte l'ensemble **après filtrage** : un total qui inclurait des
    lignes que la page suivante ne ramènerait jamais annoncerait une suite qui
    n'existe pas.
    """
    return {
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "rows": [render(row) for row in rows[offset : offset + limit]],
    }
