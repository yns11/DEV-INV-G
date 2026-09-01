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
    "ScopeLocation",
    "ErpJournalResponse",
    "ScopeCandidate",
    "ScopeDeclared",
    "RescanLocation",
    "DriftResponse",
    "DriftsResolved",
    "LabelAlert",
    "RecountedInPlace",
    "CampaignPage",
    "ClosureChecklistResponse",
    "DeletedResponse",
    "SectionLabelsResponse",
    "GridContractResponse",
    "HealthResponse",
    "MeResponse",
    "MetricsResponse",
    "OverviewResponse",
]


class Payload(BaseModel):
    """Une charge utile de réponse.

    ``extra="allow"`` parce que ces modèles **décrivent** une réponse au lieu de
    la contraindre : un service qui ajoute une clé sans la déclarer ici doit
    faire échouer un contrôle, pas perdre la clé en vol.

    ``populate_by_name`` parce que l'entrée et la sortie n'ont pas la même
    forme, et que c'est voulu. Le fil parle ``camelCase`` — c'est l'alias, et
    FastAPI sérialise par lui. Mais ce qui *arrive* dans le modèle est souvent
    le ``model_dump`` d'un modèle de domaine, qui parle ``snake_case`` : sans ce
    drapeau, Pydantic exige l'alias, ne le trouve pas, et la route répond 500
    dès qu'elle a une ligne à rendre — c'est arrivé sur les trois routes des
    comptages avancés, en production, au premier journal importé.

    Le drapeau n'élargit que l'entrée. La sortie reste en alias, donc l'écran
    lit toujours les mêmes clés.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
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
    #: « volume » ou « lakebase » — laquelle des deux archives reçoit les
    #: pièces. Les deux pannes possibles n'ont rien en commun, et
    #: ``evidenceConfigured`` seul ne dit pas laquelle chercher.
    evidence_store: str = Field(alias="evidenceStore")
    frontend_built: bool = Field(alias="frontendBuilt")
    frontend: FrontendBuild
    llm_endpoint: str = Field(alias="llmEndpoint")
    startup_error: str | None = Field(default=None, alias="startupError")
    migrations: MigrationState


class EvidenceProbeResponse(Payload):
    """Ce que l'archive répond quand on essaie vraiment d'y écrire.

    ``configured`` distingue les deux pannes, qui n'appellent pas le même
    geste : aucune archive déclarée, ou une archive déclarée mais fermée.
    """

    ok: bool
    configured: bool
    path: str | None = None
    detail: str | None = None


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


class SectionLabelsResponse(Payload):
    """Les en-têtes de section retenus pour une zone, après nettoyage.

    Ce que la route rend est ce qui est **enregistré**, pas ce qui a été
    envoyé : un texte vide n'est pas stocké — il remet le défaut — et l'écran
    doit voir cette différence tout de suite plutôt qu'au prochain
    rechargement.
    """

    labels: dict[str, str]


class Permissions(Payload):
    """La matrice de gel, telle que l'écran la lit pour désactiver un bouton."""

    thresholds: bool
    items: bool
    boms: bool
    locations: bool
    book_stock: bool = Field(alias="bookStock")
    zones: bool
    count_journals: bool = Field(alias="countJournals")
    early_counts: bool = Field(alias="earlyCounts")
    count_sheets: bool = Field(alias="countSheets")
    count_entries: bool = Field(alias="countEntries")
    adjustments: bool
    analysis: bool
    backflush: bool
    stock_flow: bool = Field(alias="stockFlow")
    #: Les paramètres de campagne autres que les seuils — aujourd'hui le seul
    #: réglage « Accepter des formules dans les comptages ». Ouvert plus
    #: longtemps que ``thresholds``, et délibérément : voir ``Editable``.
    settings: bool


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
    #: Emplacements précomptés et scellés. Non nul, l'analyse s'ouvre même sans
    #: gel : leur référence est déjà posée et ne bougera plus.
    sealed_locations: int = Field(default=0, alias="sealedLocations")


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
    #: Comment se lit chaque code de :attr:`choices`. Vide sur une colonne non
    #: codée. Déclaré ici et pas seulement dans le contrat : c'est ce champ que
    #: la grille lit pour ne plus proposer « LINE_SIDE » dans une liste.
    choice_labels: dict[str, str] = Field(default_factory=dict, alias="choiceLabels")
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


# --------------------------------------------------------------------------- #
# Comptages avancés
# --------------------------------------------------------------------------- #

class ScopeLocation(Payload):
    warehouse_id: str = Field(alias="warehouseId")
    location_id: str = Field(alias="locationId")


class ErpJournalResponse(Payload):
    """Un journal tel que l'ERP le tient, avec son périmètre déclaré."""

    id: str
    campaign_id: str = Field(alias="campaignId")
    journal_number: str = Field(alias="journalNumber")
    kind: str
    description: str = ""
    site_id: str = Field(default="", alias="siteId")
    #: Le postage tel que l'en-tête ERP le déclare, distinct du statut de
    #: workflow : c'est lui que le scellement d'un lot exige.
    erp_posted: bool = Field(alias="erpPosted")
    erp_posted_at: str | None = Field(default=None, alias="erpPostedAt")
    line_count: int = Field(alias="lineCount")
    last_imported_at: str | None = Field(default=None, alias="lastImportedAt")
    scope: list[ScopeLocation] = Field(default_factory=list)
    #: Faux tant que personne n'a désigné les emplacements du journal — et tant
    #: qu'il l'est, aucun lot ne peut s'ouvrir dessus.
    scope_declared: bool = Field(alias="scopeDeclared")
    #: Déclarer le périmètre scelle : les deux gestes n'en font qu'un.
    is_sealed: bool = Field(default=False, alias="isSealed")
    sealed_at: str | None = Field(default=None, alias="sealedAt")
    sealed_by: str = Field(default="", alias="sealedBy")
    #: La date du relevé physique, lue dans les lignes du journal.
    counted_on: str | None = Field(default=None, alias="countedOn")
    warehouses: list[str] = Field(default_factory=list)


class ScopeCandidate(Payload):
    """Un emplacement que le journal *pourrait* couvrir."""

    warehouse_id: str = Field(alias="warehouseId")
    location_id: str = Field(alias="locationId")
    line_count: int = Field(alias="lineCount")
    item_count: int = Field(alias="itemCount")
    qty_on_hand: float = Field(alias="qtyOnHand")
    qty_counted: float = Field(alias="qtyCounted")


class ScopeDeclared(Payload):
    locations: int


class RescanLabel(Payload):
    """Une étiquette qui met un emplacement scellé en question."""

    label_id: str = Field(alias="labelId")
    item_number: str = Field(alias="itemNumber")
    other_warehouse_id: str = Field(alias="otherWarehouseId")
    other_location_id: str = Field(alias="otherLocationId")
    comment: str = ""
    decided_by: str = Field(default="", alias="decidedBy")


class RescanLocation(Payload):
    """Un emplacement scellé qu'il faut desceller et rescanner.

    Ce que l'issue « signaler » produit : on n'a pas tranché sur pièce, et la
    façon d'en sortir est d'aller recompter.
    """

    warehouse_id: str = Field(alias="warehouseId")
    location_id: str = Field(alias="locationId")
    journal_number: str = Field(default="", alias="journalNumber")
    erp_journal_id: str | None = Field(default=None, alias="erpJournalId")
    is_sealed: bool = Field(default=False, alias="isSealed")
    labels: list[RescanLabel] = Field(default_factory=list)


class DriftResponse(Payload):
    """``ERP@J − physique@T0`` sur un emplacement scellé, attendue nulle."""

    id: str
    campaign_id: str = Field(alias="campaignId")
    erp_journal_id: str | None = Field(default=None, alias="erpJournalId")
    warehouse_id: str = Field(alias="warehouseId")
    location_id: str = Field(alias="locationId")
    item_number: str = Field(alias="itemNumber")
    #: La référence de l'emplacement — le stock ERP d'avant son précomptage.
    qty_erp_t0: float = Field(alias="qtyErpT0")
    qty_physical_t0: float = Field(alias="qtyPhysicalT0")
    qty_erp_j: float = Field(alias="qtyErpJ")
    drift_qty: float = Field(alias="driftQty")
    drift_value: float = Field(alias="driftValue")
    is_material: bool = Field(alias="isMaterial")
    resolution: str | None = None
    cause_code: str = Field(default="", alias="causeCode")
    comment: str = ""
    resolved_at: str | None = Field(default=None, alias="resolvedAt")
    resolved_by: str = Field(default="", alias="resolvedBy")
    is_resolved: bool = Field(alias="isResolved")
    #: Vrai tant qu'une dérive matérielle n'a pas d'issue : le passage en
    #: analyse est alors refusé.
    blocks_analysis: bool = Field(alias="blocksAnalysis")


class DriftsResolved(Payload):
    resolved: int


class LabelAlert(Payload):
    """Une étiquette d'un emplacement scellé, comptée dans un autre journal.

    Le seul contrôle qui descende au grain de l'étiquette, et celui qui rattrape
    ce que la dérive ne voit pas.
    """

    label_id: str = Field(alias="labelId")
    item_number: str = Field(alias="itemNumber")
    sealed_warehouse_id: str = Field(alias="sealedWarehouseId")
    sealed_location_id: str = Field(alias="sealedLocationId")
    other_warehouse_id: str = Field(alias="otherWarehouseId")
    other_location_id: str = Field(alias="otherLocationId")
    other_journal_number: str = Field(alias="otherJournalNumber")
    other_qty_counted: float = Field(alias="otherQtyCounted")
    #: L'issue donnée, ou rien tant que personne n'est allé voir.
    decision: str | None = None
    comment: str = ""
    decided_by: str = Field(default="", alias="decidedBy")
    decided_at: str | None = Field(default=None, alias="decidedAt")


class RecountedInPlace(Payload):
    """Un emplacement scellé qu'un second journal a recompté **sur place**.

    Distinct de :class:`LabelAlert`, et la distinction porte : là, l'étiquette
    est où elle doit être, il n'y a pas de nouvel emplacement, et aucune des
    trois issues ne s'applique. Ce qui se joue est un second comptage du même
    emplacement — seul le journal qui le possède est retenu.
    """

    sealed_warehouse_id: str = Field(alias="sealedWarehouseId")
    sealed_location_id: str = Field(alias="sealedLocationId")
    owner_journal_number: str = Field(alias="ownerJournalNumber")
    other_journal_number: str = Field(alias="otherJournalNumber")
    label_count: int = Field(alias="labelCount")
