"""
Read-model functions — the Python port of `services/analytics.ts`.

Every function here answers one question a page asks ("what changed today?",
"who is cheapest?", "what did we spend in August?") and returns plain
dataclasses. Views stay thin and these stay easy to test.

The original file was pure: it took arrays and returned shapes. These take
querysets or ids and read the database, which is the point of the port — the
work moves to SQL. Where a function loops in Python instead, it is because the
comparison is positional ("this rate against the one below it"), which SQL
window functions could do but not more clearly at a kirana shop's data size.
"""

from __future__ import annotations

import datetime as dt
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from django.db.models import Count, DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import Product, Purchase, Rate, Wholesaler
from . import calc
from .calc import Trend

ZERO = Decimal("0")
MONEY_FIELD = DecimalField(max_digits=16, decimal_places=2)


def today() -> dt.date:
    """Today in the project's timezone, not UTC."""
    return timezone.localdate()


def month_key(value: dt.date) -> str:
    return value.strftime("%Y-%m")


def month_bounds(key: str) -> tuple[dt.date, dt.date]:
    """First and last day of a `YYYY-MM` key."""
    year, month = (int(part) for part in key.split("-")[:2])
    first = dt.date(year, month, 1)
    last = dt.date(year + (month == 12), (month % 12) + 1, 1) - dt.timedelta(days=1)
    return first, last


# --------------------------------------------------------------------------
# Rate movement
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RateChange:
    """A rate joined with the rate it replaced, and the movement between them."""

    rate: Rate
    previous_rate: Optional[Rate] = None
    difference: Decimal = ZERO
    percentage_change: Decimal = ZERO
    trend: Trend = "same"

    @property
    def product(self) -> Optional[Product]:
        return self.rate.product

    @property
    def wholesaler(self) -> Optional[Wholesaler]:
        return self.rate.wholesaler

    @property
    def has_history(self) -> bool:
        return self.previous_rate is not None

    @property
    def abs_difference(self) -> Decimal:
        return abs(self.difference)


def build_change(rate: Rate, previous: Optional[Rate] = None) -> RateChange:
    """
    Compare one rate with the one before it.

    With nothing to compare against, the movement is zero and the trend is
    "same" rather than a fake increase from nil.
    """
    if previous is None:
        return RateChange(rate=rate)
    diff = calc.difference(rate.purchase_rate, previous.purchase_rate)
    return RateChange(
        rate=rate,
        previous_rate=previous,
        difference=diff,
        percentage_change=calc.percentage_change(
            rate.purchase_rate, previous.purchase_rate
        ),
        trend=calc.trend_of(diff),
    )


def rates_for_product(product_id, rates: Optional[Iterable[Rate]] = None) -> list[Rate]:
    """One product's rates, newest first."""
    if rates is not None:
        return sort_newest_first([r for r in rates if r.product_id == product_id])
    return list(Rate.objects.for_product(product_id).with_related().newest_first())


def sort_newest_first(rates: Iterable[Rate]) -> list[Rate]:
    """In-Python version of the model's default ordering."""
    return sorted(
        rates,
        key=lambda r: (r.date, r.created_at, r.pk or 0),
        reverse=True,
    )


def latest_rate(product_id, rates: Optional[Iterable[Rate]] = None) -> Optional[Rate]:
    ordered = rates_for_product(product_id, rates)
    return ordered[0] if ordered else None


def latest_rate_from(product_id, wholesaler_id) -> Optional[Rate]:
    """The most recent rate from one wholesaler for one product."""
    return (
        Rate.objects.filter(product_id=product_id, wholesaler_id=wholesaler_id)
        .with_related()
        .newest_first()
        .first()
    )


def rate_history(product_id) -> list[RateChange]:
    """
    A product's whole history, newest first, each row against the row below.

    This is the table on the Rate history page.
    """
    ordered = rates_for_product(product_id)
    return [
        build_change(rate, ordered[index + 1] if index + 1 < len(ordered) else None)
        for index, rate in enumerate(ordered)
    ]


def latest_changes() -> list[RateChange]:
    """
    One row per product: its newest rate against the one before it.

    Powers the dashboard groups (costlier / cheaper / unchanged) and the
    "last change" column on the Products page. Products with no rates at all
    are left out — there is nothing to say about them yet.
    """
    grouped: dict[int, list[Rate]] = defaultdict(list)
    for rate in Rate.objects.with_related().newest_first():
        grouped[rate.product_id].append(rate)

    changes = []
    for ordered in grouped.values():
        changes.append(build_change(ordered[0], ordered[1] if len(ordered) > 1 else None))
    return changes


def recent_changes(limit: int = 8) -> list[RateChange]:
    """The newest rate entries across all products, each with its movement."""
    grouped: dict[int, list[Rate]] = defaultdict(list)
    all_rates = list(Rate.objects.with_related().newest_first())
    for rate in all_rates:
        grouped[rate.product_id].append(rate)

    changes = []
    for rate in all_rates[:limit]:
        history = grouped[rate.product_id]
        index = next(i for i, r in enumerate(history) if r.pk == rate.pk)
        previous = history[index + 1] if index + 1 < len(history) else None
        changes.append(build_change(rate, previous))
    return changes


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DashboardStats:
    total_products: int = 0
    total_wholesalers: int = 0
    todays_rates: int = 0
    increased: int = 0
    decreased: int = 0
    unchanged: int = 0
    total_purchase_amount: Decimal = ZERO


def dashboard_stats(changes: Optional[Sequence[RateChange]] = None) -> DashboardStats:
    if changes is None:
        changes = latest_changes()
    spend = Purchase.objects.aggregate(
        total=Coalesce(Sum("final_amount"), Value(ZERO), output_field=MONEY_FIELD)
    )["total"]
    return DashboardStats(
        total_products=Product.objects.count(),
        total_wholesalers=Wholesaler.objects.count(),
        todays_rates=Rate.objects.filter(date=today()).count(),
        increased=sum(1 for c in changes if c.trend == "up"),
        decreased=sum(1 for c in changes if c.trend == "down"),
        unchanged=sum(1 for c in changes if c.trend == "same"),
        total_purchase_amount=calc.round2(spend),
    )


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------


@dataclass(slots=True)
class WholesalerQuote:
    rate: Rate
    is_cheapest: bool = False
    difference_from_cheapest: Decimal = ZERO

    @property
    def wholesaler(self) -> Optional[Wholesaler]:
        return self.rate.wholesaler


def wholesaler_quotes(product_id) -> list[WholesalerQuote]:
    """
    The newest quote from each wholesaler for one product, cheapest first.

    Drives both the supplier comparison page and the "lowest price" hint on
    the rate form. Ties share the cheapest badge.
    """
    seen: "OrderedDict[int, Rate]" = OrderedDict()
    for rate in Rate.objects.for_product(product_id).with_related().newest_first():
        seen.setdefault(rate.wholesaler_id, rate)

    quotes = [WholesalerQuote(rate=rate) for rate in seen.values()]
    quotes.sort(key=lambda q: q.rate.purchase_rate)

    if quotes:
        lowest = quotes[0].rate.purchase_rate
        for quote in quotes:
            quote.is_cheapest = quote.rate.purchase_rate == lowest
            quote.difference_from_cheapest = calc.difference(
                quote.rate.purchase_rate, lowest
            )
    return quotes


def cheapest_quote(product_id) -> Optional[WholesalerQuote]:
    quotes = wholesaler_quotes(product_id)
    return quotes[0] if quotes else None


def rate_on_date(product_id, date: dt.date) -> Optional[Rate]:
    """
    The rate in effect on a date — the newest one recorded on or before it.

    This is why picking a date with no entry still resolves to something
    sensible instead of a blank row.
    """
    return (
        Rate.objects.for_product(product_id)
        .on_or_before(date)
        .with_related()
        .newest_first()
        .first()
    )


@dataclass(slots=True)
class DateComparison:
    product: Optional[Product] = None
    earlier_date: Optional[dt.date] = None
    later_date: Optional[dt.date] = None
    old_rate: Optional[Rate] = None
    new_rate: Optional[Rate] = None
    difference: Decimal = ZERO
    percentage_change: Decimal = ZERO
    trend: Trend = "same"

    @property
    def is_complete(self) -> bool:
        return self.old_rate is not None and self.new_rate is not None


def compare_dates(product_id, date1: dt.date, date2: dt.date) -> DateComparison:
    """Compare what a product cost on two dates, in either order."""
    earlier, later = (date1, date2) if date1 <= date2 else (date2, date1)
    product = Product.objects.filter(pk=product_id).first()
    old_rate = rate_on_date(product_id, earlier)
    new_rate = rate_on_date(product_id, later)

    comparison = DateComparison(
        product=product,
        earlier_date=earlier,
        later_date=later,
        old_rate=old_rate,
        new_rate=new_rate,
    )
    if not old_rate or not new_rate:
        return comparison

    comparison.difference = calc.difference(
        new_rate.purchase_rate, old_rate.purchase_rate
    )
    comparison.percentage_change = calc.percentage_change(
        new_rate.purchase_rate, old_rate.purchase_rate
    )
    comparison.trend = calc.trend_of(comparison.difference)
    return comparison


# --------------------------------------------------------------------------
# Chart data
# --------------------------------------------------------------------------


@dataclass(slots=True)
class PricePoint:
    date: dt.date
    rate: Decimal
    effective_rate: Decimal
    wholesaler_name: str


def price_series(product_id) -> list[PricePoint]:
    """Points for the price chart, oldest first."""
    return [
        PricePoint(
            date=rate.date,
            rate=rate.purchase_rate,
            effective_rate=rate.effective_rate,
            wholesaler_name=(
                rate.wholesaler.company_name if rate.wholesaler else "Unknown"
            ),
        )
        for rate in Rate.objects.for_product(product_id).with_related().oldest_first()
    ]


# --------------------------------------------------------------------------
# Spend
# --------------------------------------------------------------------------


@dataclass(slots=True)
class WholesalerSpend:
    wholesaler: Optional[Wholesaler]
    amount: Decimal
    purchases: int


def spend_by_wholesaler(month: Optional[str] = None) -> list[WholesalerSpend]:
    """Total spend per wholesaler, biggest first. Optionally for one month."""
    queryset = Purchase.objects.all()
    if month:
        first, last = month_bounds(month)
        queryset = queryset.filter(date__gte=first, date__lte=last)

    rows = (
        queryset.values("wholesaler")
        .annotate(
            amount=Coalesce(Sum("final_amount"), Value(ZERO), output_field=MONEY_FIELD),
            purchases=Count("id"),
        )
        .order_by("-amount")
    )
    wholesalers = Wholesaler.objects.in_bulk([row["wholesaler"] for row in rows])
    return [
        WholesalerSpend(
            wholesaler=wholesalers.get(row["wholesaler"]),
            amount=calc.round2(row["amount"]),
            purchases=row["purchases"],
        )
        for row in rows
    ]


@dataclass(slots=True)
class MonthPoint:
    month: str
    amount: Decimal


def purchase_trend(limit: Optional[int] = None) -> list[MonthPoint]:
    """Monthly purchase totals, oldest first — the spend trend line."""
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for date, amount in Purchase.objects.values_list("date", "final_amount"):
        totals[month_key(date)] += calc.to_decimal(amount)

    points = [
        MonthPoint(month=key, amount=calc.round2(value))
        for key, value in sorted(totals.items())
    ]
    return points[-limit:] if limit else points


def available_months() -> list[str]:
    """Every `YYYY-MM` that has data, newest first, plus the current month."""
    keys = {month_key(date) for date in Purchase.objects.values_list("date", flat=True)}
    keys.update(month_key(date) for date in Rate.objects.values_list("date", flat=True))
    keys.add(month_key(today()))
    return sorted(keys, reverse=True)


# --------------------------------------------------------------------------
# Monthly report
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ProductTotal:
    product: Optional[Product]
    quantity: Decimal
    amount: Decimal


@dataclass(slots=True)
class ExpensiveRow:
    product: Optional[Product]
    rate: Decimal
    unit: str


@dataclass(slots=True)
class MonthlyReport:
    month: str
    label: str
    total_purchase_amount: Decimal = ZERO
    total_quantity: Decimal = ZERO
    purchase_count: int = 0
    top_products: list[ProductTotal] = field(default_factory=list)
    most_expensive: list[ExpensiveRow] = field(default_factory=list)
    increased: list[RateChange] = field(default_factory=list)
    decreased: list[RateChange] = field(default_factory=list)
    by_wholesaler: list[WholesalerSpend] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return bool(
            self.purchase_count or self.increased or self.decreased or self.most_expensive
        )


def month_label(key: str) -> str:
    first, _ = month_bounds(key)
    return first.strftime("%B %Y")


def monthly_report(month: str, top_n: int = 5) -> MonthlyReport:
    """
    Everything the Reports page shows for one month.

    Rate movements are still measured against the previous rate wherever it
    falls, even if that was in an earlier month — otherwise the first entry of
    every month would look like a fresh start.
    """
    first, last = month_bounds(month)
    report = MonthlyReport(month=month, label=month_label(month))

    purchases = list(
        Purchase.objects.filter(date__gte=first, date__lte=last).select_related(
            "product", "wholesaler"
        )
    )
    report.purchase_count = len(purchases)
    report.total_purchase_amount = calc.total(p.final_amount for p in purchases)
    report.total_quantity = calc.total(p.quantity for p in purchases)

    product_totals: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for purchase in purchases:
        entry = product_totals[purchase.product_id]
        entry[0] += calc.to_decimal(purchase.quantity)
        entry[1] += calc.to_decimal(purchase.final_amount)

    products = Product.objects.in_bulk(product_totals.keys())
    report.top_products = sorted(
        (
            ProductTotal(
                product=products.get(product_id),
                quantity=calc.round2(quantity),
                amount=calc.round2(amount),
            )
            for product_id, (quantity, amount) in product_totals.items()
        ),
        key=lambda row: row.amount,
        reverse=True,
    )[:top_n]

    report.by_wholesaler = spend_by_wholesaler(month=month)

    # Rate movements recorded during the month.
    grouped: dict[int, list[Rate]] = defaultdict(list)
    for rate in Rate.objects.with_related().newest_first():
        grouped[rate.product_id].append(rate)

    changes: list[RateChange] = []
    newest_in_month: dict[int, Rate] = {}
    for history in grouped.values():
        for index, rate in enumerate(history):
            if not (first <= rate.date <= last):
                continue
            previous = history[index + 1] if index + 1 < len(history) else None
            changes.append(build_change(rate, previous))
            newest_in_month.setdefault(rate.product_id, rate)

    report.increased = sorted(
        (c for c in changes if c.trend == "up"),
        key=lambda c: c.difference,
        reverse=True,
    )
    report.decreased = sorted(
        (c for c in changes if c.trend == "down"), key=lambda c: c.difference
    )
    report.most_expensive = sorted(
        (
            ExpensiveRow(product=rate.product, rate=rate.purchase_rate, unit=rate.unit)
            for rate in newest_in_month.values()
        ),
        key=lambda row: row.rate,
        reverse=True,
    )[:top_n]

    return report
