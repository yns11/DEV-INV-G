"""Ce qu'un import a le droit d'écrire quand une partie du fichier est refusée.

Un import produit deux choses : des lignes acceptées, et des lignes rejetées.
La question posée ici est de savoir si les premières doivent partir en base
quand les secondes existent — et la réponse dépend entièrement de ce que
l'écriture fait au jeu de données déjà présent.

**Un import qui complète** — le référentiel articles, les emplacements, les
lignes de feuilles — ajoute et met à jour. Trois lignes refusées sur quatre
mille, ce sont trois lignes qui manquent, visibles dans le rapport, et que le
prochain chargement apportera. Écrire les 3 997 autres est utile, et ne détruit
rien.

**Un import qui remplace** — le snapshot de stock ERP, l'écart backflush, une
nomenclature chargée en mode remplacement — efface l'ensemble précédent avant
d'écrire le nouveau. Trois lignes refusées sur quatre mille deviennent alors
trois lignes **supprimées** : la nomenclature passe de 4 000 liens à 3 997, et
plus rien ne dit que les trois manquants ont existé. L'éclatement du WIP se
fait ensuite contre une nomenclature incomplète, la consolidation produit des
quantités fausses, et l'écart d'inventaire qui en sort porte sur des articles
que personne ne saura relier au fichier mal formé du matin.

C'est la même faute que la troncature silencieuse des lectures ERP : un
ensemble amputé qui se présente comme complet. Un import qui remplace refuse
donc d'écrire dès qu'une ligne est rejetée.

**La dérogation existe, et se voit.** Un fichier dont on sait que dix lignes
sont irrécupérables doit pouvoir passer le jour de l'inventaire ; c'est un
choix, il est explicite (`allow_partial`), et il est écrit dans la trace comme
dans le rapport du lot. Il n'est pas le défaut.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PartialWriteRefusal", "refuse_partial_write"]

#: Combien de motifs de rejet la phrase de refus cite avant de s'arrêter. Au-delà
#: on ne lit plus, et le rapport complet est de toute façon dans la réponse.
_REASONS_SHOWN = 3


@dataclass(frozen=True, slots=True)
class PartialWriteRefusal:
    """Le refus d'écrire un ensemble amputé, et de quoi le comprendre."""

    #: Ce que l'utilisateur lit.
    message: str
    #: Combien de lignes ont été refusées.
    rejected: int
    #: Combien auraient été écrites.
    accepted: int

    def __bool__(self) -> bool:  # pragma: no cover - lisibilité aux appels
        return True


def refuse_partial_write(
    *,
    wholesale: bool,
    rejected: int,
    accepted: int,
    allow_partial: bool = False,
    what: str = "Ce chargement",
    reasons: tuple[str, ...] = (),
) -> PartialWriteRefusal | None:
    """Faut-il refuser d'écrire ? Le refus, ou ``None``.

    :param wholesale: l'écriture remplace-t-elle l'ensemble précédent.
    :param rejected: lignes refusées à la lecture ou au mappage.
    :param accepted: lignes qui seraient écrites.
    :param allow_partial: la dérogation explicite de l'appelant.
    :param what: comment nommer l'ensemble dans la phrase — « Le stock ERP ».
    :param reasons: les premiers motifs de rejet, cités pour que le fichier soit
        corrigeable sans aller chercher le rapport.

    Un import qui complète n'est jamais refusé : il n'efface rien.
    """
    if not wholesale or rejected <= 0 or allow_partial:
        return None

    total = rejected + accepted
    head = (
        f"{what} remplace l'ensemble existant, et {rejected} ligne(s) sur "
        f"{total} ont été refusées. L'écriture est annulée : elle laisserait "
        f"{accepted} ligne(s) là où il y en avait davantage, sans que rien ne "
        "dise lesquelles ont disparu."
    )
    shown = [r for r in reasons if r][:_REASONS_SHOWN]
    detail = ""
    if shown:
        more = len(reasons) - len(shown)
        detail = " Motifs : " + " ; ".join(shown)
        detail += f" (et {more} autre(s))." if more > 0 else "."
    tail = (
        " Corrigez le fichier et rechargez-le, ou demandez explicitement un "
        "chargement partiel si ces lignes sont irrécupérables."
    )
    return PartialWriteRefusal(
        message=head + detail + tail, rejected=rejected, accepted=accepted
    )
