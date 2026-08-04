"""The dry run and the commit must answer with the same shape.

The client cannot tell the two responses apart — it renders both with the same
component. When the preview was serialised by its own function it quietly lost
``warnings``, and reading ``result.warnings.length`` threw during render: React
unmounted the tree and the whole application went blank, with a 200 in the
server log and nothing else to go on.

So the shape is pinned here rather than trusted.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from inventory.config import Settings
from inventory.services.import_service import ImportOutcome, ImportService

TEXT = "Article\tDescription\tType\nP-001\tVis M6\tCOMPONENT\n"


def _preview(contract: str = "items", text: str = TEXT) -> dict[str, Any]:
    """A dry run touches no database: it only needs the row-count ceiling."""
    ctx = SimpleNamespace(settings=Settings())
    service = ImportService(cast(Any, ctx))
    return service.preview(contract, mode="paste", text=text)


def test_preview_carries_every_key_of_a_commit() -> None:
    committed = set(ImportOutcome(target="items").as_dict())
    previewed = set(_preview())
    assert committed <= previewed, f"clés perdues : {sorted(committed - previewed)}"


def test_preview_adds_only_the_sample() -> None:
    extra = set(_preview()) - set(ImportOutcome(target="items").as_dict())
    assert extra == {"sample"}


def test_the_collections_the_client_iterates_are_always_present() -> None:
    """Each of these is dereferenced unguarded by the import report."""
    report = _preview()
    for key in (
        "errors",
        "warnings",
        "missingColumns",
        "unknownColumns",
        "duplicateKeys",
    ):
        assert isinstance(report[key], list), f"{key} doit toujours être une liste"
    assert isinstance(report["details"], dict)


def test_preview_counts_the_rows_it_accepted() -> None:
    """`rowsAccepted` drives the confirmation button and the summary."""
    report = _preview()
    assert report["rowsAccepted"] == 1
    assert report["rowsReceived"] == 1
    assert report["rowsRejected"] == 0


def test_preview_reports_a_missing_required_column() -> None:
    report = _preview(text="Description\nVis M6\n")
    assert report["missingColumns"], "l'absence d'une colonne requise doit bloquer"
    assert report["ok"] is False
