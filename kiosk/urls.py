"""Kiosk routes — a single page, everything else happens over the API.

Plus the installable-app files (see kiosk/pwa.py). They live at the site
root because a service worker can only look after pages at or below its own
address, and the app is the whole site.
"""

from django.contrib.auth.views import LogoutView
from django.urls import path

from . import pwa, views

app_name = "kiosk"

urlpatterns = [
    path("", views.screen, name="screen"),
    path("catalog/", views.catalog, name="catalog"),
    path("login/", views.KioskLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("manifest.webmanifest", pwa.manifest, name="pwa-manifest"),
    path("sw.js", pwa.service_worker, name="pwa-service-worker"),
    path("pwa/icon-<int:size>.png", pwa.icon, name="pwa-icon"),
    path(
        "pwa/icon-<int:size>-maskable.png",
        pwa.icon,
        {"maskable": True},
        name="pwa-icon-maskable",
    ),
]
