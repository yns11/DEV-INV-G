"""Le jour J, qui doit faire quoi.

L'écran de campagne donnait des barres de progression : « 62 % des zones »,
« 8 journaux sur 14 ». Un pourcentage répond à « où en est-on », jamais à « que
faire maintenant ». Le matin d'un inventaire, un responsable de secteur pose
trois questions, et aucune n'a de réponse dans un pourcentage :

* qu'est-ce qui attend **une décision de moi** ?
* qu'est-ce que je peux **fermer tout de suite** ?
* qui **n'a pas commencé** ?

Ce module compose les files de travail qui y répondent. Deux choix les rendent
utilisables sur le terrain plutôt que jolies sur un écran.

**Une file est nommée, pas seulement comptée.** « 3 zones à arbitrer » oblige à
ouvrir un écran pour savoir lesquelles ; « Z04, Z07, Z12 » permet d'y aller.
Les noms sont bornés — au-delà, la file dit combien il en reste — parce qu'une
liste de deux cents codes ne se lit pas davantage qu'un nombre.

**L'ordre est celui de l'action, pas celui du parcours.** Ce qui attend une
décision vient avant ce qu'on peut fermer, qui vient avant ce qui est en cours,
qui vient avant ce qui n'a pas commencé. Trier par phase remettrait « pas
commencé » en tête, c'est-à-dire la file sur laquelle le responsable ne peut
rien faire lui-même.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["NAMES_SHOWN", "Queue", "work_queues"]

#: Combien de noms une file montre avant de compter le reste.
#:
#: Douze tient sur une ligne d'écran et suffit à répartir un secteur. Au-delà,
#: ce n'est plus une file de travail : c'est la liste complète, et elle a son
#: écran.
NAMES_SHOWN = 12


@dataclass(frozen=True, slots=True)
class Queue:
    """Une file de travail : ce qu'elle attend, combien, et lesquels."""

    code: str
    label: str
    #: Ce qu'il y a à faire, en une phrase. Le libellé nomme la file ; celui-ci
    #: dit le geste, parce que « Arbitrages » ne dit pas qu'il faut trancher.
    action: str
    count: int
    #: Les premiers noms — codes de zone, clés de journal — pour aller droit au
    #: but sans ouvrir l'écran.
    names: tuple[str, ...]
    #: Fragment de route, relatif à la campagne.
    where: str

    @property
    def hidden(self) -> int:
        return max(0, self.count - len(self.names))

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "action": self.action,
            "count": self.count,
            "names": list(self.names),
            "hidden": self.hidden,
            "where": self.where,
        }


def _queue(
    code: str, label: str, action: str, names: Sequence[str], where: str
) -> Queue:
    ordered = sorted(names)
    return Queue(
        code=code,
        label=label,
        action=action,
        count=len(ordered),
        names=tuple(ordered[:NAMES_SHOWN]),
        where=where,
    )


def work_queues(
    *,
    zones_to_arbitrate: Sequence[str] = (),
    zones_ready_to_close: Sequence[str] = (),
    zones_in_progress: Sequence[str] = (),
    zones_not_started: Sequence[str] = (),
    journals_in_progress: Sequence[str] = (),
    journals_not_started: Sequence[str] = (),
    include_empty: bool = False,
) -> list[Queue]:
    """Les files du jour, dans l'ordre où on les traite.

    Une file vide disparaît. C'est la différence entre un tableau de bord et un
    tableau de commandement : le premier montre six cases dont quatre à zéro,
    le second montre les deux qui appellent quelqu'un. ``include_empty`` existe
    pour les contrôles, qui ont besoin de voir la forme complète.
    """
    queues = [
        _queue(
            "ZONES_TO_ARBITRATE",
            "Zones à arbitrer",
            "Deux comptages se contredisent : trancher, sinon la zone ne peut "
            "pas être fermée.",
            zones_to_arbitrate,
            # La sous-section, pas seulement l'écran : « ?vue=arbitration »
            # ouvre l'onglet où l'on tranche. Renvoyer sur l'onglet des zones
            # demanderait un clic de plus à chaque fois, sur la file la plus
            # urgente des six.
            "compil?vue=arbitration",
        ),
        _queue(
            "ZONES_READY_TO_CLOSE",
            "Zones prêtes à fermer",
            "Tout est compté et rien n'est en litige : les fermer débloque le "
            "passage en analyse.",
            zones_ready_to_close,
            "compil",
        ),
        _queue(
            "ZONES_IN_PROGRESS",
            "Zones en cours",
            "Des quantités ont été saisies, il en reste.",
            zones_in_progress,
            "compil",
        ),
        _queue(
            "JOURNALS_IN_PROGRESS",
            "Journaux commencés",
            "Des lignes sont comptées : poster quand l'emplacement est fini.",
            journals_in_progress,
            "comptage",
        ),
        _queue(
            "ZONES_NOT_STARTED",
            "Zones non commencées",
            "Aucune quantité relevée : y envoyer quelqu'un.",
            zones_not_started,
            "compil",
        ),
        _queue(
            "JOURNALS_NOT_STARTED",
            "Journaux non commencés",
            "Aucune ligne comptée sur cet emplacement.",
            journals_not_started,
            "comptage",
        ),
    ]
    return [q for q in queues if include_empty or q.count]
