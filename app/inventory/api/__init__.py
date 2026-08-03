"""HTTP layer: FastAPI routers, request/response schemas and dependencies.

Routers contain no business logic. They validate input, call one service method
and shape the response — so the same use case can be driven from a job, a test
or a future CLI without going through HTTP.
"""

from .app import create_app

__all__ = ["create_app"]
