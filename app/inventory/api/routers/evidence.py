"""Relecture des pièces justificatives archivées.

Deux natures, une même question : « montrez-moi le fichier d'où vient ce
chiffre ». Le chemin du volume n'est jamais renvoyé au navigateur — l'écran
demande la pièce d'un lot ou d'une feuille, et le serveur sait où elle est. Un
chemin dans une URL serait à la fois du jargon exposé à l'utilisateur et une
adresse que rien n'oblige à rester celle qu'on a écrite.
"""

from __future__ import annotations

import mimetypes
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ...services import EvidenceService
from ..deps import CampaignDep, evidence_service
from ..downloads import attachment

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["pièces justificatives"])

Evidence = Annotated[EvidenceService, Depends(evidence_service)]


def _guess_type(filename: str) -> str:
    """Le type du fichier archivé, deviné par son extension.

    Il n'est pas conservé en base : l'extension suffit, et une pièce qu'on ne
    sait pas nommer se télécharge très bien en flux d'octets.
    """
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


@router.get("/imports/{batch_id}/evidence", summary="Fichier d'origine d'un chargement")
def import_evidence(campaign: CampaignDep, batch_id: str, service: Evidence) -> Any:
    """Le fichier tel qu'il a été reçu, avant toute interprétation."""
    found = service.of_import(campaign, batch_id)
    return attachment(found.content, found.filename, _guess_type(found.filename))


@router.get("/sheets/{sheet_id}/evidence", summary="Scan d'origine d'une feuille")
def sheet_evidence(campaign: CampaignDep, sheet_id: str, service: Evidence) -> Any:
    """Le scan qui a produit les quantités lues par l'IA."""
    found = service.of_sheet(campaign, sheet_id)
    return attachment(found.content, found.filename, _guess_type(found.filename))


@router.get("/scans", summary="Les scans archivés de la campagne")
def archived_scans(campaign: CampaignDep, service: Evidence) -> list[dict[str, Any]]:
    """Ce que l'archive contient, pièce par pièce.

    L'archive existait et se téléchargeait déjà, mais seulement pour qui
    connaissait l'identifiant de la feuille qui la porte : impossible de savoir
    ce qu'elle contient, ni si elle contient quelque chose. Une archive qu'on ne
    peut pas énumérer ne se contrôle pas.

    La liste ne porte pas les octets — une pile de deux cents pages en pèse
    trente mégaoctets. Chaque ligne dit ce qu'il faut pour décider de l'ouvrir,
    et le téléchargement passe ensuite par la feuille, donc par la barrière de
    campagne déjà en place.
    """
    return service.scans(campaign)
