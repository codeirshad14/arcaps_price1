"""ScanLog admin — read-only audit trail of every lookup, hit or miss."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from import_export import fields, resources
from import_export.admin import ImportExportModelAdmin
from import_export.widgets import ForeignKeyWidget

from ..models import DisplayProfile, Product, ScanLog


class ScanLogResource(resources.ModelResource):
    """Export only — useful for spotting codes customers scan that you do not
    stock yet."""

    product = fields.Field(
        column_name="product",
        attribute="product",
        widget=ForeignKeyWidget(Product, "barcode"),
    )
    station = fields.Field(
        column_name="station",
        attribute="station",
        widget=ForeignKeyWidget(DisplayProfile, "slug"),
    )

    class Meta:
        model = ScanLog
        fields = ("created_at", "code", "found", "product", "station", "source")
        export_order = fields


@admin.register(ScanLog)
class ScanLogAdmin(ImportExportModelAdmin):
    resource_classes = [ScanLogResource]
    list_display = (
        "created_at", "code", "found", "product", "company", "station",
        "customer_session", "source",
    )
    list_filter = ("found", "source", "company", "station", "created_at")
    search_fields = (
        "code", "product__name", "product__barcode",
        "customer_session__mobile_number", "customer_session__name",
    )
    date_hierarchy = "created_at"
    list_select_related = ("product", "station", "company", "customer_session")
    list_per_page = 50

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj=None):
        return [field.name for field in self.model._meta.fields]
