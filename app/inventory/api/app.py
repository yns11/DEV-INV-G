"""FastAPI application factory.

One process serves both the JSON API and the pre-built React SPA, because only
one service may bind to ``DATABRICKS_APP_PORT``.

Cross-cutting behaviour lives here and nowhere else:

* structured JSON logging to stdout — the only thing the platform captures;
* one exception handler mapping the error taxonomy onto HTTP statuses;
* a request-id on every request and every log line;
* schema migrations applied at start-up, before the first request is served.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import get_settings
from ..errors import InventoryError
from ..metrics import REGISTRY
from .responses import HealthResponse, MeResponse, MetricsResponse
from .routers import (
    analysis,
    assistant,
    campaigns,
    counting,
    data,
    evidence,
    generic,
    managers,
    reports,
    stock_flow,
)

log = logging.getLogger("inventory")

__all__ = ["create_app"]

#: Directory holding the built SPA (``npm run build`` copies it here).
STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Databricks Apps captures stdout/stderr only — file logs die with the
    container — and a structured line is the difference between grepping and
    querying when an inventory day goes wrong.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for attr in ("request_id", "actor", "path", "method", "status", "duration_ms"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log is replaced by our middleware, which knows the
    # request id and the acting user. Silence it at the source rather than
    # letting it propagate to the root handler.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    # uvicorn.error carries the start-up and shutdown lines, which we do want —
    # but only once. Clearing its handlers lets the record reach the root
    # handler installed above; adding a handler here as well would print every
    # line twice, doubling the log volume on a platform that only captures
    # stdout.
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers = []
    uvicorn_error.propagate = True


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #

def _migration_state(settings: Any) -> dict[str, Any]:
    """Applied versions, and the ones still missing.

    Never raises: this is the payload somebody reads *because* something is
    wrong, and it must not be the thing that fails.
    """
    if not settings.lakebase_configured:
        return {"applied": [], "pending": [], "error": "Lakebase non configuré."}
    try:
        from ..db import get_database
        from ..db.migrations import applied_versions, discover

        applied = applied_versions(get_database(settings))
        return {
            "applied": sorted(applied),
            "pending": [v for v, _ in discover() if v not in applied],
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - infrastructure dependent
        return {"applied": [], "pending": [], "error": str(exc)}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Apply migrations at start-up, dispose of the pool at shutdown.

    Migrations are idempotent, so several app replicas starting together is
    safe. A failure is logged but does *not* prevent start-up: the health
    endpoint then reports the degradation, which beats a container that
    crash-loops and shows nothing at all.
    """
    settings = get_settings()
    _configure_logging(settings.log_level)
    log.info("Starting %s (env=%s)", settings.app_name, settings.env)

    app.state.ready = False
    app.state.startup_error = None
    if settings.lakebase_configured:
        try:
            from ..db import get_database
            from ..db.migrations import apply_all

            database = get_database(settings)
            applied = apply_all(database)
            if applied:
                log.info("Applied migrations: %s", ", ".join(applied))
            app.state.ready = database.ping()
            # Un scan encore « en cours » appartient à un conteneur qui n'existe
            # plus : son PDF vivait dans sa mémoire. Le laisser dans cet état
            # afficherait une progression qui n'avancera jamais.
            from ..services import abandon_orphan_jobs

            abandon_orphan_jobs()
        except Exception as exc:  # pragma: no cover - infrastructure dependent
            app.state.startup_error = str(exc)
            log.exception("Lakebase initialisation failed")
    else:
        app.state.startup_error = (
            "Lakebase n'est pas configuré (PGHOST/PGDATABASE/PGUSER absents)."
        )
        log.warning(app.state.startup_error)

    try:
        yield
    finally:
        from ..db import reset_database
        from ..services import shutdown_workers

        # Le fil de scan avant le pool : il écrit dedans.
        shutdown_workers()
        reset_database()
        log.info("Shutdown complete")


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def _route_of(request: Request) -> str:
    """Le gabarit de la route, jamais le chemin appelé.

    ``/campaigns/{campaign_id}/items`` est une série ; le chemin brut en ferait
    une par campagne, et le registre grossirait avec l'usage. Une requête qui
    n'a atteint aucune route — un 404, un scan de vulnérabilité — n'a pas de
    gabarit : la ranger sous un seul nom est ce qui empêche mille chemins
    inventés de créer mille séries.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "(inconnue)"


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Campagnes Inventaire",
        description=(
            "Pilotage de bout en bout des campagnes d'inventaire physique : "
            "préparation, comptage, analyse & ajustements, clôture."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # ---- middleware --------------------------------------------------------
    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = (time.perf_counter() - started) * 1000
            log.exception(
                "Unhandled error",
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "method": request.method,
                    "duration_ms": round(duration, 1),
                },
            )
            raise
        duration = (time.perf_counter() - started) * 1000
        response.headers["x-request-id"] = request_id
        if request.url.path.startswith("/api"):
            REGISTRY.observe(
                request.method, _route_of(request), response.status_code, duration
            )
            log.info(
                "%s %s → %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "request_id": request_id,
                    "path": request.url.path,
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round(duration, 1),
                    "actor": request.headers.get("x-forwarded-email"),
                },
            )
        return response

    # ---- error handling ----------------------------------------------------
    @app.exception_handler(InventoryError)
    async def handle_domain_error(request: Request, exc: InventoryError) -> JSONResponse:
        """Map the error taxonomy onto HTTP once, for every route."""
        if exc.status_code >= 500:
            log.error("Upstream failure: %s", exc.message, extra={
                "request_id": getattr(request.state, "request_id", None)
            })
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "La requête est mal formée.",
                "details": {"errors": exc.errors()[:20]},
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Never leak an internal message to the browser.

        The request id is returned so a user can quote it and the exact stack
        trace can be found in the logs.
        """
        request_id = getattr(request.state, "request_id", None)
        log.exception("Unexpected error", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": (
                    "Une erreur interne est survenue. Communiquez l'identifiant "
                    "de requête au support."
                ),
                "details": {"requestId": request_id},
            },
        )

    # ---- routes ------------------------------------------------------------
    def _database_ok() -> bool:
        from ..db import get_database

        if not settings.lakebase_configured:
            return False
        try:
            return get_database(settings).ping()
        except Exception:  # pragma: no cover - infrastructure dependent
            return False

    @app.get("/api/health/live", tags=["système"], summary="Sonde de vivacité")
    def health_live() -> dict[str, Any]:
        """Le processus répond-il ? Rien d'autre.

        Aucune dépendance n'est consultée : la vivacité dit « ce conteneur
        n'est pas figé », et c'est sur cette réponse que la plateforme décide de
        le recycler. Y faire entrer l'état de Lakebase reviendrait à faire
        redémarrer en boucle des conteneurs parfaitement sains le jour où la
        base est indisponible — le redémarrage ne répare pas la base, et la
        rafale de reconnexions qu'il provoque l'empêche de revenir.
        """
        return {"status": "ok", "version": app.version}

    @app.get(
        "/api/health/ready",
        tags=["système"],
        summary="Sonde de disponibilité",
        responses={503: {"description": "Le conteneur ne peut pas servir."}},
    )
    def health_ready(response: Response) -> dict[str, Any]:
        """Ce conteneur peut-il servir une requête ? Répond 503 sinon.

        C'est la sonde que la plateforme lit pour décider de lui envoyer du
        trafic. Le diagnostic complet — ``/api/health`` — répond 200 quoi qu'il
        arrive, ce qui est juste pour un humain qui vient lire l'état et faux
        pour un orchestrateur : un conteneur dont les migrations ont échoué
        recevait des requêtes exactement comme les autres, et les servait avec
        des erreurs SQL. Ici, l'indisponibilité est dans le **code de statut**,
        seul endroit qu'une sonde regarde.

        Une migration en attente compte comme une indisponibilité : le schéma
        n'est pas celui que le code attend, et servir dans cet état produit des
        colonnes manquantes plutôt qu'un refus franc.
        """
        migrations = _migration_state(settings)
        ready = (
            _database_ok()
            and not migrations["pending"]
            and not migrations["error"]
            and getattr(app.state, "startup_error", None) is None
        )
        if not ready:
            response.status_code = 503
        return {
            "ready": ready,
            "database": _database_ok(),
            "pendingMigrations": migrations["pending"],
            "startupError": getattr(app.state, "startup_error", None),
        }

    @app.get(
        "/api/health",
        tags=["système"],
        summary="Diagnostic complet",
        responses={200: {"model": HealthResponse}},
    )
    def health() -> dict[str, Any]:
        """Tout ce qu'on peut savoir de ce conteneur, en une réponse.

        Répond toujours 200 : c'est une page de diagnostic, lue par un humain
        qui cherche pourquoi quelque chose ne marche pas, et une page de
        diagnostic qui refuse de s'afficher quand ça va mal ne sert à rien. Les
        deux sondes que la plateforme interroge sont ``/api/health/live`` et
        ``/api/health/ready``.
        """
        database_ok = _database_ok()
        return {
            "status": "ok" if database_ok else "degraded",
            "ready": database_ok,
            "version": app.version,
            "env": settings.env,
            "lakebaseConfigured": settings.lakebase_configured,
            "warehouseConfigured": bool(settings.warehouse_id),
            # Faux, l'application marche entièrement : les chargements
            # aboutissent, ils n'ont simplement aucune pièce jointe à proposer.
            # C'est exactement ce que ce diagnostic doit permettre de constater
            # avant qu'on le découvre en cherchant une feuille six mois plus
            # tard.
            "evidenceConfigured": settings.evidence_configured,
            # False means the deployment shipped without `app/static/`, i.e.
            # the API answers but the browser gets no interface.
            "frontendBuilt": STATIC_DIR.exists(),
            # *Which* interface it shipped. « J'ai redéployé et rien n'a
            # changé » has two causes that look identical from a browser — the
            # upload did not carry the new build, or the browser is serving a
            # cached shell — and no amount of reloading tells them apart. This
            # names the bundle the container actually holds, so one `curl`
            # settles it.
            "frontend": _frontend_state(),
            "llmEndpoint": settings.llm_endpoint,
            "startupError": getattr(app.state, "startup_error", None),
            # Which schema versions this container actually applied. A failed
            # migration is logged and start-up continues on purpose — a
            # crash-looping container shows nothing at all — but the trade-off
            # only works if the state is readable from outside. It was not:
            # « the app does not create the columns » took a round trip to
            # diagnose because nothing said which migrations had run.
            "migrations": _migration_state(settings),
        }

    @app.get(
        "/api/metrics",
        tags=["système"],
        summary="Métriques d'exploitation",
        responses={200: {"model": MetricsResponse}},
    )
    def metrics(hours: Annotated[int, Query(ge=1, le=168)] = 24) -> dict[str, Any]:
        """Ce qu'un exploitant vient mesurer quand la journée se passe mal.

        Quatre familles, et rien d'autre. Les **requêtes**, agrégées par
        gabarit de route : combien, combien en erreur, et combien de
        millisecondes au p95 — la seule façon de répondre à « qu'est-ce qui est
        lent » sans exporter des heures de journaux. Le **pool** de connexions,
        dont l'épuisement se manifestait jusqu'ici par des requêtes qui
        attendent quinze secondes puis échouent, sans que rien ne nomme la
        cause. Le **miroir ERP**, dont la fraîcheur décide si les écarts
        affichés veulent dire quelque chose. Les **chargements et les scans**
        récents, parce qu'un contrat mal accordé rejette quelques lignes à
        chaque fichier sans que personne n'ouvre le rapport.

        Répond toujours 200, comme ``/api/health`` : une page qu'on vient lire
        *parce que* quelque chose ne va pas ne doit pas être la deuxième chose
        qui ne marche pas. Chaque bloc porte donc son propre message d'erreur
        plutôt que de faire échouer la réponse entière.

        **JSON, et non le format d'exposition Prometheus.** Rien ne scrute ce
        conteneur : les applications Databricks n'exposent pas de cible de
        collecte, et ces compteurs vivent le temps du processus. Une seconde
        sérialisation, que personne n'analyserait, coûterait un format de plus
        à tenir à jour.
        """
        return {
            "version": app.version,
            "env": settings.env,
            "http": REGISTRY.snapshot(),
            "pool": _pool_state(),
            "erpMirror": _ops_block(lambda repo: repo.erp_freshness()),
            "imports": _ops_block(lambda repo: repo.import_volumes(hours=hours)),
            "scanJobs": _ops_block(lambda repo: repo.scan_jobs(hours=hours)),
        }

    def _pool_state() -> dict[str, Any]:
        """Les compteurs du pool psycopg, ou pourquoi il n'y en a pas.

        ``requests_waiting`` durablement non nul est le signe qu'on cherche :
        le pool est trop petit, ou une requête ne rend pas sa connexion.
        """
        from ..db import get_database

        if not settings.lakebase_configured:
            return {"error": "Lakebase non configuré."}
        try:
            return dict(get_database(settings).stats)
        except Exception as exc:  # pragma: no cover - infrastructure dependent
            return {"error": str(exc)}

    def _ops_block(read: Any) -> Any:
        """Un bloc de la réponse, ou son erreur, jamais une réponse en échec."""
        from ..db import get_database
        from ..db.repositories import OperationsRepository

        if not settings.lakebase_configured:
            return {"error": "Lakebase non configuré."}
        try:
            return read(OperationsRepository(get_database(settings)))
        except Exception as exc:  # pragma: no cover - infrastructure dependent
            log.warning("Lecture d'exploitation impossible: %s", exc)
            return {"error": str(exc)}

    @app.get(
        "/api/me",
        tags=["système"],
        summary="Utilisateur connecté",
        responses={200: {"model": MeResponse}},
    )
    def me(request: Request) -> dict[str, Any]:
        """Who the app thinks you are, and where that came from.

        Surfaced in the UI so nobody has to guess which identity their audit
        entries will carry.
        """
        from .deps import get_current_user

        actor = get_current_user(
            request,
            request.headers.get("x-forwarded-email"),
            request.headers.get("x-forwarded-preferred-username"),
            request.headers.get("x-forwarded-user"),
        )
        return {
            "actor": actor,
            # `get_current_user` refuse désormais une requête déployée sans
            # identité : arriver ici signifie qu'on en a une, ou qu'on est en
            # local. Seul le repli local reste « non authentifié ».
            "authenticated": actor != "local@dev",
            "source": (
                "databricks-apps"
                if request.headers.get("x-forwarded-email")
                else "local"
            ),
        }

    api_routers = (
        campaigns.router,
        data.router,
        counting.router,
        generic.router,
        managers.router,
        analysis.router,
        reports.router,
        evidence.router,
        assistant.router,
        stock_flow.router,
    )
    for router in api_routers:
        app.include_router(router, prefix="/api")

    # ---- SPA ---------------------------------------------------------------
    _mount_spa(app)
    return app


#: Shown at ``/`` when the SPA was never built. ``app/static/`` is generated,
#: so it is git-ignored and absent from a fresh clone: forgetting the build
#: step ships a working API with no interface. Without this page the browser
#: gets FastAPI's bare ``{"detail":"Not Found"}``, which says nothing about the
#: cause — and the app is otherwise healthy, so nothing else raises the alarm.
_NO_FRONTEND_PAGE = """<!doctype html>
<html lang="fr"><meta charset="utf-8">
<title>Interface non construite — Campagnes Inventaire</title>
<style>
 body{font:16px/1.6 system-ui,sans-serif;margin:0;display:grid;place-items:center;
      min-height:100vh;background:#0f172a;color:#e2e8f0}
 main{max-width:44rem;padding:2rem}
 h1{font-size:1.4rem;margin:0 0 .5rem}
 code,pre{background:#1e293b;border-radius:6px}
 code{padding:.1rem .35rem}
 pre{padding:1rem;overflow-x:auto}
 a{color:#7dd3fc}
</style>
<main>
 <h1>L'API fonctionne, l'interface n'a pas été construite</h1>
 <p>Le dossier <code>app/static/</code> est absent de ce déploiement. Il est
    généré à partir de <code>frontend/</code> et exclu du dépôt&nbsp;: il faut
    donc le construire <em>avant</em> chaque déploiement.</p>
 <pre>cd frontend
npm ci
npm run build          # écrit app/static/
databricks apps deploy -t prod --profile PROD</pre>
 <p>Sous Linux ou macOS, <code>make deploy</code> enchaîne les deux étapes.
    L'API reste utilisable en attendant&nbsp;:
    <a href="/api/health">/api/health</a> ·
    <a href="/api/health/ready">/api/health/ready</a> ·
    <a href="/api/metrics">/api/metrics</a> ·
    <a href="/api/docs">/api/docs</a></p>
</main></html>
"""


def _frontend_state() -> dict[str, Any]:
    """Which built SPA this container is serving.

    The hashed bundle name *is* the build identity: Vite derives it from the
    content, so two deployments of the same sources produce the same name and
    any change produces a different one. Comparing it with the local
    `app/static/assets/` after a build answers « did my deployment land? »
    without a single guess.

    Never raises: this is part of the payload somebody reads when something is
    already wrong.
    """
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return {"bundle": None, "builtAt": None, "assets": 0}
    try:
        html = index.read_text(encoding="utf-8", errors="replace")
        bundle = next(
            (m for m in _MAIN_BUNDLE.findall(html) if "index-" in m), None
        )
        assets = STATIC_DIR / "assets"
        return {
            "bundle": bundle,
            "builtAt": dt.datetime.fromtimestamp(
                index.stat().st_mtime, dt.UTC
            ).isoformat(),
            "assets": len(list(assets.iterdir())) if assets.is_dir() else 0,
        }
    except Exception as exc:  # pragma: no cover — depends on the filesystem
        return {"bundle": None, "builtAt": None, "assets": 0, "error": str(exc)}


#: `<script src="/assets/index-XXXXXXXX.js">`, as Vite writes it.
_MAIN_BUNDLE = re.compile(r"/assets/([A-Za-z0-9_.-]+\.js)")


#: ``index.html`` is the one file that must never be cached.
#:
#: Every other asset carries a content hash in its name, so a new build produces
#: new names and the old files can sit in the browser cache forever. The shell
#: is the opposite: same URL, new content at every deployment, and it is the
#: file that *names* the hashed bundles. Served without a directive, a browser
#: is free to cache it heuristically — and it does, typically for a tenth of the
#: file's age. That is exactly the « I redeployed and nothing changed » failure:
#: the shell comes from the cache, points at yesterday's bundle, and the old
#: interface loads perfectly.
#:
#: The cost is one request per navigation for a file of a few hundred bytes —
#: the shell names the bundles, it does not contain them. Nothing else pays:
#: everything it points at is hashed and cached below for a year.
_NEVER_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

#: A year, and immutable: the browser may not even revalidate.
_CACHE_FOREVER = {"Cache-Control": "public, max-age=31536000, immutable"}


def _cache_headers(path: Path) -> dict[str, str]:
    """How long a static file may be kept, decided by whether its name is a hash.

    Vite writes ``index-D5wpFZpw.js``; anything carrying that shape can be cached
    without limit. Everything else — the shell, a favicon, a manifest — keeps the
    conservative answer, because a stale one of those is indistinguishable from a
    failed deployment.
    """
    return _CACHE_FOREVER if _HASHED_NAME.search(path.name) else _NEVER_CACHE


#: ``name-<hash>.ext``, the shape Vite gives every file it fingerprints.
_HASHED_NAME = re.compile(r"-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$")


def _mount_spa(app: FastAPI) -> None:
    """Serve the built React app, with client-side routing support."""
    if not STATIC_DIR.exists():
        log.warning(
            "No built frontend at %s — API only. Run `make build-frontend`.",
            STATIC_DIR,
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        async def missing_frontend(full_path: str) -> Any:
            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"code": "not_found", "message": "Route API inconnue.",
                             "details": {"path": f"/{full_path}"}},
                )
            # 503, not 404: the route exists, its payload is missing.
            return HTMLResponse(_NO_FRONTEND_PAGE, status_code=503)

        return

    # No `StaticFiles` mount for `/assets`: it sets no `Cache-Control` of its
    # own, so the caching policy would live in two places and only one of them
    # would be right. The catch-all below already serves any real file under
    # `static/`, and serving everything through it puts the whole policy in
    # `_cache_headers`, where it can be read in one go.
    index = STATIC_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Any:
        """Serve a real file when one exists, otherwise the SPA shell.

        Anything under ``/api`` has already been matched by a router, so a miss
        there must 404 rather than silently return HTML — an HTML body where
        JSON was expected is a genuinely confusing failure to debug.
        """
        if full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"code": "not_found", "message": "Route API inconnue.",
                         "details": {"path": f"/{full_path}"}},
            )
        candidate = (STATIC_DIR / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(STATIC_DIR.resolve())
        ):
            return FileResponse(candidate, headers=_cache_headers(candidate))
        return FileResponse(index, headers=_NEVER_CACHE)
