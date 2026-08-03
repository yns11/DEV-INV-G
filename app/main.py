"""Databricks Apps entry point.

The platform starts the app with ``python main.py`` (see ``app.yaml`` and the
``config.command`` block of ``databricks.yml``), and this module reads the port
from the environment itself.

Why not ``uvicorn main:app --port ${DATABRICKS_APP_PORT}``? ``${...}`` is also
the Asset Bundle interpolation syntax. The bundle resolver tries to expand it
against the bundle tree, finds no such node, and aborts the deployment with
``invalid dependency "${DATABRICKS_APP_PORT}", no such node ""``. Reading the
variable in Python keeps the placeholder out of any bundle-interpolated field,
and keeps a single start-up path shared by the platform and local runs.

Binding to ``0.0.0.0`` on the injected port is mandatory — a hard-coded port or
``localhost`` is the single most common cause of a 502 Bad Gateway here.
"""

from __future__ import annotations

import os

from inventory.api import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Keep these options in sync with the docstring above: this is the only
    # start-up path, so what runs locally is what runs on the platform.
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")),
        # A single process: the Lakebase pool is shared in-process, so extra
        # workers would multiply connections without adding throughput on the
        # 2 vCPU container.
        workers=1,
        # Slightly above the platform's 120 s proxy timeout budget so idle
        # keep-alive connections are not dropped mid-request.
        timeout_keep_alive=75,
        # Access logs are emitted by our own middleware as structured JSON.
        access_log=False,
        reload=os.environ.get("INV_ENV", "local") == "local",
        log_config=None,  # our JSON formatter is installed in the lifespan
    )
