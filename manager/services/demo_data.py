"""
Sample data — the port of `data/demoData.ts`.

Same product list, same wholesalers, same seeded generator, so the demo looks
identical to the React version. Dates are worked out relative to today, which
means the dashboard always has something recent to show no matter when this
runs.

Nothing here runs on its own. The app starts empty; this is only loaded from
Settings or by `manage.py load_demo_data`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import (
    DEFAULT_CATEGORY_NAMES,
    Category,
    Product,
    Purchase,
    Rate,
    Wholesaler,
)
from . import calc


class SeededRandom:
    """
    The same linear congruential generator the TypeScript version used.

    Python's `random` would do, but reproducing the exact algorithm means the
    sample data matches the original app run for run, which makes the two
    easy to compare side by side.
    """

    MODULUS = 4294967296

    def __init__(self, seed: int) -> None:
        self.value = seed

    def next(self) -> Decimal:
        self.value = (self.value * 1664525 + 1013904223) % self.MODULUS
        return Decimal(self.value) / Decimal(self.MODULUS)


@dataclass(frozen=True)
class ProductSeed:
    name: str
    category: str
    brand: str
    unit: str
    pack_size: str
    base_rate: Decimal
    minimum_stock: Decimal | None = None


def _d(value: str | int) -> Decimal:
    return Decimal(str(value))


PRODUCT_SEEDS: list[ProductSeed] = [
    ProductSeed("Sugar", "Sugar & Salt", "Local Mill", "kg", "50 kg bag", _d(42), _d(100)),
    ProductSeed("Wheat", "Grains & Flour", "Sharbati", "kg", "50 kg bag", _d(28), _d(200)),
    ProductSeed("Rice", "Grains & Flour", "Kolam", "kg", "25 kg bag", _d(50), _d(150)),
    ProductSeed("Toor Dal", "Pulses & Dal", "Unbranded", "kg", "30 kg bag", _d(152), _d(60)),
    ProductSeed("Moong Dal", "Pulses & Dal", "Unbranded", "kg", "30 kg bag", _d(118), _d(40)),
    ProductSeed("Masoor Dal", "Pulses & Dal", "Unbranded", "kg", "30 kg bag", _d(96), _d(40)),
    ProductSeed("Chana Dal", "Pulses & Dal", "Unbranded", "kg", "30 kg bag", _d(88), _d(50)),
    ProductSeed("Salt", "Sugar & Salt", "Tata", "kg", "1 kg x 24", _d(20), _d(80)),
    ProductSeed("Tea", "Tea & Coffee", "Red Label", "kg", "1 kg pouch", _d(220), _d(20)),
    ProductSeed("Coffee", "Tea & Coffee", "Bru", "kg", "500 g x 12", _d(480), _d(10)),
    ProductSeed("Refined Oil", "Edible Oil & Ghee", "Fortune", "litre", "15 L tin", _d(120), _d(60)),
    ProductSeed("Mustard Oil", "Edible Oil & Ghee", "Dhara", "litre", "15 L tin", _d(145), _d(45)),
    ProductSeed("Ghee", "Edible Oil & Ghee", "Amul", "litre", "1 L x 12", _d(560), _d(15)),
    ProductSeed("Biscuits", "Snacks & Biscuits", "Parle-G", "box", "60 packs", _d(310), _d(12)),
    ProductSeed("Namkeen", "Snacks & Biscuits", "Haldiram", "kg", "400 g x 20", _d(205), _d(25)),
    ProductSeed("Spices", "Spices & Masala", "Everest", "kg", "500 g x 20", _d(390), _d(15)),
    ProductSeed("Detergent", "Home Care", "Nirma", "kg", "5 kg pack", _d(68), _d(50)),
    ProductSeed("Soap", "Personal Care", "Lifebuoy", "box", "72 pieces", _d(640), _d(10)),
    ProductSeed("Shampoo", "Personal Care", "Clinic Plus", "box", "96 sachets", _d(215), _d(8)),
    ProductSeed("Toothpaste", "Personal Care", "Colgate", "box", "24 x 100 g", _d(1180), _d(6)),
]

WHOLESALER_SEEDS = [
    {
        "name": "Rajesh Sharma",
        "company_name": "Raj Traders",
        "mobile": "9827012345",
        "address": "Siyaganj Main Road, Indore",
        "notes": "Best for sugar and dal. Delivers on Tuesday.",
    },
    {
        "name": "Anil Gupta",
        "company_name": "ABC Wholesale",
        "mobile": "9826154321",
        "address": "Chhawni Grain Market, Indore",
        "notes": "Credit of 15 days available.",
    },
    {
        "name": "Mahesh Jain",
        "company_name": "XYZ Distributors",
        "mobile": "9893098765",
        "address": "Malharganj, Indore",
        "notes": "FMCG and personal care stockist.",
    },
    {
        "name": "Suresh Patel",
        "company_name": "Patel Kirana Bhandar",
        "mobile": "9977011223",
        "address": "Ratlam Road, Ujjain",
        "notes": "Cheaper oil, transport charged extra.",
    },
    {
        "name": "Imran Khan",
        "company_name": "Khan Brothers Agency",
        "mobile": "9302244556",
        "address": "Sanwer Road, Indore",
        "notes": "Own delivery vehicle, no transport cost.",
    },
]

#: Each wholesaler quotes a little above or below the base rate.
WHOLESALER_BIAS = [
    Decimal("1.0"),
    Decimal("0.955"),
    Decimal("1.028"),
    Decimal("0.982"),
    Decimal("1.011"),
]

#: Deliberately flat so the dashboard's "no change" group is never empty.
STABLE_PRODUCTS = {"Salt", "Wheat", "Detergent"}

#: Four dated snapshots across the last three weeks.
DATE_OFFSETS = [21, 14, 7, 0]

SEED = 20260915


def days_ago(days: int) -> dt.date:
    return timezone.localdate() - dt.timedelta(days=days)


@transaction.atomic
def load_demo_data(reset: bool = True) -> dict[str, int]:
    """
    Fill the database with a realistic three weeks of rates.

    With `reset` on (the default, matching the app's "Load sample data"
    button) existing records are cleared first so the sample set is not mixed
    into real data.
    """
    if reset:
        clear_all_data()

    random = SeededRandom(SEED)

    for name in DEFAULT_CATEGORY_NAMES:
        Category.objects.get_or_create(name=name)

    products = [
        Product.objects.create(
            name=seed.name,
            category=seed.category,
            brand=seed.brand,
            unit=seed.unit,
            pack_size=seed.pack_size,
            minimum_stock=seed.minimum_stock,
            notes="",
        )
        for seed in PRODUCT_SEEDS
    ]
    wholesalers = [Wholesaler.objects.create(**seed) for seed in WHOLESALER_SEEDS]

    rates: list[Rate] = []

    for product_index, seed in enumerate(PRODUCT_SEEDS):
        product = products[product_index]
        running = seed.base_rate
        stable = seed.name in STABLE_PRODUCTS

        for step, offset in enumerate(DATE_OFFSETS):
            if stable:
                drift = Decimal("0")
            else:
                drift = (random.next() - Decimal("0.42")) * seed.base_rate * Decimal("0.09")
            running = max(calc.round2(running + drift), calc.round2(seed.base_rate * Decimal("0.8")))

            wholesaler_index = (product_index + step) % len(wholesalers)
            bias = Decimal("1") if stable else WHOLESALER_BIAS[wholesaler_index]
            purchase_rate = calc.round2(running * bias)

            if seed.unit == "box":
                quantity = 5 + int(random.next() * 10)
            else:
                quantity = 25 + int(random.next() * 75)

            transport = Decimal("0") if wholesaler_index == 4 else _d(round(random.next() * 4) * 50)
            other = Decimal("50") if random.next() > Decimal("0.8") else Decimal("0")

            rates.append(
                Rate(
                    product=product,
                    wholesaler=wholesalers[wholesaler_index],
                    date=days_ago(offset),
                    purchase_rate=purchase_rate,
                    unit=seed.unit,
                    quantity=_d(quantity),
                    transport_cost=transport,
                    other_cost=other,
                    effective_rate=calc.effective_rate(
                        purchase_rate, quantity, transport, other
                    ),
                    notes="",
                )
            )

        # A competing quote yesterday for the first ten products, which is
        # what makes "who is cheapest" worth looking at.
        if product_index < 10:
            wholesaler_index = (product_index + 2) % len(wholesalers)
            bias = WHOLESALER_BIAS[wholesaler_index]
            purchase_rate = calc.round2(running * bias * Decimal("0.97"))
            rates.append(
                Rate(
                    product=product,
                    wholesaler=wholesalers[wholesaler_index],
                    date=days_ago(1),
                    purchase_rate=purchase_rate,
                    unit=seed.unit,
                    quantity=_d(50),
                    transport_cost=_d(100),
                    other_cost=Decimal("0"),
                    effective_rate=calc.effective_rate(purchase_rate, 50, 100, 0),
                    notes="Quote taken on phone.",
                )
            )

    Rate.objects.bulk_create(rates)
    # bulk_create skips save(), so re-read the rows to build purchases from
    # exactly what landed in the database.
    saved_rates = list(Rate.objects.with_related().oldest_first())

    purchases: list[Purchase] = []
    for index in range(20):
        product = products[(index * 3) % len(PRODUCT_SEEDS)]
        product_rates = [r for r in saved_rates if r.product_id == product.pk]
        if not product_rates:
            continue
        source = product_rates[index % len(product_rates)]
        quantity = _d(5 + (index % 6) if product.unit == "box" else 25 + ((index * 7) % 76))
        total = calc.total_amount(quantity, source.purchase_rate)
        transport = source.transport_cost
        purchases.append(
            Purchase(
                product=product,
                wholesaler=source.wholesaler,
                date=source.date,
                quantity=quantity,
                rate=source.purchase_rate,
                total_amount=total,
                transport_cost=transport,
                other_cost=Decimal("0"),
                final_amount=calc.final_amount(total, transport, Decimal("0")),
                notes="",
            )
        )

    Purchase.objects.bulk_create(purchases)

    return {
        "categories": Category.objects.count(),
        "products": len(products),
        "wholesalers": len(wholesalers),
        "rates": len(rates),
        "purchases": len(purchases),
    }


@transaction.atomic
def clear_all_data() -> dict[str, int]:
    """
    Delete every record, leaving shop settings alone.

    This is the "Delete everything" button. Settings survive on purpose —
    losing the shop name and currency along with the data would be a nasty
    surprise.
    """
    counts = {
        "purchases": Purchase.objects.count(),
        "rates": Rate.objects.count(),
        "products": Product.objects.count(),
        "wholesalers": Wholesaler.objects.count(),
        "categories": Category.objects.count(),
    }
    Purchase.objects.all().delete()
    Rate.objects.all().delete()
    Product.objects.all().delete()
    Wholesaler.objects.all().delete()
    Category.objects.all().delete()
    return counts
