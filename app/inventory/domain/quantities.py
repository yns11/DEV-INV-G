"""Decimal helpers for quantities and money.

Stock valuation and reconciliation must be *reproducible*: summing the same set
of lines twice has to yield bit-identical results, and a total must equal the
sum of the parts it is displayed next to. Binary floats do not guarantee either
(``0.1 + 0.2 != 0.3``), which is one root cause of the "totals that do not tie
out" symptom in the legacy workbook.

The domain therefore uses :class:`decimal.Decimal` end to end:

* quantities keep 6 decimal places — enough for kilograms, metres and litres
  that appear in the bill of materials (``4.86 KG``, ``0.23 KG``, ``11 m``);
* money keeps 2 decimal places, rounded half-up, the accounting convention.

Rounding happens at well-defined boundaries only (persistence and display), so
intermediate arithmetic never loses precision.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

__all__ = [
    "QTY_EXP",
    "MONEY_EXP",
    "ZERO",
    "to_decimal",
    "quantize_qty",
    "quantize_money",
    "safe_ratio",
]

#: Six decimal places for quantities (grams, metres, fractional BOM ratios).
QTY_EXP = Decimal("0.000001")
#: Two decimal places for monetary amounts.
MONEY_EXP = Decimal("0.01")

ZERO = Decimal("0")


def to_decimal(value: Any, *, default: Decimal | None = None) -> Decimal:
    """Coerce *value* to :class:`Decimal` without ever going through binary float.

    Accepts ``Decimal``, ``int``, ``str`` and ``float``. Floats are routed
    through :meth:`Decimal.from_float` then normalised, which keeps the exact
    binary value rather than a surprising ``0.1 -> 0.1000000000000000055…``
    literal in the database.

    Strings tolerate the two separators the ERP and French Excel exports mix:
    ``"1 234,56"``, ``"1,234.56"`` and ``"1234.56"`` all parse to the same value.

    :raises ValueError: when the value cannot be interpreted and no *default*
        was supplied. Silent coercion to zero is exactly what hid broken cells
        in the Excel process, so it is never the default behaviour.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ValueError("boolean is not a numeric quantity")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal.from_float(value).quantize(QTY_EXP, rounding=ROUND_HALF_UP)
    if isinstance(value, str):
        text = value.strip().replace(" ", "").replace(" ", "")
        if not text:
            if default is not None:
                return default
            raise ValueError("empty string is not a numeric quantity")
        # "1.234,56" (fr) -> "1234.56" ; "1,234.56" (en) -> "1234.56"
        if "," in text and "." in text:
            text = (
                text.replace(".", "").replace(",", ".")
                if text.rfind(",") > text.rfind(".")
                else text.replace(",", "")
            )
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            if default is not None:
                return default
            raise ValueError(f"{value!r} is not a numeric quantity") from exc
    if value is None:
        if default is not None:
            return default
        raise ValueError("missing numeric quantity")
    raise ValueError(f"unsupported numeric type: {type(value).__name__}")


def quantize_qty(value: Decimal) -> Decimal:
    """Round a quantity to the storage precision (6 decimals, half-up)."""
    return value.quantize(QTY_EXP, rounding=ROUND_HALF_UP)


def quantize_money(value: Decimal) -> Decimal:
    """Round an amount to cents (half-up, the accounting convention)."""
    return value.quantize(MONEY_EXP, rounding=ROUND_HALF_UP)


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Return ``numerator / denominator`` or ``None`` when the base is zero.

    Returning ``None`` rather than 0 or ``inf`` keeps "no meaningful ratio"
    distinguishable from "ratio of zero" in every KPI downstream.
    """
    if denominator == 0:
        return None
    return numerator / denominator
