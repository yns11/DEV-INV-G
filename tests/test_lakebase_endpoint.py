"""Resolution of the Lakebase endpoint backing PGHOST.

Attaching a ``postgres`` resource to a Databricks App publishes connection
parameters but not the endpoint's resource path, and only that path can mint a
database credential. The lookup that bridges the two is pure logic over the
API's response shape, so it is tested here rather than discovered in
production — where it fails as an app that starts, answers ``/api/health`` and
serves 502 on every business route.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from inventory.config import Settings
from inventory.db.engine import _CredentialProvider
from inventory.errors import UpstreamError

HOST = "ep-calm-frost-d3ycqaxm.database.eu-west-1.cloud.databricks.com"
BRANCH = "projects/inventaire/branches/production"


@dataclass
class _Hosts:
    host: str | None = None
    read_only_host: str | None = None
    read_only_pooled_host: str | None = None
    read_write_pooled_host: str | None = None


@dataclass
class _Status:
    hosts: _Hosts
    endpoint_type: str = "ENDPOINT_TYPE_READ_WRITE"


@dataclass
class _Endpoint:
    name: str
    status: _Status


class _FakePostgres:
    def __init__(self, endpoints: list[_Endpoint]) -> None:
        self._endpoints = endpoints
        self.calls = 0

    def list_endpoints(self, parent: str) -> list[_Endpoint]:
        self.calls += 1
        assert parent == BRANCH
        return self._endpoints


class _FakeClient:
    def __init__(self, endpoints: list[_Endpoint]) -> None:
        self.postgres = _FakePostgres(endpoints)


def _provider(**overrides: object) -> _CredentialProvider:
    values: dict[str, object] = {
        "PGHOST": HOST,
        "PGDATABASE": "databricks_postgres",
        "PGUSER": "sp-client-id",
        "INV_LAKEBASE_BRANCH": BRANCH,
    }
    values.update(overrides)
    return _CredentialProvider(Settings(**values))  # type: ignore[arg-type]


def _endpoint(name: str, **hosts: str) -> _Endpoint:
    return _Endpoint(name=f"{BRANCH}/endpoints/{name}", status=_Status(_Hosts(**hosts)))


def test_matches_the_endpoint_advertising_pghost() -> None:
    client = _FakeClient(
        [
            _endpoint("replica", host="other.database.cloud.databricks.com"),
            _endpoint("primary", host=HOST),
        ]
    )
    assert _provider()._resolve_endpoint(client) == f"{BRANCH}/endpoints/primary"


def test_matches_a_pooled_host() -> None:
    """PGHOST may be the pooled address rather than the direct one."""
    client = _FakeClient(
        [_endpoint("primary", host="direct.example", read_write_pooled_host=HOST)]
    )
    assert _provider()._resolve_endpoint(client) == f"{BRANCH}/endpoints/primary"


def test_resolution_is_cached() -> None:
    client = _FakeClient([_endpoint("primary", host=HOST)])
    provider = _provider()
    first = provider._resolve_endpoint(client)
    assert provider._resolve_endpoint(client) == first
    assert client.postgres.calls == 1, "l'endpoint doit être résolu une seule fois"


def test_falls_back_to_the_read_write_endpoint() -> None:
    """An unadvertised PGHOST form must not take the app down."""
    read_only = _Endpoint(
        name=f"{BRANCH}/endpoints/replica",
        status=_Status(_Hosts(host="replica.example"), "ENDPOINT_TYPE_READ_ONLY"),
    )
    writable = _endpoint("primary", host="unexpected.example")
    client = _FakeClient([read_only, writable])
    assert _provider()._resolve_endpoint(client) == f"{BRANCH}/endpoints/primary"


def test_raises_when_no_endpoint_can_serve() -> None:
    read_only = _Endpoint(
        name=f"{BRANCH}/endpoints/replica",
        status=_Status(_Hosts(host="replica.example"), "ENDPOINT_TYPE_READ_ONLY"),
    )
    with pytest.raises(UpstreamError) as excinfo:
        _provider()._resolve_endpoint(_FakeClient([read_only]))
    assert "INV_LAKEBASE_ENDPOINT" in str(excinfo.value)


def test_raises_an_actionable_error_without_a_branch() -> None:
    with pytest.raises(UpstreamError) as excinfo:
        _provider(INV_LAKEBASE_BRANCH="")._resolve_endpoint(_FakeClient([]))
    assert "INV_LAKEBASE_BRANCH" in str(excinfo.value)
