"""L'intertitre et les lignes qu'il chapeaute.

La feuille porte la même information sous deux formes, et il faut les deux :

* une ligne ``SUBSECTION``, **à sa place** dans le document. C'est elle qui
  s'imprime et que l'écran d'aperçu déplace — le séparateur que le compteur
  cherche des yeux sur la page ;
* une colonne ``subsection`` recopiée sur chaque ligne d'article. C'est elle qui
  entre dans la clé d'unicité, parce que cette clé doit se calculer sur une
  ligne **seule** — à l'import, où l'ordre du fichier ne veut encore rien dire.

Les deux ne peuvent pas diverger sans qu'un article soit compté sous un
intertitre et dédoublonné sous un autre. La colonne est donc **dérivée** des
séparateurs dès que le document entier est réécrit, et ce module est le seul
endroit où cette dérivation est écrite.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from .enums import CountLineKind

__all__ = ["subsections_of"]


def subsections_of(lines: Iterable[Mapping[str, Any]]) -> Iterator[str]:
    """Pour chaque ligne, dans l'ordre, l'intertitre sous lequel elle se trouve.

    Rendu pour *toutes* les lignes, séparateurs compris — l'appelant zippe le
    résultat sur sa séquence sans avoir à compter. Un séparateur n'est sous
    aucun intertitre : c'en est un.

    Une ligne vide ne referme pas l'intertitre courant. Dans les classeurs qu'on
    remplace, elle sert justement à aérer *à l'intérieur* d'un groupe, et lui
    faire clore le groupe couperait « Stock physique B15 » en deux au premier
    espace laissé par un préparateur.
    """
    current = ""
    for line in lines:
        kind = str(line.get("line_kind") or CountLineKind.ARTICLE)
        if kind == str(CountLineKind.SUBSECTION):
            current = str(line.get("label") or "").strip()
            yield ""
            continue
        yield current
