"""
Tests for the models and the read model.

The comparison rules are the point of the whole app, so the cases here are the
ones that were easy to get subtly wrong in the port: which rate counts as the
"previous" one, what happens on a date with no entry, and how ties are broken
when two quotes land on the same day.
"""

import datetime as dt
from decimal import Decimal

from django.test import TestCase

from manager.models import Category, Product, Purchase, Rate, ShopSettings, Wholesaler
from manager.services import analytics


def make_product(name="Sugar", unit="kg", category="Sugar & Salt") -> Product:
    return Product.objects.create(name=name, category=category, unit=unit)


def make_wholesaler(company="Raj Traders") -> Wholesaler:
    return Wholesaler.objects.create(company_name=company)


class ModelCalculationTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.wholesaler = make_wholesaler()

    def test_effective_rate_is_computed_on_save(self):
        rate = Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 1),
            purchase_rate=Decimal("42"),
            quantity=Decimal("100"),
            transport_cost=Decimal("200"),
            unit="kg",
        )
        self.assertEqual(rate.effective_rate, Decimal("44.00"))

    def test_unit_follows_the_product_when_left_blank(self):
        rate = Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 1),
            purchase_rate=Decimal("42"),
            quantity=Decimal("10"),
        )
        self.assertEqual(rate.unit, "kg")

    def test_purchase_totals_are_computed_on_save(self):
        purchase = Purchase.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 1),
            quantity=Decimal("50"),
            rate=Decimal("42.50"),
            transport_cost=Decimal("200"),
            other_cost=Decimal("50"),
        )
        self.assertEqual(purchase.total_amount, Decimal("2125.00"))
        self.assertEqual(purchase.final_amount, Decimal("2375.00"))

    def test_deleting_a_product_takes_its_rates_and_purchases(self):
        Rate.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 1),
            purchase_rate=Decimal("42"),
            quantity=Decimal("10"),
        )
        Purchase.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 1),
            quantity=Decimal("10"),
            rate=Decimal("42"),
        )
        self.product.delete()
        self.assertEqual(Rate.objects.count(), 0)
        self.assertEqual(Purchase.objects.count(), 0)


class SettingsSingletonTests(TestCase):
    def test_load_creates_one_row_and_reuses_it(self):
        first = ShopSettings.load()
        second = ShopSettings.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ShopSettings.objects.count(), 1)

    def test_default_currency_is_rupees(self):
        self.assertEqual(ShopSettings.load().currency_symbol, "₹")


class CategoryTests(TestCase):
    def test_remember_is_case_insensitive(self):
        Category.remember("Dry fruits")
        Category.remember("dry FRUITS")
        self.assertEqual(Category.objects.count(), 1)

    def test_remember_ignores_blank_input(self):
        self.assertIsNone(Category.remember("   "))
        self.assertEqual(Category.objects.count(), 0)

    def test_in_use_reflects_products(self):
        category = Category.remember("Pulses & Dal")
        self.assertFalse(category.in_use)
        make_product(name="Toor Dal", category="Pulses & Dal")
        self.assertTrue(category.in_use)


class RateMovementTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.raj = make_wholesaler("Raj Traders")
        self.abc = make_wholesaler("ABC Wholesale")

    def add_rate(self, rate, day, wholesaler=None, quantity="10"):
        return Rate.objects.create(
            product=self.product,
            wholesaler=wholesaler or self.raj,
            date=dt.date(2026, 9, day),
            purchase_rate=Decimal(rate),
            quantity=Decimal(quantity),
        )

    def test_history_compares_each_row_with_the_one_below(self):
        self.add_rate("40", 1)
        self.add_rate("44", 5)
        self.add_rate("42", 10)

        history = analytics.rate_history(self.product.pk)
        self.assertEqual(len(history), 3)

        # Newest first: 42 against 44 is a fall of 2.
        self.assertEqual(history[0].rate.purchase_rate, Decimal("42.00"))
        self.assertEqual(history[0].difference, Decimal("-2.00"))
        self.assertEqual(history[0].trend, "down")

        # 44 against 40 is a rise of 4.
        self.assertEqual(history[1].difference, Decimal("4.00"))
        self.assertEqual(history[1].trend, "up")

        # The oldest row has nothing beneath it.
        self.assertFalse(history[2].has_history)
        self.assertEqual(history[2].difference, Decimal("0"))
        self.assertEqual(history[2].trend, "same")

    def test_latest_changes_gives_one_row_per_product(self):
        self.add_rate("40", 1)
        self.add_rate("45", 5)

        other = make_product(name="Wheat")
        Rate.objects.create(
            product=other,
            wholesaler=self.raj,
            date=dt.date(2026, 9, 3),
            purchase_rate=Decimal("28"),
            quantity=Decimal("50"),
        )

        changes = {c.product.name: c for c in analytics.latest_changes()}
        self.assertEqual(set(changes), {"Sugar", "Wheat"})
        self.assertEqual(changes["Sugar"].difference, Decimal("5.00"))
        self.assertFalse(changes["Wheat"].has_history)

    def test_products_with_no_rates_are_left_out(self):
        make_product(name="Ghee")
        self.assertEqual(analytics.latest_changes(), [])

    def test_same_day_entries_keep_entry_order(self):
        first = self.add_rate("40", 5)
        second = self.add_rate("41", 5, wholesaler=self.abc)

        ordered = analytics.rates_for_product(self.product.pk)
        self.assertEqual([r.pk for r in ordered], [second.pk, first.pk])


class WholesalerQuoteTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.raj = make_wholesaler("Raj Traders")
        self.abc = make_wholesaler("ABC Wholesale")
        self.xyz = make_wholesaler("XYZ Distributors")

    def quote(self, wholesaler, rate, day):
        return Rate.objects.create(
            product=self.product,
            wholesaler=wholesaler,
            date=dt.date(2026, 9, day),
            purchase_rate=Decimal(rate),
            quantity=Decimal("50"),
        )

    def test_uses_the_newest_quote_per_wholesaler_cheapest_first(self):
        self.quote(self.raj, "45", 1)
        self.quote(self.raj, "43", 8)  # supersedes the 45
        self.quote(self.abc, "41", 5)
        self.quote(self.xyz, "47", 6)

        quotes = analytics.wholesaler_quotes(self.product.pk)
        self.assertEqual(
            [q.wholesaler.company_name for q in quotes],
            ["ABC Wholesale", "Raj Traders", "XYZ Distributors"],
        )
        self.assertEqual(quotes[0].rate.purchase_rate, Decimal("41.00"))
        self.assertTrue(quotes[0].is_cheapest)
        self.assertFalse(quotes[1].is_cheapest)
        self.assertEqual(quotes[1].difference_from_cheapest, Decimal("2.00"))

    def test_ties_share_the_cheapest_badge(self):
        self.quote(self.raj, "40", 1)
        self.quote(self.abc, "40", 1)

        quotes = analytics.wholesaler_quotes(self.product.pk)
        self.assertTrue(all(q.is_cheapest for q in quotes))

    def test_cheapest_quote_is_none_without_rates(self):
        self.assertIsNone(analytics.cheapest_quote(self.product.pk))


class DateComparisonTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.wholesaler = make_wholesaler()
        for rate, day in (("40", 1), ("46", 10)):
            Rate.objects.create(
                product=self.product,
                wholesaler=self.wholesaler,
                date=dt.date(2026, 9, day),
                purchase_rate=Decimal(rate),
                quantity=Decimal("50"),
            )

    def test_a_date_with_no_entry_falls_back_to_the_one_before_it(self):
        # Nothing was recorded on the 5th, so it resolves to the 1st.
        rate = analytics.rate_on_date(self.product.pk, dt.date(2026, 9, 5))
        self.assertEqual(rate.purchase_rate, Decimal("40.00"))

    def test_no_rate_before_the_date_gives_nothing(self):
        self.assertIsNone(analytics.rate_on_date(self.product.pk, dt.date(2026, 8, 1)))

    def test_compare_dates_reports_the_movement(self):
        comparison = analytics.compare_dates(
            self.product.pk, dt.date(2026, 9, 5), dt.date(2026, 9, 15)
        )
        self.assertTrue(comparison.is_complete)
        self.assertEqual(comparison.difference, Decimal("6.00"))
        self.assertEqual(comparison.percentage_change, Decimal("15.00"))
        self.assertEqual(comparison.trend, "up")

    def test_dates_given_backwards_are_sorted(self):
        comparison = analytics.compare_dates(
            self.product.pk, dt.date(2026, 9, 15), dt.date(2026, 9, 5)
        )
        self.assertEqual(comparison.earlier_date, dt.date(2026, 9, 5))
        self.assertEqual(comparison.difference, Decimal("6.00"))

    def test_incomplete_comparison_when_history_is_missing(self):
        comparison = analytics.compare_dates(
            self.product.pk, dt.date(2026, 8, 1), dt.date(2026, 9, 15)
        )
        self.assertFalse(comparison.is_complete)
        self.assertEqual(comparison.difference, Decimal("0"))


class DashboardStatsTests(TestCase):
    def test_groups_products_by_movement(self):
        raj = make_wholesaler()
        rising = make_product(name="Sugar")
        falling = make_product(name="Rice")
        flat = make_product(name="Salt")

        for product, rates in (
            (rising, ("40", "45")),
            (falling, ("50", "48")),
            (flat, ("20", "20")),
        ):
            for index, rate in enumerate(rates):
                Rate.objects.create(
                    product=product,
                    wholesaler=raj,
                    date=dt.date(2026, 9, 1 + index * 5),
                    purchase_rate=Decimal(rate),
                    quantity=Decimal("50"),
                )

        stats = analytics.dashboard_stats()
        self.assertEqual(stats.total_products, 3)
        self.assertEqual(stats.increased, 1)
        self.assertEqual(stats.decreased, 1)
        self.assertEqual(stats.unchanged, 1)

    def test_total_purchase_amount_sums_final_amounts(self):
        product = make_product()
        wholesaler = make_wholesaler()
        for quantity in ("10", "20"):
            Purchase.objects.create(
                product=product,
                wholesaler=wholesaler,
                date=dt.date(2026, 9, 1),
                quantity=Decimal(quantity),
                rate=Decimal("10"),
                transport_cost=Decimal("50"),
            )
        stats = analytics.dashboard_stats()
        self.assertEqual(stats.total_purchase_amount, Decimal("400.00"))

    def test_empty_database_gives_zeroes(self):
        stats = analytics.dashboard_stats()
        self.assertEqual(stats.total_products, 0)
        self.assertEqual(stats.total_purchase_amount, Decimal("0.00"))


class MonthlyReportTests(TestCase):
    def setUp(self):
        self.product = make_product()
        self.other = make_product(name="Rice", unit="kg")
        self.wholesaler = make_wholesaler()

    def test_report_totals_only_count_that_month(self):
        Purchase.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 9, 10),
            quantity=Decimal("10"),
            rate=Decimal("40"),
        )
        Purchase.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 8, 10),
            quantity=Decimal("10"),
            rate=Decimal("30"),
        )

        report = analytics.monthly_report("2026-09")
        self.assertEqual(report.purchase_count, 1)
        self.assertEqual(report.total_purchase_amount, Decimal("400.00"))
        self.assertEqual(report.label, "September 2026")

    def test_movement_is_measured_against_the_previous_month(self):
        # August rate, then a September rate: the September row should still
        # show the rise rather than looking like a first entry.
        for rate, date in (("40", dt.date(2026, 8, 28)), ("44", dt.date(2026, 9, 2))):
            Rate.objects.create(
                product=self.product,
                wholesaler=self.wholesaler,
                date=date,
                purchase_rate=Decimal(rate),
                quantity=Decimal("50"),
            )

        report = analytics.monthly_report("2026-09")
        self.assertEqual(len(report.increased), 1)
        self.assertEqual(report.increased[0].difference, Decimal("4.00"))

    def test_month_bounds_handles_december(self):
        first, last = analytics.month_bounds("2026-12")
        self.assertEqual(first, dt.date(2026, 12, 1))
        self.assertEqual(last, dt.date(2026, 12, 31))

    def test_available_months_are_newest_first(self):
        Purchase.objects.create(
            product=self.product,
            wholesaler=self.wholesaler,
            date=dt.date(2026, 7, 1),
            quantity=Decimal("1"),
            rate=Decimal("1"),
        )
        months = analytics.available_months()
        self.assertEqual(months, sorted(months, reverse=True))
        self.assertIn("2026-07", months)


class SpendTests(TestCase):
    def test_spend_by_wholesaler_is_biggest_first(self):
        product = make_product()
        raj = make_wholesaler("Raj Traders")
        abc = make_wholesaler("ABC Wholesale")

        Purchase.objects.create(
            product=product, wholesaler=raj, date=dt.date(2026, 9, 1),
            quantity=Decimal("10"), rate=Decimal("10"),
        )
        Purchase.objects.create(
            product=product, wholesaler=abc, date=dt.date(2026, 9, 2),
            quantity=Decimal("10"), rate=Decimal("50"),
        )

        rows = analytics.spend_by_wholesaler()
        self.assertEqual(rows[0].wholesaler.company_name, "ABC Wholesale")
        self.assertEqual(rows[0].amount, Decimal("500.00"))
        self.assertEqual(rows[1].amount, Decimal("100.00"))

    def test_purchase_trend_is_oldest_first(self):
        product = make_product()
        wholesaler = make_wholesaler()
        for month in (7, 8, 9):
            Purchase.objects.create(
                product=product, wholesaler=wholesaler,
                date=dt.date(2026, month, 5),
                quantity=Decimal("1"), rate=Decimal("100"),
            )
        points = analytics.purchase_trend()
        self.assertEqual([p.month for p in points], ["2026-07", "2026-08", "2026-09"])

    def test_purchase_trend_limit_keeps_the_latest_months(self):
        product = make_product()
        wholesaler = make_wholesaler()
        for month in (5, 6, 7, 8):
            Purchase.objects.create(
                product=product, wholesaler=wholesaler,
                date=dt.date(2026, month, 5),
                quantity=Decimal("1"), rate=Decimal("10"),
            )
        points = analytics.purchase_trend(limit=2)
        self.assertEqual([p.month for p in points], ["2026-07", "2026-08"])


class PriceSeriesTests(TestCase):
    def test_series_is_oldest_first_with_wholesaler_names(self):
        product = make_product()
        wholesaler = make_wholesaler("Raj Traders")
        for rate, day in (("44", 10), ("40", 1)):
            Rate.objects.create(
                product=product, wholesaler=wholesaler,
                date=dt.date(2026, 9, day),
                purchase_rate=Decimal(rate), quantity=Decimal("50"),
            )
        series = analytics.price_series(product.pk)
        self.assertEqual([point.rate for point in series], [Decimal("40.00"), Decimal("44.00")])
        self.assertEqual(series[0].wholesaler_name, "Raj Traders")
