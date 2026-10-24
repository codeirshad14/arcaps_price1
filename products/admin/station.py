"""DisplayProfile (showroom station) admin — one row per showroom system."""

from __future__ import annotations

from django import forms
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import SafeString

from ..models import DisplayProfile
from ._shared import image_tag


@admin.register(DisplayProfile)
class DisplayProfileAdmin(admin.ModelAdmin):
    """One row per showroom system."""

    list_display = (
        "name",
        "slug",
        "company_scope",
        "theme_swatch",
        "display_duration_seconds",
        "enable_voice_search",
        "is_default",
        "is_active",
    )
    list_filter = ("company", "is_active", "is_default", "theme_preset")
    search_fields = ("name", "slug", "store_name")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("company",)
    list_select_related = ("company",)
    readonly_fields = ("updated_at", "logo_preview", "background_preview", "open_link")
    save_on_top = True

    fieldsets = (
        (
            "Station",
            {
                "fields": (
                    "name", "slug", "open_link", "is_default", "is_active", "company",
                ),
                "description": "Each system opens <code>/?station=&lt;slug&gt;</code> once "
                "and remembers it."
                "<br><strong>Company</strong> decides whose products this screen shows — "
                "leave blank for <em>all companies</em>, where a code found in more than "
                "one company shows each match side by side."
                "<br>Every screen always shows both the retail and wholesale price for "
                "whatever it scans.",
            },
        ),
        (
            "General",
            {
                "fields": (
                    "store_name",
                    "welcome_message",
                    "currency_symbol",
                    "logo",
                    "logo_preview",
                ),
            },
        ),
        (
            "Timing",
            {
                "fields": ("display_duration_seconds",),
                "description": "Seconds a product stays on screen before returning "
                "to the idle 'please scan' state.",
            },
        ),
        (
            "Screen Display Fields",
            {
                "fields": (
                    "show_name",
                    "show_image",
                    "show_price",
                    "show_mrp",
                    "show_discount",
                    "show_description",
                    "show_availability",
                    "availability_mode",
                ),
                "description": "Tick the details that appear on the customer-facing "
                "screen. <strong>Changes apply from the next scan onward</strong> — "
                "no restart or browser refresh needed.",
            },
        ),
        (
            "Voice & wake word",
            {
                "fields": (
                    "enable_voice_search",
                    "voice_input_language",
                    "enable_wake_word",
                    "wake_phrases",
                ),
                "description": "The wake word keeps the microphone listening "
                "continuously. It needs Chrome or Edge, microphone permission, and "
                "an internet connection — audio is processed by the browser's "
                "speech service. There is always a Stop button on screen.",
            },
        ),
        (
            "Spoken price (speaker)",
            {
                "fields": (
                    "enable_price_speech",
                    "speech_language",
                    "speech_voice_preference",
                    "speech_template",
                    "speech_rate",
                    "speech_pitch",
                ),
                "description": "Reads the price aloud when a product appears. Uses "
                "the voices installed on the showroom PC, so it works offline. "
                "A matching voice must be installed for the language you choose "
                "(Windows: Settings → Time &amp; Language → Speech). "
                "<br>For a soft female voice, install <em>Microsoft Heera</em> "
                "(English India) or <em>Microsoft Kalpana</em> (Hindi) and leave "
                "the preference on <strong>Female</strong>.",
            },
        ),
        (
            "Multiple products",
            {
                "fields": ("enable_multi_scan", "max_multi_items"),
                "description": "Lets a customer compare several products by "
                "scanning them one after another.",
            },
        ),
        (
            "Background",
            {
                "fields": (
                    "background_image",
                    "background_preview",
                    "background_fit",
                    "background_overlay",
                ),
                "description": "Leave the image empty to use the theme gradient. "
                "If the image looks cropped, switch <strong>fit</strong> to "
                "<em>Contain</em>.",
            },
        ),
        (
            "Theme & colours",
            {
                "fields": (
                    "theme_preset",
                    "color_bg_start",
                    "color_bg_end",
                    "color_accent",
                    "color_price",
                    "color_text",
                    "color_text_dim",
                    "color_in_stock",
                    "color_out_of_stock",
                    "card_opacity",
                ),
                "description": "Pick a preset for a one-click look. Choosing "
                "<em>Custom</em> keeps whatever colours you set here.<br>"
                "<strong>Note:</strong> selecting any non-custom preset overwrites "
                "the colours below when you save.",
            },
        ),
        ("Timestamps", {"classes": ("collapse",), "fields": ("updated_at",)}),
    )

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Render the colour fields as native colour pickers."""
        if db_field.name.startswith("color_"):
            kwargs["widget"] = forms.TextInput(
                attrs={"type": "color", "style": "height:38px;width:90px;padding:2px;"}
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    @admin.display(description="Shows", ordering="company__name")
    def company_scope(self, obj: DisplayProfile) -> SafeString:
        if obj.company is None:
            return format_html(
                '<span style="color:#0f766e;font-weight:600;">All companies</span>'
            )
        return format_html("<strong>{}</strong>", obj.company.name)

    @admin.display(description="Theme")
    def theme_swatch(self, obj: DisplayProfile) -> SafeString:
        """Show the actual palette rather than just its name."""
        return format_html(
            '<span title="{}" style="display:inline-block;width:64px;height:18px;'
            "border-radius:4px;border:1px solid #ccc;"
            'background:linear-gradient(90deg,{},{});"></span>'
            '<span style="display:inline-block;width:18px;height:18px;margin-left:4px;'
            'border-radius:50%;border:1px solid #ccc;background:{};vertical-align:top;"></span>',
            obj.get_theme_preset_display(),
            obj.color_bg_start,
            obj.color_bg_end,
            obj.color_accent,
        )

    @admin.display(description="Kiosk URL")
    def open_link(self, obj: DisplayProfile) -> SafeString:
        if not obj.pk:
            return "—"
        url = f"/?station={obj.slug}"
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    @admin.display(description="Current logo")
    def logo_preview(self, obj: DisplayProfile) -> SafeString:
        return image_tag(obj.logo, height=90)

    @admin.display(description="Current background")
    def background_preview(self, obj: DisplayProfile) -> SafeString:
        return image_tag(obj.background_image, height=160)
