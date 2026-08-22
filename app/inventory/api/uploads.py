"""Lire un fichier téléversé sans se laisser remplir la mémoire.

``await file.read()`` charge tout, puis on regarde la taille. Sur un conteneur
d'application qui dispose de quelques gigaoctets et qui sert plusieurs
utilisateurs à la fois, c'est l'ordre inverse de celui qu'il faut : le plafond
existait bien — ``INV_MAX_UPLOAD_BYTES`` — mais il n'était consulté qu'une fois
les octets en mémoire, et seulement sur la route d'import. Le scan d'une pile de
feuilles, la réconciliation et l'assistant lisaient sans rien regarder du tout.

Un fichier de trois gigaoctets, déposé par erreur ou non, était donc lu en
entier avant d'être refusé — quand il l'était. Le refus arrivait après le
dommage, et le dommage touchait aussi les requêtes des voisins.

Ce module lit par tranches et s'arrête **dès** que le plafond est franchi. Le
refus coûte alors la taille d'une tranche, pas celle du fichier.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..errors import ValidationError

log = logging.getLogger(__name__)

__all__ = ["read_upload"]

#: Taille d'une tranche de lecture. Assez grande pour que le surcoût d'appels
#: reste négligeable sur un fichier de plusieurs mégaoctets, assez petite pour
#: que le dépassement du plafond ne coûte qu'elle.
_CHUNK = 1 << 20  # 1 Mio


def _human(size: int) -> str:
    """Une taille en mégaoctets, telle qu'on la lit sur un écran."""
    return f"{size / 1e6:.1f} Mo" if size < 1e9 else f"{size / 1e9:.2f} Go"


async def read_upload(
    file: Any, *, what: str = "Le fichier", ceiling: int | None = None
) -> bytes:
    """Le contenu de *file*, ou un refus avant d'avoir tout lu.

    ``what`` nomme ce qui est refusé dans le message — « Le scan », « Le
    fichier », « La pièce jointe » — parce qu'un utilisateur qui dépose une pile
    de feuilles et un utilisateur qui charge un export ne cherchent pas au même
    endroit.

    ``ceiling`` remplace ``INV_MAX_UPLOAD_BYTES`` là où une route en a un plus
    strict — les pièces jointes de l'assistant, qui partent vers un modèle et
    non vers un tableur. La lecture s'arrête à la première tranche qui franchit
    le plafond retenu : la taille exacte du fichier n'est donc pas connue, et le
    message dit « plus de », ce qui est la vérité.
    """
    ceiling = get_settings().max_upload_bytes if ceiling is None else ceiling
    chunks: list[bytes] = []
    read = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        read += len(chunk)
        if read > ceiling:
            # Les tranches déjà lues sont relâchées ici plutôt qu'à la sortie de
            # la fonction : sur un fichier au bord du plafond, c'est la
            # différence entre soixante mégaoctets rendus tout de suite et
            # soixante mégaoctets retenus le temps que l'exception remonte.
            chunks.clear()
            log.warning(
                "Téléversement refusé : plus de %s reçus, plafond %s.",
                _human(read), _human(ceiling),
            )
            raise ValidationError(
                f"{what} dépasse la taille maximale acceptée "
                f"({_human(ceiling)}). La lecture a été interrompue : "
                "découpez le fichier, ou faites relever le plafond.",
                maxBytes=ceiling,
            )
        chunks.append(chunk)
    return b"".join(chunks)
