"""
Views — one per page of the original React app.

Where the React version kept everything in memory and re-rendered on state
change, these read what the page needs and hand it to a template. Two
patterns are worth pointing out:

* **Adding a rate is two POSTs.** The first validates and shows a review
  screen (movement, effective rate, cheapest supplier, and a warning if an
  entry for that day already exists). The second writes. That is the React
  confirmation modal turned into a page, so nothing is saved until the second
  press — and it works without JavaScript.

* **Forms are pages, not modals.** A dialog holding a form does not survive a
  page reload or a back button on a server-rendered app. A `?next=` parameter
  carries the person back to the list they came from.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    CategoryForm,
    CompareDatesForm,
    CompareWholesalersForm,
    ImportRatesForm,
    ProductFilterForm,
    ProductForm,
    PurchaseFilterForm,
    PurchaseForm,
    RateForm,
    RateHistoryFilterForm,
    ShopSettingsForm,
    WholesalerForm,
)
from .models import Category, Product, Purchase, Rate, ShopSettings, Wholesaler
from .services import analytics, calc, charts, demo_data, exporters


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def guard(view):
    """Applies login_required only when REQUIRE_LOGIN is switched on."""
    if getattr(django_settings, "REQUIRE_LOGIN", False):
        return login_required(view)
    return view


def safe_next(request, fallback: str) -> str:
    """A redirect target from `?next=`, only if it points at this site."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


@guard
@require_GET
def dashboard(request):
    """Today's picture: what moved, what was recorded, what was spent."""
    changes = analytics.latest_changes()
    stats = analytics.dashboard_stats(changes)

    # Newest first, then by the size of the movement, as in the original.
    changes.sort(key=lambda c: (c.rate.date, c.abs_difference), reverse=True)

    movement = request.GET.get("movement", "")
    if movement in {"up", "down", "same"}:
        visible = [c for c in changes if c.trend == movement]
    else:
        movement = ""
        visible = changes

    spend = analytics.spend_by_wholesaler()
    context: dict[str, Any] = {
        "page_title": "Dashboard",
        "stats": stats,
        "changes": visible[:8],
        "total_changes": len(changes),
        "movement": movement,
        "filters": [
            {"key": "", "label": "All products", "count": len(changes)},
            {"key": "up", "label": "Costlier", "count": stats.increased},
            {"key": "down", "label": "Cheaper", "count": stats.decreased},
            {"key": "same", "label": "Unchanged", "count": stats.unchanged},
        ],
        "recent_rates": analytics.recent_changes(6),
        "recent_purchases": Purchase.objects.select_related("product", "wholesaler")[:5],
        "latest_increase": next((c for c in changes if c.trend == "up"), None),
        "latest_decrease": next((c for c in changes if c.trend == "down"), None),
        "bars": charts.trend_bars(analytics.purchase_trend(limit=6)),
        "spend": charts.share_rows(
            [
                (
                    row.wholesaler.company_name if row.wholesaler else "Removed wholesaler",
                    row.amount,
                    f"{row.purchases} purchase{'s' if row.purchases != 1 else ''}",
                )
                for row in spend[:4]
            ]
        ),
        "has_products": Product.objects.exists(),
        "has_rates": Rate.objects.exists(),
        "today": analytics.today(),
    }
    return render(request, "manager/dashboard.html", context)


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------


@guard
@require_GET
def products(request):
    """The product list, each row showing its latest rate and last movement."""
    filter_form = ProductFilterForm(request.GET or None)
    queryset = filter_form.filter(Product.objects.all())

    changes = {change.rate.product_id: change for change in analytics.latest_changes()}
    rows = [{"product": product, "change": changes.get(product.pk)} for product in queryset]

    context = {
        "page_title": "Products",
        "rows": rows,
        "filter_form": filter_form,
        "total": Product.objects.count(),
        "filtered": len(rows),
        "is_filtered": bool(request.GET.get("q") or request.GET.get("category")),
    }
    return render(request, "manager/products.html", context)


@guard
def product_form(request, pk: int | None = None):
    product = get_object_or_404(Product, pk=pk) if pk else None
    form = ProductForm(request.POST or None, instance=product)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(
            request,
            f"Saved {saved.name}." if product else f"Added {saved.name}.",
        )
        return redirect(safe_next(request, reverse("products")))

    context = {
        "page_title": "Edit product" if product else "Add product",
        "form": form,
        "product": product,
        "next": safe_next(request, reverse("products")),
        "cancel_url": safe_next(request, reverse("products")),
        "rate_count": product.rates.count() if product else 0,
    }
    return render(request, "manager/product_form.html", context)


@guard
def product_delete(request, pk: int):
    """
    Deleting a product takes its rates and purchases with it.

    The confirmation page says exactly how many rows will go, because the
    cascade is the part that surprises people.
    """
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        name = product.name
        product.delete()
        messages.success(request, f"Deleted {name} and everything recorded against it.")
        return redirect(safe_next(request, reverse("products")))

    context = {
        "page_title": f"Delete {product.name}?",
        "object": product,
        "kind": "product",
        "warning": (
            "Its rate history and purchase records will be deleted too. "
            "This cannot be undone."
        ),
        "counts": [
            ("rates", product.rates.count()),
            ("purchases", product.purchases.count()),
        ],
        "cancel_url": safe_next(request, reverse("products")),
        "next": safe_next(request, reverse("products")),
    }
    return render(request, "manager/confirm_delete.html", context)


# --------------------------------------------------------------------------
# Wholesalers
# --------------------------------------------------------------------------


@guard
@require_GET
def wholesalers(request):
    query = (request.GET.get("q") or "").strip()
    queryset = Wholesaler.objects.annotate(
        rate_count=Count("rates", distinct=True),
        purchase_total=Coalesce(
            Sum("purchases__final_amount"),
            Value(Decimal("0")),
            output_field=Purchase._meta.get_field("final_amount"),
        ),
    )
    if query:
        queryset = queryset.filter(
            Q(company_name__icontains=query)
            | Q(name__icontains=query)
            | Q(mobile__icontains=query)
            | Q(address__icontains=query)
        )

    context = {
        "page_title": "Wholesalers",
        "wholesalers": queryset,
        "query": query,
        "total": Wholesaler.objects.count(),
        "filtered": queryset.count(),
    }
    return render(request, "manager/wholesalers.html", context)


@guard
def wholesaler_form(request, pk: int | None = None):
    wholesaler = get_object_or_404(Wholesaler, pk=pk) if pk else None
    form = WholesalerForm(request.POST or None, instance=wholesaler)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(
            request,
            f"Saved {saved.company_name}." if wholesaler else f"Added {saved.company_name}.",
        )
        return redirect(safe_next(request, reverse("wholesalers")))

    context = {
        "page_title": "Edit wholesaler" if wholesaler else "Add wholesaler",
        "form": form,
        "wholesaler": wholesaler,
        "cancel_url": safe_next(request, reverse("wholesalers")),
        "next": safe_next(request, reverse("wholesalers")),
    }
    return render(request, "manager/wholesaler_form.html", context)


@guard
def wholesaler_delete(request, pk: int):
    wholesaler = get_object_or_404(Wholesaler, pk=pk)
    if request.method == "POST":
        name = wholesaler.company_name
        wholesaler.delete()
        messages.success(request, f"Deleted {name} and everything recorded against them.")
        return redirect(safe_next(request, reverse("wholesalers")))

    context = {
        "page_title": f"Delete {wholesaler.company_name}?",
        "object": wholesaler,
        "kind": "wholesaler",
        "warning": (
            "Every rate they quoted and every purchase from them will be "
            "deleted too. This cannot be undone."
        ),
        "counts": [
            ("rates", wholesaler.rates.count()),
            ("purchases", wholesaler.purchases.count()),
        ],
        "cancel_url": safe_next(request, reverse("wholesalers")),
        "next": safe_next(request, reverse("wholesalers")),
    }
    return render(request, "manager/confirm_delete.html", context)


# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------


def _rate_preview(product, wholesaler, rate_value, quantity, transport, other, exclude_pk=None):
    """
    The panel beside the rate form: what this quote means.

    Previous rate comes from the same wholesaler when there is one, otherwise
    the newest from anyone — so the comparison is never blank without reason.
    """
    preview: dict[str, Any] = {
        "product": product,
        "previous": None,
        "previous_source": "",
        "effective_rate": calc.effective_rate(rate_value, quantity, transport, other),
        "difference": Decimal("0"),
        "percentage_change": Decimal("0"),
        "trend": "same",
        "cheapest": None,
        "extra_cost": calc.round2(calc.to_decimal(transport) + calc.to_decimal(other)),
    }
    if not product:
        return preview

    previous = None
    if wholesaler:
        previous = analytics.latest_rate_from(product.pk, wholesaler.pk)
        if previous and previous.pk == exclude_pk:
            previous = None
        if previous:
            preview["previous_source"] = f"Last from {wholesaler.company_name}"

    if previous is None:
        candidates = [r for r in analytics.rates_for_product(product.pk) if r.pk != exclude_pk]
        previous = candidates[0] if candidates else None
        if previous:
            preview["previous_source"] = (
                f"Last recorded, from {previous.wholesaler.company_name}"
            )

    preview["previous"] = previous
    if previous and calc.to_decimal(rate_value) > 0:
        diff = calc.difference(rate_value, previous.purchase_rate)
        preview["difference"] = diff
        preview["percentage_change"] = calc.percentage_change(
            rate_value, previous.purchase_rate
        )
        preview["trend"] = calc.trend_of(diff)

    preview["cheapest"] = analytics.cheapest_quote(product.pk)
    return preview


@guard
def add_rate(request, pk: int | None = None):
    """
    Record a quote, or edit one.

    Three POST actions:
      * `review` — validate and show the summary
      * `save`   — write it, optionally replacing a same-day entry
      * anything else falls back to showing the form again
    """
    instance = get_object_or_404(Rate, pk=pk) if pk else None
    action = request.POST.get("action", "")
    form = RateForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        duplicate = form.find_duplicate()
        # `replace_id` is only honoured when the person pressed "Update the
        # existing one", so the second button on that screen still creates a
        # separate entry.
        replace_id = request.POST.get("replace_id") if action == "replace" else ""

        if action in {"save", "replace"}:
            if replace_id:
                # "Update existing" on the duplicate warning.
                target = get_object_or_404(Rate, pk=replace_id)
                for field_name, value in data.items():
                    setattr(target, field_name, value)
                target.unit = data["product"].unit
                target.save()
                messages.success(
                    request,
                    f"Updated the {data['date']:%d-%m-%Y} rate for {target.product.name}.",
                )
            else:
                saved = form.save()
                messages.success(
                    request,
                    f"Saved {saved.product.name} at {saved.purchase_rate} per {saved.unit}"
                    f" from {saved.wholesaler.company_name}.",
                )
            if instance:
                return redirect(
                    safe_next(
                        request,
                        f"{reverse('rate_history')}?product={data['product'].pk}",
                    )
                )
            # Adding: stay here so several quotes can be entered in a row.
            return redirect(f"{reverse('add_rate')}?date={data['date']:%Y-%m-%d}")

        if action == "review":
            preview = _rate_preview(
                data["product"],
                data["wholesaler"],
                data["purchase_rate"],
                data["quantity"],
                data["transport_cost"],
                data["other_cost"],
                exclude_pk=instance.pk if instance else None,
            )
            context = {
                "page_title": "Review this rate",
                "form": form,
                "preview": preview,
                "duplicate": duplicate,
                "instance": instance,
                "values": data,
                "hidden_fields": _rate_hidden_fields(request.POST),
                "next": safe_next(request, ""),
            }
            return render(request, "manager/rate_review.html", context)

    # GET, or a form that did not validate.
    initial_product = None
    if request.method == "GET":
        product_id = request.GET.get("product")
        date = parse_date(request.GET.get("date"))
        initial: dict[str, Any] = {}
        if product_id and Product.objects.filter(pk=product_id).exists():
            initial["product"] = product_id
            initial_product = Product.objects.get(pk=product_id)
        if date:
            initial["date"] = date
        if initial:
            form = RateForm(initial=initial, instance=instance)

    preview = None
    if instance:
        preview = _rate_preview(
            instance.product,
            instance.wholesaler,
            instance.purchase_rate,
            instance.quantity,
            instance.transport_cost,
            instance.other_cost,
            exclude_pk=instance.pk,
        )
    elif initial_product:
        preview = _rate_preview(initial_product, None, 0, 0, 0, 0)

    context = {
        "page_title": "Edit rate" if instance else "Add rate",
        "form": form,
        "instance": instance,
        "preview": preview,
        "has_products": Product.objects.exists(),
        "has_wholesalers": Wholesaler.objects.exists(),
        "recent": Rate.objects.with_related().newest_first()[:5],
        "next": safe_next(request, ""),
    }
    return render(request, "manager/add_rate.html", context)


def _rate_hidden_fields(post) -> list[dict[str, str]]:
    """The submitted values, ready to be re-posted from the review screen."""
    keys = [
        "product",
        "wholesaler",
        "date",
        "purchase_rate",
        "quantity",
        "transport_cost",
        "other_cost",
        "notes",
    ]
    return [{"name": key, "value": post.get(key, "")} for key in keys]


@guard
def rate_delete(request, pk: int):
    rate = get_object_or_404(Rate.objects.with_related(), pk=pk)
    fallback = f"{reverse('rate_history')}?product={rate.product_id}"
    if request.method == "POST":
        product_name = rate.product.name
        date = rate.date
        rate.delete()
        messages.success(request, f"Deleted the {date:%d-%m-%Y} rate for {product_name}.")
        return redirect(safe_next(request, fallback))

    context = {
        "page_title": "Delete this rate?",
        "object": rate,
        "kind": "rate",
        "warning": (
            "Only this one entry is removed. The rest of the product's history "
            "stays as it is."
        ),
        "counts": [],
        "cancel_url": safe_next(request, fallback),
        "next": safe_next(request, fallback),
    }
    return render(request, "manager/confirm_delete.html", context)


@guard
@require_GET
def rate_history(request):
    """One product's full history, with a chart and the usual filters."""
    filter_form = RateHistoryFilterForm(request.GET or None)
    filter_form.is_valid()
    data = filter_form.cleaned_data if filter_form.is_bound else {}

    product = data.get("product")
    # Fall back to the first product so the page is never empty for no reason.
    if not product and not request.GET.get("product"):
        product = Product.objects.first()
        if product:
            filter_form = RateHistoryFilterForm(initial={"product": product.pk})

    changes: list[analytics.RateChange] = []
    chart = charts.LineChart()
    quotes: list[analytics.WholesalerQuote] = []

    if product:
        changes = analytics.rate_history(product.pk)
        chart = charts.price_chart(analytics.price_series(product.pk))
        quotes = analytics.wholesaler_quotes(product.pk)

        wholesaler = data.get("wholesaler")
        movement = data.get("movement")
        date_from = data.get("date_from")
        date_to = data.get("date_to")

        if wholesaler:
            changes = [c for c in changes if c.rate.wholesaler_id == wholesaler.pk]
        if movement:
            changes = [c for c in changes if c.trend == movement]
        if date_from:
            changes = [c for c in changes if c.rate.date >= date_from]
        if date_to:
            changes = [c for c in changes if c.rate.date <= date_to]

    rates = [c.rate for c in changes]
    summary = {
        "count": len(changes),
        "lowest": min((r.purchase_rate for r in rates), default=None),
        "highest": max((r.purchase_rate for r in rates), default=None),
        "average": calc.average([r.purchase_rate for r in rates]) if rates else None,
        "increases": sum(1 for c in changes if c.trend == "up"),
        "decreases": sum(1 for c in changes if c.trend == "down"),
    }

    context = {
        "page_title": "Rate history",
        "filter_form": filter_form,
        "product": product,
        "changes": changes,
        "chart": chart,
        "quotes": quotes,
        "summary": summary,
        "has_products": Product.objects.exists(),
        "is_filtered": any(
            request.GET.get(key) for key in ("wholesaler", "movement", "date_from", "date_to")
        ),
    }
    return render(request, "manager/rate_history.html", context)


# --------------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------------


@guard
@require_GET
def compare(request):
    """
    Two comparisons on one page: date against date, and supplier against
    supplier. The `mode` parameter picks the tab.
    """
    mode = request.GET.get("mode", "dates")
    if mode not in {"dates", "wholesalers"}:
        mode = "dates"

    today = analytics.today()
    date_form = CompareDatesForm(
        request.GET if request.GET.get("date1") else None,
        initial={
            "date1": today - dt.timedelta(days=7),
            "date2": today,
            "product": request.GET.get("product") or None,
        },
    )
    wholesaler_form = CompareWholesalersForm(
        request.GET if request.GET.get("product") and mode == "wholesalers" else None,
        initial={"product": request.GET.get("product") or None},
    )

    comparison = None
    quotes: list[analytics.WholesalerQuote] = []
    chart = charts.LineChart()

    if mode == "dates" and date_form.is_bound and date_form.is_valid():
        data = date_form.cleaned_data
        comparison = analytics.compare_dates(
            data["product"].pk, data["date1"], data["date2"]
        )
        chart = charts.price_chart(analytics.price_series(data["product"].pk))

    if mode == "wholesalers" and wholesaler_form.is_bound and wholesaler_form.is_valid():
        product = wholesaler_form.cleaned_data["product"]
        quotes = analytics.wholesaler_quotes(product.pk)
        chart = charts.price_chart(analytics.price_series(product.pk))

    context = {
        "page_title": "Compare rates",
        "mode": mode,
        "date_form": date_form,
        "wholesaler_form": wholesaler_form,
        "comparison": comparison,
        "quotes": quotes,
        "chart": chart,
        "has_products": Product.objects.exists(),
        "selected_product": (
            wholesaler_form.cleaned_data.get("product")
            if wholesaler_form.is_bound and wholesaler_form.is_valid()
            else None
        ),
    }
    return render(request, "manager/compare.html", context)


# --------------------------------------------------------------------------
# Purchases
# --------------------------------------------------------------------------


@guard
@require_GET
def purchases(request):
    filter_form = PurchaseFilterForm(request.GET or None)
    queryset = filter_form.filter(
        Purchase.objects.select_related("product", "wholesaler")
    )

    totals = queryset.aggregate(
        amount=Coalesce(
            Sum("final_amount"),
            Value(Decimal("0")),
            output_field=Purchase._meta.get_field("final_amount"),
        ),
        goods=Coalesce(
            Sum("total_amount"),
            Value(Decimal("0")),
            output_field=Purchase._meta.get_field("total_amount"),
        ),
        count=Count("id"),
    )
    extras = calc.round2(calc.to_decimal(totals["amount"]) - calc.to_decimal(totals["goods"]))

    context = {
        "page_title": "Purchases",
        "purchases": queryset,
        "filter_form": filter_form,
        "totals": totals,
        "extras": extras,
        "total_count": Purchase.objects.count(),
        "is_filtered": any(
            request.GET.get(key)
            for key in ("product", "wholesaler", "date_from", "date_to")
        ),
        "can_add": Product.objects.exists() and Wholesaler.objects.exists(),
    }
    return render(request, "manager/purchases.html", context)


@guard
def purchase_form(request, pk: int | None = None):
    instance = get_object_or_404(Purchase, pk=pk) if pk else None
    form = PurchaseForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        saved = form.save()
        messages.success(
            request,
            f"Recorded {saved.quantity:g} {saved.product.unit} of {saved.product.name} "
            f"for {saved.final_amount}.",
        )
        return redirect(safe_next(request, reverse("purchases")))

    if request.method == "GET" and not instance:
        initial: dict[str, Any] = {}
        product_id = request.GET.get("product")
        if product_id and Product.objects.filter(pk=product_id).exists():
            product = Product.objects.get(pk=product_id)
            initial["product"] = product.pk
            # Pre-fill the rate from the latest quote, which is usually what
            # was actually paid.
            latest = product.latest_rate()
            if latest:
                initial["rate"] = latest.purchase_rate
                initial["wholesaler"] = latest.wholesaler_id
        if initial:
            form = PurchaseForm(initial=initial)

    context = {
        "page_title": "Edit purchase" if instance else "Record a purchase",
        "form": form,
        "instance": instance,
        "cancel_url": safe_next(request, reverse("purchases")),
        "next": safe_next(request, reverse("purchases")),
    }
    return render(request, "manager/purchase_form.html", context)


@guard
def purchase_delete(request, pk: int):
    purchase = get_object_or_404(Purchase.objects.select_related("product"), pk=pk)
    if request.method == "POST":
        purchase.delete()
        messages.success(request, "Deleted that purchase record.")
        return redirect(safe_next(request, reverse("purchases")))

    context = {
        "page_title": "Delete this purchase?",
        "object": purchase,
        "kind": "purchase",
        "warning": "Only this record is removed. Rates are not affected.",
        "counts": [],
        "cancel_url": safe_next(request, reverse("purchases")),
        "next": safe_next(request, reverse("purchases")),
    }
    return render(request, "manager/confirm_delete.html", context)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


@guard
@require_GET
def reports(request):
    months = analytics.available_months()
    month = request.GET.get("month") or (months[0] if months else None)
    if month not in months and months:
        month = months[0]

    report = analytics.monthly_report(month) if month else None
    changes = analytics.latest_changes()

    context = {
        "page_title": "Reports",
        "months": months,
        "month": month,
        "report": report,
        "increased": sorted(
            (c for c in changes if c.trend == "up"), key=lambda c: c.difference, reverse=True
        ),
        "decreased": sorted(
            (c for c in changes if c.trend == "down"), key=lambda c: c.difference
        ),
        "unchanged": [c for c in changes if c.trend == "same"],
        "bars": charts.trend_bars(analytics.purchase_trend()),
        "spend": charts.share_rows(
            [
                (
                    row.wholesaler.company_name if row.wholesaler else "Removed wholesaler",
                    row.amount,
                    f"{row.purchases} purchase{'s' if row.purchases != 1 else ''}",
                )
                for row in analytics.spend_by_wholesaler()
            ]
        ),
        "has_data": Rate.objects.exists() or Purchase.objects.exists(),
    }
    return render(request, "manager/reports.html", context)


# --------------------------------------------------------------------------
# Settings and data management
# --------------------------------------------------------------------------


@guard
def shop_settings(request):
    settings_obj = ShopSettings.load()
    form = ShopSettingsForm(instance=settings_obj)
    category_form = CategoryForm()
    import_form = ImportRatesForm()

    if request.method == "POST":
        action = request.POST.get("action", "settings")

        if action == "settings":
            form = ShopSettingsForm(request.POST, instance=settings_obj)
            if form.is_valid():
                form.save()
                messages.success(request, "Saved your shop details.")
                return redirect("settings")

        elif action == "add_category":
            category_form = CategoryForm(request.POST)
            if category_form.is_valid():
                Category.remember(category_form.cleaned_data["name"])
                messages.success(
                    request, f"Added the category {category_form.cleaned_data['name']}."
                )
                return redirect("settings")

        elif action == "import":
            import_form = ImportRatesForm(request.POST, request.FILES)
            if import_form.is_valid():
                result = exporters.import_rate_csv(import_form.cleaned_data["file"])
                if result.ok:
                    messages.success(request, f"Imported: {result.summary()}.")
                else:
                    messages.warning(request, f"Nothing imported. {result.summary()}.")
                for problem in result.errors:
                    messages.warning(request, problem)
                return redirect("settings")

        elif action == "restore":
            import_form = ImportRatesForm(request.POST, request.FILES)
            upload = request.FILES.get("file")
            if upload:
                result = exporters.restore_backup(upload)
                if result.errors:
                    for problem in result.errors:
                        messages.error(request, problem)
                else:
                    messages.success(request, f"Restored: {result.summary()}.")
            else:
                messages.error(request, "Choose a backup file to restore.")
            return redirect("settings")

    categories = Category.objects.all()
    category_usage = {
        row["category"]: row["total"]
        for row in Product.objects.values("category").annotate(total=Count("id"))
    }

    context = {
        "page_title": "Settings",
        "form": form,
        "category_form": category_form,
        "import_form": import_form,
        "categories": [
            {"object": category, "used_by": category_usage.get(category.name, 0)}
            for category in categories
        ],
        "counts": {
            "products": Product.objects.count(),
            "wholesalers": Wholesaler.objects.count(),
            "rates": Rate.objects.count(),
            "purchases": Purchase.objects.count(),
            "categories": categories.count(),
        },
        "database": django_settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1],
    }
    return render(request, "manager/settings.html", context)


@guard
@require_POST
def category_delete(request, pk: int):
    category = get_object_or_404(Category, pk=pk)
    if category.in_use:
        messages.error(
            request,
            f"{category.name} is still used by a product. Change those products first.",
        )
    else:
        name = category.name
        category.delete()
        messages.success(request, f"Removed the category {name}.")
    return redirect("settings")


@guard
def load_sample_data(request):
    """Fills the app with three weeks of realistic rates. Replaces what is there."""
    if request.method == "POST":
        counts = demo_data.load_demo_data(reset=True)
        messages.success(
            request,
            "Loaded sample data: {products} products, {wholesalers} wholesalers, "
            "{rates} rates and {purchases} purchases.".format(**counts),
        )
        return redirect("dashboard")

    context = {
        "page_title": "Load sample data?",
        "heading": "Load sample data?",
        "message": (
            "This replaces everything currently recorded with 20 products, 5 "
            "wholesalers and three weeks of rates, so the comparison screens "
            "have something to show. Export a backup first if you want to keep "
            "what you have."
        ),
        "confirm_label": "Load sample data",
        "tone": "default",
        "cancel_url": reverse("settings"),
        "action_url": reverse("load_sample_data"),
    }
    return render(request, "manager/confirm_action.html", context)


@guard
def reset_data(request):
    """Deletes every record, keeping shop settings."""
    if request.method == "POST":
        counts = demo_data.clear_all_data()
        total = sum(counts.values())
        messages.success(
            request,
            f"Deleted everything — {total} records in all. Your shop details are kept.",
        )
        return redirect("dashboard")

    context = {
        "page_title": "Delete everything?",
        "heading": "Delete everything?",
        "message": (
            "Every product, wholesaler, rate and purchase will be deleted. Your "
            "shop details and currency stay. This cannot be undone, so export a "
            "backup first if there is any doubt."
        ),
        "confirm_label": "Delete everything",
        "tone": "danger",
        "cancel_url": reverse("settings"),
        "action_url": reverse("reset_data"),
    }
    return render(request, "manager/confirm_action.html", context)


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


@guard
@require_GET
def export(request, dataset: str, fmt: str):
    """CSV or Excel for rates, purchases, products and wholesalers."""
    if dataset == "monthly":
        month = request.GET.get("month") or analytics.available_months()[0]
        header, rows = exporters.monthly_report_rows(month)
        stem, sheet = f"report-{month}", "Monthly report"
    else:
        try:
            stem, sheet, builder = exporters.EXPORTS[dataset]
        except KeyError as exc:
            raise Http404("There is no export for that.") from exc
        header, rows = builder()

    if fmt == "xlsx":
        return exporters.excel_response(stem, sheet, header, rows)
    if fmt == "csv":
        return exporters.csv_response(stem, header, rows)
    raise Http404("Unknown export format.")


@guard
@require_GET
def export_backup(request):
    return exporters.backup_response()


# --------------------------------------------------------------------------
# Global search
# --------------------------------------------------------------------------


@guard
@require_GET
def search(request):
    """
    The global search box.

    Products, wholesalers and rate notes in one list, so a half-remembered
    name gets you somewhere useful.
    """
    query = (request.GET.get("q") or "").strip()
    results: dict[str, list] = {"products": [], "wholesalers": [], "rates": []}

    if len(query) >= 2:
        results["products"] = list(
            Product.objects.filter(
                Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(category__icontains=query)
            )[:8]
        )
        results["wholesalers"] = list(
            Wholesaler.objects.filter(
                Q(company_name__icontains=query)
                | Q(name__icontains=query)
                | Q(mobile__icontains=query)
            )[:8]
        )
        results["rates"] = list(
            Rate.objects.with_related()
            .filter(Q(notes__icontains=query) | Q(product__name__icontains=query))
            .newest_first()[:8]
        )

    context = {
        "page_title": "Search",
        "query": query,
        "results": results,
        "total": sum(len(items) for items in results.values()),
    }
    return render(request, "manager/search.html", context)


@guard
@require_GET
def rate_preview_api(request):
    """
    JSON for the live panel on the rate form.

    Optional: without JavaScript the same numbers appear on the review screen
    instead. This just saves a round trip while typing.
    """
    product = Product.objects.filter(pk=request.GET.get("product") or 0).first()
    wholesaler = Wholesaler.objects.filter(pk=request.GET.get("wholesaler") or 0).first()
    if not product:
        return JsonResponse({"ok": False, "reason": "no-product"})

    rate_value = calc.to_decimal(request.GET.get("rate"))
    quantity = calc.to_decimal(request.GET.get("quantity"))
    transport = calc.to_decimal(request.GET.get("transport"))
    other = calc.to_decimal(request.GET.get("other"))
    exclude = request.GET.get("exclude") or None

    preview = _rate_preview(
        product,
        wholesaler,
        rate_value,
        quantity,
        transport,
        other,
        exclude_pk=int(exclude) if exclude and exclude.isdigit() else None,
    )
    previous = preview["previous"]
    cheapest = preview["cheapest"]
    symbol = ShopSettings.load().currency_symbol

    return JsonResponse(
        {
            "ok": True,
            "unit": product.unit,
            "symbol": symbol,
            "effective_rate": str(preview["effective_rate"]),
            "extra_cost": str(preview["extra_cost"]),
            "difference": str(preview["difference"]),
            "percentage_change": str(preview["percentage_change"]),
            "trend": preview["trend"],
            "previous": (
                {
                    "rate": str(previous.purchase_rate),
                    "date": previous.date.isoformat(),
                    "wholesaler": previous.wholesaler.company_name,
                    "source": preview["previous_source"],
                }
                if previous
                else None
            ),
            "cheapest": (
                {
                    "rate": str(cheapest.rate.purchase_rate),
                    "wholesaler": cheapest.wholesaler.company_name if cheapest.wholesaler else "",
                    "date": cheapest.rate.date.isoformat(),
                }
                if cheapest
                else None
            ),
        }
    )


def not_found(request, exception=None):
    return render(request, "manager/404.html", status=404)
