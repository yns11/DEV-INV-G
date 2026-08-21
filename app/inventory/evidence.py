"""Archivage des pièces justificatives dans un volume Unity Catalog.

Ce que l'inventaire produit de plus fragile, ce ne sont pas les chiffres : c'est
ce qui les justifie. Un écart de quarante mille euros signé par un contrôleur se
défend six mois plus tard avec la feuille manuscrite qui l'a produit, ou ne se
défend pas. Or le conteneur de l'application est éphémère : le fichier chargé y
vit le temps de la requête et disparaît avec elle.

D'où ce module. Chaque fichier reçu — export ERP, scan de feuille — est déposé
dans le volume avant d'être oublié, et son chemin est écrit à côté de ce qu'il a
produit : ``import_batch.storage_path`` pour un chargement,
``count_sheet.evidence_path`` pour une feuille scannée.

**L'archivage ne fait jamais échouer ce qu'il accompagne.** Volume absent, droit
manquant, API indisponible : la méthode le journalise et renvoie ``None``. Le
chargement, lui, aboutit. C'est un choix, et il tient en une phrase : perdre un
import de deux cent mille lignes parce que l'archive est en panne coûterait bien
plus que de ne pas archiver le fichier. Les deux colonnes sont donc nullables,
et l'écran distingue « pas de pièce » de « pièce archivée ».

**Le chemin se lit sans l'application.** Un volume se parcourt depuis l'espace
de travail, et quelqu'un qui cherche la feuille d'une campagne doit la trouver
sans requête SQL :

    /Volumes/<catalogue>/<schéma>/<volume>/<campagne>/<nature>/<horodatage>-<nom>

L'horodatage précède le nom pour que l'ordre alphabétique du dossier soit
l'ordre chronologique — c'est celui dans lequel on cherche.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import unicodedata
from typing import Any

from .config import Settings
from .errors import NotFoundError

log = logging.getLogger(__name__)

__all__ = ["EvidenceStore", "safe_name"]

#: Longueur maximale du nom de fichier conservé dans le chemin. Un scanner
#: produit volontiers des noms de cent cinquante caractères ; au-delà de
#: soixante on ne lit plus rien de plus, et le chemin complet reste manipulable.
_NAME_MAX = 60

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str, *, fallback: str = "fichier") -> str:
    """Un segment de chemin sûr, tiré d'un nom saisi par un humain.

    Les libellés de campagne et les noms de scan portent des accents, des
    espaces et parfois une barre oblique. Une barre oblique changerait
    l'arborescence du volume, ce qui n'est pas un détail cosmétique : le fichier
    partirait ailleurs que là où le chemin enregistré prétend qu'il est.

    >>> safe_name("Inventaire T3 / atelier")
    'Inventaire-T3-atelier'
    >>> safe_name("relevé n°4.pdf")
    'releve-n4.pdf'
    >>> safe_name("///")
    'fichier'
    """
    flat = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    cleaned = _UNSAFE.sub("-", flat).strip("-.")
    return cleaned[:_NAME_MAX] or fallback


class EvidenceStore:
    """Dépose et relit les pièces justificatives d'une campagne.

    Le client est injectable pour que la suite de tests n'ait besoin ni d'un
    espace de travail ni du SDK — la même règle que la lecture ERP.
    """

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    # ------------------------------------------------------------------ état

    @property
    def available(self) -> bool:
        """Si un dépôt peut être tenté.

        Ne vérifie que la configuration : joindre l'espace de travail pour le
        savoir coûterait un aller-retour à chaque import, et l'échec réel est
        de toute façon rattrapé au dépôt.
        """
        return self._settings.evidence_configured

    @property
    def root(self) -> str:
        return self._settings.uc_volume_path

    # ----------------------------------------------------------------- écrit

    def put(
        self,
        payload: bytes,
        *,
        campaign_code: str,
        kind: str,
        filename: str,
        at: dt.datetime | None = None,
    ) -> str | None:
        """Archive *payload* et renvoie son chemin, ou ``None`` si impossible.

        ``kind`` sépare les natures de pièces dans le volume (« imports »,
        « scans ») : c'est ce qui permet de retrouver toutes les feuilles
        scannées d'une campagne sans les trier une à une.
        """
        if not self.available:
            return None
        if not payload:
            return None

        path = self.path_for(
            campaign_code=campaign_code, kind=kind, filename=filename, at=at
        )
        try:
            self._files().upload(path, payload, overwrite=True)
        except Exception as exc:
            # Journalisé en avertissement, pas en erreur : ce qui comptait —
            # les lignes chargées — a abouti. L'appelant ne le voit pas passer.
            log.warning(
                "Pièce justificative non archivée (%s) : %s — %s",
                path, type(exc).__name__, exc,
            )
            return None
        log.info("Pièce archivée : %s (%d octets)", path, len(payload))
        return path

    def path_for(
        self,
        *,
        campaign_code: str,
        kind: str,
        filename: str,
        at: dt.datetime | None = None,
    ) -> str:
        """Où *filename* sera déposé. Séparé de :meth:`put` pour être testable."""
        stamp = (at or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%S")
        return "/".join((
            self.root,
            safe_name(campaign_code, fallback="campagne"),
            safe_name(kind, fallback="divers"),
            f"{stamp}-{safe_name(filename)}",
        ))

    # ------------------------------------------------------------------- lit

    def get(self, path: str) -> bytes:
        """Relit une pièce archivée.

        Contrairement au dépôt, la relecture échoue franchement : elle répond à
        quelqu'un qui a cliqué sur « pièce jointe » et attend un fichier. Une
        réponse vide le laisserait croire que la pièce n'existe pas, alors
        qu'elle peut n'être qu'inaccessible.
        """
        self._assert_inside(path)
        try:
            response = self._files().download(path)
        except Exception as exc:
            log.error("Pièce illisible (%s) : %s", path, exc)
            raise NotFoundError(
                "Cette pièce justificative est introuvable dans l'archive. "
                "Elle a pu être déplacée ou supprimée du volume."
            ) from exc
        contents = getattr(response, "contents", response)
        data = contents.read() if hasattr(contents, "read") else contents
        return bytes(data)

    # --------------------------------------------------------------- interne

    def _assert_inside(self, path: str) -> None:
        """Refuse un chemin qui sortirait du volume de l'application.

        Le chemin vient de la base, donc d'un fichier que l'application a
        elle-même déposé — mais il transite par une URL, et une colonne texte
        n'est pas une garantie. Le seul endroit où cette vérification a un coût
        nul est ici, juste avant la lecture.
        """
        root = self.root.rstrip("/") + "/"
        if not path.startswith(root) or ".." in path:
            raise NotFoundError("Chemin de pièce justificative invalide.")

    def _files(self) -> Any:
        if self._client is not None:
            return self._client.files
        from databricks.sdk import WorkspaceClient

        self._client = WorkspaceClient()
        return self._client.files
