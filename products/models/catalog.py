"""
The sellable catalogue: categories and products.

Pricing, in one line: ``retail_price`` and ``wholesale_price`` are what gets
entered directly. ``given_disc`` (a percentage) is subtracted from
``retail_price`` to get the retail selling price, and ``gst`` (a percentage)
is then added on top of *both* tiers to get what's actually shown/charged.
See ``Product.retail_final_price`` / ``Product.wholesale_final_price``.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .company import Company


def _blank_to_none(value: str | None) -> str | None:
    """Normalise an optional unique field to ``None`` when it is empty.

    Django stores an untouched optional CharField as ``""``. A ``unique=True``
    column treats every ``""`` as the same value, so the second product left
    without an ``alias`` would raise an IntegrityError. ``NULL`` is exempt
    from uniqueness in both SQLite and PostgreSQL, which is exactly the
    "unique if set" behaviour we want.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


class Category(models.Model):
    """Optional grouping for products (Caps, Bags, Accessories...)."""

    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    """A scannable product.

    ``barcode`` holds whatever the scanner types — a real barcode or a QR
    payload, they arrive through the same keyboard-wedge path. ``alias``
    covers the case where an item carries two different codes — the same
    term Tally itself uses on a stock item.
    """

    # --- Ownership ----------------------------------------------------------
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="Which company's catalogue this product belongs to.",
    )

    # --- Identity -----------------------------------------------------------
    #
    # These are unique per company, NOT globally: two companies legitimately
    # sell different things under the same barcode. The constraints live in
    # Meta so the pair (company, code) is what the database enforces.
    barcode = models.CharField(
        max_length=64,
        db_index=True,
        help_text="Primary scannable code. Works for both barcodes and QR codes. "
        "Must be unique within its company.",
    )
    alias = models.CharField(
        max_length=64,
        db_index=True,
        null=True,
        blank=True,
        help_text="Optional second code, e.g. a QR value that differs from the barcode.",
    )

    # --- Descriptive --------------------------------------------------------
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )

    # --- Pricing --------------------------------------------------------------
    retail_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="List price, before discount and GST.",
    )
    given_disc = models.DecimalField(
        "discount %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percentage off the retail price. 0 means no discount.",
    )
    wholesale_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Bulk-buyer rate, before GST.",
    )
    gst = models.DecimalField(
        "GST %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Tax percentage. Added on top of both prices below.",
    )
    hsn = models.CharField(
        "HSN/SAC code",
        max_length=8,
        blank=True,
        help_text="For GST filing. Not shown on the customer screen.",
    )

    # --- Stock (mirrored from Tally) ---------------------------------------
    closing_stock = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
        help_text="Closing balance from Tally.",
    )
    unit = models.CharField(
        max_length=20,
        default="PCS",
        help_text="Tally unit of measure.",
    )

    # --- Media --------------------------------------------------------------
    image = models.ImageField(
        upload_to="products/",
        null=True,
        blank=True,
        help_text="Optional. The screen shows a placeholder when empty.",
    )

    # --- Flags and sync bookkeeping ----------------------------------------
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive products are never returned by a scan.",
    )
    tally_guid = models.CharField(
        max_length=100,
        db_index=True,
        null=True,
        blank=True,
        help_text="Tally's own identifier, kept for reference only. Tally "
        "reuses these across stock items, so it is NOT unique and is not "
        "used to match records — the part number (barcode) is.",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company__name", "name"]
        indexes = [
            # The lookup endpoint filters on company + is_active alongside the
            # code, so a composite index keeps the hot path to one index scan.
            models.Index(fields=["company", "is_active", "barcode"]),
            models.Index(fields=["is_active", "barcode"]),
        ]
        constraints = [
            # Per-company uniqueness. Two companies may both sell "1345";
            # one company may not list it twice.
            models.UniqueConstraint(
                fields=["company", "barcode"], name="unique_barcode_per_company"
            ),
            # NULLs are exempt in SQLite and PostgreSQL, which is what makes
            # this "unique if set" rather than "required".
            models.UniqueConstraint(
                fields=["company", "alias"], name="unique_alias_per_company"
            ),
            # `tally_guid` deliberately has NO uniqueness constraint. Tally
            # reuses a GUID across stock items, so requiring it to be unique
            # rejected legitimate rows — a whole sync could fail on a
            # duplicate that Tally considers perfectly normal. The part
            # number, held in `barcode`, is the identity that matters.
        ]

    def __str__(self) -> str:
        return f"{self.barcode} — {self.name}"

    def save(self, *args, **kwargs):
        # Guard the unique-if-set columns before they reach the database.
        self.alias = _blank_to_none(self.alias)
        self.tally_guid = _blank_to_none(self.tally_guid)
        self.barcode = (self.barcode or "").strip()

        # A product that arrives without a picture borrows its company's
        # default, so a freshly synced catalogue is not a wall of placeholders.
        #
        # Creation only, and deliberately so: re-applying it on every save
        # would make removing a product's picture impossible, since the next
        # save would silently put the default back.
        if self._state.adding and not self.image:
            self.image = self._company_default_image()

        super().save(*args, **kwargs)

    def _company_default_image(self) -> str:
        """Path of the owning company's fallback picture, or ``""``.

        The path is copied rather than the file: every product without its own
        picture points at the one uploaded image instead of each getting a
        duplicate of it on disk.
        """
        if not self.company_id:
            return ""
        default = self.company.default_product_image
        return default.name if default else ""

    # --- Pricing --------------------------------------------------------------

    @property
    def retail_selling_price(self) -> Decimal:
        """Retail price after ``given_disc``, before GST."""
        if not self.given_disc:
            return self.retail_price
        discount = self.retail_price * self.given_disc / Decimal("100")
        return (self.retail_price - discount).quantize(Decimal("0.01"))

    def _with_gst(self, amount: Decimal) -> Decimal:
        if not self.gst:
            return amount
        return (amount * (Decimal("100") + self.gst) / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def retail_price_with_gst(self) -> Decimal:
        """Retail list price with GST, before any discount.

        Used as the struck-through "was" price when a discount is set — the
        equivalent of the old MRP.
        """
        return self._with_gst(self.retail_price)

    @property
    def retail_final_price(self) -> Decimal:
        """What a retail customer actually pays: discount applied, GST added on top."""
        return self._with_gst(self.retail_selling_price)

    @property
    def wholesale_final_price(self) -> Decimal:
        """What a wholesale buyer actually pays: GST added on top of the wholesale rate."""
        return self._with_gst(self.wholesale_price)

    # --- Derived values -----------------------------------------------------

    @property
    def availability_label(self) -> str:
        """Human-readable stock state for the screen."""
        return "In Stock" if self.closing_stock > 0 else "Out of Stock"

    @property
    def stock_quantity_label(self) -> str:
        """Stock as a tidy quantity + unit, e.g. ``"42 PCS"``."""
        quantity = self.closing_stock
        if quantity == quantity.to_integral_value():
            text = str(int(quantity))
        else:
            # format(..., "f") avoids the scientific notation normalize() can
            # produce for values like 100.000.
            text = format(quantity.normalize(), "f")
        return f"{text} {self.unit}".strip()
