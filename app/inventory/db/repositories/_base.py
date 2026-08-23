"""Fondations partagées des dépôts.

Voir :mod:`inventory.db.repositories` pour les trois règles que
tous les dépôts appliquent.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..engine import Database


def new_id() -> str:
    """A fresh technical identifier (UUID4 as text)."""
    return str(uuid.uuid4())

class _Base:
    """Shared plumbing: connection access and row → model helpers."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # -- low-level helpers ---------------------------------------------------

    def _fetch_all(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> list[dict[str, Any]]:
        if conn is not None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, params)
                return list(cur.fetchall())
        with self.db.cursor() as cur:
            cur.execute(query, params)
            return list(cur.fetchall())

    def _fetch_one(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> dict[str, Any] | None:
        rows = self._fetch_all(query, params, conn=conn)
        return rows[0] if rows else None

    def _execute(
        self, query: str, params: Sequence[Any] | dict[str, Any] | None = None,
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.rowcount
        with self.db.cursor() as cur:
            cur.execute(query, params)
            return cur.rowcount

    def _execute_many(
        self, query: str, rows: Sequence[Sequence[Any]],
        *, conn: psycopg.Connection | None = None,
    ) -> int:
        if not rows:
            return 0
        if conn is not None:
            with conn.cursor() as cur:
                cur.executemany(query, rows)
                return cur.rowcount
        with self.db.cursor() as cur:
            cur.executemany(query, rows)
            return cur.rowcount

class _NullContext:
    """Adapt an existing connection to the ``with`` protocol."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection:
        return self._conn

    def __exit__(self, *exc: Any) -> bool:
        return False
