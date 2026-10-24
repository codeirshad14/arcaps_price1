"""Audit trail: one row per lookup, hit or miss, from any screen."""

from __future__ import annotations

from django.db import models

from .catalog import Product
from .company import Company
from .customer_portal import CustomerSession
from .station import DisplayProfile


class ScanLog(models.Model):
    """One row per lookup, hit or miss.

    The misses are the valuable part: they tell you which codes customers are
    scanning that are not in the database yet.
    """

    SOURCE_SCANNER = "scanner"
    SOURCE_VOICE = "voice"
    SOURCE_MANUAL = "manual"
    SOURCE_CHOICES = [
        (SOURCE_SCANNER, "Scanner"),
        (SOURCE_VOICE, "Voice"),
        (SOURCE_MANUAL, "Manual entry"),
    ]

    code = models.CharField(max_length=128, db_index=True)
    found = models.BooleanField(default=False, db_index=True)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_logs",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_logs",
        help_text="Company scope in force for this scan.",
    )
    station = models.ForeignKey(
        DisplayProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_logs",
        help_text="Which station the scan came from.",
    )
    customer_session = models.ForeignKey(
        CustomerSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_logs",
        help_text="Set when this scan came from the customer self-service "
        "portal rather than a staff kiosk.",
    )
    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
        default=SOURCE_SCANNER,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "scan log"
        verbose_name_plural = "scan logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        outcome = "found" if self.found else "not found"
        return f"{self.code} ({outcome})"
