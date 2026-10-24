"""Admin for the customer self-service QR portal: settings and visit log."""

from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString

from ..models import (
    CustomerPortalSettings,
    CustomerSession,
    ScanLog,
    ScreenHeaderSettings,
)


class CustomerScanLogInline(admin.TabularInline):
    """Every lookup this customer made during their visit.

    This is the literal answer to "which product/price is this mobile number
    checking, how many times": open the session, see the list.
    """

    model = ScanLog
    fk_name = "customer_session"
    extra = 0
    fields = ("created_at", "code", "found", "product", "source")
    readonly_fields = ("created_at", "code", "found", "product", "source")
    can_delete = False
    verbose_name = "lookup"
    verbose_name_plural = "lookups made during this session"

    def has_add_permission(self, request: HttpRequest, obj=None) -> bool:
        return False


@admin.register(CustomerSession)
class CustomerSessionAdmin(admin.ModelAdmin):
    """Read-only: every walk-in customer visit, searchable by mobile number."""

    list_display = (
        "name", "mobile_number", "company", "started_at", "expires_at",
        "status", "lookup_count",
    )
    list_filter = ("company", "started_at")
    search_fields = ("name", "mobile_number")
    date_hierarchy = "started_at"
    list_select_related = ("company",)
    list_per_page = 50
    inlines = [CustomerScanLogInline]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj=None):
        return [field.name for field in self.model._meta.fields]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_lookups=Count("scan_logs"))

    @admin.display(description="Lookups", ordering="_lookups")
    def lookup_count(self, obj: CustomerSession) -> int:
        return obj._lookups

    @admin.display(description="Status")
    def status(self, obj: CustomerSession) -> SafeString:
        if obj.is_expired:
            return format_html('<span style="color:#999;">ended</span>')
        return format_html('<span style="color:#15803d;font-weight:600;">active</span>')


@admin.register(CustomerPortalSettings)
class CustomerPortalSettingsAdmin(admin.ModelAdmin):
    """Singleton-style: at most one row, editing the shop-wide QR portal."""

    list_display = ("title", "session_minutes", "branding_station", "is_enabled")
    readonly_fields = (
        "updated_at",
        "qr_preview",
        "poster_link",
        "entry_url",
        "rotate_button",
        "access_token_rotated_at",
    )
    autocomplete_fields = ("branding_station",)

    fieldsets = (
        (
            "Customer landing page",
            {
                "fields": ("title", "subtitle", "is_enabled"),
                "description": "Shown on the page a customer sees right after "
                "scanning the shop's QR code.",
            },
        ),
        (
            "Wording",
            {
                "fields": (
                    "welcome_heading",
                    "prompt_title",
                    "prompt_subtitle",
                    "submit_label",
                    "finish_label",
                    "idle_heading",
                    "expired_heading",
                    "expired_message",
                    "poster_instructions",
                ),
                "description": "Every label a customer sees on the portal. "
                "Leave any of them blank to keep the standard wording. The "
                "top bar is set separately, under Screen header settings, "
                "because it is shared with the counter screen."
                "<br><strong>Expired heading</strong> and <strong>expired "
                "message</strong> are the closing page, shown when a visit "
                "times out or the customer taps the finish button. That page "
                "has no way back by design — scanning the poster again is the "
                "only way in.",
            },
        ),
        (
            "Session",
            {
                "fields": ("session_minutes",),
                "description": "How long a customer stays signed in before "
                "being automatically logged out.",
            },
        ),
        (
            "Entry address &amp; QR code",
            {
                "fields": (
                    "entry_url",
                    "qr_preview",
                    "access_token_rotated_at",
                    "rotate_button",
                ),
                "description": "The customer entry URL carries a random code "
                "so it cannot be reached by guessing. If you think the printed "
                "address has been copied — someone checking your prices every "
                "day without coming in — change it here."
                "<br><strong>Change it as often as you like.</strong> Nothing "
                "else on this page is affected: the wording, branding and "
                "session length all stay as they are, and only the address "
                "changes.",
            },
        ),
        (
            "Branding",
            {
                "fields": ("branding_station", "poster_link"),
                "description": "Whose logo and theme colours the customer "
                "page borrows. Leave blank to use the default station.",
            },
        ),
        ("Timestamps", {"classes": ("collapse",), "fields": ("updated_at",)}),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        # Only one row makes sense — the QR is shop-wide, not per-row.
        return not CustomerPortalSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    @admin.display(description="Customer entry URL")
    def entry_url(self, obj: CustomerPortalSettings) -> SafeString:
        if not obj.pk:
            return format_html('<span style="color:#999;">Save first.</span>')
        return format_html(
            '<code style="font-size:13px;">{}</code>'
            '<p class="help" style="margin-top:6px;">This is what the QR code '
            'points at. Use the button below to change it.</p>',
            obj.entry_path,
        )

    @admin.display(description="Change the QR code")
    def rotate_button(self, obj: CustomerPortalSettings) -> SafeString:
        """The rotate control, on the page where the address is shown.

        This used to be a bulk action, which meant leaving this page, ticking
        a checkbox in the list and finding the right entry in a dropdown — for
        something meant to be done on the spot, the moment you suspect the
        address has leaked.
        """
        if not obj.pk:
            return format_html('<span style="color:#999;">Save first.</span>')

        url = reverse(
            "admin:products_customerportalsettings_rotate_token", args=[obj.pk]
        )
        return format_html(
            '<a href="{}" class="button" style="background:#b91c1c;color:#fff;'
            'padding:8px 16px;border-radius:6px;font-weight:600;'
            'display:inline-block;">Change the QR code now</a>'
            '<p class="help" style="margin-top:8px;">You will be asked to '
            'confirm first. The old address stops working immediately, anyone '
            'using it right now is signed out, and the poster must be '
            'reprinted.</p>',
            url,
        )

    # --- Rotating the entry address ----------------------------------------

    def get_urls(self):
        """Add the rotate view, ahead of the admin's own catch-all patterns."""
        own = [
            path(
                "<path:object_id>/rotate-token/",
                self.admin_site.admin_view(self.rotate_token_view),
                name="products_customerportalsettings_rotate_token",
            ),
        ]
        return own + super().get_urls()

    def rotate_token_view(self, request: HttpRequest, object_id: str) -> HttpResponse:
        """Confirm, then issue a new entry address.

        GET renders the confirmation; only POST changes anything, so the
        address can never be rotated by something merely following a link —
        a prefetching browser, or a bookmark opened by mistake.
        """
        settings_row = self.get_object(request, unquote(object_id))
        if settings_row is None:
            raise Http404("No customer portal settings to change.")
        if not self.has_change_permission(request, settings_row):
            raise PermissionDenied

        if request.method == "POST":
            ended = settings_row.rotate_access_token()
            self.message_user(
                request,
                f"New customer QR code issued. The previous address no longer "
                f"works and {ended} visit(s) in progress were signed out. "
                f"Reprint the poster from /customer/poster/.",
                messages.SUCCESS,
            )
            return redirect(
                reverse(
                    "admin:products_customerportalsettings_change",
                    args=[settings_row.pk],
                )
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Change the customer QR code",
            "settings_row": settings_row,
            "active_visits": CustomerSession.objects.filter(
                ended_at__isnull=True, expires_at__gt=timezone.now()
            ).count(),
            "opts": self.model._meta,
        }
        return render(
            request,
            "admin/products/customerportalsettings/rotate_token.html",
            context,
        )

    @admin.display(description="QR code")
    def qr_preview(self, obj: CustomerPortalSettings) -> SafeString:
        if not obj.pk:
            return format_html('<span style="color:#999;">Save first to preview.</span>')
        return format_html(
            '<img src="/customer/qr.png" style="height:160px;width:160px;'
            'border:1px solid #e5e7eb;border-radius:6px;" />'
        )

    @admin.display(description="Printable poster")
    def poster_link(self, obj: CustomerPortalSettings) -> SafeString:
        if not obj.pk:
            return "—"
        return format_html(
            '<a href="/customer/poster/" target="_blank">Open /customer/poster/</a>'
        )


@admin.register(ScreenHeaderSettings)
class ScreenHeaderSettingsAdmin(admin.ModelAdmin):
    """The top bar for every screen, in one place.

    Before this existed the shop name was configured twice — once per station
    for the counter screen, once on the portal for the customer page — and the
    two could disagree.
    """

    list_display = ("__str__", "counter_label", "customer_label", "updated_at")
    readonly_fields = ("updated_at",)

    fieldsets = (
        (
            "Shared",
            {
                "fields": ("navbar_name",),
                "description": "Shown in the top bar of the counter screen, "
                "the sign-in page and the customer page. Leave blank to keep "
                "using each station's own store name.",
            },
        ),
        (
            "Which screen is which",
            {
                "fields": ("counter_label", "customer_label", "show_station_on_counter"),
                "description": "The small word under the name, so it is "
                "obvious at a glance whether you are looking at the staff "
                "counter or a customer's phone.",
            },
        ),
        ("Timestamps", {"classes": ("collapse",), "fields": ("updated_at",)}),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        # One shop, one header.
        return not ScreenHeaderSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False
