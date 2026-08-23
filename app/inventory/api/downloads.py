"""Réponse HTTP d'un fichier à télécharger.

Partagée par les rapports et les pièces justificatives : deux routeurs, un seul
endroit où se règle la façon dont un navigateur reçoit un fichier.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse

from fastapi.responses import Response

__all__ = ["attachment"]

#: Ce qu'un nom de fichier ne peut pas porter dans un en-tête. Le guillemet et
#: la barre oblique inverse fermeraient la valeur entre guillemets ; le
#: retour à la ligne ouvrirait un en-tête supplémentaire.
_HEADER_UNSAFE = re.compile(r'[\\"\r\n\x00-\x1f\x7f]')


def _ascii_fallback(filename: str, *, default: str = "fichier") -> str:
    """La version ASCII du nom, pour le paramètre ``filename``.

    Un en-tête HTTP se transporte en latin-1. « relevé.xlsx » y passe encore,
    « scan №4.pdf » non : le caractère numéro n'a pas de représentation latin-1
    et l'encodage de la réponse échouerait — un téléchargement transformé en
    erreur serveur par le seul nom du fichier.

    D'où deux paramètres dans l'en-tête, ce que prévoit la RFC 6266 : celui-ci,
    replié en ASCII pour les clients anciens, et ``filename*`` qui porte le vrai
    nom en UTF-8. Les navigateurs actuels lisent le second.
    """
    flat = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    return _HEADER_UNSAFE.sub("", flat).strip() or default


def attachment(payload: bytes, filename: str, media_type: str) -> Response:
    """Réponse en pièce jointe, avec un nom de fichier au format RFC 6266.

    Les libellés de campagne portent des accents, et les scans arrivent avec le
    nom que leur a donné le copieur — c'est-à-dire n'importe lequel. Le nom est
    donc replié pour l'en-tête et redonné intact dans ``filename*``.
    """
    quoted = urllib.parse.quote(filename, safe="")
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_ascii_fallback(filename)}"; '
                f"filename*=UTF-8''{quoted}"
            ),
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
        },
    )
