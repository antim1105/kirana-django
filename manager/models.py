"""
The domain model, ported one-to-one from `src/types/index.ts`.

Two rules from the original app are enforced here rather than in a view, so
they hold no matter what writes to the database — the admin, a management
command or a future API:

* `Rate.effective_rate` is always recomputed on save.
* `Purchase.total_amount` and `Purchase.final_amount` are always recomputed
  on save.

Category and unit are deliberately free text, not foreign keys or choices.
The shop types "peti", "bori" or "Pooja items" and the app accepts it; the
`Category` table only exists to remember what has been typed before so it can
be offered as a suggestion and used as a filter.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .services import calc

#: Suggestions only — the unit field accepts anything typed into it.
UNIT_SUGGESTIONS: list[str] = [
    "kg",
    "g",
    "quintal",
    "litre",
    "ml",
    "piece",
    "packet",
    "dozen",
    "bag",
    "box",
    "tin",
    "peti",
    "bori",
]

DEFAULT_CATEGORY_NAMES: list[str] = [
    "Grains & Flour",
    "Pulses & Dal",
    "Sugar & Salt",
    "Edible Oil & Ghee",
    "Tea & Coffee",
    "Spices & Masala",
    "Snacks & Biscuits",
    "Home Care",
    "Personal Care",
]

MONEY = {"max_digits": 12, "decimal_places": 2}
BIG_MONEY = {"max_digits": 14, "decimal_places": 2}
NON_NEGATIVE = [MinValueValidator(Decimal("0"))]


class TimestampedModel(models.Model):
    """`createdAt` / `updatedAt` from the TypeScript interfaces."""

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Category(TimestampedModel):
    """A product category the shop has used at least once."""

    name = models.CharField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def remember(cls, name: str) -> "Category | None":
        """
        Save a typed category so it shows up as a suggestion next time.

        Matching is case-insensitive, so "dry fruits" does not become a second
        entry next to "Dry fruits". Returns the existing or created row, or
        None for blank input.
        """
        cleaned = (name or "").strip()
        if not cleaned:
            return None
        existing = cls.objects.filter(name__iexact=cleaned).first()
        if existing:
            return existing
        return cls.objects.create(name=cleaned)

    @property
    def in_use(self) -> bool:
        return Product.objects.filter(category__iexact=self.name).exists()


class Product(TimestampedModel):
    """Something the shop buys from wholesalers."""

    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=80, help_text="Type any category you use.")
    brand = models.CharField(max_length=80, blank=True)
    unit = models.CharField(
        max_length=16,
        help_text="Every rate for this product is recorded per this unit.",
    )
    pack_size = models.CharField(max_length=60, blank=True)
    minimum_stock = models.DecimalField(
        **MONEY, null=True, blank=True, validators=NON_NEGATIVE
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["category"])]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return f"{reverse('rate_history')}?product={self.pk}"

    @property
    def label(self) -> str:
        """Name with brand, for dropdowns and report rows."""
        return f"{self.name} ({self.brand})" if self.brand else self.name

    def latest_rate(self) -> "Rate | None":
        return self.rates.first()


class Wholesaler(TimestampedModel):
    """Someone the shop buys from. The company name is what identifies them."""

    company_name = models.CharField(max_length=120)
    name = models.CharField(max_length=120, blank=True, help_text="Contact person.")
    mobile = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["company_name"]

    def __str__(self) -> str:
        return self.company_name

    def get_absolute_url(self) -> str:
        return reverse("wholesalers")


class RateQuerySet(models.QuerySet):
    """
    Newest first, with ties on the same date broken by entry order.

    The original app sorted by date descending and then by `createdAt`
    descending, so two quotes recorded on the same day keep the order they
    were typed in. `-id` is the final tie-break for rows saved in the same
    transaction, where the timestamps can be identical.
    """

    def newest_first(self) -> "RateQuerySet":
        return self.order_by("-date", "-created_at", "-id")

    def oldest_first(self) -> "RateQuerySet":
        return self.order_by("date", "created_at", "id")

    def with_related(self) -> "RateQuerySet":
        return self.select_related("product", "wholesaler")

    def for_product(self, product_id) -> "RateQuerySet":
        return self.filter(product_id=product_id)

    def on_or_before(self, date) -> "RateQuerySet":
        return self.filter(date__lte=date)


class Rate(TimestampedModel):
    """
    A price one wholesaler quoted for one product on one day.

    This is the heart of the app: everything else compares these rows against
    each other.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="rates")
    wholesaler = models.ForeignKey(
        Wholesaler, on_delete=models.CASCADE, related_name="rates"
    )
    date = models.DateField()
    purchase_rate = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    unit = models.CharField(max_length=16)
    quantity = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    transport_cost = models.DecimalField(**MONEY, default=Decimal("0"), validators=NON_NEGATIVE)
    other_cost = models.DecimalField(**MONEY, default=Decimal("0"), validators=NON_NEGATIVE)
    effective_rate = models.DecimalField(
        **MONEY,
        editable=False,
        help_text="purchase_rate + (transport_cost + other_cost) / quantity",
    )
    notes = models.TextField(blank=True)

    objects = RateQuerySet.as_manager()

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["product", "-date"], name="rates_product_date_idx"),
            models.Index(fields=["wholesaler", "-date"], name="rates_whl_date_idx"),
            models.Index(fields=["date"], name="rates_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product} @ {self.purchase_rate} ({self.date})"

    def save(self, *args, **kwargs):
        # The unit follows the product unless one was set explicitly.
        if not self.unit and self.product_id:
            self.unit = self.product.unit
        self.effective_rate = calc.effective_rate(
            self.purchase_rate, self.quantity, self.transport_cost, self.other_cost
        )
        super().save(*args, **kwargs)

    @property
    def extra_cost(self) -> Decimal:
        return calc.round2(calc.to_decimal(self.transport_cost) + calc.to_decimal(self.other_cost))

    def duplicates(self) -> RateQuerySet:
        """
        Other entries for the same product, wholesaler and date.

        Used to raise the "a rate for this day already exists" warning before
        anything is written.
        """
        return (
            Rate.objects.filter(
                product_id=self.product_id,
                wholesaler_id=self.wholesaler_id,
                date=self.date,
            )
            .exclude(pk=self.pk)
            .newest_first()
        )


class Purchase(TimestampedModel):
    """An actual buy, as opposed to a quoted rate."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="purchases")
    wholesaler = models.ForeignKey(
        Wholesaler, on_delete=models.CASCADE, related_name="purchases"
    )
    date = models.DateField()
    quantity = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    rate = models.DecimalField(**MONEY, validators=NON_NEGATIVE)
    total_amount = models.DecimalField(
        **BIG_MONEY, editable=False, help_text="quantity * rate"
    )
    transport_cost = models.DecimalField(**MONEY, default=Decimal("0"), validators=NON_NEGATIVE)
    other_cost = models.DecimalField(**MONEY, default=Decimal("0"), validators=NON_NEGATIVE)
    final_amount = models.DecimalField(
        **BIG_MONEY,
        editable=False,
        help_text="total_amount + transport_cost + other_cost",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["-date"], name="purchases_date_idx"),
            models.Index(fields=["product", "-date"], name="purchases_prod_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.product} x {self.quantity} = {self.final_amount}"

    def save(self, *args, **kwargs):
        self.total_amount = calc.total_amount(self.quantity, self.rate)
        self.final_amount = calc.final_amount(
            self.total_amount, self.transport_cost, self.other_cost
        )
        super().save(*args, **kwargs)

    @property
    def effective_rate(self) -> Decimal:
        """What the goods actually cost per unit, delivery included."""
        return calc.effective_rate(
            self.rate, self.quantity, self.transport_cost, self.other_cost
        )


class ShopSettings(models.Model):
    """
    Shop preferences — one row, always.

    This replaces the `kwrm.settings` localStorage key. `load()` returns the
    single row, creating it with defaults on first use, so templates and views
    never have to handle a missing object.
    """

    class DateFormat(models.TextChoices):
        DAY_FIRST_DASH = "dd-MM-yyyy", "31-12-2026"
        ISO = "yyyy-MM-dd", "2026-12-31"
        DAY_FIRST_SLASH = "dd/MM/yyyy", "31/12/2026"

    singleton_id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    business_name = models.CharField(max_length=120, default="My Kirana Store")
    shop_address = models.TextField(blank=True)
    mobile = models.CharField(max_length=20, blank=True)
    currency = models.CharField(max_length=8, default="INR")
    currency_symbol = models.CharField(max_length=4, default="₹")
    default_unit = models.CharField(max_length=16, default="kg")
    date_format = models.CharField(
        max_length=12, choices=DateFormat.choices, default=DateFormat.DAY_FIRST_DASH
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "shop settings"
        verbose_name_plural = "shop settings"
        constraints = [
            models.CheckConstraint(
                condition=Q(singleton_id=1), name="shop_settings_single_row"
            )
        ]

    def __str__(self) -> str:
        return self.business_name

    def save(self, *args, **kwargs):
        self.singleton_id = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover - guard only
        raise RuntimeError("Shop settings cannot be deleted, only edited.")

    @classmethod
    def load(cls) -> "ShopSettings":
        obj, _ = cls.objects.get_or_create(singleton_id=1)
        return obj
