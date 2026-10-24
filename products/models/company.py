"""Companies (separate catalogues) and per-login company access."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from .station import DisplayProfile


class Company(models.Model):
    """A trading entity whose catalogue is kept separate from the others.

    This exists because two companies routinely reuse the same barcode for
    different goods. Product codes are therefore unique *within* a company,
    never globally, and every lookup is scoped to a company so "1 2 3" cannot
    resolve to company B's item on company A's screen.
    """

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    short_code = models.CharField(
        max_length=8,
        blank=True,
        help_text="Optional short label shown on screen when several companies "
        "match one code, e.g. AR, MPA.",
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text="Used for products and stations that do not name a company.",
    )
    default_product_image = models.ImageField(
        upload_to="branding/",
        null=True,
        blank=True,
        help_text="Picture given to a <strong>newly created</strong> product of "
        "this company that arrives without one — whether it is added here or "
        "pushed in from Tally. Changing it later does not touch products that "
        "already exist, and a product whose picture is removed by hand stays "
        "without one. Leave empty to keep showing the 'No Image Available' "
        "placeholder instead.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        return f"{self.name} (default)" if self.is_default else self.name

    @property
    def label(self) -> str:
        """Short label if one is set, otherwise the full name."""
        return self.short_code or self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Exactly one default; demote the others rather than rejecting a save.
        if self.is_default:
            Company.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )

    @classmethod
    def get_default(cls) -> "Company":
        """The fallback company, created on first use so nothing is nullable."""
        company = cls.objects.filter(is_default=True).first()
        if company:
            return company
        company = cls.objects.order_by("pk").first()
        if company:
            return company
        return cls.objects.create(name="Default Company", slug="default", is_default=True)


class UserCompanyAccess(models.Model):
    """Restricts one login to a single company's catalogue.

    A showroom can be several screens and several logins. Locking a user to a
    company means whoever signs in at that till sees only their own products,
    whatever station the machine happens to be pointed at — which is the
    safer default when one PC is shared.
    """

    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="company_access",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="users",
        help_text="Leave blank to let this login see every company.",
    )
    default_station = models.ForeignKey(
        # String reference: DisplayProfile lives in station.py, which imports
        # Company from this file. A direct import here would be circular.
        "products.DisplayProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        help_text="Optional. Opens this station when the user signs in.",
    )

    class Meta:
        verbose_name = "user company access"
        verbose_name_plural = "user company access"

    def __str__(self) -> str:
        scope = self.company.name if self.company else "all companies"
        return f"{self.user.username} → {scope}"


def resolve_company(user=None, profile: "DisplayProfile | None" = None) -> Company | None:
    """Work out whose catalogue to show.

    Precedence, narrowest first:

    1. The signed-in user's company, if one is set. A login restricted to
       company A must not see company B by opening a different station.
    2. Otherwise the station's company.
    3. Otherwise ``None``, meaning every company.

    Returning ``None`` rather than a company is deliberate — it lets the
    caller skip the filter entirely instead of guessing a default.
    """
    if user is not None and getattr(user, "is_authenticated", False):
        access = getattr(user, "company_access", None)
        if access is not None and access.company_id:
            return access.company

    if profile is not None and profile.company_id:
        return profile.company

    return None
