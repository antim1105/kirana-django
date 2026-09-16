"""
Import a backup from the original React app's export.

That export's shape is different from this app's own JSON backup
(`export_backup` / `restore_backup`): it uses camelCase keys, product ids
from the old app (`prd_...`), and — in at least one real export seen in the
wild — rates with no wholesaler recorded against them at all, which this
app's Rate model requires.

This command is deliberately generic enough to run more than once:

* Products are matched by name (case-insensitive). An existing product is
  left alone; a new name creates one, and its category is remembered. A
  top-level `categories` list, if present, is remembered too.
* Every rate needs a wholesaler. If the file's rate has no `wholesalerId`,
  or that id is not in a `wholesalers` list in the file, the rate is
  attributed to one placeholder wholesaler instead — by default named
  "Unknown supplier" — so nothing is dropped. Rename that wholesaler
  from the Wholesalers page once you know who actually quoted these.
* A rate for the same product, wholesaler and date that already exists is
  updated rather than duplicated, so importing the same file twice is safe.
* If this file *does* name a real wholesaler for a rate, but an earlier,
  less complete import already recorded that same product and date under
  the placeholder, that existing row is upgraded in place — its wholesaler
  is corrected to the real one rather than leaving a stray placeholder-
  attributed duplicate sitting next to a new, correctly attributed row.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from manager.models import Category, Product, Rate, Wholesaler
from manager.services import calc


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    # The old app always wrote YYYY-MM-DD, but a couple of other formats are
    # accepted too in case the file was hand-edited.
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = "Import products and rates from the old React app's JSON export."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the exported .json file")
        parser.add_argument(
            "--wholesaler",
            default="Unknown supplier",
            help=(
                "Wholesaler to attach rates to when the file does not name "
                "one, and the name of the placeholder to upgrade away from "
                "when it does. Default: 'Unknown supplier'."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without changing the database.",
        )

    def handle(self, *args, **options):
        path = options["path"]
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise CommandError(f"No file at {path!r}.") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"That file is not valid JSON: {exc}") from exc

        categories_in = payload.get("categories", [])
        products_in = payload.get("products", [])
        rates_in = payload.get("rates", [])
        purchases_in = payload.get("purchases", [])
        wholesalers_in = {w.get("id"): w for w in payload.get("wholesalers", [])}

        if not products_in and not rates_in:
            raise CommandError("That file has no products or rates to import.")

        dry_run = options["dry_run"]
        placeholder_name = options["wholesaler"]

        with transaction.atomic():
            if not dry_run:
                for record in categories_in:
                    Category.remember(record.get("name") or "")

            id_to_product: dict[str, Product] = {}
            new_products = 0
            for record in products_in:
                name = (record.get("name") or "").strip()
                if not name:
                    continue
                product = Product.objects.filter(name__iexact=name).first()
                if not product:
                    new_products += 1
                    category = (record.get("category") or "Uncategorised").strip()
                    product = Product(
                        name=name,
                        category=category,
                        brand=record.get("brand") or "",
                        unit=(record.get("unit") or "unit").strip(),
                        pack_size=record.get("packSize") or "",
                        notes=record.get("notes") or "",
                    )
                    if not dry_run:
                        product.save()
                        Category.remember(category)
                if record.get("id"):
                    id_to_product[record["id"]] = product

            # One placeholder wholesaler covers every rate whose file entry
            # has no wholesalerId, or whose id isn't in a wholesalers list.
            placeholder = Wholesaler.objects.filter(
                company_name__iexact=placeholder_name
            ).first()
            if not placeholder:
                placeholder = Wholesaler(company_name=placeholder_name)
                if not dry_run:
                    placeholder.save()

            id_to_wholesaler: dict[str, Wholesaler] = {}
            new_wholesalers = 0
            for wid, record in wholesalers_in.items():
                company = (record.get("companyName") or "").strip()
                if not company:
                    continue
                wholesaler = Wholesaler.objects.filter(company_name__iexact=company).first()
                if not wholesaler:
                    new_wholesalers += 1
                    wholesaler = Wholesaler(
                        company_name=company,
                        name=record.get("name") or "",
                        mobile=record.get("mobile") or "",
                        address=record.get("address") or "",
                        notes=record.get("notes") or "",
                    )
                    if not dry_run:
                        wholesaler.save()
                id_to_wholesaler[wid] = wholesaler

            created_rates = 0
            updated_rates = 0
            upgraded_rates = 0
            skipped_rates = 0
            used_placeholder = 0

            for record in rates_in:
                product = id_to_product.get(record.get("productId"))
                if not product:
                    skipped_rates += 1
                    continue

                date = parse_date(record.get("date"))
                rate_value = calc.to_decimal(record.get("purchaseRate"), default=None)
                if not date or rate_value is None or rate_value <= 0:
                    skipped_rates += 1
                    continue

                named_wholesaler = id_to_wholesaler.get(record.get("wholesalerId"))
                wholesaler = named_wholesaler or placeholder
                if named_wholesaler is None:
                    used_placeholder += 1

                quantity = calc.to_decimal(record.get("quantity"), default=Decimal("1"))
                if quantity <= 0:
                    quantity = Decimal("1")
                transport = calc.to_decimal(record.get("transportCost"))
                other = calc.to_decimal(record.get("otherCost"))
                unit = (record.get("unit") or product.unit).strip()
                notes = record.get("notes") or ""

                # In a dry run, a brand-new product or wholesaler is never
                # saved, so it has no primary key yet and can't be used in a
                # database lookup — something that doesn't exist yet can't
                # already have a rate recorded against it either, so this
                # only costs us being able to detect exact repeat rows.
                existing = None
                if product.pk and wholesaler.pk:
                    existing = Rate.objects.filter(
                        product=product, wholesaler=wholesaler, date=date
                    ).first()

                # This file names a real wholesaler for a product/date
                # already recorded, less precisely, under the placeholder —
                # correct that row instead of leaving a duplicate sitting
                # next to it. This lookup only needs the product to be
                # saved and the placeholder's name as a string, so it still
                # works in a dry run even though the *new* wholesaler named
                # here has no primary key yet.
                upgrading = False
                if not existing and named_wholesaler is not None and product.pk:
                    candidate = Rate.objects.filter(
                        product=product,
                        date=date,
                        wholesaler__company_name__iexact=placeholder_name,
                    ).first()
                    if candidate:
                        existing = candidate
                        existing.wholesaler = named_wholesaler
                        upgrading = True

                if existing:
                    existing.purchase_rate = rate_value
                    existing.unit = unit
                    existing.quantity = quantity
                    existing.transport_cost = transport
                    existing.other_cost = other
                    existing.notes = notes or existing.notes
                    if upgrading:
                        upgraded_rates += 1
                    else:
                        updated_rates += 1
                    if not dry_run:
                        existing.save()
                else:
                    created_rates += 1
                    if not dry_run:
                        Rate.objects.create(
                            product=product,
                            wholesaler=wholesaler,
                            date=date,
                            purchase_rate=rate_value,
                            unit=unit,
                            quantity=quantity,
                            transport_cost=transport,
                            other_cost=other,
                            notes=notes,
                        )

            imported_purchases = 0
            skipped_purchases = 0
            for record in purchases_in:
                product = id_to_product.get(record.get("productId"))
                date = parse_date(record.get("date"))
                quantity = calc.to_decimal(record.get("quantity"), default=None)
                rate_value = calc.to_decimal(record.get("rate"), default=None)
                if not (product and date and quantity and rate_value):
                    skipped_purchases += 1
                    continue
                wholesaler = id_to_wholesaler.get(record.get("wholesalerId")) or placeholder
                imported_purchases += 1
                if not dry_run:
                    from manager.models import Purchase

                    Purchase.objects.get_or_create(
                        product=product,
                        wholesaler=wholesaler,
                        date=date,
                        quantity=quantity,
                        rate=rate_value,
                        defaults={
                            "transport_cost": calc.to_decimal(record.get("transportCost")),
                            "other_cost": calc.to_decimal(record.get("otherCost")),
                            "notes": record.get("notes") or "",
                        },
                    )

            if dry_run:
                transaction.set_rollback(True)

        style = self.style.WARNING if dry_run else self.style.SUCCESS
        prefix = "Would import" if dry_run else "Imported"
        self.stdout.write(style(f"{prefix}:"))
        self.stdout.write(f"  Products: {new_products} new, {len(id_to_product)} matched total")
        if new_wholesalers or wholesalers_in:
            self.stdout.write(f"  Wholesalers from file: {new_wholesalers} new")
        self.stdout.write(
            f"  Rates: {created_rates} created, {updated_rates} updated, "
            f"{upgraded_rates} upgraded from placeholder, {skipped_rates} skipped"
        )
        if used_placeholder:
            action = "would attach" if dry_run else "attached"
            self.stdout.write(
                self.style.WARNING(
                    f"  {used_placeholder} rate(s) {action} to the placeholder "
                    f"wholesaler {placeholder_name!r} — rename it once you know "
                    f"who actually quoted them."
                )
            )
        if upgraded_rates:
            action = "would upgrade" if dry_run else "upgraded"
            self.stdout.write(
                f"  {upgraded_rates} rate(s) previously under {placeholder_name!r} "
                f"{action} to their real wholesaler from this file."
            )
        if purchases_in:
            self.stdout.write(
                f"  Purchases: {imported_purchases} imported, {skipped_purchases} skipped"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
