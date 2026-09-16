"""
Forms, carrying over the validation the React forms did by hand.

Rules kept from the original:

* Duplicate product names are rejected, case-insensitively.
* A rate's date cannot be in the future.
* Rate and quantity must be greater than zero — quantity because transport is
  spread over it, so zero would make the effective rate meaningless.
* Transport and other costs cannot be negative.
* Category and unit are free text with suggestions, never a closed dropdown.
"""

from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from .models import (
    UNIT_SUGGESTIONS,
    Category,
    Product,
    Purchase,
    Rate,
    ShopSettings,
    Wholesaler,
)
from .services import analytics

CONTROL = "field-control"
CONTROL_SM = "field-control field-control-sm"


class StyledFormMixin:
    """
    Applies the shared input styling and marks invalid fields.

    The error class is added in `full_clean` rather than `__init__` on
    purpose: reading `self.errors` during `__init__` would validate the form
    before a subclass had finished setting up its fields, and the result is
    cached, so later changes to `required` would be ignored.
    """

    error_class_name = "field-control-error"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                continue
            widget.attrs.setdefault("class", CONTROL)
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("rows", 3)

    def full_clean(self):
        super().full_clean()
        for name in self.errors:
            field = self.fields.get(name)
            if not field:
                continue
            classes = field.widget.attrs.get("class", CONTROL)
            if self.error_class_name not in classes:
                field.widget.attrs["class"] = f"{classes} {self.error_class_name}"


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs={"class": CONTROL, **(attrs or {})}, format="%Y-%m-%d")


def decimal_input(step: str = "0.01", placeholder: str = "0.00") -> forms.NumberInput:
    return forms.NumberInput(
        attrs={"class": CONTROL, "step": step, "min": "0", "placeholder": placeholder}
    )


def unit_suggestions() -> list[str]:
    """Units already used by a product, then the built-in suggestions."""
    used = list(
        Product.objects.exclude(unit="")
        .order_by("unit")
        .values_list("unit", flat=True)
        .distinct()
    )
    extras = [unit for unit in UNIT_SUGGESTIONS if unit not in used]
    return used + extras


# --------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------


class ProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "category",
            "brand",
            "unit",
            "pack_size",
            "minimum_stock",
            "notes",
        ]
        labels = {
            "pack_size": "Pack size",
            "minimum_stock": "Minimum stock",
        }
        help_texts = {
            "category": "Type any category — anything you have used before is suggested.",
            "unit": "Rates for this product are recorded per this unit.",
            "pack_size": "Optional, e.g. 50 kg bag.",
            "minimum_stock": "Optional. Used only as your own reminder.",
        }
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Sugar", "autofocus": True}),
            "category": forms.TextInput(
                attrs={"placeholder": "Sugar & Salt", "list": "category-options"}
            ),
            "brand": forms.TextInput(attrs={"placeholder": "Local Mill"}),
            "unit": forms.TextInput(attrs={"placeholder": "kg", "list": "unit-options"}),
            "pack_size": forms.TextInput(attrs={"placeholder": "50 kg bag"}),
            "minimum_stock": decimal_input(placeholder="100"),
            "notes": forms.Textarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].initial = self.instance.unit or ShopSettings.load().default_unit
        self.category_options = list(
            Category.objects.values_list("name", flat=True)
        ) or []
        self.unit_options = unit_suggestions()

    def clean_name(self) -> str:
        name = (self.cleaned_data["name"] or "").strip()
        clash = Product.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("You already have a product with this name.")
        return name

    def clean_category(self) -> str:
        category = (self.cleaned_data["category"] or "").strip()
        if not category:
            raise ValidationError("Enter a category, or type a new one.")
        # Reuse the existing spelling so "dal" and "Dal" don't both appear.
        existing = Category.objects.filter(name__iexact=category).first()
        return existing.name if existing else category

    def clean_unit(self) -> str:
        unit = (self.cleaned_data["unit"] or "").strip()
        if not unit:
            raise ValidationError("Enter the unit rates are quoted in, e.g. kg.")
        return unit

    def save(self, commit: bool = True) -> Product:
        product = super().save(commit=commit)
        if commit:
            # A category typed into the form is remembered for next time.
            Category.remember(product.category)
        return product


# --------------------------------------------------------------------------
# Wholesalers
# --------------------------------------------------------------------------


class WholesalerForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Wholesaler
        fields = ["company_name", "name", "mobile", "address", "notes"]
        labels = {
            "company_name": "Company name",
            "name": "Contact person",
        }
        help_texts = {
            "company_name": "This is how the wholesaler appears everywhere else.",
            "notes": "Delivery days, credit terms, what they are good for.",
        }
        widgets = {
            "company_name": forms.TextInput(
                attrs={"placeholder": "Raj Traders", "autofocus": True}
            ),
            "name": forms.TextInput(attrs={"placeholder": "Rajesh Sharma"}),
            "mobile": forms.TextInput(
                attrs={"placeholder": "9827012345", "inputmode": "tel"}
            ),
            "address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(),
        }

    def clean_company_name(self) -> str:
        company = (self.cleaned_data["company_name"] or "").strip()
        clash = Wholesaler.objects.filter(company_name__iexact=company)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError("You already have a wholesaler with this name.")
        return company

    def clean_mobile(self) -> str:
        mobile = (self.cleaned_data.get("mobile") or "").strip()
        if mobile and not mobile.replace(" ", "").replace("-", "").replace("+", "").isdigit():
            raise ValidationError("Enter digits only, e.g. 9827012345.")
        return mobile


# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------


class RateForm(StyledFormMixin, forms.ModelForm):
    """
    The rate entry form.

    Saving happens in two steps in the view: this form validates, then a
    review screen shows the movement and any same-day clash before anything
    is written.
    """

    class Meta:
        model = Rate
        fields = [
            "product",
            "wholesaler",
            "date",
            "purchase_rate",
            "quantity",
            "transport_cost",
            "other_cost",
            "notes",
        ]
        labels = {
            "purchase_rate": "Rate quoted",
            "quantity": "Quantity bought",
            "transport_cost": "Transport cost",
            "other_cost": "Other cost",
        }
        help_texts = {
            "quantity": "Transport and other charges are spread over this.",
            "transport_cost": "Total for the lot, not per unit.",
            "notes": "How the quote came in, conditions, anything worth remembering.",
        }
        widgets = {
            "date": DateInput(),
            "purchase_rate": decimal_input(placeholder="42.00"),
            "quantity": decimal_input(placeholder="100"),
            "transport_cost": decimal_input(placeholder="0"),
            "other_cost": decimal_input(placeholder="0"),
            "notes": forms.Textarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.all()
        self.fields["wholesaler"].queryset = Wholesaler.objects.all()
        self.fields["product"].empty_label = "Choose a product"
        self.fields["wholesaler"].empty_label = "Choose a wholesaler"
        self.fields["date"].initial = analytics.today()
        self.fields["transport_cost"].initial = Decimal("0")
        self.fields["other_cost"].initial = Decimal("0")
        self.fields["transport_cost"].required = False
        self.fields["other_cost"].required = False

        if not self.fields["product"].queryset.exists():
            self.fields["product"].help_text = "Add a product first."
        if not self.fields["wholesaler"].queryset.exists():
            self.fields["wholesaler"].help_text = "Add a wholesaler first."

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > analytics.today():
            raise ValidationError("The date cannot be in the future.")
        return date

    def clean_purchase_rate(self) -> Decimal:
        rate = self.cleaned_data["purchase_rate"]
        if rate is None or rate <= 0:
            raise ValidationError("Enter a rate greater than zero.")
        return rate

    def clean_quantity(self) -> Decimal:
        quantity = self.cleaned_data["quantity"]
        if quantity is None or quantity <= 0:
            raise ValidationError(
                "Enter the quantity so transport can be spread over it."
            )
        return quantity

    def clean_transport_cost(self) -> Decimal:
        return self.cleaned_data.get("transport_cost") or Decimal("0")

    def clean_other_cost(self) -> Decimal:
        return self.cleaned_data.get("other_cost") or Decimal("0")

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        if product:
            # The unit always follows the product, as in the original form.
            self.instance.unit = product.unit
        return cleaned

    def find_duplicate(self) -> Rate | None:
        """An existing entry for the same product, wholesaler and date."""
        if not self.is_valid():
            return None
        clash = Rate.objects.filter(
            product=self.cleaned_data["product"],
            wholesaler=self.cleaned_data["wholesaler"],
            date=self.cleaned_data["date"],
        ).with_related()
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        return clash.newest_first().first()


# --------------------------------------------------------------------------
# Purchases
# --------------------------------------------------------------------------


class PurchaseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Purchase
        fields = [
            "product",
            "wholesaler",
            "date",
            "quantity",
            "rate",
            "transport_cost",
            "other_cost",
            "notes",
        ]
        labels = {
            "rate": "Rate paid",
            "transport_cost": "Transport cost",
            "other_cost": "Other cost",
        }
        help_texts = {
            "rate": "Per unit. The total is worked out for you.",
        }
        widgets = {
            "date": DateInput(),
            "quantity": decimal_input(placeholder="50"),
            "rate": decimal_input(placeholder="42.00"),
            "transport_cost": decimal_input(placeholder="0"),
            "other_cost": decimal_input(placeholder="0"),
            "notes": forms.Textarea(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = analytics.today()
        self.fields["product"].empty_label = "Choose a product"
        self.fields["wholesaler"].empty_label = "Choose a wholesaler"
        for name in ("transport_cost", "other_cost"):
            self.fields[name].required = False
            self.fields[name].initial = Decimal("0")

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > analytics.today():
            raise ValidationError("The date cannot be in the future.")
        return date

    def clean_quantity(self) -> Decimal:
        quantity = self.cleaned_data["quantity"]
        if quantity is None or quantity <= 0:
            raise ValidationError("Enter how much you bought.")
        return quantity

    def clean_rate(self) -> Decimal:
        rate = self.cleaned_data["rate"]
        if rate is None or rate <= 0:
            raise ValidationError("Enter a rate greater than zero.")
        return rate

    def clean_transport_cost(self) -> Decimal:
        return self.cleaned_data.get("transport_cost") or Decimal("0")

    def clean_other_cost(self) -> Decimal:
        return self.cleaned_data.get("other_cost") or Decimal("0")


# --------------------------------------------------------------------------
# Settings, categories, import
# --------------------------------------------------------------------------


class ShopSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ShopSettings
        fields = [
            "business_name",
            "shop_address",
            "mobile",
            "currency_symbol",
            "default_unit",
            "date_format",
        ]
        labels = {
            "business_name": "Business name",
            "shop_address": "Shop address",
            "mobile": "Mobile number",
            "currency_symbol": "Currency symbol",
            "default_unit": "Default unit",
            "date_format": "Date format",
        }
        help_texts = {
            "business_name": "Shown in the sidebar and on printed reports.",
            "default_unit": "Pre-filled when you add a product.",
        }
        widgets = {
            "shop_address": forms.Textarea(attrs={"rows": 2}),
            "default_unit": forms.TextInput(attrs={"list": "unit-options"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit_options = unit_suggestions()

    def clean_currency_symbol(self) -> str:
        return (self.cleaned_data["currency_symbol"] or "₹").strip() or "₹"


class CategoryForm(StyledFormMixin, forms.Form):
    name = forms.CharField(
        max_length=80,
        label="",
        widget=forms.TextInput(attrs={"placeholder": "New category name"}),
    )

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Category.objects.filter(name__iexact=name).exists():
            raise ValidationError("That category already exists.")
        return name


class ImportRatesForm(StyledFormMixin, forms.Form):
    """
    CSV import for rates.

    Products and wholesalers named in the file are created if they are not
    already there, which is what made the original importer usable for a first
    load from a spreadsheet.
    """

    file = forms.FileField(
        label="CSV file",
        help_text=(
            "Columns: Date, Product, Wholesaler, Rate, Unit, Quantity, "
            "Transport, Other, Notes. A header row is expected."
        ),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError("That file is larger than 5 MB. Split it up.")
        name = (upload.name or "").lower()
        if not name.endswith(".csv"):
            raise ValidationError("Upload a .csv file.")
        return upload


# --------------------------------------------------------------------------
# Filters (GET forms — never required, never blocking)
# --------------------------------------------------------------------------


class ProductFilterForm(StyledFormMixin, forms.Form):
    q = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={"placeholder": "Search name, brand or category", "type": "search"}
        ),
    )
    category = forms.ChoiceField(required=False, label="", choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        names = Category.objects.values_list("name", flat=True)
        self.fields["category"].choices = [("", "All categories")] + [
            (name, name) for name in names
        ]

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        query = (self.cleaned_data.get("q") or "").strip()
        category = self.cleaned_data.get("category")
        if category:
            queryset = queryset.filter(category=category)
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(brand__icontains=query)
                | Q(category__icontains=query)
            )
        return queryset


class RateHistoryFilterForm(StyledFormMixin, forms.Form):
    MOVEMENT_CHOICES = [
        ("", "Any movement"),
        ("up", "Price increased"),
        ("down", "Price decreased"),
        ("same", "No change"),
    ]

    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label="Product",
        empty_label="Choose a product",
    )
    wholesaler = forms.ModelChoiceField(
        queryset=Wholesaler.objects.none(),
        required=False,
        label="Wholesaler",
        empty_label="All wholesalers",
    )
    movement = forms.ChoiceField(
        choices=MOVEMENT_CHOICES, required=False, label="Movement"
    )
    date_from = forms.DateField(required=False, label="From", widget=DateInput())
    date_to = forms.DateField(required=False, label="To", widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.all()
        self.fields["wholesaler"].queryset = Wholesaler.objects.all()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The end date is before the start date.")
        return cleaned


class CompareDatesForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), label="Product", empty_label="Choose a product"
    )
    date1 = forms.DateField(label="Earlier date", widget=DateInput())
    date2 = forms.DateField(label="Later date", widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.all()


class CompareWholesalersForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), label="Product", empty_label="Choose a product"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.all()


class PurchaseFilterForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label="", empty_label="All products"
    )
    wholesaler = forms.ModelChoiceField(
        queryset=Wholesaler.objects.none(),
        required=False,
        label="",
        empty_label="All wholesalers",
    )
    date_from = forms.DateField(required=False, label="", widget=DateInput())
    date_to = forms.DateField(required=False, label="", widget=DateInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.all()
        self.fields["wholesaler"].queryset = Wholesaler.objects.all()

    def filter(self, queryset):
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data.get("product"):
            queryset = queryset.filter(product=data["product"])
        if data.get("wholesaler"):
            queryset = queryset.filter(wholesaler=data["wholesaler"])
        if data.get("date_from"):
            queryset = queryset.filter(date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(date__lte=data["date_to"])
        return queryset
