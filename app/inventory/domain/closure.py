"""La clôture se prépare, elle ne se subit pas.

Clôturer est le seul geste irréversible du parcours. Ce qui l'empêche était
déjà calculé — :func:`campaign_transition_blockers` le fait — mais on ne le
découvrait qu'en ouvrant la fenêtre qui clôture, c'est-à-dire au moment de
cliquer. Trois points bloquants apparaissaient alors d'un coup, un vendredi
soir, et il fallait repartir dans trois écrans.

Cette liste de contrôle répond avant, et elle répond en trois tons.

**Ce qui bloque** n'est pas recalculé ici : les entrées bloquantes sont
construites à partir des mêmes constats que le refus. Les rejouer autrement
serait la façon dont l'écran et le serveur finissent par ne plus dire la même
chose — l'écran annonçant « prêt » sur une campagne que la clôture refuse.

**Ce qui mérite un regard** n'empêche rien, et c'est justement pourquoi il faut
le montrer. Un écart accepté sans un mot d'explication est une décision que
personne ne saura défendre dans six mois ; une suggestion de l'IA que personne
n'a tranchée est un travail commencé et laissé là. Aucun des deux ne justifie
d'interdire la clôture — ce serait rendre la clôture impossible sur des points
que l'exploitant a le droit d'assumer — mais les taire revient à faire comme
s'ils n'existaient pas.

**Ce qui est fait** figure aussi. Une liste qui ne montre que les reproches se
lit comme une machine à empêcher ; une liste qui montre les neuf points, dont
six sont verts, se lit comme un état des lieux — et c'est ce qu'on vient
chercher.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .models import ControlFinding

__all__ = ["ChecklistItem", "ChecklistState", "closure_checklist"]


class ChecklistState(StrEnum):
    """Les trois tons de la liste, dans l'ordre où on les lit."""

    BLOCKING = "BLOCKING"
    ATTENTION = "ATTENTION"
    DONE = "DONE"


#: L'ordre de lecture : ce qui arrête, ce qui mérite un regard, ce qui est fait.
_ORDER = {
    ChecklistState.BLOCKING: 0,
    ChecklistState.ATTENTION: 1,
    ChecklistState.DONE: 2,
}

#: Où va-t-on pour résoudre un point. Le fragment est relatif à la campagne :
#: sans lui, « rechargez le fichier corrigé » laisse chercher l'écran.
WHERE: dict[str, str] = {
    "MATERIAL_VARIANCES_UNEXPLAINED": "ecarts",
    "IMPORTS_WITH_REJECTS": "articles",
    "PUBLICATION_NOT_DONE": "compil",
    "ACCEPTED_WITHOUT_COMMENT": "ecarts",
    "AI_SUGGESTIONS_UNTOUCHED": "ecarts",
    "SHEETS_CHANGED_AFTER_CONSOLIDATION": "compil",
}

#: Le libellé court de chaque point. Le message détaillé vient d'ailleurs — du
#: constat bloquant pour les uns, du calcul ci-dessous pour les autres — mais
#: le titre doit tenir sur une ligne et se lire sans le détail.
LABELS: dict[str, str] = {
    "MATERIAL_VARIANCES_UNEXPLAINED": "Écarts matériels expliqués",
    "IMPORTS_WITH_REJECTS": "Chargements sans lignes refusées",
    "PUBLICATION_NOT_DONE": "Archive Delta publiée",
    "ACCEPTED_WITHOUT_COMMENT": "Acceptations motivées",
    "AI_SUGGESTIONS_UNTOUCHED": "Suggestions de l'IA tranchées",
    "SHEETS_CHANGED_AFTER_CONSOLIDATION": "Consolidation à jour",
    "BOOK_STOCK_FROZEN": "Stock ERP gelé",
    "JOURNALS_POSTED": "Journaux de comptage postés",
    "ZONES_DONE": "Zones GENERIQUE terminées",
}


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    """Un point de la liste, et ce qu'on peut en faire."""

    code: str
    label: str
    state: ChecklistState
    detail: str
    #: Fragment de route où le point se résout, relatif à la campagne.
    where: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "label": self.label,
            "state": str(self.state),
            "detail": self.detail,
            "where": self.where,
        }


def closure_checklist(
    *,
    blockers: Sequence[ControlFinding],
    accepted_without_comment: int = 0,
    ai_suggestions_untouched: int = 0,
    sheets_changed_after_consolidation: bool = False,
    book_stock_frozen: bool = True,
    journals_pending: int = 0,
    zones_open: int = 0,
) -> list[ChecklistItem]:
    """L'état des lieux avant le geste irréversible.

    ``blockers`` arrive de :func:`campaign_transition_blockers`, et n'est pas
    recalculé : c'est ce qui garantit que la liste et le refus disent la même
    chose. Un constat bloquant dont le code est inconnu ici figure quand même,
    sous son propre message — mieux vaut un libellé technique qu'un point
    bloquant invisible.
    """
    items: list[ChecklistItem] = []
    blocked = {b.code for b in blockers}

    for finding in blockers:
        items.append(
            ChecklistItem(
                code=finding.code,
                label=LABELS.get(finding.code, finding.code),
                state=ChecklistState.BLOCKING,
                detail=finding.message,
                where=WHERE.get(finding.code),
            )
        )

    # Les trois points bloquants qui *ne* bloquent pas cette fois-ci : ils
    # méritent d'apparaître en vert, sans quoi la liste ne dit pas qu'ils ont
    # été vérifiés.
    for code, detail in (
        (
            "MATERIAL_VARIANCES_UNEXPLAINED",
            "Chaque écart matériel porte une cause ou une acceptation explicite.",
        ),
        (
            "IMPORTS_WITH_REJECTS",
            "Le dernier chargement de chaque grille a tout accepté.",
        ),
        (
            "PUBLICATION_NOT_DONE",
            "Le dossier a sa copie opposable dans l'archive Delta.",
        ),
    ):
        if code not in blocked:
            items.append(
                ChecklistItem(
                    code=code,
                    label=LABELS[code],
                    state=ChecklistState.DONE,
                    detail=detail,
                )
            )

    # ---- ce qui mérite un regard --------------------------------------------

    if accepted_without_comment:
        items.append(
            ChecklistItem(
                code="ACCEPTED_WITHOUT_COMMENT",
                label=LABELS["ACCEPTED_WITHOUT_COMMENT"],
                state=ChecklistState.ATTENTION,
                detail=(
                    f"{accepted_without_comment} écart(s) acceptés sans un mot "
                    "d'explication. L'acceptation est tracée et signée, donc "
                    "elle suffit à clôturer — mais « accepté » sans phrase est "
                    "une décision que personne ne saura défendre devant un "
                    "contrôle."
                ),
                where=WHERE["ACCEPTED_WITHOUT_COMMENT"],
            )
        )
    else:
        items.append(
            ChecklistItem(
                code="ACCEPTED_WITHOUT_COMMENT",
                label=LABELS["ACCEPTED_WITHOUT_COMMENT"],
                state=ChecklistState.DONE,
                detail="Chaque acceptation porte son explication.",
            )
        )

    if ai_suggestions_untouched:
        items.append(
            ChecklistItem(
                code="AI_SUGGESTIONS_UNTOUCHED",
                label=LABELS["AI_SUGGESTIONS_UNTOUCHED"],
                state=ChecklistState.ATTENTION,
                detail=(
                    f"{ai_suggestions_untouched} suggestion(s) de cause n'ont "
                    "été ni retenues ni écartées. Ce n'est pas un manque au "
                    "sens du contrôle — ces écarts ne sont pas matériels — mais "
                    "c'est un travail commencé et laissé là."
                ),
                where=WHERE["AI_SUGGESTIONS_UNTOUCHED"],
            )
        )

    if sheets_changed_after_consolidation:
        items.append(
            ChecklistItem(
                code="SHEETS_CHANGED_AFTER_CONSOLIDATION",
                label=LABELS["SHEETS_CHANGED_AFTER_CONSOLIDATION"],
                state=ChecklistState.ATTENTION,
                detail=(
                    "Des lignes de feuille ont été modifiées après la dernière "
                    "consolidation enregistrée. Les quantités consolidées ne "
                    "sont donc plus celles des feuilles : relancez la "
                    "consolidation, ou vérifiez que la modification était sans "
                    "effet sur les totaux."
                ),
                where=WHERE["SHEETS_CHANGED_AFTER_CONSOLIDATION"],
            )
        )
    else:
        items.append(
            ChecklistItem(
                code="SHEETS_CHANGED_AFTER_CONSOLIDATION",
                label=LABELS["SHEETS_CHANGED_AFTER_CONSOLIDATION"],
                state=ChecklistState.DONE,
                detail="La consolidation reflète l'état des feuilles.",
            )
        )

    # ---- ce que les phases précédentes ont déjà exigé ------------------------
    #
    # Ces trois-là gardent l'entrée en analyse, donc ils sont vrais depuis
    # longtemps quand on vient clôturer. Les montrer n'est pas une redite : la
    # liste est un état des lieux du dossier, et un dossier dont on ne dit pas
    # que le stock ERP est gelé se relit mal six mois plus tard.

    items.append(
        ChecklistItem(
            code="BOOK_STOCK_FROZEN",
            label=LABELS["BOOK_STOCK_FROZEN"],
            state=ChecklistState.DONE if book_stock_frozen else ChecklistState.BLOCKING,
            detail=(
                "Le stock ERP est figé : les écarts sont reproductibles."
                if book_stock_frozen
                else "Le stock ERP n'a jamais été gelé."
            ),
        )
    )
    items.append(
        ChecklistItem(
            code="JOURNALS_POSTED",
            label=LABELS["JOURNALS_POSTED"],
            state=ChecklistState.DONE if not journals_pending else ChecklistState.BLOCKING,
            detail=(
                "Tous les journaux sont postés ou forcés au stock ERP."
                if not journals_pending
                else f"{journals_pending} journal(aux) ne sont pas postés."
            ),
        )
    )
    items.append(
        ChecklistItem(
            code="ZONES_DONE",
            label=LABELS["ZONES_DONE"],
            state=ChecklistState.DONE if not zones_open else ChecklistState.BLOCKING,
            detail=(
                "Toutes les zones GENERIQUE sont terminées."
                if not zones_open
                else f"{zones_open} zone(s) ne sont pas terminées."
            ),
        )
    )

    items.sort(key=lambda item: _ORDER[item.state])
    return items
