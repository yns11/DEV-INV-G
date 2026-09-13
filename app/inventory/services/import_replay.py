"""Rejouer un chargement déjà fait, depuis le fichier qu'il a laissé.

Il n'y a pas de bouton « annuler un import », et il n'y en aura probablement
jamais : une ligne mise à jour en place ne garde pas son image d'avant, et
inventer un historique de valeurs pour ça coûterait une table, sa rétention et
son écran. Ce qui existe, et qui suffit dans presque tous les cas, c'est le
**fichier d'origine** : chaque chargement de fichier est archivé tel qu'il a été
reçu. Le rejouer remet les quantités qu'il portait, puisque l'import remplace.

Ce module ne fait donc rien de neuf. Il retire seulement l'étape manuelle —
retrouver le lot, télécharger la pièce, revenir à l'écran d'import, la
reprendre — qui séparait l'exploitant d'un retour en arrière que l'application
pouvait faire seule.

Ce qu'il ne rejoue pas
----------------------
**Un collage ou une lecture ERP.** Ils ne laissent pas de fichier : un collage
est déjà dans les lignes chargées, une lecture se refait par sa requête. Le
refus le dit plutôt que de laisser chercher un bouton absent.

**Le statut des journaux.** Un journal passé à POSTED par le chargement d'alors
le reste ; le rejeu ne le rouvre pas. C'est cohérent avec le reste — seul le
passage *vers* POSTED est gardé, le retour en cours se fait depuis l'écran — et
mieux vaut que le rejeu ne touche pas à ce qu'un humain a pu décider entre-temps.

Ce qu'il retrouve tout seul
---------------------------
Les bornes de période de l'écart backflush. Elles ne sont pas dans la table des
lots, mais dans le **rapport** que le lot a conservé, où l'import les a écrites.
Sans elles le rejeu porterait sur la période proposée du jour, c'est-à-dire sur
une autre question que celle qu'on rejoue — et rendrait un chiffre faux sans le
dire.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from ..domain.models import Campaign
from ..errors import NotFoundError, ValidationError

if TYPE_CHECKING:  # pragma: no cover - import circulaire à l'exécution seule
    from .import_batches import ImportOutcome
    from .import_service import ImportService

__all__ = ["PERIOD_TARGETS", "TARGET_METHODS", "replay_batch", "resolve_target"]

#: Cible d'import → méthode du service qui la traite.
#:
#: Déclarée ici plutôt que dans la route : le rejeu a besoin du même
#: aiguillage, et deux copies auraient fini par diverger sur ce qu'une cible
#: veut dire. La route l'importe.
TARGET_METHODS = {
    "items": "import_items",
    "boms": "import_boms",
    "book_stock": "import_book_stock",
    "count_journal_lines": "import_journal_lines",
    "count_sheets": "import_count_sheets",
    "adjustments": "import_adjustments",
    "backflush": "import_backflush",
    "locations": "import_locations",
}

#: Les grilles dont le chargement est qualifié par une période.
PERIOD_TARGETS = ("backflush",)


def resolve_target(target: str) -> str:
    """La méthode qui charge *target*, ou un refus qui nomme les cibles connues."""
    method = TARGET_METHODS.get(target)
    if method is None:
        raise ValidationError(
            f"Cible d'import inconnue : {target!r}.",
            allowed=sorted(TARGET_METHODS),
        )
    return method


def _period_of(target: str, report: dict[str, Any]) -> dict[str, Any]:
    """Les bornes que le lot d'origine avait, relues dans son rapport."""
    if target not in PERIOD_TARGETS:
        return {}
    details = report.get("details") or {}
    start, end = details.get("periodStart"), details.get("periodEnd")
    if not start or not end:
        raise ValidationError(
            "Ce chargement ne dit pas sur quelle période il portait : le "
            "rejouer le calculerait sur une autre, sans le dire. Rechargez-le "
            "depuis l'écran en choisissant les bornes.",
            target=target,
        )
    return {
        "period_start": dt.date.fromisoformat(str(start)),
        "period_end": dt.date.fromisoformat(str(end)),
    }


def replay_batch(
    service: ImportService, campaign: Campaign, batch_id: str
) -> ImportOutcome:
    """Repasser le fichier du lot *batch_id* par l'importeur qui l'avait lu.

    Aucune garde propre : le rejeu écrit exactement ce que l'import écrit, et
    c'est donc la garde de l'import qui doit décider — celle de la cible, pas
    une autre. Elle est franchie par la méthode appelée, comme pour un
    chargement ordinaire.
    """
    ctx = service.ctx
    row = ctx.imports.replayable(campaign.id, batch_id)
    if row is None:
        raise NotFoundError(
            "Ce chargement n'a pas de fichier archivé, il ne peut pas être "
            "rejoué. Les collages et les lectures ERP n'en produisent pas.",
            batchId=batch_id,
        )

    target = str(row["target"])
    method = resolve_target(target)
    # Les bornes d'abord : ce refus-là porte sur ce que le lot dit de lui-même,
    # et rien ne justifie d'aller chercher des octets pour l'apprendre.
    period = _period_of(target, row["report"] or {})

    payload = ctx.evidence.get(row["storage_path"])
    if payload is None:
        raise NotFoundError(
            "Le fichier de ce chargement est introuvable dans l'archive.",
            batchId=batch_id,
        )

    return getattr(service, method)(
        campaign,
        mode="file",
        payload=payload,
        filename=row["filename"] or f"{target}.bin",
        **period,
    )
