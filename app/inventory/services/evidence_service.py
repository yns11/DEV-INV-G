"""Retrouver la pièce justificative d'un chargement ou d'une feuille.

Deux natures, une même question : « montrez-moi le fichier d'où vient ce
chiffre ». Ce qui les sépare de la simple lecture d'un octet, c'est **la
barrière de campagne** : une pièce se demande toujours au nom d'une campagne, et
c'est ici qu'on vérifie qu'elle lui appartient bien.

Cette vérification vivait dans une route. Elle y était juste, et pourtant mal
placée : un contrôle d'accès écrit dans le routeur n'est appliqué que par les
appelants qui passent par ce routeur, et rien ne le rappelle à celui qui ajoute
la deuxième route.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..domain.models import Campaign
from ..errors import NotFoundError
from .context import ServiceContext

log = logging.getLogger(__name__)

__all__ = ["ArchivedEvidence", "EvidenceService"]


@dataclass(frozen=True, slots=True)
class ArchivedEvidence:
    """Le contenu d'une pièce, et le nom sous lequel elle doit être servie.

    Le chemin du volume n'y figure pas : il n'a rien à faire dans une réponse,
    et le laisser sortir en ferait une adresse que quelqu'un finirait par
    fabriquer à la main.
    """

    content: bytes
    filename: str


class EvidenceService:
    """L'archive, lue au nom d'une campagne et jamais autrement."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    def of_import(self, campaign: Campaign, batch_id: str) -> ArchivedEvidence:
        """Le fichier tel qu'il a été reçu, avant toute interprétation.

        C'est ce qui permet de rejouer un chargement contesté : les lignes en
        base sont le résultat d'une lecture, celui-ci en est la source.
        """
        ctx = self.ctx
        row = ctx.imports.evidence_of(campaign.id, batch_id)
        if row is None:
            raise NotFoundError(
                "Ce chargement n'a pas de fichier archivé. Les collages et les "
                "lectures ERP n'en produisent pas."
            )
        filename = row["filename"] or "piece-jointe"
        return ArchivedEvidence(
            content=ctx.evidence.get(row["storage_path"]), filename=filename
        )

    def of_sheet(self, campaign: Campaign, sheet_id: str) -> ArchivedEvidence:
        """Le scan qui a produit les quantités lues par l'IA.

        Une valeur extraite d'une image se défend en montrant l'image. Quand la
        feuille vient d'un scan groupé, c'est la pile entière qui est renvoyée :
        c'est bien ce document-là qui la justifie.

        La feuille est refusée si elle n'est pas de cette campagne. Le message
        est le même que pour une feuille inexistante, et volontairement : dire
        « elle existe, mais ailleurs » apprend à qui essaie qu'un identifiant
        deviné a touché quelque chose.
        """
        ctx = self.ctx
        sheet = ctx.sheets.get_sheet(sheet_id)
        if sheet.campaign_id != campaign.id:
            raise NotFoundError("Feuille introuvable dans cette campagne.")
        if not sheet.evidence_path:
            raise NotFoundError(
                "Cette feuille n'a pas de scan archivé. Ses quantités ont été "
                "saisies à la main, ou lues avant la mise en service de l'archive."
            )
        return ArchivedEvidence(
            content=ctx.evidence.get(sheet.evidence_path),
            filename=sheet.evidence_path.rsplit("/", 1)[-1],
        )
