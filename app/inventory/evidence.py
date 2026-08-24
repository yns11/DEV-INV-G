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

**L'archivage échoue-t-il en silence ?** Cela dépend de ce qu'il archive, et
c'est l'appelant qui tranche par ``required``.

*Par défaut, non bloquant.* Un export ERP se relit dans l'ERP ; perdre un import
de deux cent mille lignes parce que le volume est en panne coûterait plus cher
que de ne pas archiver le fichier. Volume absent, droit manquant, API
indisponible : la méthode le journalise et renvoie ``None``, le chargement
aboutit. Les colonnes sont donc nullables, et l'écran distingue « pas de pièce »
de « pièce archivée ».

*Sur demande, bloquant.* ``required=True`` fait échouer l'opération. C'est le
régime des scans de feuilles manuscrites : le papier repart dans l'atelier et
finit à la benne, le modèle a lu ce qu'il a lu, et sans l'image, la quantité
n'a plus rien derrière elle. Écrire ces chiffres en sachant que la pièce qui
les justifie n'a pas été archivée reviendrait à fabriquer un comptage
invérifiable — précisément ce que l'application existe pour empêcher.

**Le chemin se lit sans l'application.** Un volume se parcourt depuis l'espace
de travail, et quelqu'un qui cherche la feuille d'une campagne doit la trouver
sans requête SQL :

    /Volumes/<cat>/<schéma>/<vol>/<campagne>/<nature>/<horodatage>-<abcdef12>-<nom>

L'horodatage précède le nom pour que l'ordre alphabétique du dossier soit
l'ordre chronologique — c'est celui dans lequel on cherche.

**Le fragment hexadécimal n'est pas décoratif.** Le chemin ne portait que
l'horodatage à la seconde et le nom du fichier, et le dépôt était fait en
``overwrite=True``. Deux scans nommés ``scan.pdf`` déposés dans la même seconde
— deux feuilles envoyées ensemble, un re-scan après correction — écrivaient au
même endroit : le second effaçait le premier, et la feuille dont la base
conservait le chemin pointait alors sur l'image d'une autre. Le fragment est
l'empreinte du contenu : deux fichiers différents ne peuvent plus se retrouver
au même chemin, et deux dépôts du **même** fichier convergent vers le même,
ce qui est le comportement voulu.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import logging
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .errors import NotFoundError, UpstreamError

log = logging.getLogger(__name__)

__all__ = [
    "ArchivedFile", "EvidenceStore", "archive_advice", "safe_name", "volume_of",
]

#: Longueur maximale du nom de fichier conservé dans le chemin. Un scanner
#: produit volontiers des noms de cent cinquante caractères ; au-delà de
#: soixante on ne lit plus rien de plus, et le chemin complet reste manipulable.
_NAME_MAX = 60

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Longueur du fragment d'empreinte inséré dans le chemin. Huit caractères
#: hexadécimaux, soit quatre milliards de valeurs : la collision demanderait
#: deux contenus distincts dont le sha256 partage ses trente-deux premiers bits,
#: dans la même seconde et sous le même nom. Plus long n'ajouterait rien qu'un
#: chemin moins lisible.
_DIGEST_CHARS = 8


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """Une pièce déposée, et de quoi vérifier plus tard que c'est bien elle.

    L'empreinte répond à la seule question qui compte au moment d'un contrôle :
    le fichier que je relis est-il celui que le modèle a lu ? Le chemin seul ne
    le dit pas — un volume se modifie depuis l'espace de travail.
    """

    path: str
    sha256: str
    size: int
    mime: str

    def __str__(self) -> str:  # pragma: no cover - confort de journalisation
        return self.path


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


def _looks_like_already_there(exc: Exception) -> bool:
    """L'échec est-il « ce chemin est déjà pris » ?

    Le SDK ne présente pas une exception dédiée : selon la version et le
    transport, c'est une ``AlreadyExists``, un 409, ou un message. Le chemin
    portant l'empreinte du contenu, un chemin pris l'est par un fichier
    identique — le distinguer d'une vraie panne évite de refuser un re-dépôt
    parfaitement légitime.
    """
    name = type(exc).__name__
    if name in {"AlreadyExists", "ResourceAlreadyExists", "FileExistsError"}:
        return True
    text = str(exc).lower()
    return "already exists" in text or "existe déjà" in text


def _described(path: str, payload: bytes, digest: str, filename: str) -> ArchivedFile:
    """Ce qui sera écrit à côté de ce que la pièce a produit."""
    guessed, _ = mimetypes.guess_type(filename)
    return ArchivedFile(
        path=path,
        sha256=digest,
        size=len(payload),
        mime=guessed or "application/octet-stream",
    )


def volume_of(path: str) -> tuple[str, str, str] | None:
    """Le catalogue, le schéma et le volume que ce chemin désigne.

    ``/Volumes/<cat>/<schéma>/<vol>/…`` — trois segments après ``/Volumes``.
    Les extraire permet d'écrire des GRANT copiables tels quels : « accordez
    WRITE VOLUME sur le volume » oblige encore à retrouver lequel.
    """
    parts = [p for p in path.split("/") if p]
    if len(parts) < 4 or parts[0] != "Volumes":
        return None
    return parts[1], parts[2], parts[3]


def archive_advice(exc: Exception, *, path: str, principal: str | None = None) -> str:
    """Pourquoi le dépôt a échoué, et le geste qui le débloque.

    « La pièce n'a pas pu être archivée » décrit l'effet et tait la cause. Or
    les causes possibles appellent des gestes qui n'ont rien à voir : accorder
    un droit, créer le volume, corriger une variable. Sans elle, la panne se
    diagnostique par aller-retour — et l'erreur remonte d'un scan lancé en
    arrière-plan, dont seul le message est conservé : ce que ce texte ne dit
    pas est perdu.

    Le service principal est nommé quand il est connu. Un administrateur qui
    lit « accordez WRITE VOLUME » sans savoir *à qui* doit encore le chercher,
    et l'application est la seule à connaître l'identité sous laquelle elle
    s'exécute — ce n'est pas celle de la personne connectée.

    Les **trois** privilèges sont donnés, jamais le dernier seul. Unity Catalog
    traverse la hiérarchie : ``WRITE VOLUME`` sans ``USE CATALOG`` ni
    ``USE SCHEMA`` ne donne rien, et le refus qui suit nomme le maillon
    manquant, pas celui qu'on vient d'accorder. Ce message a fait exactement
    cette erreur une fois : il conseillait le troisième, l'exploitant l'a posé,
    et le refus suivant réclamait le premier.
    """
    text = str(exc).lower()
    who = principal or "<le service principal de l'application>"

    if any(k in text for k in ("permission", "denied", "forbidden", "403")):
        parts = volume_of(path)
        if parts:
            catalog, schema, volume = parts
            grants = (
                f"GRANT USE CATALOG ON CATALOG {catalog} TO `{who}` ; "
                f"GRANT USE SCHEMA ON SCHEMA {catalog}.{schema} TO `{who}` ; "
                f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME "
                f"{catalog}.{schema}.{volume} TO `{who}`"
            )
        else:
            grants = (
                "GRANT USE CATALOG sur le catalogue, USE SCHEMA sur le schéma, "
                f"puis READ VOLUME, WRITE VOLUME sur le volume, à `{who}`"
            )
        return (
            f"Le dépôt est refusé sur « {path} ». Unity Catalog exige les "
            f"**trois** privilèges, et le dernier seul ne donne rien : {grants}. "
            "Le catalogue a beau être celui des tables, l'identité n'est pas la "
            "même — les tables sont écrites par le job sous l'identité qui le "
            "lance, le volume par l'application sous la sienne. Si le premier "
            "GRANT est lui-même refusé faute de MANAGE sur le catalogue, c'est "
            "son propriétaire qui doit le poser : « DESCRIBE CATALOG EXTENDED "
            f"{parts[0] if parts else '<catalogue>'} » le nomme."
        )
    if any(k in text for k in ("not found", "does not exist", "404", "no such")):
        return (
            f"Le chemin « {path} » est introuvable. Le volume n'existe pas "
            "encore — rejouez « make uc » — ou INV_UC_CATALOG / INV_UC_SCHEMA / "
            "INV_UC_VOLUME ne désignent pas celui qui a été créé."
        )
    if any(k in text for k in ("unauthenticated", "401", "invalid token")):
        return (
            "L'application n'a pas pu s'authentifier auprès de l'espace de "
            "travail. Vérifiez que l'app est bien déployée avec son service "
            "principal ; un jeton expiré se règle en la redémarrant."
        )
    return f"Échec du dépôt sur « {path} »."



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
        required: bool = False,
    ) -> ArchivedFile | None:
        """Archive *payload* et renvoie ce qui a été déposé, ou ``None``.

        ``kind`` sépare les natures de pièces dans le volume (« imports »,
        « scans ») : c'est ce qui permet de retrouver toutes les feuilles
        scannées d'une campagne sans les trier une à une.

        ``required`` dit ce qu'il advient d'un échec — voir l'en-tête du module.
        Sous ``required=True``, une archive indisponible, un droit manquant ou
        un volume non configuré lèvent :class:`UpstreamError` : l'opération ne
        doit pas écrire de chiffres qu'aucune pièce ne justifie.
        """
        if not payload:
            if required:
                raise UpstreamError(
                    "Fichier vide : il n'y a rien à archiver, et donc rien qui "
                    "justifierait les quantités lues."
                )
            return None
        if not self.available:
            if required:
                raise UpstreamError(
                    "L'archivage des pièces justificatives n'est pas configuré. "
                    "Cette opération produit des quantités qui doivent rester "
                    "vérifiables : déclarez le volume Unity Catalog avant de la "
                    "relancer."
                )
            return None

        digest = hashlib.sha256(payload).hexdigest()
        path = self.path_for(
            campaign_code=campaign_code, kind=kind, filename=filename,
            at=at, digest=digest,
        )
        try:
            # Jamais `overwrite=True` : le chemin porte désormais l'empreinte du
            # contenu, donc un chemin déjà pris ne peut l'être que par un
            # fichier **identique**. Écraser n'apporterait rien et masquerait le
            # jour où cette propriété cesserait d'être vraie.
            #
            # Un flux, pas des octets. `files.upload` déclare `contents:
            # BinaryIO` et certaines versions du SDK appellent `seekable()`
            # dessus pour savoir si elles peuvent rejouer la requête : des
            # octets nus y échouent sur un `AttributeError`, et l'archivage
            # n'aboutissait donc **jamais**. En silence pour un import — le
            # régime non bloquant journalise et rend `None` — et par un refus
            # pour un scan.
            self._files().upload(path, io.BytesIO(payload), overwrite=False)
        except Exception as exc:
            if _looks_like_already_there(exc):
                # Le même fichier, déposé deux fois. C'est le cas nominal d'un
                # re-scan à l'identique après une erreur ailleurs : la pièce est
                # là, et c'est la bonne.
                log.info("Pièce déjà archivée, contenu identique : %s", path)
                return _described(path, payload, digest, filename)
            if required:
                log.error(
                    "Pièce justificative obligatoire non archivée (%s) : %s — %s",
                    path, type(exc).__name__, exc,
                )
                advice = archive_advice(
                    exc, path=path,
                    principal=self._settings.service_principal_id,
                )
                raise UpstreamError(
                    "La pièce justificative n'a pas pu être archivée. "
                    "L'opération est interrompue : elle produirait des "
                    f"quantités que rien ne rattacherait au document lu. "
                    f"{advice} Détail : {type(exc).__name__} : {exc}",
                    cause=str(exc),
                    path=path,
                ) from exc
            # Journalisé en avertissement, pas en erreur : ce qui comptait —
            # les lignes chargées — a abouti. L'appelant ne le voit pas passer.
            log.warning(
                "Pièce justificative non archivée (%s) : %s — %s",
                path, type(exc).__name__, exc,
            )
            return None
        log.info("Pièce archivée : %s (%d octets)", path, len(payload))
        return _described(path, payload, digest, filename)

    def probe(self) -> dict[str, Any]:
        """L'archivage marchera-t-il ? Vérifié en déposant, pas en supposant.

        ``evidence_configured`` ne lit que la configuration. Elle disait donc
        « oui » à un conteneur dont le service principal n'a aucun droit sur le
        catalogue — et la panne n'apparaissait qu'au premier scan, c'est-à-dire
        le jour de l'inventaire, sur une feuille manuscrite déjà repartie à
        l'atelier.

        Cette sonde écrit un octet dans le volume et le retire. C'est le seul
        moyen de répondre : la traversée du catalogue, le droit sur le schéma
        et le droit d'écriture sur le volume sont trois refus distincts, et
        aucun ne se déduit de la configuration.

        Rendue par ``/api/health/evidence``, jamais par ``/api/health`` : un
        aller-retour par sonde de disponibilité serait payé toutes les
        secondes, pour une réponse qui ne change qu'au jour d'un GRANT.
        """
        if not self.available:
            return {
                "ok": False,
                "configured": False,
                "detail": (
                    "Aucun volume déclaré : INV_UC_CATALOG, INV_UC_SCHEMA et "
                    "INV_UC_VOLUME doivent l'être pour que les pièces soient "
                    "archivées."
                ),
            }
        path = f"{self.root.rstrip('/')}/_diagnostic/ecriture.probe"
        try:
            self._files().upload(path, io.BytesIO(b"."), overwrite=True)
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "path": path,
                "detail": (
                    f"{archive_advice(exc, path=path, principal=self._settings.service_principal_id)} "
                    f"Détail : {type(exc).__name__} : {exc}"
                ),
            }
        # Le retrait n'est pas la question posée : une sonde qui laisserait son
        # fichier serait pénible, une sonde qui échouerait *sur le retrait*
        # dirait « l'archivage ne marche pas » alors qu'il vient de marcher.
        try:
            self._files().delete(path)
        except Exception as exc:  # pragma: no cover - dépend du droit DELETE
            log.info("Fichier de diagnostic laissé en place (%s) : %s", path, exc)
        return {"ok": True, "configured": True, "path": path}

    def path_for(
        self,
        *,
        campaign_code: str,
        kind: str,
        filename: str,
        at: dt.datetime | None = None,
        digest: str = "",
    ) -> str:
        """Où *filename* sera déposé. Séparé de :meth:`put` pour être testable.

        ``digest`` est le sha256 du contenu ; ses premiers caractères entrent
        dans le nom déposé. C'est ce qui rend le chemin propre à un contenu, et
        non plus à une seconde et un nom de fichier.
        """
        stamp = (at or dt.datetime.now(dt.UTC)).strftime("%Y%m%dT%H%M%S")
        short = (digest or "0" * _DIGEST_CHARS)[:_DIGEST_CHARS]
        return "/".join((
            self.root,
            safe_name(campaign_code, fallback="campagne"),
            safe_name(kind, fallback="divers"),
            f"{stamp}-{short}-{safe_name(filename)}",
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
