"""
Admin registrations.

The app's own screens are the intended way in; this is here for bulk fixes
and for looking at the raw rows when something does not add up.
"""

from django.contrib import admin

from .models import Category, Product, Purchase, Rate, ShopSettings, Wholesaler


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "in_use", "created_at")
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "brand", "unit", "pack_size", "minimum_stock")
    list_filter = ("category", "unit")
    search_fields = ("name", "brand", "category")
    ordering = ("name",)


@admin.register(Wholesaler)
class WholesalerAdmin(admin.ModelAdmin):
    list_display = ("company_name", "name", "mobile")
    search_fields = ("company_name", "name", "mobile")
    ordering = ("company_name",)


@admin.register(Rate)
class RateAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "product",
        "wholesaler",
        "purchase_rate",
        "unit",
        "quantity",
        "effective_rate",
    )
    list_filter = ("date", "wholesaler", "product")
    search_fields = ("product__name", "wholesaler__company_name", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("product", "wholesaler")
    readonly_fields = ("effective_rate", "created_at", "updated_at")


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "product",
        "wholesaler",
        "quantity",
        "rate",
        "total_amount",
        "final_amount",
    )
    list_filter = ("date", "wholesaler")
    search_fields = ("product__name", "wholesaler__company_name", "notes")
    date_hierarchy = "date"
    autocomplete_fields = ("product", "wholesaler")
    readonly_fields = ("total_amount", "final_amount", "created_at", "updated_at")


@admin.register(ShopSettings)
class ShopSettingsAdmin(admin.ModelAdmin):
    list_display = ("business_name", "currency_symbol", "default_unit", "date_format")

    def has_add_permission(self, request):
        # One row only; edit the existing one.
        return not ShopSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
