"""The top bar shared by every screen.

Both the staff kiosk and the customer portal show the same bar: the shop's
name, and a word saying which screen you are looking at ("Counter" or
"Customer"). Those settings live here, in one place, rather than in the
station config and the portal config separately — which is how they were
before, and meant the name had to be changed twice and could disagree.
"""

from __future__ import annotations

from django.db import models


class ScreenHeaderSettings(models.Model):
    """Shop-wide top-bar wording. A singleton, like CustomerPortalSettings."""

    navbar_name = models.CharField(
        max_length=120,
        blank=True,
        help_text="Name shown in the top bar of BOTH the counter screen and "
        "the customer page. Leave blank to use the station's store name.",
    )
    counter_label = models.CharField(
        max_length=60,
        blank=True,
        help_text="Word under the name on the staff counter screen. Leave "
        "blank for 'Counter'.",
    )
    customer_label = models.CharField(
        max_length=60,
        blank=True,
        help_text="Word under the name on the customer's own phone. Leave "
        "blank for 'Customer'.",
    )
    show_station_on_counter = models.BooleanField(
        default=True,
        help_text="Also show which station and price list the counter screen "
        "is using. Useful while setting up, noise once it is running.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "screen header settings"
        verbose_name_plural = "screen header settings"

    LABEL_DEFAULTS = {
        "counter_label": "Counter",
        "customer_label": "Customer",
    }

    def __str__(self) -> str:
        return self.navbar_name or "Screen header"

    @classmethod
    def get_solo(cls) -> "ScreenHeaderSettings":
        """The one settings row, created on first use so nothing is nullable."""
        row = cls.objects.first()
        if row:
            return row
        return cls.objects.create()

    def label(self, field: str) -> str:
        """The configured wording for ``field``, or its built-in default."""
        value = (getattr(self, field, "") or "").strip()
        return value or self.LABEL_DEFAULTS.get(field, "")

    def name_for(self, station) -> str:
        """The bar's name, falling back to whichever station is in force.

        The fallback is per-station rather than a constant so that a shop
        which never opens this page keeps exactly the behaviour it had.
        """
        configured = (self.navbar_name or "").strip()
        if configured:
            return configured
        return getattr(station, "store_name", "") or ""
