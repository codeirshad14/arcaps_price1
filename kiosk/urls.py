"""Kiosk routes — a single page, everything else happens over the API."""

from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "kiosk"

urlpatterns = [
    path("", views.screen, name="screen"),
    path("login/", views.KioskLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
