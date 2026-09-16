"""
Export and import — the port of `services/exportService.ts` and `utils/csv.ts`.

The browser version had to build files in JavaScript and trigger a download.
Here the rows are built server-side and streamed back as an HTTP response,
which is both simpler and means large exports do not have to fit in a tab's
memory.

Excel export uses openpyxl when it is installed. If it is not, the view falls
back to CSV rather than failing — Excel opens CSV perfectly well, and an
export button that errors is worse than one that gives you a slightly plainer
file.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Iterator, Sequence

from django.http import HttpResponse, StreamingHttpResponse
from django.utils.text import slugify

from ..models import Product, Purchase, Rate, ShopSettings, Wholesaler
from . import analytics, calc

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    HAS_OPENPYXL = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_OPENPYXL = False


Row = Sequence[object]


# --------------------------------------------------------------------------
# Row builders — one per export, matching the original column order
# --------------------------------------------------------------------------


def rate_rows() -> tuple[list[str], list[Row]]:
    header = [
        "Date",
        "Product",
        "Brand",
        "Category",
        "Wholesaler",
        "Rate",
        "Unit",
        "Quantity",
        "Transport",
        "Other",
        "Effective rate",
        "Change",
        "Change %",
        "Notes",
    ]
    rows: list[Row] = []
    changes: list[analytics.RateChange] = []
    for product_id in (
        Rate.objects.values_list("product_id", flat=True).distinct()
    ):
        changes.extend(analytics.rate_history(product_id))

    changes.sort(key=lambda c: (c.rate.date, c.rate.created_at), reverse=True)
    for change in changes:
        rate = change.rate
        rows.append(
            [
                rate.date.isoformat(),
                rate.product.name if rate.product else "",
                rate.product.brand if rate.product else "",
                rate.product.category if rate.product else "",
                rate.wholesaler.company_name if rate.wholesaler else "",
                rate.purchase_rate,
                rate.unit,
                rate.quantity,
                rate.transport_cost,
                rate.other_cost,
                rate.effective_rate,
                change.difference if change.has_history else "",
                change.percentage_change if change.has_history else "",
                rate.notes,
            ]
        )
    return header, rows


def purchase_rows() -> tuple[list[str], list[Row]]:
    header = [
        "Date",
        "Product",
        "Wholesaler",
        "Quantity",
        "Unit",
        "Rate",
        "Total",
        "Transport",
        "Other",
        "Final amount",
        "Notes",
    ]
    rows: list[Row] = [
        [
            purchase.date.isoformat(),
            purchase.product.name if purchase.product else "",
            purchase.wholesaler.company_name if purchase.wholesaler else "",
            purchase.quantity,
            purchase.product.unit if purchase.product else "",
            purchase.rate,
            purchase.total_amount,
            purchase.transport_cost,
            purchase.other_cost,
            purchase.final_amount,
            purchase.notes,
        ]
        for purchase in Purchase.objects.select_related("product", "wholesaler")
    ]
    return header, rows


def product_rows() -> tuple[list[str], list[Row]]:
    header = [
        "Name",
        "Category",
        "Brand",
        "Unit",
        "Pack size",
        "Minimum stock",
        "Latest rate",
        "Latest rate date",
        "Notes",
    ]
    rows: list[Row] = []
    for product in Product.objects.prefetch_related("rates"):
        latest = product.latest_rate()
        rows.append(
            [
                product.name,
                product.category,
                product.brand,
                product.unit,
                product.pack_size,
                product.minimum_stock if product.minimum_stock is not None else "",
                latest.purchase_rate if latest else "",
                latest.date.isoformat() if latest else "",
                product.notes,
            ]
        )
    return header, rows


def wholesaler_rows() -> tuple[list[str], list[Row]]:
    header = [
        "Company name",
        "Contact person",
        "Mobile",
        "Address",
        "Rates recorded",
        "Total purchases",
        "Notes",
    ]
    spend = {
        row.wholesaler.pk: row
        for row in analytics.spend_by_wholesaler()
        if row.wholesaler
    }
    rows: list[Row] = []
    for wholesaler in Wholesaler.objects.all():
        totals = spend.get(wholesaler.pk)
        rows.append(
            [
                wholesaler.company_name,
                wholesaler.name,
                wholesaler.mobile,
                wholesaler.address.replace("\n", ", "),
                wholesaler.rates.count(),
                totals.amount if totals else Decimal("0"),
                wholesaler.notes,
            ]
        )
    return header, rows


def monthly_report_rows(month: str) -> tuple[list[str], list[Row]]:
    report = analytics.monthly_report(month)
    header = ["Section", "Item", "Detail", "Value"]
    rows: list[Row] = [
        ["Summary", "Total spent", report.label, report.total_purchase_amount],
        ["Summary", "Purchases", report.label, report.purchase_count],
        ["Summary", "Total quantity", report.label, report.total_quantity],
    ]
    for row in report.top_products:
        rows.append(
            [
                "Top products",
                row.product.name if row.product else "Removed product",
                f"{row.quantity} {row.product.unit if row.product else ''}".strip(),
                row.amount,
            ]
        )
    for row in report.by_wholesaler:
        rows.append(
            [
                "By wholesaler",
                row.wholesaler.company_name if row.wholesaler else "Removed wholesaler",
                f"{row.purchases} purchases",
                row.amount,
            ]
        )
    for change in report.increased:
        rows.append(
            [
                "Price increased",
                change.product.name if change.product else "",
                change.wholesaler.company_name if change.wholesaler else "",
                change.difference,
            ]
        )
    for change in report.decreased:
        rows.append(
            [
                "Price decreased",
                change.product.name if change.product else "",
                change.wholesaler.company_name if change.wholesaler else "",
                change.difference,
            ]
        )
    return header, rows


EXPORTS = {
    "rates": ("rates", "Rates", rate_rows),
    "purchases": ("purchases", "Purchases", purchase_rows),
    "products": ("products", "Products", product_rows),
    "wholesalers": ("wholesalers", "Wholesalers", wholesaler_rows),
}


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


def _filename(stem: str, extension: str) -> str:
    shop = slugify(ShopSettings.load().business_name) or "kirana"
    stamp = analytics.today().isoformat()
    return f"{shop}-{stem}-{stamp}.{extension}"


class _Echo:
    """A file-like object that just returns what it is handed, for streaming."""

    def write(self, value: str) -> str:
        return value


def csv_response(stem: str, header: Sequence[str], rows: Iterable[Row]) -> StreamingHttpResponse:
    """Streams CSV so a long export never builds up in memory."""
    writer = csv.writer(_Echo())

    def stream() -> Iterator[str]:
        # BOM first, so Excel opens rupee symbols and Hindi text correctly.
        yield "\ufeff"
        yield writer.writerow(header)
        for row in rows:
            yield writer.writerow(["" if cell is None else cell for cell in row])

    response = StreamingHttpResponse(stream(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_filename(stem, "csv")}"'
    return response


def excel_response(stem: str, sheet_name: str, header: Sequence[str], rows: Iterable[Row]):
    """A real .xlsx when openpyxl is available, otherwise CSV."""
    if not HAS_OPENPYXL:  # pragma: no cover
        return csv_response(stem, header, rows)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    sheet.append(list(header))

    heading_fill = PatternFill("solid", fgColor="233152")
    for cell in sheet[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = heading_fill
        cell.alignment = Alignment(vertical="center")

    widths = [len(str(value)) for value in header]
    for row in rows:
        cleaned = ["" if cell is None else cell for cell in row]
        sheet.append(cleaned)
        for index, value in enumerate(cleaned):
            widths[index] = max(widths[index], min(len(str(value)), 60))

    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width + 3

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial")

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{_filename(stem, "xlsx")}"'
    return response


def _decimal_to_str(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    raise TypeError(f"Not JSON serialisable: {type(value)!r}")


def backup_response() -> HttpResponse:
    """
    A full JSON backup of everything, restorable by `restore_backup`.

    Records reference products and wholesalers by name rather than by primary
    key, so a backup can be restored into an empty database and still join up.
    """
    settings_obj = ShopSettings.load()
    payload = {
        "format": "kirana-wholesale-rate-manager",
        "version": 2,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "settings": {
            "business_name": settings_obj.business_name,
            "shop_address": settings_obj.shop_address,
            "mobile": settings_obj.mobile,
            "currency": settings_obj.currency,
            "currency_symbol": settings_obj.currency_symbol,
            "default_unit": settings_obj.default_unit,
            "date_format": settings_obj.date_format,
        },
        "products": [
            {
                "name": p.name,
                "category": p.category,
                "brand": p.brand,
                "unit": p.unit,
                "pack_size": p.pack_size,
                "minimum_stock": p.minimum_stock,
                "notes": p.notes,
            }
            for p in Product.objects.all()
        ],
        "wholesalers": [
            {
                "company_name": w.company_name,
                "name": w.name,
                "mobile": w.mobile,
                "address": w.address,
                "notes": w.notes,
            }
            for w in Wholesaler.objects.all()
        ],
        "rates": [
            {
                "product": r.product.name,
                "wholesaler": r.wholesaler.company_name,
                "date": r.date,
                "purchase_rate": r.purchase_rate,
                "unit": r.unit,
                "quantity": r.quantity,
                "transport_cost": r.transport_cost,
                "other_cost": r.other_cost,
                "notes": r.notes,
            }
            for r in Rate.objects.with_related().oldest_first()
        ],
        "purchases": [
            {
                "product": p.product.name,
                "wholesaler": p.wholesaler.company_name,
                "date": p.date,
                "quantity": p.quantity,
                "rate": p.rate,
                "transport_cost": p.transport_cost,
                "other_cost": p.other_cost,
                "notes": p.notes,
            }
            for p in Purchase.objects.select_related("product", "wholesaler")
        ],
    }
    body = json.dumps(payload, default=_decimal_to_str, indent=2, ensure_ascii=False)
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{_filename("backup", "json")}"'
    return response


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    new_products: int = 0
    new_wholesalers: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def ok(self) -> bool:
        return bool(self.created or self.updated)

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"{self.created} rate{'s' if self.created != 1 else ''} added")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.new_products:
            parts.append(f"{self.new_products} new product{'s' if self.new_products != 1 else ''}")
        if self.new_wholesalers:
            parts.append(
                f"{self.new_wholesalers} new wholesaler{'s' if self.new_wholesalers != 1 else ''}"
            )
        if self.skipped:
            parts.append(f"{self.skipped} row{'s' if self.skipped != 1 else ''} skipped")
        return ", ".join(parts) or "Nothing to import"


def restore_backup(upload) -> ImportResult:
    """
    Loads a JSON backup produced by `backup_response`.

    This adds to what is already there rather than wiping it: products and
    wholesalers are matched by name, and a rate for a product, wholesaler and
    date that already exists is updated. To get a clean restore, delete
    everything first from Settings.
    """
    result = ImportResult()
    try:
        payload = json.loads(upload.read())
    except (json.JSONDecodeError, UnicodeDecodeError):
        result.errors.append("That file is not readable JSON.")
        return result

    if not isinstance(payload, dict) or "rates" not in payload:
        result.errors.append("That JSON is not a backup from this app.")
        return result

    for record in payload.get("products", []):
        name = (record.get("name") or "").strip()
        if not name or Product.objects.filter(name__iexact=name).exists():
            continue
        Product.objects.create(
            name=name,
            category=(record.get("category") or "Uncategorised").strip(),
            brand=record.get("brand") or "",
            unit=(record.get("unit") or "kg").strip(),
            pack_size=record.get("pack_size") or "",
            minimum_stock=calc.to_decimal(record.get("minimum_stock"), default=None),
            notes=record.get("notes") or "",
        )
        result.new_products += 1

    from ..models import Category

    for product in Product.objects.all():
        Category.remember(product.category)

    for record in payload.get("wholesalers", []):
        company = (record.get("company_name") or "").strip()
        if not company or Wholesaler.objects.filter(company_name__iexact=company).exists():
            continue
        Wholesaler.objects.create(
            company_name=company,
            name=record.get("name") or "",
            mobile=record.get("mobile") or "",
            address=record.get("address") or "",
            notes=record.get("notes") or "",
        )
        result.new_wholesalers += 1

    products = {p.name.lower(): p for p in Product.objects.all()}
    wholesalers = {w.company_name.lower(): w for w in Wholesaler.objects.all()}

    def resolve(record: dict, key: str, lookup: dict):
        name = (record.get(key) or "").strip().lower()
        return lookup.get(name)

    for record in payload.get("rates", []):
        product = resolve(record, "product", products)
        wholesaler = resolve(record, "wholesaler", wholesalers)
        date = _parse_date((record.get("date") or "").strip())
        rate_value = calc.to_decimal(record.get("purchase_rate"), default=None)
        if not (product and wholesaler and date and rate_value and rate_value > 0):
            result.skipped += 1
            continue

        defaults = {
            "purchase_rate": rate_value,
            "unit": (record.get("unit") or product.unit).strip(),
            "quantity": calc.to_decimal(record.get("quantity")),
            "transport_cost": calc.to_decimal(record.get("transport_cost")),
            "other_cost": calc.to_decimal(record.get("other_cost")),
            "notes": record.get("notes") or "",
        }
        existing = Rate.objects.filter(
            product=product, wholesaler=wholesaler, date=date
        ).first()
        if existing:
            for attribute, value in defaults.items():
                setattr(existing, attribute, value)
            existing.save()
            result.updated += 1
        else:
            Rate.objects.create(
                product=product, wholesaler=wholesaler, date=date, **defaults
            )
            result.created += 1

    for record in payload.get("purchases", []):
        product = resolve(record, "product", products)
        wholesaler = resolve(record, "wholesaler", wholesalers)
        date = _parse_date((record.get("date") or "").strip())
        quantity = calc.to_decimal(record.get("quantity"), default=None)
        rate_value = calc.to_decimal(record.get("rate"), default=None)
        if not (product and wholesaler and date and quantity and rate_value):
            continue
        Purchase.objects.get_or_create(
            product=product,
            wholesaler=wholesaler,
            date=date,
            quantity=quantity,
            rate=rate_value,
            defaults={
                "transport_cost": calc.to_decimal(record.get("transport_cost")),
                "other_cost": calc.to_decimal(record.get("other_cost")),
                "notes": record.get("notes") or "",
            },
        )

    saved = payload.get("settings") or {}
    if saved:
        settings_obj = ShopSettings.load()
        for attribute in (
            "business_name",
            "shop_address",
            "mobile",
            "currency",
            "currency_symbol",
            "default_unit",
            "date_format",
        ):
            if saved.get(attribute):
                setattr(settings_obj, attribute, saved[attribute])
        settings_obj.save()

    return result


def _pick(row: dict[str, str], *names: str) -> str:
    """Reads a column by any of its accepted header spellings."""
    for name in names:
        for key, value in row.items():
            if key and key.strip().lower() == name:
                return (value or "").strip()
    return ""


def _parse_date(value: str) -> dt.date | None:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def import_rate_csv(upload) -> ImportResult:
    """
    Loads rates from a CSV, creating any products and wholesalers it names.

    A row whose product, wholesaler and date already exist updates that entry
    instead of adding a second one, so re-importing a corrected file does not
    double up the history.
    """
    result = ImportResult()

    raw = upload.read()
    if isinstance(raw, bytes):
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:  # pragma: no cover
            result.errors.append("Could not read the file's text encoding.")
            return result
    else:
        text = raw

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.errors.append("That file has no header row.")
        return result

    settings_obj = ShopSettings.load()
    default_unit = settings_obj.default_unit

    for line_number, row in enumerate(reader, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue

        product_name = _pick(row, "product", "product name", "item")
        wholesaler_name = _pick(row, "wholesaler", "company", "company name", "supplier")
        date_text = _pick(row, "date")
        rate_text = _pick(row, "rate", "purchase rate", "price")

        missing = [
            label
            for label, value in (
                ("product", product_name),
                ("wholesaler", wholesaler_name),
                ("date", date_text),
                ("rate", rate_text),
            )
            if not value
        ]
        if missing:
            result.skipped += 1
            result.errors.append(f"Row {line_number}: missing {', '.join(missing)}.")
            continue

        date = _parse_date(date_text)
        if not date:
            result.skipped += 1
            result.errors.append(f"Row {line_number}: could not read the date {date_text!r}.")
            continue

        purchase_rate = calc.to_decimal(rate_text, default=None) if rate_text else None
        if purchase_rate is None or purchase_rate <= 0:
            result.skipped += 1
            result.errors.append(f"Row {line_number}: rate {rate_text!r} is not a number.")
            continue

        unit = _pick(row, "unit") or default_unit
        quantity = calc.to_decimal(_pick(row, "quantity", "qty"), default=Decimal("0"))
        transport = calc.to_decimal(_pick(row, "transport", "transport cost"))
        other = calc.to_decimal(_pick(row, "other", "other cost"))
        notes = _pick(row, "notes", "note", "remark")

        product = Product.objects.filter(name__iexact=product_name).first()
        if not product:
            product = Product.objects.create(
                name=product_name,
                category=_pick(row, "category") or "Uncategorised",
                brand=_pick(row, "brand"),
                unit=unit,
                pack_size=_pick(row, "pack size", "pack"),
                notes="",
            )
            from ..models import Category  # local import avoids a cycle at module load

            Category.remember(product.category)
            result.new_products += 1

        wholesaler = Wholesaler.objects.filter(company_name__iexact=wholesaler_name).first()
        if not wholesaler:
            wholesaler = Wholesaler.objects.create(
                company_name=wholesaler_name,
                mobile=_pick(row, "mobile", "phone"),
            )
            result.new_wholesalers += 1

        if quantity <= 0:
            # Nothing to spread transport over; record the quoted rate as-is.
            quantity = Decimal("1") if (transport or other) else Decimal("0")

        existing = Rate.objects.filter(
            product=product, wholesaler=wholesaler, date=date
        ).first()
        if existing:
            existing.purchase_rate = purchase_rate
            existing.unit = unit or product.unit
            existing.quantity = quantity
            existing.transport_cost = transport
            existing.other_cost = other
            existing.notes = notes or existing.notes
            existing.save()
            result.updated += 1
        else:
            Rate.objects.create(
                product=product,
                wholesaler=wholesaler,
                date=date,
                purchase_rate=purchase_rate,
                unit=unit or product.unit,
                quantity=quantity,
                transport_cost=transport,
                other_cost=other,
                notes=notes,
            )
            result.created += 1

    # Long error lists are unhelpful in a toast; keep the first few.
    if len(result.errors) > 6:
        hidden = len(result.errors) - 6
        result.errors = result.errors[:6] + [f"…and {hidden} more rows with problems."]
    return result
