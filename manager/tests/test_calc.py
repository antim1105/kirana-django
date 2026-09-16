"""
Tests for the arithmetic.

These are the rules the whole app rests on, and they are the ones the README
of the original project documented, so they are pinned down here with the
worked example from that README among them.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from manager.services import calc


class RoundingTests(SimpleTestCase):
    def test_rounds_half_up_to_two_places(self):
        self.assertEqual(calc.round2("1.005"), Decimal("1.01"))
        self.assertEqual(calc.round2("2.345"), Decimal("2.35"))
        self.assertEqual(calc.round2(42), Decimal("42.00"))

    def test_to_decimal_survives_junk(self):
        self.assertEqual(calc.to_decimal(None), Decimal("0"))
        self.assertEqual(calc.to_decimal(""), Decimal("0"))
        self.assertEqual(calc.to_decimal("not a number"), Decimal("0"))
        self.assertEqual(calc.to_decimal(" 42.50 "), Decimal("42.50"))
        self.assertIsNone(calc.to_decimal("", default=None))


class DifferenceTests(SimpleTestCase):
    def test_difference_is_current_minus_previous(self):
        self.assertEqual(calc.difference("45", "42"), Decimal("3.00"))
        self.assertEqual(calc.difference("40", "42"), Decimal("-2.00"))
        self.assertEqual(calc.difference("42", "42"), Decimal("0.00"))

    def test_percentage_change(self):
        self.assertEqual(calc.percentage_change("110", "100"), Decimal("10.00"))
        self.assertEqual(calc.percentage_change("90", "100"), Decimal("-10.00"))

    def test_percentage_change_guards_against_zero(self):
        # A previous rate of zero would divide by zero; the original returned 0.
        self.assertEqual(calc.percentage_change("50", "0"), Decimal("0"))

    def test_trend_direction(self):
        self.assertEqual(calc.trend_of("0.5"), "up")
        self.assertEqual(calc.trend_of("-0.5"), "down")
        self.assertEqual(calc.trend_of("0"), "same")

    def test_tiny_movements_count_as_no_change(self):
        self.assertEqual(calc.trend_of("0.00001"), "same")
        self.assertEqual(calc.trend_of("-0.00001"), "same")


class EffectiveRateTests(SimpleTestCase):
    def test_worked_example_from_the_brief(self):
        # 42/kg for 100 kg with 200 transport is 44/kg.
        self.assertEqual(
            calc.effective_rate("42", "100", "200", "0"), Decimal("44.00")
        )

    def test_other_costs_are_spread_too(self):
        self.assertEqual(
            calc.effective_rate("100", "10", "50", "50"), Decimal("110.00")
        )

    def test_zero_quantity_leaves_the_rate_as_quoted(self):
        self.assertEqual(calc.effective_rate("42", "0", "200", "0"), Decimal("42.00"))

    def test_no_extras_means_no_change(self):
        self.assertEqual(calc.effective_rate("42", "100"), Decimal("42.00"))


class PurchaseTotalTests(SimpleTestCase):
    def test_total_amount(self):
        self.assertEqual(calc.total_amount("50", "42.50"), Decimal("2125.00"))

    def test_final_amount_adds_the_extras(self):
        self.assertEqual(
            calc.final_amount("2125", "200", "50"), Decimal("2375.00")
        )


class AggregateTests(SimpleTestCase):
    def test_average(self):
        self.assertEqual(calc.average(["10", "20", "30"]), Decimal("20.00"))

    def test_average_of_nothing_is_zero(self):
        self.assertEqual(calc.average([]), Decimal("0"))

    def test_total(self):
        self.assertEqual(calc.total(["1.005", "2.005"]), Decimal("3.01"))

    def test_safe_div_returns_none_on_zero(self):
        self.assertIsNone(calc.safe_div("10", "0"))
        self.assertEqual(calc.safe_div("10", "4"), Decimal("2.50"))
