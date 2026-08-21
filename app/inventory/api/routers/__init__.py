"""API routers, one per bounded area of the product."""

from . import (
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

__all__ = [
    "analysis", "assistant", "campaigns", "counting", "data", "evidence", "generic",
    "managers", "reports", "stock_flow",
]
