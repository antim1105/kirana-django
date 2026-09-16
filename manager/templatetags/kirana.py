"""
Template filters — the port of `utils/format.ts`.

Formatting lives here rather than in views so a template can show the same
number as currency, as a signed change or as a percentage without the view
having to decide in advance.

Indian digit grouping (1,23,456.78 rather than 123,456.78) is done by hand:
Python's `locale` module would need the en_IN locale generated on the host,
which is not a safe assumption on a shared server.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, ROUND_HALF_UP

from django import template
from django.utils.html import format_html

from ..models import ShopSettings
from ..services import analytics, calc

register = template.Library()

DASH = "—"

#: Tailwind classes per movement. Green is a price coming down, which is good
#: news for a buyer; red is a price going up.
TREND_CLASSES = {
    "up": {
        "text": "text-rose-700",
        "bg": "bg-rose-50",
        "border": "border-rose-200",
        "dot": "bg-rose-500",
        "stroke": "#e11d48",
    },
    "down": {
        "text": "text-emerald-700",
        "bg": "bg-emerald-50",
        "border": "border-emerald-200",
        "dot": "bg-emerald-500",
        "stroke": "#059669",
    },
    "same": {
        "text": "text-slate-600",
        "bg": "bg-slate-50",
        "border": "border-slate-200",
        "dot": "bg-slate-400",
        "stroke": "#64748b",
    },
}

TREND_TEXT = {
    "up": "Price increased",
    "down": "Price decreased",
    "same": "No change",
}

TREND_ARROW = {"up": "▲", "down": "▼", "same": "•"}


# --------------------------------------------------------------------------
# Numbers and money
# --------------------------------------------------------------------------


def group_indian(digits: str) -> str:
    """12345678 -> 1,23,45,678: last three digits, then pairs."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs: list[str] = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs + [tail])


def _quantise(value, decimals: int) -> Decimal:
    exponent = Decimal(1).scaleb(-int(decimals))
    return calc.to_decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)


@register.filter
def number(value, decimals: int = 2) -> str:
    """1234.5 -> 1,234.50. No currency symbol."""
    quantised = _quantise(value, decimals)
    text = f"{abs(quantised):f}"
    whole, _, fraction = text.partition(".")
    sign = "-" if quantised < 0 else ""
    grouped = group_indian(whole)
    return f"{sign}{grouped}.{fraction}" if fraction else f"{sign}{grouped}"


@register.simple_tag(takes_context=True)
def money(context, value, decimals: int = 2) -> str:
    """Currency with the shop's symbol: {% money rate %} -> ₹1,234.50"""
    if value is None or value == "":
        return DASH
    symbol = _symbol(context)
    quantised = _quantise(value, decimals)
    sign = "-" if quantised < 0 else ""
    return f"{sign}{symbol}{number(abs(quantised), decimals)}"


@register.simple_tag(takes_context=True)
def signed_money(context, value, decimals: int = 2) -> str:
    """Same as `money` but positive values carry a leading +."""
    quantised = _quantise(value, decimals)
    text = money(context, quantised, decimals)
    return f"+{text}" if quantised > 0 else text


def _symbol(context) -> str:
    settings_obj = context.get("settings_obj") if hasattr(context, "get") else None
    if settings_obj is not None:
        return settings_obj.currency_symbol
    return ShopSettings.load().currency_symbol


@register.filter
def percent(value) -> str:
    """12.5 -> +12.50%, -3 -> -3.00%, None -> —"""
    if value is None or value == "":
        return DASH
    quantised = _quantise(value, 2)
    sign = "+" if quantised > 0 else ""
    return f"{sign}{quantised:.2f}%"


@register.filter
def quantity(value, unit: str = "") -> str:
    """Drops pointless decimals: 50.00 kg reads better as 50 kg."""
    amount = calc.to_decimal(value)
    if amount == amount.to_integral_value():
        text = number(amount, 0)
    else:
        text = number(amount, 2)
    return f"{text} {unit}".strip()


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

_DATE_PATTERNS = {
    "dd-MM-yyyy": "%d-%m-%Y",
    "yyyy-MM-dd": "%Y-%m-%d",
    "dd/MM/yyyy": "%d/%m/%Y",
}


@register.simple_tag(takes_context=True)
def shop_date(context, value) -> str:
    """A date in the format chosen in Settings."""
    if not value:
        return DASH
    settings_obj = context.get("settings_obj") or ShopSettings.load()
    pattern = _DATE_PATTERNS.get(settings_obj.date_format, "%d-%m-%Y")
    return value.strftime(pattern)


@register.filter
def short_date(value) -> str:
    """"15 Sep" — the compact label used in charts and tight table cells."""
    if not value:
        return DASH
    return value.strftime("%d %b")


@register.filter
def month_label(key: str) -> str:
    """"2026-09" -> "September 2026"."""
    try:
        return analytics.month_label(key)
    except (ValueError, AttributeError):
        return key or DASH


@register.filter
def relative_date(value) -> str:
    """"Today", "Yesterday", "5 days ago" — for the activity feed."""
    if not isinstance(value, dt.date):
        return DASH
    days = (analytics.today() - value).days
    if days == 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 0:
        return f"In {abs(days)} days"
    if days < 30:
        return f"{days} days ago"
    months = round(days / 30)
    return "1 month ago" if months == 1 else f"{months} months ago"


# --------------------------------------------------------------------------
# Movement presentation
# --------------------------------------------------------------------------


@register.filter
def trend_class(trend: str, part: str = "text") -> str:
    """{{ change.trend|trend_class:'bg' }} -> bg-rose-50"""
    return TREND_CLASSES.get(trend or "same", TREND_CLASSES["same"]).get(part, "")


@register.filter
def trend_text(trend: str) -> str:
    return TREND_TEXT.get(trend or "same", TREND_TEXT["same"])


@register.filter
def trend_arrow(trend: str) -> str:
    return TREND_ARROW.get(trend or "same", TREND_ARROW["same"])


@register.simple_tag
def trend_badge(trend: str, label: str) -> str:
    classes = TREND_CLASSES.get(trend or "same", TREND_CLASSES["same"])
    return format_html(
        '<span class="inline-flex items-center gap-1 rounded-full border '
        '{} {} {} px-2 py-0.5 text-xs font-medium">'
        '<span aria-hidden="true">{}</span>{}</span>',
        classes["bg"],
        classes["border"],
        classes["text"],
        TREND_ARROW.get(trend or "same", "•"),
        label,
    )


# --------------------------------------------------------------------------
# Small helpers used by the templates
# --------------------------------------------------------------------------


@register.filter
def plural(count, word: str = "") -> str:
    """{{ n|plural:'product' }} -> "1 product" / "4 products"."""
    try:
        total = int(count)
    except (TypeError, ValueError):
        total = 0
    suffix = "" if total == 1 else "s"
    return f"{total} {word}{suffix}".strip()


@register.simple_tag(takes_context=True)
def query_string(context, **kwargs) -> str:
    """
    The current query string with some keys replaced.

    Lets a filter form keep its other selections when one link changes a
    single parameter. Passing None drops a key.
    """
    request = context.get("request")
    params = request.GET.copy() if request else {}
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode() if hasattr(params, "urlencode") else ""
    return f"?{encoded}" if encoded else ""


@register.filter
def dash(value):
    """Blank-safe display: empty values become an em dash."""
    if value is None or value == "":
        return DASH
    return value
