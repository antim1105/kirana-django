"""
Tests for the pages and forms.

The two-step rate flow is the part of the port most worth protecting: a first
POST must show a review screen without writing anything, and only the second
one should save. The duplicate warning's three choices are checked too.
"""

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from manager.forms import ProductForm, RateForm
from manager.models import Category, Product, Purchase, Rate, ShopSettings, Wholesaler


class PageTests(TestCase):
    """Every page should load, empty database or not."""

    def test_pages_load_when_empty(self):
        for name in (
            "dashboard",
            "products",
            "wholesalers",
            "add_rate",
            "rate_history",
            "compare",
            "purchases",
            "reports",
            "settings",
            "search",
        ):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_pages_load_with_data(self):
        from manager.services import demo_data

        demo_data.load_demo_data()
        for name in ("dashboard", "products", "rate_history", "reports", "purchases"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_dashboard_asks_for_a_product_first(self):
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Start with a product")

    def test_unknown_page_is_a_404(self):
        self.assertEqual(self.client.get("/no-such-page/").status_code, 404)


class ProductFormTests(TestCase):
    def test_duplicate_names_are_rejected(self):
        Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        form = ProductForm(data={"name": "sugar", "category": "Sugar & Salt", "unit": "kg"})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)

    def test_editing_keeps_its_own_name(self):
        product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        form = ProductForm(
            data={"name": "Sugar", "category": "Sugar & Salt", "unit": "kg"},
            instance=product,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_typed_category_is_remembered(self):
        form = ProductForm(data={"name": "Kaju", "category": "Dry fruits", "unit": "kg"})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertTrue(Category.objects.filter(name="Dry fruits").exists())

    def test_existing_category_spelling_is_reused(self):
        Category.objects.create(name="Dry fruits")
        form = ProductForm(data={"name": "Kaju", "category": "DRY FRUITS", "unit": "kg"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["category"], "Dry fruits")

    def test_category_and_unit_are_required(self):
        form = ProductForm(data={"name": "Kaju", "category": "  ", "unit": " "})
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)
        self.assertIn("unit", form.errors)


class RateFormTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        self.wholesaler = Wholesaler.objects.create(company_name="Raj Traders")

    def payload(self, **overrides):
        data = {
            "product": self.product.pk,
            "wholesaler": self.wholesaler.pk,
            "date": timezone.localdate().isoformat(),
            "purchase_rate": "42",
            "quantity": "100",
            "transport_cost": "200",
            "other_cost": "0",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_future_dates_are_rejected(self):
        tomorrow = (timezone.localdate() + dt.timedelta(days=1)).isoformat()
        form = RateForm(data=self.payload(date=tomorrow))
        self.assertFalse(form.is_valid())
        self.assertIn("date", form.errors)

    def test_zero_rate_is_rejected(self):
        form = RateForm(data=self.payload(purchase_rate="0"))
        self.assertFalse(form.is_valid())
        self.assertIn("purchase_rate", form.errors)

    def test_zero_quantity_is_rejected(self):
        form = RateForm(data=self.payload(quantity="0"))
        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)

    def test_blank_extras_default_to_zero(self):
        form = RateForm(data=self.payload(transport_cost="", other_cost=""))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["transport_cost"], Decimal("0"))


class AddRateFlowTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        self.wholesaler = Wholesaler.objects.create(company_name="Raj Traders")
        self.other = Wholesaler.objects.create(company_name="ABC Wholesale")
        self.today = timezone.localdate()

    def payload(self, action, **overrides):
        data = {
            "action": action,
            "product": self.product.pk,
            "wholesaler": self.wholesaler.pk,
            "date": self.today.isoformat(),
            "purchase_rate": "42",
            "quantity": "100",
            "transport_cost": "200",
            "other_cost": "0",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_review_writes_nothing(self):
        response = self.client.post(reverse("add_rate"), self.payload("review"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review this rate")
        self.assertEqual(Rate.objects.count(), 0)

    def test_review_shows_the_effective_rate(self):
        response = self.client.post(reverse("add_rate"), self.payload("review"))
        # 42 + 200/100 = 44
        self.assertContains(response, "44.00")

    def test_save_creates_the_rate(self):
        response = self.client.post(reverse("add_rate"), self.payload("save"))
        self.assertEqual(response.status_code, 302)
        rate = Rate.objects.get()
        self.assertEqual(rate.purchase_rate, Decimal("42.00"))
        self.assertEqual(rate.effective_rate, Decimal("44.00"))
        self.assertEqual(rate.unit, "kg")

    def test_review_warns_about_a_same_day_entry(self):
        Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=self.today,
            purchase_rate=Decimal("40"),
            quantity=Decimal("50"),
        )
        response = self.client.post(reverse("add_rate"), self.payload("review"))
        self.assertContains(response, "already exists")
        self.assertContains(response, "Update the existing one")
        self.assertContains(response, "Create a second entry")

    def test_replace_updates_the_existing_entry(self):
        existing = Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=self.today,
            purchase_rate=Decimal("40"),
            quantity=Decimal("50"),
        )
        self.client.post(
            reverse("add_rate"),
            self.payload("replace", replace_id=existing.pk),
        )
        self.assertEqual(Rate.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.purchase_rate, Decimal("42.00"))

    def test_save_ignores_replace_id_and_creates_a_second_entry(self):
        existing = Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=self.today,
            purchase_rate=Decimal("40"),
            quantity=Decimal("50"),
        )
        # "Create a second entry" posts action=save with replace_id present.
        self.client.post(
            reverse("add_rate"),
            self.payload("save", replace_id=existing.pk),
        )
        self.assertEqual(Rate.objects.count(), 2)

    def test_previous_rate_prefers_the_same_wholesaler(self):
        Rate.objects.create(
            product=self.product, wholesaler=self.other,
            date=self.today - dt.timedelta(days=1),
            purchase_rate=Decimal("30"), quantity=Decimal("50"),
        )
        Rate.objects.create(
            product=self.product, wholesaler=self.wholesaler,
            date=self.today - dt.timedelta(days=5),
            purchase_rate=Decimal("40"), quantity=Decimal("50"),
        )
        response = self.client.post(reverse("add_rate"), self.payload("review"))
        # Compared against Raj's 40, not ABC's more recent 30.
        self.assertContains(response, "Last from Raj Traders")
        self.assertContains(response, "2.00")

    def test_invalid_form_returns_to_the_form(self):
        response = self.client.post(reverse("add_rate"), self.payload("review", purchase_rate="0"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "greater than zero")
        self.assertEqual(Rate.objects.count(), 0)


class PurchaseViewTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        self.wholesaler = Wholesaler.objects.create(company_name="Raj Traders")

    def test_recording_a_purchase_computes_totals(self):
        self.client.post(
            reverse("purchase_create"),
            {
                "product": self.product.pk,
                "wholesaler": self.wholesaler.pk,
                "date": timezone.localdate().isoformat(),
                "quantity": "50",
                "rate": "42.50",
                "transport_cost": "200",
                "other_cost": "50",
                "notes": "",
            },
        )
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.total_amount, Decimal("2125.00"))
        self.assertEqual(purchase.final_amount, Decimal("2375.00"))


class DeleteViewTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        self.wholesaler = Wholesaler.objects.create(company_name="Raj Traders")
        self.rate = Rate.objects.create(
            product=self.product, wholesaler=self.wholesaler,
            date=timezone.localdate(),
            purchase_rate=Decimal("42"), quantity=Decimal("50"),
        )

    def test_delete_asks_before_deleting(self):
        response = self.client.get(reverse("product_delete", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be undone")
        self.assertEqual(Product.objects.count(), 1)

    def test_confirming_deletes_the_product_and_its_rates(self):
        self.client.post(reverse("product_delete", args=[self.product.pk]))
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Rate.objects.count(), 0)

    def test_deleting_one_rate_leaves_the_product(self):
        self.client.post(reverse("rate_delete", args=[self.rate.pk]))
        self.assertEqual(Rate.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 1)

    def test_next_must_point_at_this_site(self):
        response = self.client.post(
            reverse("rate_delete", args=[self.rate.pk]),
            {"next": "https://example.com/phish"},
        )
        self.assertNotIn("example.com", response["Location"])


class SettingsViewTests(TestCase):
    def test_saving_shop_details(self):
        self.client.post(
            reverse("settings"),
            {
                "action": "settings",
                "business_name": "Sharma Kirana",
                "shop_address": "Main Road",
                "mobile": "9827012345",
                "currency_symbol": "₹",
                "default_unit": "kg",
                "date_format": "dd-MM-yyyy",
            },
        )
        self.assertEqual(ShopSettings.load().business_name, "Sharma Kirana")

    def test_adding_a_category(self):
        self.client.post(reverse("settings"), {"action": "add_category", "name": "Pooja items"})
        self.assertTrue(Category.objects.filter(name="Pooja items").exists())

    def test_a_category_in_use_cannot_be_removed(self):
        Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        category = Category.objects.create(name="Sugar & Salt")
        self.client.post(reverse("category_delete", args=[category.pk]))
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())

    def test_load_and_clear_sample_data(self):
        ShopSettings.load()
        self.client.post(reverse("load_sample_data"))
        self.assertEqual(Product.objects.count(), 20)
        self.assertTrue(Rate.objects.exists())

        self.client.post(reverse("reset_data"))
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Rate.objects.count(), 0)
        # Shop settings survive a wipe.
        self.assertEqual(ShopSettings.objects.count(), 1)


class RatePreviewApiTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sugar", category="Sugar & Salt", unit="kg")
        self.wholesaler = Wholesaler.objects.create(company_name="Raj Traders")

    def test_preview_returns_the_effective_rate(self):
        response = self.client.get(
            reverse("rate_preview_api"),
            {
                "product": self.product.pk,
                "wholesaler": self.wholesaler.pk,
                "rate": "42",
                "quantity": "100",
                "transport": "200",
            },
        )
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["effective_rate"], "44.00")
        self.assertEqual(data["unit"], "kg")

    def test_preview_without_a_product_says_so(self):
        response = self.client.get(reverse("rate_preview_api"))
        self.assertFalse(response.json()["ok"])
