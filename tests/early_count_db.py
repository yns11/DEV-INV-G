"""Une base jetable pour les contrôles des comptages avancés.

Pas un module de test : pytest ne le collecte pas, les fichiers de contrôle
l'importent.

Pourquoi une base à part plutôt que le schéma partagé — deux raisons, apprises
l'une après l'autre. Un contrôle qui supprime son schéma en fin de course
emporte tous les autres avec lui. Et une mutation censée prouver quelque chose
meurt sur la garde d'empreinte des migrations déjà appliquées, c'est-à-dire pour
la mauvaise raison, dès que le contrôle écrit dans une base où elles le sont.

``Settings(pg_database=…)`` ne suffit pas : le champ porte l'alias ``PGDATABASE``
et la variable d'environnement l'emporte sur l'argument. D'où le passage par
l'environnement, et la vérification de ``current_database()`` avant d'écrire quoi
que ce soit.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest


def admin_dsn() -> str:
    host = os.environ.get("PGHOST")
    if not host:
        pytest.skip("PGHOST absent : pas de PostgreSQL pour ce contrôle")
    return (
        f"host={host} port={os.environ.get('PGPORT', '5432')} "
        f"user={os.environ.get('PGUSER', 'postgres')} "
        f"password={os.environ.get('PGPASSWORD', '')} dbname=postgres"
    )


@contextmanager
def disposable_database(name: str) -> Iterator[Any]:
    """Ouvrir une base neuve, migrée, et la supprimer en sortant."""
    psycopg = pytest.importorskip("psycopg")
    try:
        admin = psycopg.connect(admin_dsn(), autocommit=True)
    except Exception as exc:  # pragma: no cover - dépend de l'infrastructure
        pytest.skip(f"PostgreSQL injoignable : {exc}")

    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')

    from inventory.config import Settings
    from inventory.db.engine import Database
    from inventory.db.migrations import apply_all

    previous = os.environ.get("PGDATABASE")
    os.environ["PGDATABASE"] = name
    try:
        database = Database(Settings())
        with database.connection() as conn:
            reached = conn.execute("SELECT current_database() AS d").fetchone()["d"]
        assert reached == name, (
            f"Ce contrôle écrirait dans « {reached} », pas dans la base jetable."
        )
        apply_all(database)
        yield database
        database.close()
    finally:
        if previous is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = previous

    with psycopg.connect(admin_dsn(), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')


def make_campaign(db: Any, code: str) -> str:
    """Une campagne minimale, et son identifiant."""
    import uuid

    campaign_id = str(uuid.uuid4())
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO campaign (id, code, label, count_date, created_by) "
            "VALUES (%s, %s, '', current_date, 'test')",
            (campaign_id, code),
        )
    return campaign_id
