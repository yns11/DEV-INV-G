"""Application configuration.

Every workspace-specific value is read from the environment so that nothing
identifying a workspace, warehouse or database ever lands in source control.
On Databricks Apps the platform injects the resource-backed variables
(``DATABRICKS_WAREHOUSE_ID``, ``PGHOST``, ...) from the ``app.yaml`` /
``databricks.yml`` resource declarations (``valueFrom`` in the former,
``value_from`` in the latter).

Local development uses the same names, loaded from a ``.env`` file — see
``.env.example`` et ``docs/03-guide-deploiement.md``.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]

#: La racine du dépôt, déduite du module et non du répertoire courant.
#: ``app/inventory/config.py`` → ``app/inventory`` → ``app`` → la racine.
_ROOT = Path(__file__).resolve().parents[2]

#: Les fichiers ``.env`` lus, du moins prioritaire au plus prioritaire.
#:
#: ``env_file=".env"`` seul se résout contre le répertoire courant du
#: processus. Or l'application démarre depuis ``app/`` — ``make run`` fait
#: ``cd app && python main.py``, et ``app.yaml`` lance ``python main.py``
#: depuis la charge utile — tandis que le démarrage rapide documenté place le
#: fichier à la racine du dépôt. Le fichier était donc écrit là où personne ne
#: le lisait : l'application démarrait en mode dégradé, sans Lakebase, sans
#: rien dire de plus qu'un ``lakebaseConfigured: false`` dans ``/api/health``.
#:
#: Les deux emplacements sont lus, le répertoire courant l'emportant, pour que
#: le fichier trouvé jusqu'ici continue de l'être.
ENV_FILES = (_ROOT / ".env", Path(".env"))


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ runtime
    env: Literal["local", "dev", "prod"] = Field(default="local", alias="INV_ENV")
    log_level: str = Field(default="INFO", alias="INV_LOG_LEVEL")
    app_name: str = Field(default="campagnes-inventaire", alias="DATABRICKS_APP_NAME")

    # ------------------------------------------------------------- databricks
    warehouse_id: str | None = Field(default=None, alias="DATABRICKS_WAREHOUSE_ID")
    #: The application's own service principal, injected by Databricks Apps.
    #: Unity Catalog grants are made to *it*, not to the signed-in user, so a
    #: permissions refusal can name the exact principal an admin has to grant.
    service_principal_id: str | None = Field(
        default=None, alias="DATABRICKS_CLIENT_ID"
    )
    llm_endpoint: str = Field(
        default="databricks-claude-opus-4-8", alias="INV_LLM_ENDPOINT"
    )
    #: Endpoint vision dédié à la lecture des scans. Vide, c'est
    #: :attr:`llm_endpoint` qui sert — le comportement d'avant, sans surprise.
    #:
    #: Séparé parce que les deux tâches n'ont rien en commun : l'assistant
    #: raisonne sur un dossier de campagne, la lecture d'un scan transcrit des
    #: chiffres manuscrits en JSON. Payer un modèle de raisonnement pour
    #: recopier des nombres coûte du temps sur chacune des cent feuilles d'une
    #: pile, et c'est le temps qui fait renoncer à scanner.
    scan_llm_endpoint: str = Field(default="", alias="INV_SCAN_LLM_ENDPOINT")
    #: Combien de feuilles sont lues en même temps. Le gain est réel mais borné
    #: par le débit de l'endpoint : au-delà, les appels font la queue côté
    #: serving et les 429 apparaissent. Quatre est un point de départ à mesurer,
    #: pas une valeur optimale — c'est pourquoi elle est en configuration.
    scan_max_workers: int = Field(default=4, ge=1, le=16, alias="INV_SCAN_MAX_WORKERS")
    #: Plafond de pages d'une pile scannée. Cent feuilles recto-verso en font
    #: deux cents. Au-delà, le chargement est **refusé en le disant** : tronquer
    #: en silence, ce que faisait la version précédente, perd des comptages sans
    #: que personne ne l'apprenne.
    scan_max_pages: int = Field(default=250, ge=1, alias="INV_SCAN_MAX_PAGES")
    #: Combien de pieds de page partent dans un même appel de routage. Un seul
    #: appel pour deux cents pages dépasse la charge utile acceptée ; un appel
    #: par page multiplie les allers-retours.
    scan_routing_batch: int = Field(
        default=12, ge=1, le=50, alias="INV_SCAN_ROUTING_BATCH"
    )
    #: Résolution de rastérisation des pages scannées.
    scan_dpi: int = Field(default=150, ge=72, le=400, alias="INV_SCAN_DPI")
    #: Plafond de pixels d'**une** page rendue.
    #:
    #: `render(scale=dpi/72)` alloue le bitmap lui-même, hors de portée de la
    #: garde anti-bombe de PIL. Une page dont le PDF déclare un MediaBox de deux
    #: cents pouces de côté produit, à 150 dpi, un bitmap de 30 000 × 30 000 —
    #: neuf cents mégaoctets pour une seule page, sur un conteneur qui en a six
    #: mille et les partage. Un A4 à 600 dpi tient dans 35 mégapixels ; au-delà,
    #: la résolution est réduite plutôt que la page refusée, parce qu'un
    #: MediaBox démesuré est le plus souvent un artefact de scanner et non une
    #: attaque.
    scan_max_pixels: int = Field(
        default=40_000_000, ge=1_000_000, alias="INV_SCAN_MAX_PIXELS"
    )
    #: How the campaign assistant is framed (see :mod:`inventory.ai.assistant`).
    #: A runtime setting rather than a code decision, so tightening or loosening
    #: it costs no deployment.
    assistant_profile: str = Field(default="etendu", alias="INV_ASSISTANT_PROFILE")

    # --------------------------------------------------------------- erp source
    #: Unity Catalog silver tables holding the ERP article referential and its
    #: bill of materials. Configurable because a table rename in the data
    #: platform must not require a release of this application.
    erp_schema: str = Field(
        default="emotors_data_champions.silver_erp_ye", alias="INV_ERP_SCHEMA"
    )
    erp_items_table: str = Field(
        default="silver_base_article", alias="INV_ERP_ITEMS_TABLE"
    )
    erp_bom_table: str = Field(default="silver_bom", alias="INV_ERP_BOM_TABLE")
    #: Snapshot quotidien du stock physique du site, une ligne par article ×
    #: entrepôt × emplacement, partitionné par date. La campagne en lit la
    #: photo la plus récente : c'est un état, pas un historique.
    erp_stock_table: str = Field(
        default="stock_snapshot", alias="INV_ERP_STOCK_TABLE"
    )
    #: Gold table holding the backflush variance, at parent × child × week grain.
    #: Its own schema: it is published by a different pipeline than the silver
    #: referential, and pinning both to one setting would make a rename of either
    #: break the other.
    erp_backflush_schema: str = Field(
        default="emotors_data_champions.backflush", alias="INV_ERP_BACKFLUSH_SCHEMA"
    )
    erp_backflush_table: str = Field(
        default="fact_ecart_backflush", alias="INV_ERP_BACKFLUSH_TABLE"
    )
    #: Silver table holding every stock flow at article × day grain: receipts,
    #: shipments, production, theoretical and actual consumption, scrap. One
    #: column per flow, already consolidated from the bronze layer — the legal
    #: entity, the `IsDelete` filter, the scrap bin and the de-duplication of a
    #: parent's output across its components are all applied upstream.
    #:
    #: In the referential's own schema, so it sits behind the grant the
    #: application already needs. Reading the bronze tables directly meant a
    #: second catalogue and a second `USE CATALOG` from a second owner.
    erp_movements_table: str = Field(
        default="mouvements", alias="INV_ERP_MOVEMENTS_TABLE"
    )
    #: Where the referential is read from. ``uc`` queries the silver tables
    #: directly and needs USE CATALOG on the ERP's catalog for the application's
    #: service principal — a grant only a catalog owner can make. ``mirror``
    #: reads a local copy in the application's own database, refreshed by the
    #: « Synchronisation du miroir ERP » job, which runs with an identity that
    #: already has that access. Same rows, same translation, same editable grid;
    #: what changes is who needed the grant.
    erp_source: Literal["uc", "mirror"] = Field(default="uc", alias="INV_ERP_SOURCE")

    # ------------------------------------------------------------ unity catalog
    uc_catalog: str = Field(default="emotors_data_champions", alias="INV_UC_CATALOG")
    uc_schema: str = Field(default="inventory", alias="INV_UC_SCHEMA")
    uc_volume: str = Field(default="inventory_evidence", alias="INV_UC_VOLUME")

    # -------------------------------------------------------------- evidence
    #: Where scans and imported files are archived. ``volume`` writes them to
    #: the Unity Catalog volume, which is where they belong: a volume is
    #: browsable from the workspace, so the sheet behind a forty-thousand-euro
    #: variance can be found six months later without this application.
    #:
    #: That path needs three privileges on the app's own service principal —
    #: ``USE CATALOG``, ``USE SCHEMA``, ``WRITE VOLUME`` — and the first one is
    #: granted only by a principal holding ``MANAGE`` on the catalog, i.e. its
    #: owner. On a shared catalog that owner may be out of reach, and an
    #: inventory keeps its date. ``lakebase`` then archives into the
    #: application's own schema, which it owns and already writes to: **no
    #: administrator is involved at all**.
    #:
    #: Same guarantee either way — a scan is archived before its quantities are
    #: written, or the operation is refused. What changes is who had to grant
    #: something, and whether the file is browsable outside the application.
    #:
    #: The same reversal as :attr:`erp_source`, applied to evidence rather than
    #: to rows. Switching is safe in both directions: a path records which
    #: store holds it, so pieces archived before the switch stay readable.
    evidence_store: Literal["volume", "lakebase"] = Field(
        default="volume", alias="INV_EVIDENCE_STORE"
    )

    # ----------------------------------------------------------------- lakebase
    # Injected by the platform when a Lakebase database resource is attached.
    pg_host: str | None = Field(default=None, alias="PGHOST")
    pg_port: int = Field(default=5432, alias="PGPORT")
    pg_database: str | None = Field(default=None, alias="PGDATABASE")
    pg_user: str | None = Field(default=None, alias="PGUSER")
    pg_password: str | None = Field(default=None, alias="PGPASSWORD")
    pg_sslmode: str = Field(default="require", alias="PGSSLMODE")
    #: Lakebase endpoint resource path
    #: (``projects/<p>/branches/<b>/endpoints/<e>``) — what the OAuth credential
    #: is minted against. The platform does **not** publish it: attaching a
    #: `postgres` resource injects PGHOST/PGDATABASE/PGUSER/PGPORT/PGSSLMODE and
    #: nothing more. Left empty, the endpoint is discovered from
    #: :attr:`lakebase_branch` by matching PGHOST; set it to skip that lookup.
    lakebase_endpoint: str | None = Field(default=None, alias="INV_LAKEBASE_ENDPOINT")
    #: Branch resource path (``projects/<p>/branches/<b>``) used for that
    #: discovery. Declared in `databricks.yml` from the same variables as the
    #: `postgres` resource, so the two can never point at different branches.
    lakebase_branch: str | None = Field(default=None, alias="INV_LAKEBASE_BRANCH")
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

    # ------------------------------------------------------------------ helpers
    @computed_field  # type: ignore[prop-decorator]
    @property
    def uc_schema_fqn(self) -> str:
        """Fully-qualified Unity Catalog schema, e.g. ``catalog.inventory``."""
        return f"{self.uc_catalog}.{self.uc_schema}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def uc_volume_path(self) -> str:
        """Racine du volume où sont archivées les pièces justificatives."""
        return f"/Volumes/{self.uc_catalog}/{self.uc_schema}/{self.uc_volume}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def evidence_configured(self) -> bool:
        """De quoi tenter un archivage : le volume, ou la base.

        Vide, l'archivage se tait au lieu d'échouer : une pièce justificative
        n'est pas une condition du chargement, et refuser un import de deux
        cent mille lignes parce que le volume n'est pas configuré coûterait
        infiniment plus que de ne pas archiver le fichier.

        En mode ``lakebase``, la question posée n'est plus « le volume est-il
        nommé » mais « la base est-elle joignable » : c'est elle qui garde les
        pièces, et les trois variables du volume n'ont plus de rôle.
        """
        if self.evidence_store == "lakebase":
            return self.lakebase_configured
        return bool(self.uc_catalog and self.uc_schema and self.uc_volume)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scan_endpoint(self) -> str:
        """L'endpoint qui lit les scans : le dédié s'il existe, sinon l'autre."""
        return self.scan_llm_endpoint or self.llm_endpoint

    @computed_field  # type: ignore[prop-decorator]
    @property
    def lakebase_configured(self) -> bool:
        return bool(self.pg_host and self.pg_database and self.pg_user)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def erp_items_fqn(self) -> str:
        return f"{self.erp_schema}.{self.erp_items_table}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def erp_bom_fqn(self) -> str:
        return f"{self.erp_schema}.{self.erp_bom_table}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def erp_stock_fqn(self) -> str:
        return f"{self.erp_schema}.{self.erp_stock_table}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def erp_backflush_fqn(self) -> str:
        return f"{self.erp_backflush_schema}.{self.erp_backflush_table}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def erp_movements_fqn(self) -> str:
        return f"{self.erp_schema}.{self.erp_movements_table}"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Cached so `.env` is parsed exactly once."""
    return Settings()
