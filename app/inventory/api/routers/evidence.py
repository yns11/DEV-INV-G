"""Relecture des pièces justificatives archivées.

Deux natures, une même question : « montrez-moi le fichier d'où vient ce
chiffre ». Le chemin du volume n'est jamais renvoyé au navigateur — l'écran
demande la pièce d'un lot ou d'une feuille, et le serveur sait où elle est. Un
chemin dans une URL serait à la fois du jargon exposé à l'utilisateur et une
adresse que rien n'oblige à rester celle qu'on a écrite.
"""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter

from ...errors import NotFoundError
from ..deps import CampaignDep, Ctx
from ..downloads import attachment

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["pièces justificatives"])


def _guess_type(filename: str) -> str:
    """Le type du fichier archivé, deviné par son extension.

    Il n'est pas conservé en base : l'extension suffit, et une pièce qu'on ne
    sait pas nommer se télécharge très bien en flux d'octets.
    """
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


@router.get("/imports/{batch_id}/evidence", summary="Fichier d'origine d'un chargement")
def import_evidence(campaign: CampaignDep, batch_id: str, ctx: Ctx) -> Any:
    """Le fichier tel qu'il a été reçu, avant toute interprétation.

    C'est ce qui permet de rejouer un chargement contesté : les lignes en base
    sont le résultat d'une lecture, celui-ci en est la source.
    """
    row = ctx.imports.evidence_of(campaign.id, batch_id)
    if row is None:
        raise NotFoundError(
            "Ce chargement n'a pas de fichier archivé. Les collages et les "
            "lectures ERP n'en produisent pas."
        )
    filename = row["filename"] or "piece-jointe"
    return attachment(
        ctx.evidence.get(row["storage_path"]), filename, _guess_type(filename)
    )


@router.get("/sheets/{sheet_id}/evidence", summary="Scan d'origine d'une feuille")
def sheet_evidence(campaign: CampaignDep, sheet_id: str, ctx: Ctx) -> Any:
    """Le scan qui a produit les quantités lues par l'IA.

    Une valeur extraite d'une image se défend en montrant l'image. Quand la
    feuille vient d'un scan groupé, c'est la pile entière qui est renvoyée :
    c'est bien ce document-là qui la justifie.
    """
    sheet = ctx.sheets.get_sheet(sheet_id)
    if sheet.campaign_id != campaign.id:
        raise NotFoundError("Feuille introuvable dans cette campagne.")
    if not sheet.evidence_path:
        raise NotFoundError(
            "Cette feuille n'a pas de scan archivé. Ses quantités ont été "
            "saisies à la main, ou lues avant la mise en service de l'archive."
        )
    filename = sheet.evidence_path.rsplit("/", 1)[-1]
    return attachment(
        ctx.evidence.get(sheet.evidence_path), filename, _guess_type(filename)
    )
