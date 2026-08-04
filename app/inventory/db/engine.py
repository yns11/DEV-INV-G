"""Lakebase (PostgreSQL) connection management.

Lakebase authenticates with a short-lived OAuth token used as the Postgres
password. Two consequences drive the design here:

1. **The password rotates.** A pool created once with a static password would
   start failing roughly an hour after start-up. The pool's connection kwargs
   are refreshed in place, and ``max_lifetime`` is set below the token validity
   so every connection is recycled with a fresh credential before it expires.
2. **The app container is small** (2 vCPU / 6 GB). The pool stays deliberately
   narrow; heavy analytical work belongs on the SQL warehouse, not here.

When ``PGPASSWORD`` is injected directly (local development, or a Lakebase role
with password authentication) the token machinery is bypassed entirely.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import Settings, get_settings
from ..errors import UpstreamError

log = logging.getLogger(__name__)

__all__ = ["Database", "get_database", "reset_database"]

#: Lakebase OAuth tokens are valid for one hour. Recycle connections well before
#: that, and refresh the cached credential earlier still.
_CONNECTION_MAX_LIFETIME_S = 45 * 60
_TOKEN_REFRESH_MARGIN_S = 10 * 60
_TOKEN_TTL_S = 60 * 60


class _CredentialProvider:
    """Supplies (and refreshes) the Postgres password.

    Thread-safe: several request threads may hit an expired token at once, and
    only one of them should call the Databricks API.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._static = settings.pg_password
        #: Endpoint resource path, resolved lazily and then reused.
        self._endpoint: str | None = None

    @property
    def is_static(self) -> bool:
        return bool(self._static)

    def get(self, *, force: bool = False) -> str:
        if self._static:
            return self._static
        now = time.monotonic()
        if not force and self._token and now < self._expires_at:
            return self._token
        with self._lock:
            now = time.monotonic()
            if not force and self._token and now < self._expires_at:
                return self._token
            self._token = self._mint()
            self._expires_at = now + _TOKEN_TTL_S - _TOKEN_REFRESH_MARGIN_S
            log.info("Lakebase credential refreshed")
            return self._token

    def _resolve_endpoint(self, client: Any) -> str:
        """Find the endpoint resource path backing ``PGHOST``.

        Attaching a ``postgres`` resource publishes connection *parameters*
        (PGHOST, PGDATABASE, PGUSER, ...) but not the endpoint's resource path,
        and only that path can be used to mint a credential. It is recovered
        here by listing the branch's endpoints and matching PGHOST against the
        hosts each one advertises — direct or pooled, read-write or read-only.
        Matching beats assuming the auto-provisioned name ``primary``, which
        stops being true the moment somebody adds a replica or renames it.

        Resolved once per process and cached: the mapping cannot change without
        the app being restarted with a different PGHOST.
        """
        if self._endpoint is not None:
            return self._endpoint

        branch = self._settings.lakebase_branch
        if not branch:
            raise UpstreamError(
                "Endpoint Lakebase inconnu : renseignez INV_LAKEBASE_BRANCH "
                "(projects/<projet>/branches/<branche>) ou INV_LAKEBASE_ENDPOINT."
            )

        try:
            endpoints = list(client.postgres.list_endpoints(branch))
        except Exception as exc:
            # Typically a permission gap: CAN_CONNECT_AND_CREATE on the database
            # does not imply the right to enumerate the project's endpoints.
            raise UpstreamError(
                f"Impossible de lister les endpoints de « {branch} ». Donnez au "
                "service principal l'accès au projet Lakebase, ou renseignez "
                "INV_LAKEBASE_ENDPOINT pour éviter cette recherche.",
                cause=str(exc),
            ) from exc

        host = (self._settings.pg_host or "").lower()
        fallback: str | None = None
        for endpoint in endpoints:
            status = getattr(endpoint, "status", None)
            hosts = getattr(status, "hosts", None)
            advertised = {
                str(getattr(hosts, attr, "") or "").lower()
                for attr in (
                    "host",
                    "read_only_host",
                    "read_only_pooled_host",
                    "read_write_pooled_host",
                )
            }
            if host and host in advertised:
                self._endpoint = endpoint.name
                return endpoint.name
            # Keep the first read-write endpoint in case PGHOST is a form the
            # API does not advertise; better a working guess than a hard stop.
            if fallback is None and "READ_WRITE" in str(
                getattr(status, "endpoint_type", "")
            ):
                fallback = endpoint.name

        if fallback:
            log.warning(
                "PGHOST %s not advertised by any endpoint of %s — falling back "
                "to the read-write endpoint %s",
                host,
                branch,
                fallback,
            )
            self._endpoint = fallback
            return fallback

        raise UpstreamError(
            f"Aucun endpoint Lakebase de la branche « {branch} » ne correspond "
            f"à PGHOST ({host}). Vérifiez que la ressource « postgres » pointe "
            "sur la bonne branche, ou renseignez INV_LAKEBASE_ENDPOINT."
        )

    def _mint(self) -> str:
        """Ask Databricks for a database credential for this app's identity.

        Lakebase Autoscaling mints the token against an endpoint resource path
        (see :meth:`_resolve_endpoint`). The retired provisioned tier used
        instance names instead; calling that API with a hostname fails with
        ``Database instance '<host>' not found``, so it is not attempted.
        """
        try:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient()
            endpoint = self._settings.lakebase_endpoint or self._resolve_endpoint(
                client
            )
            credential = client.postgres.generate_database_credential(endpoint)
            token = getattr(credential, "token", None)
            if not token:
                raise UpstreamError(
                    "Databricks n'a pas renvoyé de credential Lakebase."
                )
            return token
        except UpstreamError:
            raise
        except Exception as exc:  # pragma: no cover - depends on the workspace
            raise UpstreamError(
                "Impossible d'obtenir un credential Lakebase. Vérifiez que la "
                "ressource « postgres » est bien attachée à l'application.",
                cause=str(exc),
            ) from exc


def _connection_class(credentials: _CredentialProvider) -> type[psycopg.Connection]:
    """A connection class that fetches the current token as it dials.

    The pool opens connections from a fixed ``kwargs`` dict, so a password
    written there at start-up is the password every later connection uses —
    including the ones the pool opens on its own to refill itself or to replace
    one retired by ``max_lifetime``. An hour in, the token has expired and each
    of those background attempts fails with ``OAuth: User is not authorized``,
    filling the log with warnings while foreground requests limp along on a
    refresh-after-failure retry.

    Reading the credential at dial time removes the staleness entirely: the
    provider hands out a cached token and mints a new one shortly before the old
    expires, so every connection — foreground or background — starts valid.
    """

    class _LakebaseConnection(psycopg.Connection):  # type: ignore[type-arg]
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any) -> Any:
            kwargs["password"] = credentials.get()
            return super().connect(conninfo, **kwargs)

    return _LakebaseConnection


class Database:
    """Thin, explicit wrapper around a psycopg connection pool.

    Exposes only what the repositories need: a cursor context manager, a
    transaction context manager, and the migration runner. Deliberately not an
    ORM — the SQL in this project is short, reviewable and hot-path critical.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.lakebase_configured:
            raise UpstreamError(
                "Lakebase n'est pas configuré (PGHOST / PGDATABASE / PGUSER absents). "
                "Attachez une ressource « postgres » à l'application."
            )
        self._credentials = _CredentialProvider(self._settings)
        self._pool = self._build_pool()
        self._schema = self._settings.pg_schema

    # ------------------------------------------------------------------ pool

    def _connection_kwargs(self) -> dict[str, Any]:
        s = self._settings
        return {
            "host": s.pg_host,
            "port": s.pg_port,
            "dbname": s.pg_database,
            "user": s.pg_user,
            "password": self._credentials.get(),
            "sslmode": s.pg_sslmode,
            "application_name": s.app_name,
            "row_factory": dict_row,
            # Fail fast rather than hanging a request until the 120 s proxy
            # timeout, which surfaces as an unexplained 504 with empty logs.
            "connect_timeout": 10,
            "options": f"-c search_path={s.pg_schema},public -c statement_timeout=60000",
        }

    def _build_pool(self) -> ConnectionPool:
        s = self._settings
        return ConnectionPool(
            conninfo="",
            kwargs=self._connection_kwargs(),
            connection_class=_connection_class(self._credentials),
            min_size=s.pg_pool_min,
            max_size=s.pg_pool_max,
            max_lifetime=_CONNECTION_MAX_LIFETIME_S,
            max_idle=5 * 60,
            timeout=15.0,
            name="lakebase",
            open=True,
            check=ConnectionPool.check_connection,
        )

    def _refresh_credentials(self) -> None:
        """Rotate the password used for *new* connections.

        ``pool.kwargs`` is part of psycopg_pool's public surface; mutating it is
        the supported way to change connection parameters without tearing the
        pool down and dropping in-flight work.
        """
        if self._credentials.is_static:
            return
        self._pool.kwargs["password"] = self._credentials.get(force=True)

    # ------------------------------------------------------------- execution

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """Borrow a connection, retrying once on an authentication failure."""
        try:
            with self._pool.connection() as conn:
                yield conn
        except psycopg.OperationalError as exc:
            if not _looks_like_auth_failure(exc):
                raise UpstreamError(
                    "Connexion Lakebase impossible.", cause=str(exc)
                ) from exc
            log.warning("Lakebase auth failure, refreshing credential and retrying")
            self._refresh_credentials()
            try:
                with self._pool.connection() as conn:
                    yield conn
            except Exception as retry_exc:  # pragma: no cover - depends on infra
                raise UpstreamError(
                    "Connexion Lakebase impossible après renouvellement du jeton.",
                    cause=str(retry_exc),
                ) from retry_exc

    @contextmanager
    def cursor(self) -> Iterator[psycopg.Cursor]:
        """A cursor inside its own transaction, committed on clean exit."""
        with self.connection() as conn, conn.transaction(), conn.cursor() as cur:
            yield cur

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """An explicit transaction spanning several repository calls.

        Use this whenever a single user action touches more than one table —
        importing a file *and* writing its audit event, for instance — so a
        failure can never leave half of an operation behind.
        """
        with self.connection() as conn, conn.transaction():
            yield conn

    # ------------------------------------------------------------ lifecycle

    def ping(self) -> bool:
        """Cheap liveness probe used by ``/api/health``."""
        try:
            with self.cursor() as cur:
                cur.execute("SELECT 1 AS ok")
                return cur.fetchone() is not None
        except Exception:
            log.exception("Lakebase ping failed")
            return False

    def ensure_schema(self) -> None:
        """Create the application schema if the role is allowed to."""
        with self.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self._schema)
                )
            )

    def close(self) -> None:
        self._pool.close()

    @property
    def schema(self) -> str:
        return self._schema

    @property
    def stats(self) -> dict[str, Any]:
        return dict(self._pool.get_stats())


def _looks_like_auth_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "password authentication failed",
            "authentication failed",
            "expired",
            "invalid_grant",
            "28p01",
        )
    )


# --------------------------------------------------------------------------- #
# Process-wide singleton
# --------------------------------------------------------------------------- #

_db: Database | None = None
_db_lock = threading.Lock()


def get_database(settings: Settings | None = None) -> Database:
    """Return the shared :class:`Database`, creating it on first use."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database(settings)
    return _db


def reset_database() -> None:
    """Dispose of the shared pool. Used by tests and by graceful shutdown."""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
            _db = None
