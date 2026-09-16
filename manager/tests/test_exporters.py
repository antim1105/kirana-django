"""Tests for the CSV/Excel/JSON exports and the CSV importer."""

import datetime as dt
import io
import json
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from manager.models import Product, Purchase, Rate, Wholesaler
from manager.services import exporters


def csv_upload(text: str) -> SimpleUploadedFile:
    return SimpleUploadedFile("rates.csv", text.encode("utf-8"), content_type="text/csv")


class ExportResponseTests(TestCase):
    def setUp(self):
        product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        wholesaler = Wholesaler.objects.create(company_name="Raj Traders")
        Rate.objects.create(
            product=product, wholesaler=wholesaler, date=dt.date(2026, 9, 1),
            purchase_rate=Decimal("42"), quantity=Decimal("100"),
            transport_cost=Decimal("200"),
        )
        Purchase.objects.create(
            product=product, wholesaler=wholesaler, date=dt.date(2026, 9, 1),
            quantity=Decimal("50"), rate=Decimal("42"),
        )

    def test_csv_exports_stream_with_a_filename(self):
        expected = {
            "rates": "Sugar",
            "purchases": "Sugar",
            "products": "Sugar",
            "wholesalers": "Raj Traders",
        }
        for dataset, needle in expected.items():
            with self.subTest(dataset=dataset):
                response = self.client.get(reverse("export", args=[dataset, "csv"]))
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response["Content-Disposition"])
                body = b"".join(response.streaming_content).decode("utf-8")
                self.assertIn(needle, body)

    def test_rate_export_includes_the_effective_rate(self):
        response = self.client.get(reverse("export", args=["rates", "csv"]))
        body = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("44.00", body)

    def test_excel_export_is_a_real_workbook_when_openpyxl_is_present(self):
        response = self.client.get(reverse("export", args=["rates", "xlsx"]))
        self.assertEqual(response.status_code, 200)
        if exporters.HAS_OPENPYXL:
            self.assertTrue(response["Content-Type"].startswith("application/vnd"))
            self.assertEqual(response.content[:2], b"PK")

    def test_unknown_dataset_is_a_404(self):
        self.assertEqual(
            self.client.get(reverse("export", args=["nonsense", "csv"])).status_code, 404
        )

    def test_backup_is_valid_json_with_names_not_ids(self):
        response = self.client.get(reverse("export_backup"))
        payload = json.loads(response.content)
        self.assertEqual(payload["rates"][0]["product"], "Sugar")
        self.assertEqual(payload["rates"][0]["wholesaler"], "Raj Traders")
        self.assertIn("settings", payload)


class ImportTests(TestCase):
    HEADER = "Date,Product,Wholesaler,Rate,Unit,Quantity,Transport,Other,Notes\n"

    def test_import_creates_missing_products_and_wholesalers(self):
        result = exporters.import_rate_csv(
            csv_upload(self.HEADER + "2026-09-01,Sugar,Raj Traders,42,kg,100,200,0,Phone quote\n")
        )
        self.assertEqual(result.created, 1)
        self.assertEqual(result.new_products, 1)
        self.assertEqual(result.new_wholesalers, 1)

        rate = Rate.objects.get()
        self.assertEqual(rate.purchase_rate, Decimal("42.00"))
        self.assertEqual(rate.effective_rate, Decimal("44.00"))

    def test_reimporting_updates_rather_than_duplicating(self):
        exporters.import_rate_csv(
            csv_upload(self.HEADER + "2026-09-01,Sugar,Raj Traders,42,kg,100,0,0,\n")
        )
        result = exporters.import_rate_csv(
            csv_upload(self.HEADER + "2026-09-01,Sugar,Raj Traders,45,kg,100,0,0,\n")
        )
        self.assertEqual(result.updated, 1)
        self.assertEqual(Rate.objects.count(), 1)
        self.assertEqual(Rate.objects.get().purchase_rate, Decimal("45.00"))

    def test_day_first_dates_are_accepted(self):
        exporters.import_rate_csv(
            csv_upload(self.HEADER + "01-09-2026,Sugar,Raj Traders,42,kg,100,0,0,\n")
        )
        self.assertEqual(Rate.objects.get().date, dt.date(2026, 9, 1))

    def test_bad_rows_are_skipped_with_a_reason(self):
        result = exporters.import_rate_csv(
            csv_upload(
                self.HEADER
                + "2026-09-01,Sugar,Raj Traders,notanumber,kg,100,0,0,\n"
                + ",,,,,,,,\n"
                + "2026-09-02,,Raj Traders,42,kg,100,0,0,\n"
            )
        )
        self.assertEqual(result.created, 0)
        self.assertEqual(result.skipped, 2)
        self.assertTrue(result.errors)

    def test_header_only_file_imports_nothing(self):
        result = exporters.import_rate_csv(csv_upload(self.HEADER))
        self.assertFalse(result.ok)
        self.assertEqual(result.summary(), "Nothing to import")


class RestoreTests(TestCase):
    def test_a_backup_round_trips(self):
        product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        wholesaler = Wholesaler.objects.create(company_name="Raj Traders")
        Rate.objects.create(
            product=product, wholesaler=wholesaler, date=dt.date(2026, 9, 1),
            purchase_rate=Decimal("42"), quantity=Decimal("100"),
        )
        backup = self.client.get(reverse("export_backup")).content

        Rate.objects.all().delete()
        Product.objects.all().delete()
        Wholesaler.objects.all().delete()

        result = exporters.restore_backup(io.BytesIO(backup))
        self.assertEqual(result.created, 1)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Rate.objects.get().purchase_rate, Decimal("42.00"))

    def test_junk_json_is_reported_not_raised(self):
        result = exporters.restore_backup(io.BytesIO(b"{not json"))
        self.assertTrue(result.errors)
        self.assertFalse(result.ok)

    def test_valid_json_that_is_not_a_backup_is_rejected(self):
        result = exporters.restore_backup(io.BytesIO(b'{"hello": "world"}'))
        self.assertTrue(result.errors)
