"""Ce que l'API renvoie, déclaré.

Pourquoi ce module existe
-------------------------
Le frontend décrivait à la main, dans ``types.ts``, la forme de chaque réponse.
Soixante-cinq interfaces recopiées d'un langage à l'autre, qu'aucun mécanisme ne
rapprochait de ce que le backend produit vraiment. Renommer un champ côté
serveur laissait la déclaration TypeScript intacte : le compilateur restait
content, l'écran affichait ``undefined``, et rien n'échouait avant la
production.

L'audit demandait un client TypeScript **généré depuis l'OpenAPI**. Le schéma ne
s'y prêtait pas : les cent dix-sept routes déclarent ``-> dict[str, Any]``, et
l'OpenAPI ne portait donc aucune information de champ — cent treize chemins,
tous en ``{"type": "object"}``. Générer aurait produit ``Record<string, unknown>``
partout, c'est-à-dire moins que ce qui était écrit à la main. Le générateur
n'était pas le travail : déclarer ce que l'API renvoie l'était.

``responses=`` et non ``response_model=``
-----------------------------------------
Les deux renseignent l'OpenAPI. ``response_model`` **sérialise à travers le
modèle** : toute clé non déclarée disparaît de la réponse, en silence. Sur une
API dont les charges utiles sont construites à la main dans les services, une
seule omission de déclaration retirerait un champ que l'écran lit — un défaut
pire que celui qu'on corrige, et invisible jusqu'à l'écran concerné.

``responses={200: {"model": X}}`` documente sans rien filtrer : la réponse part
telle que le service l'a construite. La déclaration devient donc une
affirmation, et non une contrainte — et c'est un contrôle qui la vérifie, en
comparant chaque modèle à une charge utile réelle. La différence est celle
entre « le serveur ne peut pas renvoyer autre chose » et « on sait quand il
renvoie autre chose » ; la seconde est la seule des deux qui ne perde pas de
données.

Ce qui n'a pas besoin d'être ici
--------------------------------
Une route qui rend ``campaign.model_dump(mode="json")`` a déjà son modèle : la
classe du domaine. Elle est déclarée telle quelle, et aucune forme n'est
recopiée. Ce module ne porte que les charges utiles composites — celles qu'un
service assemble et qui n'existent nulle part comme classe.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..domain.closure import ChecklistState
from ..domain.models import Campaign
from ..ingest.contracts import FieldType

__all__ = [
    "CampaignPage",
    "ClosureChecklistResponse",
    "DeletedResponse",
    "GridContractResponse",
    "HealthResponse",
    "MeResponse",
    "MetricsResponse",
    "OverviewResponse",
    "WorkQueuesResponse",
]


class Payload(BaseModel):
    """Une charge utile de réponse.

    ``extra="allow"`` parce que ces modèles **décrivent** une réponse au lieu de
    la contraindre : un service qui ajoute une clé sans la déclarer ici doit
    faire échouer un contrôle, pas perdre la clé en vol.
    """

    model_config = ConfigDict(
        extra="allow",
        # Un champ à valeur par défaut est toujours émis : le déclarer
        # facultatif obligerait l'interface à le tester pour rien.
        json_schema_serialization_defaults_required=True,
    )


# --------------------------------------------------------------------------- #
# Système
# --------------------------------------------------------------------------- #

class FrontendBuild(Payload):
    bundle: str
    built_at: str = Field(alias="builtAt")
    assets: int


class MigrationState(Payload):
    applied: list[str]
    pending: list[str]
    error: str | None = None


class HealthResponse(Payload):
    """L'état du conteneur, tel que la sonde de disponibilité le lit."""

    status: str
    ready: bool
    version: str
    env: str
    lakebase_configured: bool = Field(alias="lakebaseConfigured")
    warehouse_configured: bool = Field(alias="warehouseConfigured")
    evidence_configured: bool = Field(alias="evidenceConfigured")
    frontend_built: bool = Field(alias="frontendBuilt")
    frontend: FrontendBuild
    llm_endpoint: str = Field(alias="llmEndpoint")
    startup_error: str | None = Field(default=None, alias="startupError")
    migrations: MigrationState


class MeResponse(Payload):
    actor: str
    authenticated: bool
    source: str


class RouteMetrics(Payload):
    method: str
    route: str
    count: int
    errors: int
    server_errors: int = Field(alias="serverErrors")
    avg_ms: float = Field(alias="avgMs")
    p50_ms: float = Field(alias="p50Ms")
    p95_ms: float = Field(alias="p95Ms")
    max_ms: float = Field(alias="maxMs")


class HttpMetrics(Payload):
    uptime_seconds: float = Field(alias="uptimeSeconds")
    requests: int
    errors: int
    server_errors: int = Field(alias="serverErrors")
    window_size: int = Field(alias="windowSize")
    routes: list[RouteMetrics]


class MirrorFreshness(Payload):
    table: str
    label: str
    rows: int
    synced_at: str | None = Field(default=None, alias="syncedAt")


class ImportVolumes(Payload):
    hours: int
    batches: int
    rows_accepted: int = Field(alias="rowsAccepted")
    rows_rejected: int = Field(alias="rowsRejected")
    batches_with_rejects: int = Field(alias="batchesWithRejects")
    last_at: str | None = Field(default=None, alias="lastAt")


class ScanJobMetrics(Payload):
    hours: int
    by_status: dict[str, int] = Field(alias="byStatus")
    running: int
    failed: int


class MetricsResponse(Payload):
    """Ce qu'on regarde quand l'application est lente, en JSON.

    Pas de format Prometheus : rien ne scrute ce conteneur, et une page qu'on
    ouvre dans un navigateur est ce qui sert réellement ici.
    """

    version: str
    env: str
    http: HttpMetrics
    pool: dict[str, int]
    erp_mirror: list[MirrorFreshness] = Field(alias="erpMirror")
    imports: ImportVolumes
    scan_jobs: ScanJobMetrics = Field(alias="scanJobs")


# --------------------------------------------------------------------------- #
# Campagnes
# --------------------------------------------------------------------------- #

class CampaignPage(Payload):
    """Une page de campagnes, et combien il y en a en tout.

    ``total`` n'est pas décoratif : c'est la seule façon pour l'interface de
    savoir qu'elle ne montre pas tout.
    """

    items: list[Campaign]
    total: int
    offset: int


class DeletedResponse(Payload):
    deleted: bool


class Permissions(Payload):
    """La matrice de gel, telle que l'écran la lit pour désactiver un bouton."""

    thresholds: bool
    items: bool
    boms: bool
    locations: bool
    book_stock: bool = Field(alias="bookStock")
    zones: bool
    count_journals: bool = Field(alias="countJournals")
    count_sheets: bool = Field(alias="countSheets")
    count_entries: bool = Field(alias="countEntries")
    adjustments: bool
    analysis: bool
    backflush: bool
    stock_flow: bool = Field(alias="stockFlow")


class Access(Payload):
    role: str
    can_write: bool = Field(alias="canWrite")
    is_owner: bool = Field(alias="isOwner")
    owner: str


class JournalProgress(Payload):
    total: int
    complete: int
    running: int
    pending: int
    #: Absent plutôt que zéro quand il n'y a aucun journal : « 0 % fait » et
    #: « rien à faire » ne se ressemblent qu'à l'écran.
    ratio: float | None = None


class GenericProgress(Payload):
    zones: int
    done: int
    ratio: float | None = None
    by_status: dict[str, int] = Field(alias="byStatus")
    pending_arbitrations: int = Field(alias="pendingArbitrations")


class CampaignCounts(Payload):
    items: int
    book_stock_lines: int = Field(alias="bookStockLines")


class Sequence(Payload):
    """Ce que la phase courante autorise, et ce qui manque sinon."""

    unlocked: dict[str, bool]
    blocked_by: dict[str, str] = Field(alias="blockedBy")


class Perimeter(Payload):
    resolved: bool
    #: Nuls tant qu'aucun gestionnaire n'est résolu pour l'utilisateur —
    #: `resolved` le dit, et les deux libellés n'ont alors rien à porter.
    manager_code: str | None = Field(default=None, alias="managerCode")
    manager_label: str | None = Field(default=None, alias="managerLabel")
    warehouses: list[str]
    catch_all: bool = Field(alias="catchAll")
    zone_count: int = Field(alias="zoneCount")
    journal_count: int = Field(alias="journalCount")


class OverviewResponse(Payload):
    """Tout ce que l'écran de campagne affiche en tête."""

    campaign: Campaign
    permissions: Permissions
    access: Access
    journal_progress: JournalProgress = Field(alias="journalProgress")
    generic_progress: GenericProgress = Field(alias="genericProgress")
    counts: CampaignCounts
    sequence: Sequence
    perimeter: Perimeter


class ClosureChecklistItem(Payload):
    code: str
    label: str
    state: ChecklistState
    detail: str
    #: Le fragment de route où traiter le point — absent quand il n'y a pas
    #: d'écran à ouvrir, et l'interface n'en fait alors pas un lien.
    where: str | None = None


class ClosureChecklistCounts(Payload):
    blocking: int
    attention: int
    done: int


class ClosureChecklistResponse(Payload):
    """L'état des lieux avant de clôturer, lisible pendant toute l'analyse."""

    ready: bool
    allowed: bool
    items: list[ClosureChecklistItem]
    counts: ClosureChecklistCounts


class WorkQueue(Payload):
    code: str
    label: str
    action: str
    count: int
    names: list[str]
    hidden: int
    where: str


class WorkQueuesResponse(Payload):
    """Ce qui attend quelqu'un, maintenant."""

    focus: bool
    queues: list[WorkQueue]
    waiting: int


# --------------------------------------------------------------------------- #
# Contrats de grille
# --------------------------------------------------------------------------- #

class GridField(Payload):
    name: str
    label: str
    type: FieldType
    required: bool
    aliases: list[str]
    choices: list[str]
    #: Les quatre types que portent réellement les contrats : un prix par
    #: défaut vaut `0`, un drapeau `False`, un type d'article `"UNKNOWN"`.
    default: str | int | bool | None = None
    help: str
    width: int


class GridContractResponse(Payload):
    """Ce qu'une grille attend en colonnes, et par quels noms on peut l'appeler."""

    key: str
    title: str
    description: str
    hint: str
    natural_key: list[str] = Field(alias="naturalKey")
    fields: list[GridField]
    examples: list[dict]
