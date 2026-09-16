"""
URLs, matching the routes in the original `App.tsx`.

The React paths are kept so any bookmark or note carries over: `/products`,
`/add-rate`, `/rate-history`, `/compare`, `/purchases`, `/reports`,
`/settings`.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Products
    path("products/", views.products, name="products"),
    path("products/new/", views.product_form, name="product_create"),
    path("products/<int:pk>/edit/", views.product_form, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    # Wholesalers
    path("wholesalers/", views.wholesalers, name="wholesalers"),
    path("wholesalers/new/", views.wholesaler_form, name="wholesaler_create"),
    path("wholesalers/<int:pk>/edit/", views.wholesaler_form, name="wholesaler_edit"),
    path("wholesalers/<int:pk>/delete/", views.wholesaler_delete, name="wholesaler_delete"),
    # Rates
    path("add-rate/", views.add_rate, name="add_rate"),
    path("rates/<int:pk>/edit/", views.add_rate, name="rate_edit"),
    path("rates/<int:pk>/delete/", views.rate_delete, name="rate_delete"),
    path("rate-history/", views.rate_history, name="rate_history"),
    # Compare
    path("compare/", views.compare, name="compare"),
    # Purchases
    path("purchases/", views.purchases, name="purchases"),
    path("purchases/new/", views.purchase_form, name="purchase_create"),
    path("purchases/<int:pk>/edit/", views.purchase_form, name="purchase_edit"),
    path("purchases/<int:pk>/delete/", views.purchase_delete, name="purchase_delete"),
    # Reports
    path("reports/", views.reports, name="reports"),
    # Settings and data
    path("settings/", views.shop_settings, name="settings"),
    path("settings/categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("settings/load-sample-data/", views.load_sample_data, name="load_sample_data"),
    path("settings/delete-everything/", views.reset_data, name="reset_data"),
    # Export
    path("export/backup.json", views.export_backup, name="export_backup"),
    path("export/<str:dataset>.<str:fmt>", views.export, name="export"),
    # Search and small helpers
    path("search/", views.search, name="search"),
    path("api/rate-preview/", views.rate_preview_api, name="rate_preview_api"),
]
