"""Databricks Apps entry point.

The platform starts this module with uvicorn (see ``app.yaml``):

    uvicorn main:app --host 0.0.0.0 --port ${DATABRICKS_APP_PORT}

Binding to ``0.0.0.0`` on the injected port is mandatory — a hard-coded port or
``localhost`` is the single most common cause of a 502 Bad Gateway on this
platform. The ``__main__`` block below applies the same rule for local runs.
"""

from __future__ import annotations

import os

from inventory.api import create_app

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("DATABRICKS_APP_PORT", "8000")),
        reload=os.environ.get("INV_ENV", "local") == "local",
        log_config=None,  # our JSON formatter is installed in the lifespan
    )
