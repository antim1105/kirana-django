"""Tests for the formatting filters, especially Indian digit grouping."""

import datetime as dt
from decimal import Decimal

from django.template import Context, Template
from django.test import TestCase
from django.utils import timezone

from manager.models import ShopSettings
from manager.templatetags import kirana


class GroupingTests(TestCase):
    def test_indian_grouping(self):
        self.assertEqual(kirana.group_indian("123"), "123")
        self.assertEqual(kirana.group_indian("1234"), "1,234")
        self.assertEqual(kirana.group_indian("123456"), "1,23,456")
        self.assertEqual(kirana.group_indian("12345678"), "1,23,45,678")

    def test_number_filter(self):
        self.assertEqual(kirana.number(Decimal("1234.5")), "1,234.50")
        self.assertEqual(kirana.number(Decimal("1234.5"), 0), "1,235")
        self.assertEqual(kirana.number(Decimal("-1234.5")), "-1,234.50")

    def test_percent_filter(self):
        self.assertEqual(kirana.percent(Decimal("12.5")), "+12.50%")
        self.assertEqual(kirana.percent(Decimal("-3")), "-3.00%")
        self.assertEqual(kirana.percent(None), "—")

    def test_quantity_drops_pointless_decimals(self):
        self.assertEqual(kirana.quantity(Decimal("50.00"), "kg"), "50 kg")
        self.assertEqual(kirana.quantity(Decimal("50.25"), "kg"), "50.25 kg")


class MoneyTagTests(TestCase):
    def render(self, template: str, **context) -> str:
        return Template("{% load kirana %}" + template).render(
            Context({"settings_obj": ShopSettings.load(), **context})
        )

    def test_money_uses_the_shop_symbol(self):
        self.assertEqual(self.render("{% money value %}", value=Decimal("1234.5")), "₹1,234.50")

    def test_signed_money_marks_a_rise(self):
        self.assertEqual(self.render("{% signed_money value %}", value=Decimal("3")), "+₹3.00")
        self.assertEqual(self.render("{% signed_money value %}", value=Decimal("-3")), "-₹3.00")

    def test_symbol_follows_settings(self):
        settings_obj = ShopSettings.load()
        settings_obj.currency_symbol = "$"
        settings_obj.save()
        self.assertEqual(self.render("{% money value %}", value=Decimal("5")), "$5.00")


class DateTests(TestCase):
    def render_date(self, value, fmt):
        settings_obj = ShopSettings.load()
        settings_obj.date_format = fmt
        settings_obj.save()
        return Template("{% load kirana %}{% shop_date value %}").render(
            Context({"settings_obj": settings_obj, "value": value})
        )

    def test_date_formats(self):
        date = dt.date(2026, 9, 15)
        self.assertEqual(self.render_date(date, "dd-MM-yyyy"), "15-09-2026")
        self.assertEqual(self.render_date(date, "yyyy-MM-dd"), "2026-09-15")
        self.assertEqual(self.render_date(date, "dd/MM/yyyy"), "15/09/2026")

    def test_relative_date(self):
        today = timezone.localdate()
        self.assertEqual(kirana.relative_date(today), "Today")
        self.assertEqual(kirana.relative_date(today - dt.timedelta(days=1)), "Yesterday")
        self.assertEqual(kirana.relative_date(today - dt.timedelta(days=5)), "5 days ago")
        self.assertEqual(kirana.relative_date(today + dt.timedelta(days=3)), "In 3 days")

    def test_month_label(self):
        self.assertEqual(kirana.month_label("2026-09"), "September 2026")

    def test_short_date(self):
        self.assertEqual(kirana.short_date(dt.date(2026, 9, 15)), "15 Sep")


class TrendTests(TestCase):
    def test_trend_classes(self):
        self.assertIn("rose", kirana.trend_class("up", "text"))
        self.assertIn("emerald", kirana.trend_class("down", "text"))
        self.assertIn("slate", kirana.trend_class("same", "text"))

    def test_unknown_trend_falls_back_to_no_change(self):
        self.assertEqual(kirana.trend_text(""), "No change")
        self.assertIn("slate", kirana.trend_class(None, "text"))

    def test_plural(self):
        self.assertEqual(kirana.plural(1, "product"), "1 product")
        self.assertEqual(kirana.plural(4, "product"), "4 products")
