"""Application error taxonomy.

The API layer maps each class onto an HTTP status once, in a single exception
handler, so routers never have to build error responses by hand.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "InventoryError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "WorkflowError",
    "FrozenError",
    "PermissionDeniedError",
    "UpstreamError",
]


class InventoryError(Exception):
    """Base class for every expected (i.e. non-bug) failure."""

    #: HTTP status the API layer should answer with.
    status_code: int = 400
    #: Stable machine-readable code, safe to branch on in the frontend.
    code: str = "inventory_error"

    def __init__(self, message: str, /, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationError(InventoryError):
    """Input did not satisfy a business or structural rule."""

    status_code = 422
    code = "validation_error"


class NotFoundError(InventoryError):
    """A referenced entity does not exist (or is not visible)."""

    status_code = 404
    code = "not_found"


class ConflictError(InventoryError):
    """Optimistic-concurrency or uniqueness conflict."""

    status_code = 409
    code = "conflict"


class WorkflowError(InventoryError):
    """The requested transition is not allowed from the current state."""

    status_code = 409
    code = "workflow_error"


class FrozenError(WorkflowError):
    """The entity is frozen by the campaign lifecycle and cannot be modified."""

    code = "frozen"


class PermissionDeniedError(InventoryError):
    status_code = 403
    code = "permission_denied"


class UpstreamError(InventoryError):
    """A dependency (warehouse, serving endpoint, Lakebase) failed."""

    status_code = 502
    code = "upstream_error"
