"""Application configuration.

Every workspace-specific value is read from the environment so that nothing
identifying a workspace, warehouse or database ever lands in source control.
On Databricks Apps the platform injects the resource-backed variables
(``DATABRICKS_WAREHOUSE_ID``, ``PGHOST``, ...) from the ``app.yaml`` /
``databricks.yml`` resource declarations (``valueFrom`` in the former,
``value_from`` in the latter).

Local development uses the same names, loaded from a ``.env`` file — see
``.env.example`` and ``docs/03-deployment-guide.md``.
"""

from __future__ import annotations

import functools
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ runtime
    env: Literal["local", "dev", "prod"] = Field(default="local", alias="INV_ENV")
    log_level: str = Field(default="INFO", alias="INV_LOG_LEVEL")
    app_port: int = Field(default=8000, alias="DATABRICKS_APP_PORT")
    app_name: str = Field(default="campagnes-inventaire", alias="DATABRICKS_APP_NAME")

    # ------------------------------------------------------------- databricks
    databricks_host: str | None = Field(default=None, alias="DATABRICKS_HOST")
    warehouse_id: str | None = Field(default=None, alias="DATABRICKS_WAREHOUSE_ID")
    llm_endpoint: str = Field(
        default="databricks-claude-sonnet-4-5", alias="INV_LLM_ENDPOINT"
    )

    # ------------------------------------------------------------ unity catalog
    uc_catalog: str = Field(default="emotors_data_champions", alias="INV_UC_CATALOG")
    uc_schema: str = Field(default="inventory", alias="INV_UC_SCHEMA")
    uc_volume: str = Field(default="inventory_evidence", alias="INV_UC_VOLUME")

    # ----------------------------------------------------------------- lakebase
    # Injected by the platform when a Lakebase database resource is attached.
    pg_host: str | None = Field(default=None, alias="PGHOST")
    pg_port: int = Field(default=5432, alias="PGPORT")
    pg_database: str | None = Field(default=None, alias="PGDATABASE")
    pg_user: str | None = Field(default=None, alias="PGUSER")
    pg_password: str | None = Field(default=None, alias="PGPASSWORD")
    pg_sslmode: str = Field(default="require", alias="PGSSLMODE")
    #: Lakebase endpoint resource path
    #: (``projects/<p>/branches/<b>/endpoints/<e>``), injected alongside the
    #: PG* variables. It is what the OAuth credential is minted against.
    lakebase_endpoint: str | None = Field(default=None, alias="LAKEBASE_ENDPOINT")
    pg_schema: str = Field(default="inventory", alias="INV_PG_SCHEMA")
    pg_pool_min: int = Field(default=1, alias="INV_PG_POOL_MIN")
    pg_pool_max: int = Field(default=8, alias="INV_PG_POOL_MAX")

    # ------------------------------------------------------------ business rules
    generic_warehouse: str = Field(default="B06VRAC", alias="INV_GENERIC_WAREHOUSE")
    generic_location: str = Field(default="GENERIQUE", alias="INV_GENERIC_LOCATION")

    #: Hard ceiling on rows accepted from a single paste/import request. Guards
    #: both the 1 MB-ish payload budget and the app container's 6 GB of RAM.
    max_import_rows: int = Field(default=200_000, alias="INV_MAX_IMPORT_ROWS")
    max_upload_bytes: int = Field(default=64 * 1024 * 1024, alias="INV_MAX_UPLOAD_BYTES")

    #: The Databricks Apps reverse proxy hard-caps a request at 120 s. Anything
    #: heavier must run as a background task, so we budget below that.
    request_budget_seconds: float = Field(default=100.0, alias="INV_REQUEST_BUDGET_S")

    # ------------------------------------------------------------------ helpers
    @computed_field  # type: ignore[prop-decorator]
    @property
    def uc_schema_fqn(self) -> str:
        """Fully-qualified Unity Catalog schema, e.g. ``catalog.inventory``."""
        return f"{self.uc_catalog}.{self.uc_schema}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uc_volume_path(self) -> str:
        """POSIX path of the UC volume used for evidence and exports."""
        return f"/Volumes/{self.uc_catalog}/{self.uc_schema}/{self.uc_volume}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lakebase_configured(self) -> bool:
        return bool(self.pg_host and self.pg_database and self.pg_user)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warehouse_http_path(self) -> str | None:
        if not self.warehouse_id:
            return None
        return f"/sql/1.0/warehouses/{self.warehouse_id}"

    def uc_table(self, name: str) -> str:
        """Fully-qualified name of a Delta table owned by this application."""
        return f"{self.uc_schema_fqn}.{name}"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Cached so `.env` is parsed exactly once."""
    return Settings()
