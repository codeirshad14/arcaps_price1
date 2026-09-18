"""Company admin, plus the per-user company-access inline on the User form."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import Count
from django.utils.html import format_html
from django.utils.safestring import SafeString

from ..models import Company, UserCompanyAccess
from ._shared import image_tag


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    """The companies whose catalogues are kept apart."""

    list_display = (
        "name",
        "short_code",
        "slug",
        "product_count",
        "station_count",
        "is_default",
        "is_active",
    )
    list_filter = ("is_active", "is_default")
    search_fields = ("name", "slug", "short_code")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("default_product_image_preview",)

    fieldsets = (
        (
            None,
            {
                "fields": ("name", "slug", "short_code", "is_default", "is_active"),
                "description": "Product codes are unique <strong>within</strong> a "
                "company, so two companies can both sell barcode 1345 without "
                "clashing. A station set to this company only ever shows these "
                "products.",
            },
        ),
        (
            "Default product picture",
            {
                "fields": ("default_product_image", "default_product_image_preview"),
                "description": "Used for a <strong>newly created</strong> product "
                "that has no picture of its own, however it was added — typed in "
                "here, posted to the API, or pushed across from Tally."
                "<br>It is applied once, at the moment the product is created. "
                "Changing this picture later leaves existing products alone, and "
                "removing a product's picture by hand keeps it removed.",
            },
        ),
    )

    @admin.display(description="Current default picture")
    def default_product_image_preview(self, obj: Company) -> SafeString:
        return image_tag(obj.default_product_image, height=160)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_products=Count("products", distinct=True),
                      _stations=Count("stations", distinct=True))
        )

    @admin.display(description="products", ordering="_products")
    def product_count(self, obj: Company) -> int:
        return obj._products

    @admin.display(description="screens", ordering="_stations")
    def station_count(self, obj: Company) -> int:
        return obj._stations


class UserCompanyAccessInline(admin.StackedInline):
    """Attach a company restriction directly to the user form."""

    model = UserCompanyAccess
    can_delete = False
    extra = 0
    max_num = 1
    verbose_name_plural = "Price checker access"
    autocomplete_fields = ("company", "default_station")
    fields = ("company", "default_station")


class UserAdmin(BaseUserAdmin):
    """Stock user admin plus the company restriction and a column for it."""

    inlines = [UserCompanyAccessInline]
    list_display = BaseUserAdmin.list_display + ("company_scope",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("company_access__company")

    @admin.display(description="Price checker scope")
    def company_scope(self, obj: User) -> SafeString:
        access = getattr(obj, "company_access", None)
        if access is None or access.company is None:
            return format_html('<span style="color:#666;">All companies</span>')
        return format_html("<strong>{}</strong>", access.company.name)


# Replace the stock User admin so the inline is available.
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
