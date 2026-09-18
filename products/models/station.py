"""Showroom stations — what each screen shows and how it looks."""

from __future__ import annotations

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from .company import Company

# Hex colours only — the frontend drops these straight into CSS variables, so
# anything else would produce a silently broken theme.
hex_colour_validator = RegexValidator(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    "Enter a hex colour such as #1a2b3c.",
)

# Ready-made colour schemes. Picking one in the admin overwrites the individual
# colour fields on save; choosing "custom" leaves them alone.
THEME_PRESETS: dict[str, dict[str, str]] = {
    "midnight": {
        "color_bg_start": "#070b16",
        "color_bg_end": "#1d1442",
        "color_accent": "#ffc247",
        "color_price": "#ffc247",
        "color_text": "#f4f7fd",
        "color_text_dim": "#aab4c9",
        "color_in_stock": "#3ddc97",
        "color_out_of_stock": "#ff7a7a",
    },
    "ocean": {
        "color_bg_start": "#04121c",
        "color_bg_end": "#0b3a4a",
        "color_accent": "#38e1d0",
        "color_price": "#7ef7e4",
        "color_text": "#effbfa",
        "color_text_dim": "#9dbdc4",
        "color_in_stock": "#5ce39b",
        "color_out_of_stock": "#ff8a8a",
    },
    "royal": {
        "color_bg_start": "#0d0620",
        "color_bg_end": "#3a1160",
        "color_accent": "#c9a2ff",
        "color_price": "#e6d4ff",
        "color_text": "#f7f3ff",
        "color_text_dim": "#b7a8d4",
        "color_in_stock": "#6ee7a8",
        "color_out_of_stock": "#ff86a8",
    },
    "warm": {
        "color_bg_start": "#1a0d06",
        "color_bg_end": "#4a1e0c",
        "color_accent": "#ffb163",
        "color_price": "#ffd9a8",
        "color_text": "#fff6ee",
        "color_text_dim": "#d3b6a2",
        "color_in_stock": "#8fd694",
        "color_out_of_stock": "#ff9a8a",
    },
    "light": {
        "color_bg_start": "#eef2f9",
        "color_bg_end": "#cdd8ec",
        "color_accent": "#c2410c",
        "color_price": "#9a3412",
        "color_text": "#111a2b",
        "color_text_dim": "#4b5768",
        "color_in_stock": "#15803d",
        "color_out_of_stock": "#b91c1c",
    },
}


class DisplayProfile(models.Model):
    """One showroom station: what it shows, how it looks.

    Several systems can run the same app and each pick a profile, so the
    counter can show a compact 3-second card while the entrance screen shows
    a big image for 8 seconds. Every station always shows both the retail
    and wholesale price for whatever it scans — there is no per-station
    price list to choose any more.
    """

    AVAILABILITY_LABEL = "label"
    AVAILABILITY_QUANTITY = "quantity"
    AVAILABILITY_MODE_CHOICES = [
        (AVAILABILITY_LABEL, 'Label only ("In Stock" / "Out of Stock")'),
        (AVAILABILITY_QUANTITY, "Quantity with unit (e.g. 42 PCS)"),
    ]

    BACKGROUND_FIT_CHOICES = [
        ("cover", "Cover — fill the screen, cropping the edges"),
        ("contain", "Contain — show the whole image, letterboxed"),
        ("stretch", "Stretch — fill the screen, ignoring proportions"),
        ("center", "Center — original size, centred"),
        ("tile", "Tile — repeat the image"),
    ]

    THEME_PRESET_CHOICES = [
        ("midnight", "Midnight (navy → indigo)"),
        ("ocean", "Ocean (teal)"),
        ("royal", "Royal (purple)"),
        ("warm", "Warm (amber → brown)"),
        ("light", "Light (for bright showrooms)"),
        ("custom", "Custom — use the colours set below"),
    ]

    # --- Identity -----------------------------------------------------------
    name = models.CharField(max_length=80, help_text='e.g. "Entrance screen".')
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text="Used in the URL: /?station=<slug>",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Used by any system that has not chosen a station.",
    )
    is_active = models.BooleanField(default=True)

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stations",
        help_text="Which company's products this screen shows. "
        "<strong>Leave blank to show every company</strong> — a scanned code "
        "that exists in more than one will then show each match side by side.",
    )

    # --- General ------------------------------------------------------------
    store_name = models.CharField(max_length=120, default="AR Caps")
    welcome_message = models.CharField(max_length=200, default="Welcome to AR Caps")
    currency_symbol = models.CharField(max_length=5, default="₹")
    logo = models.ImageField(upload_to="branding/", null=True, blank=True)
    enable_voice_search = models.BooleanField(
        default=True, help_text="Show the microphone button."
    )
    voice_input_language = models.CharField(
        max_length=10,
        choices=[
            ("auto", "Auto-detect (English first, then Hindi / Marathi)"),
            ("en-IN", "English (India) — also understands Hinglish"),
            ("hi-IN", "Hindi"),
            ("mr-IN", "Marathi"),
        ],
        default="auto",
        help_text=(
            "Which language the microphone listens for. "
            "<strong>Browsers can only decode one language per recording</strong>, so "
            "'Auto' means: try English, and if no code is heard, ask once more in "
            "another language. If your customers mostly speak one language, "
            "choosing it here makes the first attempt succeed and removes the "
            "second ask entirely."
        ),
    )

    # --- Voice / wake word --------------------------------------------------
    enable_wake_word = models.BooleanField(
        default=False,
        help_text="Listen continuously for a wake phrase. Chrome/Edge only, "
        "needs microphone permission and an internet connection.",
    )
    wake_phrases = models.TextField(
        default="hi ahmad\nhi ahmed raza\nmr caps",
        blank=True,
        help_text="One phrase per line. Spoken punctuation and case are ignored.",
    )

    # --- Spoken price (text to speech) --------------------------------------
    enable_price_speech = models.BooleanField(
        default=True,
        help_text="Read the price aloud through the speakers when a product is "
        "shown. Uses the computer's own voices, so it works without internet.",
    )
    speech_language = models.CharField(
        max_length=10,
        choices=[
            ("en-IN", "English (India)"),
            ("hi-IN", "Hindi"),
            ("mr-IN", "Marathi"),
            ("en-US", "English (US)"),
        ],
        default="en-IN",
        help_text="A matching voice must be installed on the showroom PC. "
        "Windows: Settings → Time & Language → Speech.",
    )
    speech_template = models.CharField(
        max_length=200,
        default="{name}. Price {price} rupees.",
        help_text="Placeholders: {name} {price} {mrp} {discount} {currency} "
        "{availability}. Example: 'Price {price} rupees.'",
    )
    speech_rate = models.PositiveSmallIntegerField(
        default=95,
        validators=[MinValueValidator(50), MaxValueValidator(150)],
        help_text="Speaking speed, 50–150%. Slightly slow reads as calmer and "
        "is clearer in a noisy showroom.",
    )
    speech_voice_preference = models.CharField(
        max_length=8,
        choices=[
            ("female", "Female"),
            ("male", "Male"),
            ("auto", "Any available voice"),
        ],
        default="female",
        help_text="Which installed voice to prefer. The browser only offers "
        "the voices installed on the showroom PC, so this is a preference, "
        "not a guarantee — the closest match for the language wins.",
    )
    speech_pitch = models.PositiveSmallIntegerField(
        default=105,
        validators=[MinValueValidator(50), MaxValueValidator(150)],
        help_text="Voice pitch, 50–150%. Slightly above 100 sounds warmer and "
        "softer; lower sounds sterner.",
    )

    # --- Multi-product ------------------------------------------------------
    enable_multi_scan = models.BooleanField(
        default=True,
        help_text="Scanning again while a result is showing adds a second "
        "product beside it instead of replacing it.",
    )
    max_multi_items = models.PositiveSmallIntegerField(
        default=4,
        validators=[MinValueValidator(2), MaxValueValidator(8)],
        help_text="How many products can share the screen at once.",
    )

    # --- Timing -------------------------------------------------------------
    display_duration_seconds = models.IntegerField(
        default=10,
        validators=[MinValueValidator(0)],
        help_text=(
            "Seconds a product stays on the detail panel before it clears. "
            "<strong>Set 0 to keep it on screen until the next scan</strong> — "
            "nothing clears by itself. A new scan always replaces the panel "
            "immediately, whatever the countdown is doing."
        ),
    )

    # --- Screen display fields ---------------------------------------------
    show_name = models.BooleanField(default=True, verbose_name="Show product name")
    show_image = models.BooleanField(default=True, verbose_name="Show product image")
    show_description = models.BooleanField(default=False, verbose_name="Show description")
    show_price = models.BooleanField(default=True, verbose_name="Show price")
    show_mrp = models.BooleanField(default=True, verbose_name="Show MRP")
    show_discount = models.BooleanField(default=True, verbose_name="Show discount badge")
    show_availability = models.BooleanField(default=False, verbose_name="Show availability")
    availability_mode = models.CharField(
        max_length=10,
        choices=AVAILABILITY_MODE_CHOICES,
        default=AVAILABILITY_LABEL,
        help_text="Only used when 'Show availability' is on.",
    )

    # --- Background ---------------------------------------------------------
    background_image = models.ImageField(
        upload_to="branding/",
        null=True,
        blank=True,
        help_text="Optional. The theme gradient is used when empty.",
    )
    background_fit = models.CharField(
        max_length=10,
        choices=BACKGROUND_FIT_CHOICES,
        default="cover",
        help_text="Use 'Contain' if your image is being cropped too tightly.",
    )
    background_overlay = models.PositiveSmallIntegerField(
        default=45,
        validators=[MinValueValidator(0), MaxValueValidator(90)],
        help_text="Darkening tint over the image, 0–90%. Raise it if the price "
        "is hard to read. Ignored when no image is uploaded.",
    )

    # --- Theme colours ------------------------------------------------------
    theme_preset = models.CharField(
        max_length=12,
        choices=THEME_PRESET_CHOICES,
        default="midnight",
        help_text="Picking a preset overwrites the colours below when you save.",
    )
    color_bg_start = models.CharField(
        max_length=7, default="#070b16", validators=[hex_colour_validator],
        verbose_name="Background gradient — start",
    )
    color_bg_end = models.CharField(
        max_length=7, default="#1d1442", validators=[hex_colour_validator],
        verbose_name="Background gradient — end",
    )
    color_accent = models.CharField(
        max_length=7, default="#ffc247", validators=[hex_colour_validator],
        verbose_name="Accent (buttons, scan ring, countdown)",
    )
    color_price = models.CharField(
        max_length=7, default="#ffc247", validators=[hex_colour_validator],
        verbose_name="Price text",
    )
    color_text = models.CharField(
        max_length=7, default="#f4f7fd", validators=[hex_colour_validator],
        verbose_name="Main text",
    )
    color_text_dim = models.CharField(
        max_length=7, default="#aab4c9", validators=[hex_colour_validator],
        verbose_name="Secondary text",
    )
    color_in_stock = models.CharField(
        max_length=7, default="#3ddc97", validators=[hex_colour_validator],
        verbose_name="In-stock badge",
    )
    color_out_of_stock = models.CharField(
        max_length=7, default="#ff7a7a", validators=[hex_colour_validator],
        verbose_name="Out-of-stock badge",
    )
    card_opacity = models.PositiveSmallIntegerField(
        default=8,
        validators=[MaxValueValidator(40)],
        help_text="How visible the product card is against the background, 0–40%.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "display profile (station)"
        verbose_name_plural = "display profiles (stations)"
        ordering = ["-is_default", "name"]

    def __str__(self) -> str:
        return f"{self.name} (default)" if self.is_default else self.name

    def save(self, *args, **kwargs):
        # Applying a preset is a save-time action so the admin sees the new
        # colours immediately in the form after saving.
        preset = THEME_PRESETS.get(self.theme_preset)
        if preset:
            for field, value in preset.items():
                setattr(self, field, value)

        super().save(*args, **kwargs)

        if self.is_default:
            DisplayProfile.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )

    @property
    def wake_phrase_list(self) -> list[str]:
        """Wake phrases as a normalised list, lowercase and de-duplicated."""
        seen, phrases = set(), []
        for line in (self.wake_phrases or "").splitlines():
            phrase = " ".join(line.lower().split())
            if phrase and phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
        return phrases

    @classmethod
    def resolve(cls, slug: str | None = None) -> "DisplayProfile":
        """Find the profile for a station slug, falling back sensibly.

        Order: the requested slug, then the default profile, then any active
        profile, then a freshly created one. The kiosk must always get a
        profile — a station with a typo in its URL should still show prices.
        """
        if slug:
            profile = cls.objects.filter(slug=slug, is_active=True).first()
            if profile:
                return profile

        profile = cls.objects.filter(is_default=True, is_active=True).first()
        if profile:
            return profile

        profile = cls.objects.filter(is_active=True).order_by("pk").first()
        if profile:
            return profile

        return cls.objects.create(name="Default", slug="default", is_default=True)
