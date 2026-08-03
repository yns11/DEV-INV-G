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

import json
import logging
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from ..errors import InventoryError
from .routers import analysis, campaigns, counting, data, generic, reports

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

        reset_database()
        log.info("Shutdown complete")


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
    @app.get("/api/health", tags=["système"], summary="Sonde de santé")
    def health() -> dict[str, Any]:
        """Liveness and readiness in one payload.

        Always answers 200 so the platform does not recycle a container that is
        merely degraded; ``ready`` carries the truth.
        """
        from ..db import get_database

        database_ok = False
        if settings.lakebase_configured:
            try:
                database_ok = get_database(settings).ping()
            except Exception:  # pragma: no cover - infrastructure dependent
                database_ok = False
        return {
            "status": "ok" if database_ok else "degraded",
            "ready": database_ok,
            "version": app.version,
            "env": settings.env,
            "lakebaseConfigured": settings.lakebase_configured,
            "warehouseConfigured": bool(settings.warehouse_id),
            # False means the deployment shipped without `app/static/`, i.e.
            # the API answers but the browser gets no interface.
            "frontendBuilt": STATIC_DIR.exists(),
            "llmEndpoint": settings.llm_endpoint,
            "startupError": getattr(app.state, "startup_error", None),
        }

    @app.get("/api/me", tags=["système"], summary="Utilisateur connecté")
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
            "authenticated": actor not in ("local@dev", "unknown@unauthenticated"),
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
        analysis.router,
        reports.router,
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
    <a href="/api/docs">/api/docs</a></p>
</main></html>
"""


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

    assets = STATIC_DIR / "assets"
    if assets.exists():
        # Hashed filenames: safe to cache for a year.
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

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
            return FileResponse(candidate)
        return FileResponse(index)
