"""Forward-only SQL migration runner.

Deliberately tiny: numbered ``.sql`` files applied in order, each inside its own
transaction, recorded in ``schema_migration``. No down-migrations — rolling back
a schema in a system whose whole promise is an immutable audit trail is a
guarantee nobody should offer.

Migrations run at application start-up (see ``inventory.api.lifespan``); they are
idempotent, so a cold start of several app replicas is safe.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..engine import Database

log = logging.getLogger(__name__)

__all__ = ["MIGRATIONS_DIR", "discover", "apply_all", "applied_versions"]

#: Advisory-lock key guarding the migration ledger. Any constant works as long
#: as it is stable and unique to this concern; it is namespaced by the database,
#: not by the schema, so a second application sharing the database would only
#: ever wait on it briefly at start-up.
_LOCK_KEY = 0x1E5E_4F03

MIGRATIONS_DIR = Path(__file__).parent

_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version     TEXT PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms INTEGER     NOT NULL DEFAULT 0
);
"""


def discover(directory: Path | None = None) -> list[tuple[str, Path]]:
    """Migration files sorted by version, as ``(version, path)`` pairs."""
    directory = directory or MIGRATIONS_DIR
    files = sorted(directory.glob("*.sql"))
    return [(f.stem, f) for f in files]


def _checksum(path: Path) -> str:
    """Fingerprint of a migration, insensitive to line endings.

    The first version hashed the raw bytes, which made the fingerprint depend on
    how the file happened to be checked out: the same migration deployed from a
    Windows working copy and from a Linux one produced two different sums, and
    the runner would have reported the second as "modified after application".
    Nothing about a CRLF changes what Postgres executes.

    Everything else is still compared byte for byte. A migration that has run
    must not change, and this function is not the place to be generous about it.
    """
    return hashlib.sha256(_normalised(path)).hexdigest()


def _normalised(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _legacy_checksum(path: Path) -> str:
    """The raw-bytes fingerprint, as recorded before the normalisation.

    Kept so an existing ledger keeps validating: a row written by an older
    version is recognised, then re-stamped in the new form. Without it, every
    deployed environment would see all of its migrations as modified at once —
    a false alarm that would drown the true one this check exists to raise.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_crlf_checksum(path: Path) -> str:
    """The raw-bytes fingerprint of the same file with Windows line endings.

    A ledger written from a Windows working copy recorded that form; the file in
    this container has Unix endings. The two describe the same migration.
    """
    return hashlib.sha256(_normalised(path).replace(b"\n", b"\r\n")).hexdigest()


def _restamp(db: Database, version: str, checksum: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE schema_migration SET checksum = %s WHERE version = %s",
            (checksum, version),
        )


def applied_versions(db: Database) -> dict[str, str]:
    """Versions already applied, mapped to their recorded checksum."""
    with db.cursor() as cur:
        cur.execute(_BOOTSTRAP)
        cur.execute("SELECT version, checksum FROM schema_migration")
        return {row["version"]: row["checksum"] for row in cur.fetchall()}


def apply_all(db: Database, *, directory: Path | None = None) -> list[str]:
    """Apply every pending migration. Returns the versions that were applied.

    A file whose checksum differs from the recorded one is a *modified* applied
    migration: that is a mistake (someone edited history), so it raises rather
    than silently re-running or ignoring it.
    """
    import time

    db.ensure_schema()
    already = applied_versions(db)
    applied: list[str] = []

    for version, path in discover(directory):
        checksum = _checksum(path)
        if version in already:
            recorded = already[version]
            if recorded == checksum:
                continue
            # A row written before the normalisation: the file is unchanged, only
            # the way it was fingerprinted. Re-stamp it and carry on.
            if recorded in (_legacy_checksum(path), _legacy_crlf_checksum(path)):
                _restamp(db, version, checksum)
                log.info("Migration %s re-empreintée (format hérité)", version)
                continue
            raise RuntimeError(
                f"La migration {version} a été modifiée après application "
                f"(checksum {recorded[:12]}… ≠ {checksum[:12]}…). "
                "Créez une nouvelle migration plutôt que d'éditer celle-ci."
            )

        log.info("Applying migration %s", version)
        started = time.monotonic()
        statement = path.read_text(encoding="utf-8")
        with db.transaction() as conn, conn.cursor() as cur:
            # Serialise across containers. A rolling deploy overlaps the old and
            # the new instance, and both run this at start-up: without the lock
            # they execute the same script concurrently and the loser dies on a
            # duplicate key in the ledger, leaving an app that never becomes
            # ready. The lock is transaction-scoped, so it is released whatever
            # happens next.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            # Re-read inside the lock: the instance that waited must not replay
            # a migration the winner has just committed.
            cur.execute("SELECT 1 FROM schema_migration WHERE version = %s", (version,))
            if cur.fetchone():
                log.info("Migration %s applied concurrently by another instance", version)
                continue
            # No parameters, so psycopg happily runs the whole multi-statement
            # script; the surrounding transaction makes it all-or-nothing.
            cur.execute(statement)  # type: ignore[arg-type]
            cur.execute(
                "INSERT INTO schema_migration (version, checksum, duration_ms) "
                "VALUES (%s, %s, %s)",
                (version, checksum, int((time.monotonic() - started) * 1000)),
            )
        applied.append(version)
        log.info("Migration %s applied in %.0f ms", version,
                 (time.monotonic() - started) * 1000)

    return applied
