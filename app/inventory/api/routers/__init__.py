"""API routers, one per bounded area of the product."""

from . import analysis, assistant, campaigns, counting, data, generic, managers, reports

__all__ = [
    "analysis", "assistant", "campaigns", "counting", "data", "generic",
    "managers", "reports",
]
