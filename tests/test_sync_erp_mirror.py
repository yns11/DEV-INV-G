"""Comment le job de synchronisation trouve la base qu'il doit écrire.

Un job Databricks n'est pas une App : aucune ressource ne lui est attachée, et
la plateforme ne lui injecte donc ni ``PGHOST`` ni ``PGUSER``. La première
version reprenait le contrat de l'application et s'arrêtait au premier
lancement, après avoir lu tout le référentiel — l'échec le plus coûteux
possible, puisqu'il arrive au bout du travail utile.

Ces tests pilotent un faux client SDK : ils vérifient ce que le job demande à
la plateforme et ce qu'il en déduit, sans workspace.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

JOB = Path(__file__).resolve().parents[1] / "jobs" / "sync_erp_mirror.py"


def load_job() -> Any:
    spec = importlib.util.spec_from_file_location("sync_erp_mirror", JOB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


sync = load_job()


class Args:
    """Les arguments de ligne de commande dont la connexion dépend."""

    def __init__(self, **overrides: Any) -> None:
        self.branch = "projects/inventaire/branches/production"
        self.pg_database = "databricks_postgres"
        self.pg_user = ""
        self.__dict__.update(overrides)


def endpoint(name: str, kind: str, host: str | None, **hosts: Any) -> Any:
    return type("E", (), {
        "name": name,
        "status": type("S", (), {
            "endpoint_type": kind,
            "hosts": type("H", (), {"host": host, **hosts})(),
        })(),
    })()


class FakeClient:
    def __init__(self, endpoints: list[Any], *, identity: str = "u@example.com",
                 token: str = "tok-1") -> None:
        self._endpoints = endpoints
        self._token = token
        self.credential_for: list[str] = []
        self.postgres = self
        self.current_user = self
        self._identity = identity

    def list_endpoints(self, branch: str) -> list[Any]:
        self.branch = branch
        return self._endpoints

    def generate_database_credential(self, endpoint_name: str) -> Any:
        self.credential_for.append(endpoint_name)
        return type("C", (), {"token": self._token})()

    def me(self) -> Any:
        return type("U", (), {"user_name": self._identity})()


READ_WRITE = [
    endpoint("projects/p/branches/b/endpoints/replica", "READ_ONLY", "ro.example"),
    endpoint("projects/p/branches/b/endpoints/primary", "READ_WRITE", "rw.example"),
]


class TestFindingTheDatabase:
    def test_the_endpoint_is_deduced_from_the_branch(self):
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(), client)
        assert "host=rw.example" in conninfo
        assert client.branch == "projects/inventaire/branches/production"

    def test_a_read_only_endpoint_is_never_chosen(self):
        """Il lirait sans broncher, puis refuserait le premier INSERT."""
        client = FakeClient([
            endpoint("projects/p/branches/b/endpoints/replica", "READ_ONLY", "ro.example")
        ])
        with pytest.raises(RuntimeError, match="écriture"):
            sync._lakebase_conninfo(Args(), client)

    def test_the_pooled_host_serves_when_the_direct_one_is_absent(self):
        client = FakeClient([
            endpoint("projects/p/branches/b/endpoints/primary", "READ_WRITE", None,
                     read_write_pooled_host="pool.example")
        ])
        assert "host=pool.example" in sync._lakebase_conninfo(Args(), client)

    def test_the_credential_is_minted_for_that_endpoint(self):
        """L'API prend un chemin de ressource ; un nom d'hôte échoue."""
        client = FakeClient(READ_WRITE)
        sync._lakebase_conninfo(Args(), client)
        assert client.credential_for == ["projects/p/branches/b/endpoints/primary"]

    def test_the_role_is_the_identity_running_the_job(self):
        client = FakeClient(READ_WRITE, identity="younes@societe.com")
        assert "user=younes@societe.com" in sync._lakebase_conninfo(Args(), client)

    def test_an_explicit_role_wins_over_the_running_identity(self):
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(pg_user="sync_bot"), client)
        assert "user=sync_bot" in conninfo

    def test_the_postgres_database_name_is_used_not_the_resource_id(self):
        """`databricks-postgres` est l'id de ressource ; la base est `databricks_postgres`."""
        conninfo = sync._lakebase_conninfo(Args(), FakeClient(READ_WRITE))
        assert "dbname=databricks_postgres" in conninfo

    def test_without_a_branch_it_says_what_to_pass(self):
        with pytest.raises(RuntimeError, match="--branch"):
            sync._lakebase_conninfo(Args(branch=""), FakeClient([]))

    def test_the_environment_still_wins_when_it_is_set(self, monkeypatch):
        """Exécution locale, ou rôle dédié sorti d'un secret scope."""
        monkeypatch.setenv("PGHOST", "localhost")
        monkeypatch.setenv("PGUSER", "app")
        monkeypatch.setenv("PGPASSWORD", "secret")
        client = FakeClient(READ_WRITE)
        conninfo = sync._lakebase_conninfo(Args(), client)
        assert "host=localhost" in conninfo and "user=app" in conninfo
        # Aucun appel à la plateforme : rien à découvrir.
        assert client.credential_for == []


class TestWhatTheJobSaysWhenItCannotWrite:
    """Les deux échecs attendus au premier lancement se ressemblent à l'écran
    et n'ont pas le même remède ; le job doit nommer le bon."""

    def test_an_identity_without_a_postgres_role_is_told_to_create_one(self):
        advice = sync._connection_advice(
            Exception('FATAL: role "u@example.com" does not exist')
        )
        assert "rôle Postgres" in advice and "Roles" in advice

    def test_a_refused_credential_points_at_the_connect_permission(self):
        advice = sync._connection_advice(
            Exception("FATAL: password authentication failed for user")
        )
        assert "CAN_CONNECT" in advice

    def test_a_missing_grant_points_at_the_migration_that_carries_it(self):
        advice = sync._write_advice(
            Exception("permission denied for table erp_base_article"), "inventory"
        )
        assert "migration 006" in advice

    def test_an_unexpected_failure_is_still_reported_verbatim(self):
        """Ne jamais avaler une cause qu'on n'a pas prévue."""
        assert "disque plein" in sync._connection_advice(Exception("disque plein"))
        assert "disque plein" in sync._write_advice(Exception("disque plein"), "inventory")


class TestTheColumnsCopied:
    def test_the_job_copies_the_columns_the_application_reads(self):
        """Le miroir est lu positionnellement : l'ordre est un contrat."""
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
        from inventory.ingest.erp import ITEM_COLUMNS

        assert sync.ITEM_COLUMNS == ITEM_COLUMNS
