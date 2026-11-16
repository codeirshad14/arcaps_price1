"""Small helpers shared by more than one admin module."""

from __future__ import annotations

from django.utils.html import format_html
from django.utils.safestring import SafeString
from import_export.widgets import ForeignKeyWidget

from .. import images


def image_tag(image, height: int) -> SafeString:
    """Render an uploaded image at a fixed height, or a dash when absent.

    Guards against a database row pointing at a file that no longer exists on
    disk — common after copying a database between machines.

    Shows a resized copy: the product list draws thirty of these at once, and
    at full size that page alone was tens of megabytes.
    """
    if not image:
        return format_html('<span style="color:#999;">—</span>')
    size = images.THUMBNAIL if height <= 100 else images.PREVIEW
    try:
        url = images.resized_url(image, size)
    except ValueError:  # pragma: no cover - only when storage is misconfigured
        return format_html('<span style="color:#c00;">missing</span>')
    return format_html(
        '<img src="{}" style="height:{}px;width:auto;border-radius:6px;'
        'object-fit:cover;border:1px solid #e5e7eb;" />',
        url,
        height,
    )


class CreatingForeignKeyWidget(ForeignKeyWidget):
    """Look a related row up by name, creating it when the import needs one.

    Bulk product imports routinely mention a category that does not exist yet.
    Failing the whole row for that is unhelpful, so the category is created.
    """

    def clean(self, value, row=None, **kwargs):
        if value in (None, ""):
            return None
        obj, _ = self.model.objects.get_or_create(**{self.field: str(value).strip()})
        return obj
