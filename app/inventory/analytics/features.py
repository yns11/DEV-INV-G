"""Feature engineering over a campaign's variances.

Turns the domain objects into a tidy ``pandas`` frame that every model, chart
and export in :mod:`inventory.analytics` shares. Building it once, in one place,
is what stops two screens from quoting two different numbers for the same thing
— the single most common failure of the spreadsheet it replaces.

The frame is the analytic contract: columns are documented, typed and stable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

import numpy as np
import pandas as pd

from ..domain.models import AdjustmentLine, Campaign, Item, VarianceLine, WipBreakdown
from ..domain.variance import is_material

__all__ = ["FEATURE_COLUMNS", "build_frame", "attach_wip_features", "attach_movement_features"]

#: Documented schema of the analytic frame. Anything not listed here is derived
#: downstream and must not be relied upon across modules.
FEATURE_COLUMNS: dict[str, str] = {
    "item_number": "Article",
    "warehouse_id": "Entrepôt (vide si agrégé par article)",
    "location_id": "Emplacement",
    "item_type": "COMPONENT / SEMI_FINISHED / FINISHED / PACKAGING",
    "category": "Famille métier (MEL, STATOR, …)",
    "program": "Programme (M3, M4, …); vide = commun",
    "unit": "Unité de stock",
    "unit_cost": "Coût unitaire figé au snapshot (€)",
    "book_qty": "Quantité stock ERP",
    "counted_qty": "Quantité comptée retenue",
    "adjusted_qty": "Quantité des ajustements postés",
    "book_value": "Valeur stock ERP (€)",
    "variance_qty": "Écart quantité = physique − ERP",
    "variance_value": "Écart valeur (€)",
    "abs_variance_value": "|Écart valeur| (€)",
    "physical_qty": "Stock physique = compté + ajustements",
    "counted_variance_qty": "Écart du comptage seul, avant ajustements",
    "variance_ratio": "|Écart| / stock ERP (NaN si ERP = 0)",
    "is_material": "Écart au-delà des seuils de son type",
    "counted_only": "Compté sans stock ERP",
    "book_only": "Stock ERP jamais compté",
    "book_value_share": "Part du stock ERP total",
    "variance_share": "Part de l'écart absolu total",
    "abs_variance_rank": "Rang par |écart valeur| (1 = plus gros)",
    "cumulative_variance_share": "Part cumulée (courbe de Pareto)",
}


def build_frame(
    variances: Sequence[VarianceLine],
    *,
    campaign: Campaign | None = None,
    items: Mapping[str, Item] | None = None,
) -> pd.DataFrame:
    """Build the analytic frame from reconciled variance lines.

    Decimals are converted to ``float`` here and only here: the exact arithmetic
    has already happened in the domain, and every model downstream needs floats.
    The rounding is documented rather than accidental.
    """
    if not variances:
        return pd.DataFrame(columns=list(FEATURE_COLUMNS))

    items = items or {}
    records = []
    for line in variances:
        item = items.get(line.item_number)
        material = (
            is_material(line, campaign.threshold_for(line.item_type))
            if campaign
            else False
        )
        records.append({
            "item_number": line.item_number,
            "warehouse_id": line.warehouse_id,
            "location_id": line.location_id,
            "item_type": str(line.item_type),
            "category": line.category or (item.category if item else ""),
            "program": line.program or (item.program if item else ""),
            "unit": line.unit,
            "unit_cost": _f(line.unit_cost),
            "book_qty": _f(line.book_qty),
            "counted_qty": _f(line.counted_qty),
            "adjusted_qty": _f(line.adjusted_qty),
            "book_value": _f(line.book_value),
            "variance_qty": _f(line.variance_qty),
            "variance_value": _f(line.variance_value),
            "physical_qty": _f(line.physical_qty),
            "counted_variance_qty": _f(line.counted_variance_qty),
            "is_material": material,
            "counted_only": line.counted_only,
            "book_only": line.book_only,
        })

    frame = pd.DataFrame.from_records(records)
    frame["abs_variance_value"] = frame["variance_value"].abs()

    # Relative variance: NaN (not 0, not inf) when there is no book quantity —
    # "no ratio exists" is a different statement from "the ratio is zero".
    with np.errstate(divide="ignore", invalid="ignore"):
        frame["variance_ratio"] = np.where(
            frame["book_qty"].abs() > 0,
            frame["variance_qty"].abs() / frame["book_qty"].abs(),
            np.nan,
        )

    total_book = float(frame["book_value"].abs().sum())
    total_abs = float(frame["abs_variance_value"].sum())
    frame["book_value_share"] = (
        frame["book_value"].abs() / total_book if total_book else 0.0
    )
    frame["variance_share"] = (
        frame["abs_variance_value"] / total_abs if total_abs else 0.0
    )

    frame = frame.sort_values("abs_variance_value", ascending=False).reset_index(drop=True)
    frame["abs_variance_rank"] = np.arange(1, len(frame) + 1)
    frame["cumulative_variance_share"] = frame["variance_share"].cumsum()
    return frame


def attach_wip_features(
    frame: pd.DataFrame, breakdown: Sequence[WipBreakdown] | Sequence[dict]
) -> pd.DataFrame:
    """Add "how much of this article's count came from exploded WIP?".

    A component whose counted quantity is mostly WIP-derived behaves very
    differently from one counted directly on a shelf: its variance is driven by
    BOM accuracy and production declarations, not by counting quality. Keeping
    the two apart is what makes a root-cause model useful instead of noisy.
    """
    if frame.empty:
        return frame
    if not breakdown:
        frame["wip_qty"] = 0.0
        frame["wip_share"] = 0.0
        frame["wip_parent_count"] = 0
        return frame

    rows = [
        b if isinstance(b, dict) else {
            "child_item": b.child_item,
            "child_qty": float(b.child_qty),
            "parent_item": b.parent_item,
        }
        for b in breakdown
    ]
    wip = pd.DataFrame.from_records(rows)
    wip["child_qty"] = pd.to_numeric(wip["child_qty"], errors="coerce").fillna(0.0)
    grouped = wip.groupby("child_item").agg(
        wip_qty=("child_qty", "sum"),
        wip_parent_count=("parent_item", "nunique"),
    )

    merged = frame.merge(
        grouped, left_on="item_number", right_index=True, how="left"
    )
    merged["wip_qty"] = merged["wip_qty"].fillna(0.0)
    merged["wip_parent_count"] = merged["wip_parent_count"].fillna(0).astype(int)
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["wip_share"] = np.where(
            merged["counted_qty"].abs() > 0,
            merged["wip_qty"] / merged["counted_qty"].abs(),
            0.0,
        )
    merged["wip_share"] = merged["wip_share"].clip(0.0, 1.0)
    return merged


def attach_movement_features(
    frame: pd.DataFrame, adjustments: Sequence[AdjustmentLine]
) -> pd.DataFrame:
    """Add post-count movement activity per article.

    A large variance that was fully absorbed by recounts is a *resolved* one; a
    large variance nobody has touched is the one that still needs a decision.
    """
    if frame.empty:
        return frame
    if not adjustments:
        frame["movement_count"] = 0
        frame["recount_qty"] = 0.0
        frame["adjustment_qty"] = 0.0
        frame["distinct_movement_days"] = 0
        return frame

    records = [
        {
            "item_number": a.item_number,
            "kind": str(a.kind),
            "qty": _f(a.qty),
            "day": a.physical_date,
        }
        for a in adjustments
    ]
    moves = pd.DataFrame.from_records(records)
    grouped = moves.groupby("item_number").agg(
        movement_count=("qty", "size"),
        distinct_movement_days=("day", "nunique"),
    )
    recount = (
        moves[moves["kind"] == "RECOUNT"].groupby("item_number")["qty"].sum()
        .rename("recount_qty")
    )
    adjust = (
        moves[moves["kind"] == "ADJUSTMENT"].groupby("item_number")["qty"].sum()
        .rename("adjustment_qty")
    )

    merged = frame.merge(grouped, left_on="item_number", right_index=True, how="left")
    merged = merged.merge(recount, left_on="item_number", right_index=True, how="left")
    merged = merged.merge(adjust, left_on="item_number", right_index=True, how="left")
    merged["movement_count"] = merged["movement_count"].fillna(0).astype(int)
    merged["distinct_movement_days"] = (
        merged["distinct_movement_days"].fillna(0).astype(int)
    )
    merged["recount_qty"] = merged["recount_qty"].fillna(0.0)
    merged["adjustment_qty"] = merged["adjustment_qty"].fillna(0.0)
    return merged


def _f(value: Decimal | float | int | None) -> float:
    return 0.0 if value is None else float(value)
