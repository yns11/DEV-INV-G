"""Les pièces justificatives gardées dans la base (migration 022).

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.

Ce dépôt est l'exception à la deuxième : il n'a pas de ``deleted_at``. Une pièce
justificative ne se supprime pas — c'est l'objet même de son archivage — et le
seul retrait qu'il connaisse est celui du fichier d'un octet que la sonde de
diagnostic vient d'écrire.
"""

from __future__ import annotations

from ._base import _Base

__all__ = ["EvidenceBlobRepository"]


class EvidenceBlobRepository(_Base):
    """Dépose et relit une pièce dans ``evidence_blob``."""

    def put(
        self,
        *,
        path: str,
        campaign_code: str,
        kind: str,
        filename: str,
        mime: str,
        sha256: str,
        content: bytes,
    ) -> bool:
        """Écrit la pièce. Rend vrai si elle a été insérée, faux si déjà là.

        Le chemin porte l'empreinte du contenu : un chemin déjà pris l'est par
        un fichier **identique**, c'est-à-dire par un re-dépôt du même scan
        après une erreur ailleurs. ``DO NOTHING`` est donc le comportement
        voulu, et l'appelant traite les deux cas de la même façon — la pièce est
        là, et c'est la bonne.
        """
        inserted = self._execute(
            "INSERT INTO evidence_blob "
            "(path, campaign_code, kind, filename, mime, sha256, size_bytes, "
            "content) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (path) DO NOTHING",
            (path, campaign_code, kind, filename, mime, sha256,
             len(content), content),
        )
        return bool(inserted)

    def get(self, path: str) -> bytes | None:
        """Le contenu archivé, ou ``None`` si ce chemin n'a rien."""
        row = self._fetch_one(
            "SELECT content FROM evidence_blob WHERE path = %s", (path,)
        )
        if row is None:
            return None
        # psycopg rend `bytea` en `bytes` ; `bytes()` reste juste si une version
        # future rendait un `memoryview`, et ne copie rien dans le cas nominal.
        return bytes(row["content"])

    def delete(self, path: str) -> int:
        """Retire une pièce. Pour la sonde de diagnostic, et pour elle seule."""
        return self._execute("DELETE FROM evidence_blob WHERE path = %s", (path,))
