"""La trace : événements d'audit et lots d'import.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from ...domain.enums import (
    AuditAction,
)
from ...domain.models import (
    AuditEvent,
)
from ._base import _Base, new_id

# --------------------------------------------------------------------------- #
# Audit & imports
# --------------------------------------------------------------------------- #

class AuditRepository(_Base):
    """Append-only audit trail. Database rules make UPDATE/DELETE no-ops."""

    def record(
        self,
        *,
        campaign_id: str | None,
        actor: str,
        action: AuditAction | str,
        entity_type: str,
        entity_id: str = "",
        summary: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        event_id = new_id()
        self._execute(
            "INSERT INTO audit_event (id, campaign_id, actor, action, entity_type, "
            "entity_id, summary, before, after, request_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (event_id, campaign_id, actor, str(action), entity_type, entity_id,
             summary, Jsonb(before) if before else None,
             Jsonb(after) if after else None, request_id),
            conn=conn,
        )
        return event_id

    def list(
        self,
        campaign_id: str,
        *,
        entity_type: str | None = None,
        actor: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[AuditEvent]:
        clauses = ["campaign_id = %s"]
        params: list[Any] = [campaign_id]
        if entity_type:
            clauses.append("entity_type = %s")
            params.append(entity_type)
        if actor:
            clauses.append("actor = %s")
            params.append(actor)
        params += [limit, offset]
        rows = self._fetch_all(
            "SELECT id, campaign_id, at, actor, action, entity_type, entity_id, "
            f"summary, before, after, request_id FROM audit_event "
            f"WHERE {' AND '.join(clauses)} ORDER BY at DESC LIMIT %s OFFSET %s",
            params,
        )
        return [
            AuditEvent(
                id=str(r["id"]),
                campaign_id=str(r["campaign_id"]) if r["campaign_id"] else None,
                at=r["at"], actor=r["actor"], action=r["action"],
                entity_type=r["entity_type"], entity_id=r["entity_id"],
                summary=r["summary"], before=r["before"], after=r["after"],
                request_id=r["request_id"],
            )
            for r in rows
        ]

class ImportBatchRepository(_Base):
    """Provenance of every bulk load."""

    def create(
        self,
        *,
        campaign_id: str | None,
        target: str,
        filename: str,
        content_hash: str,
        storage_path: str | None,
        rows_received: int,
        rows_accepted: int,
        rows_rejected: int,
        report: dict[str, Any],
        imported_by: str,
        batch_id: str | None = None,
        conn: psycopg.Connection | None = None,
    ) -> str:
        """Enregistre la provenance d'un chargement.

        ``batch_id`` peut être imposé par l'appelant. Les imports qui **marquent
        les lignes chargées** avec un identifiant de lot — le stock ERP, l'écart
        backflush — le tirent avant d'écrire, puis passent le même ici. Sans
        cela, deux identifiants coexistaient : celui gravé dans les lignes et
        celui de la ligne d'historique, chacun désignant le même chargement sans
        qu'aucune requête ne puisse aller de l'un à l'autre. « D'où vient cette
        quantité » n'avait alors pas de réponse.
        """
        batch_id = batch_id or new_id()
        self._execute(
            "INSERT INTO import_batch (id, campaign_id, target, filename, "
            "content_hash, storage_path, rows_received, rows_accepted, rows_rejected, "
            "report, imported_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (batch_id, campaign_id, target, filename, content_hash, storage_path,
             rows_received, rows_accepted, rows_rejected, Jsonb(report), imported_by),
            conn=conn,
        )
        return batch_id

    def find_duplicate(
        self, campaign_id: str, target: str, content_hash: str
    ) -> dict[str, Any] | None:
        """Detect a byte-identical re-upload before it duplicates rows."""
        return self._fetch_one(
            "SELECT id, filename, imported_by, imported_at, rows_accepted "
            "FROM import_batch WHERE campaign_id = %s AND target = %s "
            "AND content_hash = %s ORDER BY imported_at DESC LIMIT 1",
            (campaign_id, target, content_hash),
        )

    def evidence_of(self, campaign_id: str, batch_id: str) -> dict[str, Any] | None:
        """Le fichier archivé d'un lot, s'il en a un.

        Filtré sur la campagne autant que sur le lot : l'identifiant vient de
        l'URL, et rien d'autre n'empêcherait de télécharger la pièce d'une
        campagne à laquelle on n'a pas affaire.
        """
        return self._fetch_one(
            "SELECT filename, storage_path FROM import_batch "
            "WHERE id = %s AND campaign_id = %s AND storage_path IS NOT NULL",
            (batch_id, campaign_id),
        )

    def list(self, campaign_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._fetch_all(
            "SELECT id, target, filename, storage_path, rows_received, rows_accepted, "
            "rows_rejected, report, imported_by, imported_at FROM import_batch "
            "WHERE campaign_id = %s ORDER BY imported_at DESC LIMIT %s",
            (campaign_id, limit),
        )

    def latest_per_target(self, campaign_id: str) -> list[dict[str, Any]]:
        """Le dernier chargement de chaque grille, et ce qu'il a refusé.

        La question posée à la clôture n'est pas « un chargement a-t-il déjà
        échoué » — dix rechargements successifs sont le déroulement normal d'une
        préparation — mais « l'état actuel de cette grille vient-il d'un
        chargement amputé ». Seul le dernier compte : celui d'avant a été
        remplacé, ses rejets avec.
        """
        return self._fetch_all(
            "SELECT DISTINCT ON (target) target, rows_rejected, rows_accepted, "
            "filename, imported_at "
            "FROM import_batch WHERE campaign_id = %s "
            "ORDER BY target, imported_at DESC",
            (campaign_id,),
        )
