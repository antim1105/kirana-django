"""
Money and movement arithmetic — the Python port of `utils/calc.ts`.

Everything works in `Decimal` rather than float, because these are rupee
amounts: two-place rounding has to be exact and repeatable, not
approximately right. `to_decimal` accepts whatever the form layer hands over
(str, int, float, Decimal, None) so callers never have to pre-clean values.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Literal, Optional, Union

Number = Union[int, float, str, Decimal, None]
Trend = Literal["up", "down", "same"]

CENTS = Decimal("0.01")
ZERO = Decimal("0")

#: Movements smaller than this count as no change at all, matching the
#: 0.0001 epsilon the TypeScript version used.
EPSILON = Decimal("0.0001")


def to_decimal(value: Number, default: Decimal = ZERO) -> Decimal:
    """Coerce anything form- or JSON-shaped into a Decimal, never raising."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def round2(value: Number) -> Decimal:
    """Round half-up to two places, the way a shopkeeper would."""
    return to_decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def difference(current: Number, previous: Number) -> Decimal:
    """difference = currentRate - previousRate"""
    return round2(to_decimal(current) - to_decimal(previous))


def percentage_change(current: Number, previous: Number) -> Decimal:
    """percentageChange = ((current - previous) / previous) * 100"""
    prev = to_decimal(previous)
    if prev == ZERO:
        return ZERO
    return round2((to_decimal(current) - prev) / prev * Decimal("100"))


def trend_of(diff: Number) -> Trend:
    """Which way a price moved. Up is bad news for a buyer, down is good."""
    value = to_decimal(diff)
    if value > EPSILON:
        return "up"
    if value < -EPSILON:
        return "down"
    return "same"


def effective_rate(
    purchase_rate: Number,
    quantity: Number,
    transport_cost: Number = ZERO,
    other_cost: Number = ZERO,
) -> Decimal:
    """
    The real per-unit cost once delivery is paid for.

        effectiveRate = purchaseRate + (transportCost + otherCost) / quantity

    So 42/kg for 100 kg with 200 transport is 44/kg. With no quantity there is
    nothing to spread the charges over, so the rate stands as quoted.
    """
    qty = to_decimal(quantity)
    rate = to_decimal(purchase_rate)
    if qty == ZERO:
        return round2(rate)
    extras = to_decimal(transport_cost) + to_decimal(other_cost)
    return round2(rate + extras / qty)


def total_amount(quantity: Number, rate: Number) -> Decimal:
    """totalAmount = quantity * rate"""
    return round2(to_decimal(quantity) * to_decimal(rate))


def final_amount(total: Number, transport_cost: Number, other_cost: Number) -> Decimal:
    """finalAmount = totalAmount + transportCost + otherCost"""
    return round2(to_decimal(total) + to_decimal(transport_cost) + to_decimal(other_cost))


def average(values: Iterable[Number]) -> Decimal:
    items = [to_decimal(value) for value in values]
    if not items:
        return ZERO
    return round2(sum(items, ZERO) / Decimal(len(items)))


def total(values: Iterable[Number]) -> Decimal:
    return round2(sum((to_decimal(value) for value in values), ZERO))


def safe_div(numerator: Number, denominator: Number) -> Optional[Decimal]:
    denom = to_decimal(denominator)
    if denom == ZERO:
        return None
    return round2(to_decimal(numerator) / denom)
