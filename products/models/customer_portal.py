"""The customer self-service QR portal: shop-wide settings and visits."""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone

from .company import Company
from .station import DisplayProfile


class CustomerPortalSettings(models.Model):
    """Shop-wide configuration for the customer self-service QR portal.

    A singleton, in the same spirit as ``Company.get_default()``: there is
    only ever one shop-wide QR, so there is only ever one settings row.
    """

    title = models.CharField(
        max_length=120,
        default="Scan to Check Price",
        help_text="Heading shown on the customer landing page and the "
        "printable poster.",
    )
    subtitle = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional instruction line, e.g. 'Enter your details to "
        "see today's prices'.",
    )
    session_minutes = models.PositiveSmallIntegerField(
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(180)],
        help_text="Minutes a customer stays logged in before being "
        "automatically signed out.",
    )
    branding_station = models.ForeignKey(
        DisplayProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Whose logo and theme colours the customer page borrows. "
        "Leave blank to use the default station.",
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Switch the whole customer portal off without deleting "
        "this configuration.",
    )

    # --- Wording shown to the customer ------------------------------------
    # Every visible label on the portal lives here rather than in the
    # templates, so the wording can be changed from the admin without a code
    # change. Blank means "use the sensible default" — see label().
    welcome_heading = models.CharField(
        max_length=120,
        blank=True,
        help_text="Heading above the code box on the price-check page. "
        "Leave blank to use the station's welcome message.",
    )
    prompt_title = models.CharField(
        max_length=120,
        blank=True,
        help_text="Big prompt inside the code window. Leave blank for "
        "'Enter the product code'.",
    )
    prompt_subtitle = models.CharField(
        max_length=160,
        blank=True,
        help_text="Small line under the prompt. Leave blank for "
        "'It is printed on the tag'.",
    )
    submit_label = models.CharField(
        max_length=60,
        blank=True,
        help_text="Text on the button that looks up the price. Leave blank "
        "for 'Show Price'.",
    )
    finish_label = models.CharField(
        max_length=60,
        blank=True,
        help_text="Text on the button that ends the visit. Leave blank for "
        "'Done'.",
    )
    idle_heading = models.CharField(
        max_length=120,
        blank=True,
        help_text="Heading in the empty panel before a price is looked up. "
        "Leave blank for 'Ready when you are'.",
    )
    expired_heading = models.CharField(
        max_length=120,
        blank=True,
        help_text="Heading on the page a customer sees when their visit ends, "
        "either by timing out or by tapping the finish button. Leave blank "
        "for 'Scan to log in again'.",
    )
    expired_message = models.TextField(
        blank=True,
        help_text="Line under that heading. Leave blank for the standard "
        "wording, which tells the customer to scan the counter QR code again.",
    )
    poster_instructions = models.TextField(
        blank=True,
        help_text="Paragraph printed under the QR code on the poster. "
        "Leave blank for the standard wording.",
    )

    # --- Entry address -----------------------------------------------------
    access_token = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        help_text="Random part of the customer entry URL.",
    )
    access_token_rotated_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="When the entry URL was last changed. There is no schedule "
        "for this — change it whenever you have reason to.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    # The wording defaults, kept next to the fields they back so the two
    # cannot drift. Referenced by label() below.
    LABEL_DEFAULTS = {
        "prompt_title": "Enter the product code or name",
        "prompt_subtitle": "It is printed on the tag",
        "submit_label": "Show Price",
        "finish_label": "Done",
        "idle_heading": "Ready when you are",
        "expired_heading": "Scan to log in again",
        "expired_message": (
            "Your session has ended. Scan the QR code at the counter to "
            "check more prices."
        ),
        "poster_instructions": (
            "Scan this code with your phone's camera, enter your name and "
            "mobile number, then check today's prices yourself — no app needed."
        ),
    }

    class Meta:
        verbose_name = "customer portal settings"
        verbose_name_plural = "customer portal settings"

    def __str__(self) -> str:
        return self.title

    @staticmethod
    def new_access_token() -> str:
        """A fresh random path segment for the customer entry URL."""
        return uuid.uuid4().hex

    def save(self, *args, **kwargs):
        # Generated here rather than as a field default so that every row —
        # including ones created by a data migration — is guaranteed to have
        # one, and so rotating it is just clearing the field.
        if not self.access_token:
            self.access_token = self.new_access_token()
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "CustomerPortalSettings":
        """The one settings row, created on first use so nothing is nullable."""
        settings = cls.objects.first()
        if settings:
            if not settings.access_token:
                settings.access_token = cls.new_access_token()
                settings.save(update_fields=["access_token"])
            return settings
        return cls.objects.create()

    def rotate_access_token(self) -> int:
        """Issue a new entry URL and end every visit currently in progress.

        Returns how many visits were ended.

        Ending them is the point, not a side effect. This is what you reach
        for when the printed address may have been copied, so leaving live
        sessions alone would defeat it — whoever copied the link would simply
        carry on with the visit they already had until it timed out.

        Everything else on this row is untouched: the wording, the branding
        and the session length survive, because only the address is suspect.
        """
        self.access_token = self.new_access_token()
        self.access_token_rotated_at = timezone.now()
        self.save(update_fields=["access_token", "access_token_rotated_at"])
        return CustomerSession.end_active()

    def resolve_branding_station(self) -> DisplayProfile:
        return self.branding_station or DisplayProfile.resolve()

    def label(self, field: str) -> str:
        """The configured wording for ``field``, or its built-in default.

        Templates go through this rather than using ``|default:"..."`` so the
        fallback text lives in one place instead of being repeated — and
        differently — in each template that happens to show it.
        """
        value = (getattr(self, field, "") or "").strip()
        return value or self.LABEL_DEFAULTS.get(field, "")

    @property
    def entry_path(self) -> str:
        """Path a customer's QR code points at."""
        from django.urls import reverse

        return reverse("customer:landing", args=[self.access_token])


mobile_number_validator = RegexValidator(
    r"^\+?\d{7,15}$",
    "Enter a valid mobile number (7–15 digits, optional leading +).",
)


class CustomerSession(models.Model):
    """One customer's visit to the self-service QR portal.

    Created when someone submits the name/mobile form after scanning the
    shop's QR code. ``expires_at`` is fixed at creation time from
    ``CustomerPortalSettings.session_minutes`` — the timer does not extend
    with activity, matching how the walk-in scenario was described.
    """

    # The Django session key the customer portal stores this row's id under.
    # Shared with api.views so a /api/lookup/ call can tell a self-service
    # scan apart from a staff kiosk one without the two apps importing each
    # other's view code.
    SESSION_KEY = "customer_session_id"

    name = models.CharField(max_length=120)
    mobile_number = models.CharField(
        max_length=20, db_index=True, validators=[mobile_number_validator]
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_sessions",
        help_text="Catalogue scope in force for this visit.",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(
        null=True, blank=True, help_text="Set when the customer logs out early."
    )

    class Meta:
        verbose_name = "customer session"
        verbose_name_plural = "customer sessions"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["mobile_number", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.mobile_number})"

    @classmethod
    def end_active(cls) -> int:
        """Close every visit still running. Returns how many were ended.

        Stamping ``ended_at`` is what makes ``is_expired`` true, so the next
        request from any of these browsers is turned away by the same code
        path that handles an ordinary timeout — there is no second notion of
        "signed out" to keep in step.
        """
        now = timezone.now()
        return cls.objects.filter(ended_at__isnull=True, expires_at__gt=now).update(
            ended_at=now
        )

    @property
    def is_expired(self) -> bool:
        if self.ended_at is not None:
            return True
        return timezone.now() >= self.expires_at

    @property
    def seconds_remaining(self) -> int:
        remaining = (self.expires_at - timezone.now()).total_seconds()
        return max(0, int(remaining))
