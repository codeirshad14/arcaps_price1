"""
Category and Product admin.

Tuned for the one thing staff actually do here: bulk-check/edit prices and
stock. Thumbnails in the list, spreadsheet import/export, and filters for the
usual questions ("what's out of stock", "what's discounted").
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count, Q, QuerySet
from django.utils.html import format_html
from django.utils.safestring import SafeString
from import_export import fields, resources
from import_export.admin import ImportExportModelAdmin

from ..models import Category, Product
from ._shared import CreatingForeignKeyWidget, image_tag

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class CompanyFilter(admin.SimpleListFilter):
    """Filter by company, listing only companies that actually have products.

    A plain `list_filter = ("company",)` would list every company forever,
    including ones added and then emptied.
    """

    title = "company"
    parameter_name = "company"

    def lookups(self, request, model_admin):
        from ..models import Company

        companies = (
            Company.objects.filter(products__isnull=False)
            .distinct()
            .order_by("-is_default", "name")
        )
        return [(c.pk, str(c)) for c in companies]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        value = self.value()
        return queryset.filter(company_id=value) if value else queryset


class DuplicateBarcodeFilter(admin.SimpleListFilter):
    """Find codes used by more than one company.

    These are exactly the products that appear together on an all-companies
    screen, so it is worth being able to review them.
    """

    title = "shared across companies"
    parameter_name = "shared_code"

    def lookups(self, request, model_admin):
        return [("yes", "Barcode used by 2+ companies")]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        if self.value() != "yes":
            return queryset
        shared = (
            Product.objects.values("barcode")
            .annotate(companies=Count("company", distinct=True))
            .filter(companies__gt=1)
            .values_list("barcode", flat=True)
        )
        return queryset.filter(barcode__in=list(shared))


class StockStatusFilter(admin.SimpleListFilter):
    """Filter by whether anything is left in stock."""

    title = "stock status"
    parameter_name = "stock_status"

    def lookups(self, request, model_admin):
        return [("in", "In stock"), ("out", "Out of stock"), ("low", "Low (1–5)")]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        value = self.value()
        if value == "in":
            return queryset.filter(closing_stock__gt=0)
        if value == "out":
            return queryset.filter(closing_stock__lte=0)
        if value == "low":
            return queryset.filter(closing_stock__gt=0, closing_stock__lte=5)
        return queryset


class HasImageFilter(admin.SimpleListFilter):
    """Find the products still missing a photo — the screen shows a
    placeholder for these, so it is a useful worklist."""

    title = "image"
    parameter_name = "has_image"

    def lookups(self, request, model_admin):
        return [("yes", "Has image"), ("no", "No image")]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        if self.value() == "yes":
            return queryset.exclude(image="").exclude(image__isnull=True)
        if self.value() == "no":
            # `__in=["", None]` does not match NULL in SQL — NULL compares
            # unequal to everything, including itself. Use an explicit OR.
            return queryset.filter(Q(image="") | Q(image__isnull=True))
        return queryset


class DiscountFilter(admin.SimpleListFilter):
    """Filter by whether the product has a discount set."""

    title = "discount"
    parameter_name = "discounted"

    def lookups(self, request, model_admin):
        return [("yes", "Discounted"), ("no", "No discount")]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        value = self.value()
        if value == "yes":
            return queryset.filter(given_disc__gt=0)
        if value == "no":
            return queryset.filter(given_disc=0)
        return queryset


class PriceBandFilter(admin.SimpleListFilter):
    """Coarse retail-price bands — quicker than typing a range every time."""

    title = "price band"
    parameter_name = "price_band"
    BANDS = [
        ("0-299", "Up to ₹299", (None, 299)),
        ("300-699", "₹300 – ₹699", (300, 699)),
        ("700-1499", "₹700 – ₹1499", (700, 1499)),
        ("1500+", "₹1500 and above", (1500, None)),
    ]

    def lookups(self, request, model_admin):
        return [(key, label) for key, label, _ in self.BANDS]

    def queryset(self, request, queryset: QuerySet) -> QuerySet:
        for key, _, (low, high) in self.BANDS:
            if self.value() == key:
                if low is not None:
                    queryset = queryset.filter(retail_price__gte=low)
                if high is not None:
                    queryset = queryset.filter(retail_price__lte=high)
                return queryset
        return queryset


# ---------------------------------------------------------------------------
# Import / export resources
# ---------------------------------------------------------------------------


class ProductResource(resources.ModelResource):
    """Spreadsheet shape for bulk product maintenance.

    ``barcode`` is the identity column, so re-importing an edited export
    updates the same rows instead of creating duplicates.
    """

    category = fields.Field(
        column_name="category",
        attribute="category",
        widget=CreatingForeignKeyWidget(Category, "name"),
    )

    class Meta:
        model = Product
        import_id_fields = ("barcode",)
        skip_unchanged = True
        report_skipped = True
        fields = (
            "barcode",
            "alias",
            "name",
            "description",
            "category",
            "retail_price",
            "given_disc",
            "wholesale_price",
            "gst",
            "hsn",
            "closing_stock",
            "unit",
            "is_active",
            "tally_guid",
        )
        export_order = fields


class CategoryResource(resources.ModelResource):
    class Meta:
        model = Category
        import_id_fields = ("name",)
        fields = ("name", "is_active")


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


@admin.register(Category)
class CategoryAdmin(ImportExportModelAdmin):
    resource_classes = [CategoryResource]
    list_display = ("name", "product_count", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_products=Count("products"))

    @admin.display(description="products", ordering="_products")
    def product_count(self, obj: Category) -> int:
        return obj._products


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------


@admin.register(Product)
class ProductAdmin(ImportExportModelAdmin):
    resource_classes = [ProductResource]

    list_display = (
        "thumbnail",
        "name",
        "company_badge",
        "barcode",
        "retail_price",
        "given_disc",
        "wholesale_price",
        "gst",
        "final_price_column",
        "closing_stock",
        "availability",
        "category",
        "is_active",
    )
    list_editable = ("retail_price", "wholesale_price", "is_active")
    list_display_links = ("thumbnail", "name")
    list_filter = (
        CompanyFilter,
        "category",
        "is_active",
        StockStatusFilter,
        HasImageFilter,
        DiscountFilter,
        PriceBandFilter,
        DuplicateBarcodeFilter,
        "unit",
        "updated_at",
    )
    search_fields = ("barcode", "alias", "name", "description", "company__name", "hsn")
    list_select_related = ("category", "company")
    autocomplete_fields = ("company",)
    list_per_page = 30
    ordering = ("name",)
    date_hierarchy = "created_at"
    actions = ["activate", "deactivate"]

    readonly_fields = (
        "image_preview",
        "final_price_display",
        "tally_guid",
        "last_synced_at",
        "created_at",
        "updated_at",
    )

    fieldsets = (
        (
            "Identification",
            {
                "fields": ("company", "barcode", "alias", "name", "category", "is_active"),
                "description": "The barcode is what the scanner types. "
                "Use <em>alias</em> only when the QR value differs from the barcode."
                "<br><strong>Codes are unique within a company</strong>, so another "
                "company may use the same barcode for a different product.",
            },
        ),
        (
            "Pricing",
            {
                "fields": (
                    "retail_price", "given_disc", "wholesale_price", "gst", "hsn",
                    "final_price_display",
                ),
                "description": "<strong>Discount</strong> is a percentage off the "
                "retail price. <strong>GST</strong> is added on top of both prices "
                "to get what's actually shown and charged — enter prices "
                "<em>before</em> tax.",
            },
        ),
        ("Stock", {"fields": ("closing_stock", "unit")}),
        ("Media & description", {"fields": ("image", "image_preview", "description")}),
        (
            "Tally sync",
            {
                "classes": ("collapse",),
                "fields": ("tally_guid", "last_synced_at"),
                "description": "Filled in automatically by the sync API. Read-only.",
            },
        ),
        ("Timestamps", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Image")
    def thumbnail(self, obj: Product) -> SafeString:
        return image_tag(obj.image, height=44)

    @admin.display(description="Image preview")
    def image_preview(self, obj: Product) -> SafeString:
        return image_tag(obj.image, height=260)

    @admin.display(description="Company", ordering="company__name")
    def company_badge(self, obj: Product) -> SafeString:
        """Coloured chip, so a mixed list is scannable by eye."""
        if not obj.company:
            return format_html('<span style="color:#999;">—</span>')
        # Stable colour per company from its id, so the same company always
        # looks the same without storing a colour.
        palette = ["#1d4ed8", "#7c3aed", "#0f766e", "#b45309", "#be123c", "#4338ca"]
        colour = palette[obj.company_id % len(palette)]
        return format_html(
            '<span style="background:{}18;color:{};border:1px solid {}55;'
            'padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;'
            'white-space:nowrap;">{}</span>',
            colour, colour, colour, obj.company.label,
        )

    @admin.display(description="Final (incl. GST)")
    def final_price_column(self, obj: Product) -> SafeString:
        return format_html(
            '<span style="font-size:11px;">R ₹{} · W ₹{}</span>',
            obj.retail_final_price, obj.wholesale_final_price,
        )

    @admin.display(description="Final prices (incl. GST)")
    def final_price_display(self, obj: Product) -> str:
        if not obj.pk:
            return "—"
        return (
            f"Retail: ₹{obj.retail_final_price}  "
            f"(list ₹{obj.retail_price}, {obj.given_disc}% off)   ·   "
            f"Wholesale: ₹{obj.wholesale_final_price}"
        )

    @admin.display(description="Availability", ordering="closing_stock")
    def availability(self, obj: Product) -> SafeString:
        colour = "#15803d" if obj.closing_stock > 0 else "#b91c1c"
        background = "#dcfce7" if obj.closing_stock > 0 else "#fee2e2"
        return format_html(
            '<span style="color:{};background:{};padding:2px 8px;'
            'border-radius:999px;font-size:12px;font-weight:600;">{}</span>',
            colour,
            background,
            obj.availability_label,
        )

    @admin.action(description="Activate selected products")
    def activate(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} product(s) activated.")

    @admin.action(description="Deactivate selected products")
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} product(s) deactivated.")
