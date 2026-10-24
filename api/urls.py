"""API routes."""

from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("", views.api_root, name="root"),
    path("lookup/", views.lookup, name="lookup"),
    path("tally-product/", views.TallyProductSyncView.as_view(), name="tally-sync"),
    path("settings/", views.display_settings, name="settings"),
    path("stations/", views.stations, name="stations"),
    path("login/", views.api_login, name="login"),
    path("logout/", views.api_logout, name="logout"),
    # Full CRUD. GET is open; POST/PUT/PATCH/DELETE need the X-API-Key header.
    path("products/", views.ProductListCreateView.as_view(), name="product-list"),
    path("products/<int:pk>/", views.ProductDetailView.as_view(), name="product-detail"),
]
