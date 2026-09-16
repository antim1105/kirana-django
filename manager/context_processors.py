"""Values every template needs: shop settings and the sidebar items."""

from __future__ import annotations

from django.db import OperationalError, ProgrammingError

from .models import ShopSettings

#: The sidebar, ported from `components/layout/navItems.ts`.
NAV_ITEMS = [
    {"url": "dashboard", "label": "Dashboard", "glyph": "◧", "description": "Today's picture"},
    {"url": "products", "label": "Products", "glyph": "▤", "description": "What you stock"},
    {"url": "wholesalers", "label": "Wholesalers", "glyph": "⌂", "description": "Who you buy from"},
    {"url": "add_rate", "label": "Add rate", "glyph": "₹", "description": "Record a quote"},
    {"url": "rate_history", "label": "Rate history", "glyph": "⟲", "description": "Every past rate"},
    {"url": "compare", "label": "Compare rates", "glyph": "⇄", "description": "Dates and suppliers"},
    {"url": "purchases", "label": "Purchases", "glyph": "▦", "description": "Actual buying"},
    {"url": "reports", "label": "Reports", "glyph": "◨", "description": "Monthly and changes"},
    {"url": "settings", "label": "Settings", "glyph": "⚙", "description": "Shop details"},
]


def shop_context(request):
    """
    Adds `settings_obj` and `nav_items` to every template.

    Wrapped in a try/except so `manage.py` commands that run before the first
    migration still work instead of blowing up on a missing table.
    """
    try:
        settings_obj = ShopSettings.load()
    except (OperationalError, ProgrammingError):  # pragma: no cover
        settings_obj = ShopSettings()
    return {"settings_obj": settings_obj, "nav_items": NAV_ITEMS}
