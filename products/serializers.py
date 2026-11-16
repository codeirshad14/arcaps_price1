"""
DRF serializers.

Two directions of traffic, deliberately kept in separate serializers:

* **Out** — ``TallyProductSerializer`` renders the ``tally_product`` object the
  kiosk screen reads, with both the Retail and Wholesale price computed in.
* **In**  — ``TallyProductSyncSerializer`` validates the same shape arriving
  from Tally and upserts it.

Sharing one serializer for both would mean half the fields being read-only in
one direction and required in the other, which reads worse than two small
classes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from django.core.files.storage import default_storage
from rest_framework import serializers

from . import images
from .models import Category, Company, DisplayProfile, Product

# Money is rendered as a fixed-2dp string ("499.00"), matching DRF's default
# Decimal handling: it preserves trailing zeros and avoids float rounding on
# the way to the screen.
_MONEY = Decimal("0.01")


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(_MONEY))


def _absolute_media_url(image_field, request, size: int | None = None) -> str | None:
    """Absolute URL for an uploaded file, or ``None``.

    Returns ``None`` both when nothing was uploaded and when the row points at
    a file no longer on disk — a real situation after copying a database
    between machines. Either way the frontend draws its placeholder instead of
    a broken image.

    With ``size``, the URL is of a copy no larger than that — see
    products/images.py for why the screens must never get the raw upload.
    """
    if not image_field:
        return None
    try:
        if not default_storage.exists(image_field.name):
            return None
        url = images.resized_url(image_field, size) if size else image_field.url
    except (ValueError, OSError, NotImplementedError):
        return None
    # build_absolute_uri is what makes the URL usable from a tablet on the LAN
    # rather than only from the machine serving the page.
    return request.build_absolute_uri(url) if request else url


# ---------------------------------------------------------------------------
# Outbound: the tally_product object shown on screen
# ---------------------------------------------------------------------------


class DynamicFieldsMixin:
    """Allow a caller to restrict the rendered fields at runtime.

    Pass ``fields=[...]`` to the constructor and everything else is dropped
    from the output. The lookup endpoint uses this to honour ``?omit_hidden=1``,
    where fields switched off in the admin are removed from the payload
    entirely rather than merely ignored by the frontend.
    """

    def __init__(self, *args, fields: Iterable[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        if fields is not None:
            allowed = set(fields)
            for name in set(self.fields) - allowed:
                self.fields.pop(name)


class TallyProductSerializer(DynamicFieldsMixin, serializers.ModelSerializer):
    """The product payload the kiosk renders.

    ``prices`` always carries exactly two entries — Retail and Wholesale —
    each with the product's discount and GST already applied. The singular
    ``price``/``mrp``/``discount_percent`` mirror the Retail entry; they
    exist only for older callers (the spoken-price template) and as a safe
    fallback, not as the primary way to read a price any more.
    """

    image = serializers.SerializerMethodField()
    availability = serializers.CharField(source="availability_label", read_only=True)
    price = serializers.SerializerMethodField()
    mrp = serializers.SerializerMethodField()
    discount_percent = serializers.SerializerMethodField()
    prices = serializers.SerializerMethodField()
    company = serializers.CharField(source="company.name", read_only=True)
    company_code = serializers.CharField(source="company.label", read_only=True)

    class Meta:
        model = Product
        fields = [
            "name",
            "image",
            "description",
            "price",
            "mrp",
            "discount_percent",
            "prices",
            "closing_stock",
            "unit",
            "availability",
            "barcode",
            # Shown on the card so a code matching several companies is never
            # ambiguous on an all-companies screen.
            "company",
            "company_code",
        ]

    # --- Pricing --------------------------------------------------------------

    def get_price(self, obj: Product) -> str:
        return _money(obj.retail_final_price)

    def get_mrp(self, obj: Product) -> str | None:
        """The struck-through "was" price — only when a discount is set."""
        return _money(obj.retail_price_with_gst) if obj.given_disc else None

    def get_discount_percent(self, obj: Product) -> float:
        return float(obj.given_disc)

    def get_prices(self, obj: Product) -> list[dict[str, Any]]:
        """Retail and Wholesale, each already carrying discount + GST."""
        return [
            {
                "slug": "retail",
                "name": "Retail",
                "price": _money(obj.retail_final_price),
                "mrp": _money(obj.retail_price_with_gst) if obj.given_disc else None,
                "discount_percent": float(obj.given_disc),
                "is_default": True,
            },
            {
                "slug": "wholesale",
                "name": "Wholesale",
                "price": _money(obj.wholesale_final_price),
                "mrp": None,
                "discount_percent": 0.0,
                "is_default": False,
            },
        ]

    def get_image(self, obj: Product) -> str | None:
        return _absolute_media_url(obj.image, self.context.get("request"), images.PRODUCT)


class CatalogProductSerializer(TallyProductSerializer):
    """A product as the internal catalogue shows it.

    The kiosk payload plus what only staff need: stock, GST, HSN, category,
    alias. Every field is always sent — the station's "show this on screen"
    switches are about what customers see, and this screen is not theirs.

    ``image`` is a card-sized copy for the list; ``image_large`` the bigger
    one the detail view opens, fetched only when somebody opens it.
    """

    image_large = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()

    class Meta(TallyProductSerializer.Meta):
        fields = TallyProductSerializer.Meta.fields + [
            "id",
            "image_large",
            "alias",
            "category",
            "gst",
            "hsn",
        ]

    def get_image(self, obj: Product) -> str | None:
        return _absolute_media_url(obj.image, self.context.get("request"), images.CARD)

    def get_image_large(self, obj: Product) -> str | None:
        return _absolute_media_url(obj.image, self.context.get("request"), images.PRODUCT)

    def get_category(self, obj: Product) -> str | None:
        return obj.category.name if obj.category_id else None


class ProductListSerializer(serializers.ModelSerializer):
    """Fuller product representation for the debugging/admin-facing list."""

    image = serializers.SerializerMethodField()
    category = serializers.StringRelatedField()
    availability = serializers.CharField(source="availability_label", read_only=True)
    retail_final_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    wholesale_final_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "barcode",
            "alias",
            "name",
            "description",
            "category",
            "retail_price",
            "given_disc",
            "retail_final_price",
            "wholesale_price",
            "wholesale_final_price",
            "gst",
            "hsn",
            "closing_stock",
            "unit",
            "availability",
            "image",
            "is_active",
            "tally_guid",
            "last_synced_at",
            "updated_at",
        ]

    def get_image(self, obj: Product) -> str | None:
        return _absolute_media_url(obj.image, self.context.get("request"))


class ProductCrudSerializer(serializers.ModelSerializer):
    """Read/write serializer behind ``/api/products/``.

    Writable: everything a person or a script would maintain by hand.
    Read-only: values the model computes (the final, GST-inclusive prices;
    ``availability``) and sync bookkeeping (``last_synced_at``), which only
    the Tally endpoint should set.

    ``image`` accepts a file upload via ``multipart/form-data`` and renders as
    an absolute URL. ``category`` takes a primary key; ``category_name`` is a
    convenience for reading.
    """

    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(), required=False, allow_null=True
    )
    category_name = serializers.CharField(source="category.name", read_only=True)
    # Accepts a company slug, which is far friendlier for a sync script than
    # a database id.
    #
    # `default` rather than `required=False`: the (company, barcode) unique
    # constraint makes DRF generate a UniqueTogetherValidator, and that
    # validator requires every field it covers to be present. A default fills
    # company in before validation runs, so a single-company install never has
    # to name its company.
    company = serializers.SlugRelatedField(
        slug_field="slug",
        queryset=Company.objects.all(),
        default=Company.get_default,
    )
    company_name = serializers.CharField(source="company.name", read_only=True)
    availability = serializers.CharField(source="availability_label", read_only=True)
    retail_final_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    wholesale_final_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = Product
        fields = [
            "id",
            "company",
            "company_name",
            "barcode",
            "alias",
            "name",
            "description",
            "category",
            "category_name",
            "retail_price",
            "given_disc",
            "retail_final_price",
            "wholesale_price",
            "wholesale_final_price",
            "gst",
            "hsn",
            "closing_stock",
            "unit",
            "image",
            "availability",
            "is_active",
            "tally_guid",
            "last_synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_synced_at", "created_at", "updated_at"]
        # DRF would auto-generate UniqueTogetherValidators from the model's
        # per-company constraints. They work, but report every clash as
        # "The fields company, barcode must make a unique set" under
        # non_field_errors — unhelpful to a sync script trying to work out
        # which column was wrong. Replaced by the per-field checks in
        # validate() below.
        validators = []

    # What an omitted field resets to on PUT. See `update()` below.
    PUT_DEFAULTS: dict[str, Any] = {
        "alias": None,
        "description": "",
        "category": None,
        "given_disc": Decimal("0"),
        "gst": Decimal("0"),
        "hsn": "",
        "closing_stock": Decimal("0"),
        "unit": "PCS",
        "is_active": True,
        "tally_guid": None,
    }

    def update(self, instance: Product, validated_data: dict[str, Any]) -> Product:
        """Make PUT a real replace, not a second PATCH.

        DRF leaves ``required=False`` fields untouched when they are absent,
        which would make PUT and PATCH behave identically. A PUT is supposed to
        describe the whole resource, so anything omitted goes back to its
        default. Use PATCH when you only mean to change a few fields.

        ``image`` is deliberately excluded: a file cannot round-trip through a
        JSON PUT, so resetting it would make every JSON update silently delete
        the product photo. Send ``"image": null`` as multipart, or clear it in
        the admin, to remove one.
        """
        if not self.partial:
            for field, default in self.PUT_DEFAULTS.items():
                validated_data.setdefault(field, default)
        return super().update(instance, validated_data)

    def validate_barcode(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Barcode cannot be blank.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # Empty strings on the unique-if-set columns must become NULL, or the
        # second product without an alias collides with the first.
        for key in ("alias", "tally_guid"):
            if key in attrs:
                cleaned = (attrs[key] or "").strip()
                attrs[key] = cleaned or None

        # Codes are unique *within a company*, so each has to be checked as a
        # pair. Errors are reported against the offending field.
        #
        # `tally_guid` is deliberately absent: Tally reuses a GUID across
        # stock items, so it is reference data rather than an identity. See
        # the constraints on Product.
        company = attrs.get("company") or getattr(self.instance, "company", None)
        if company:
            for field, label in (
                ("barcode", "barcode"),
                ("alias", "alias"),
            ):
                value = attrs.get(field, getattr(self.instance, field, None))
                if not value:
                    continue                      # NULLs never clash
                clash = Product.objects.filter(company=company, **{field: value})
                if self.instance:
                    clash = clash.exclude(pk=self.instance.pk)
                if clash.exists():
                    raise serializers.ValidationError(
                        {field: f"{company.name} already has a product with this {label}."}
                    )

        return attrs


# ---------------------------------------------------------------------------
# Outbound: the display block
# ---------------------------------------------------------------------------


class DisplayProfileSerializer(serializers.ModelSerializer):
    """The ``display`` block returned alongside *every* lookup.

    Sending it on each response is what lets an admin change the duration, the
    theme or a field toggle and have the very next scan honour it — no
    restart, no browser refresh.
    """

    duration_seconds = serializers.IntegerField(
        source="display_duration_seconds", read_only=True
    )
    logo = serializers.SerializerMethodField()
    background = serializers.SerializerMethodField()
    theme = serializers.SerializerMethodField()
    voice = serializers.SerializerMethodField()
    speech = serializers.SerializerMethodField()
    multi = serializers.SerializerMethodField()
    station = serializers.SerializerMethodField()

    class Meta:
        model = DisplayProfile
        fields = [
            "station",
            "store_name",
            "welcome_message",
            "duration_seconds",
            "currency_symbol",
            "logo",
            "enable_voice_search",
            "background",
            "theme",
            "voice",
            "speech",
            "multi",
        ]

    def to_representation(self, instance: DisplayProfile) -> dict[str, Any]:
        data = super().to_representation(instance)
        # "fields" cannot be a declared serializer field — the name collides
        # with DRF's own Serializer.fields attribute — so it is injected here.
        data["fields"] = self.get_field_flags(instance)
        return data

    def get_field_flags(self, obj: DisplayProfile) -> dict[str, Any]:
        """Which product details the screen should render."""
        return {
            "name": obj.show_name,
            "image": obj.show_image,
            "description": obj.show_description,
            "price": obj.show_price,
            "mrp": obj.show_mrp,
            "discount": obj.show_discount,
            "availability": obj.show_availability,
            "availability_mode": obj.availability_mode,
        }

    def get_station(self, obj: DisplayProfile) -> dict[str, Any]:
        # The company actually in force can be narrowed by the signed-in user,
        # so the view passes it in rather than reading it off the station.
        company = self.context.get("company", obj.company)
        return {
            "slug": obj.slug,
            "name": obj.name,
            "company": company.name if company else None,
            "company_slug": company.slug if company else None,
            # None means "every company" — the frontend labels the screen
            # differently so staff can see the scope at a glance.
            "all_companies": company is None,
        }

    def get_logo(self, obj: DisplayProfile) -> str | None:
        return _absolute_media_url(obj.logo, self.context.get("request"), images.LOGO)

    def get_background(self, obj: DisplayProfile) -> dict[str, Any]:
        """Background image, how to fit it, and its darkening overlay.

        ``image: null`` tells the frontend to fall back to the theme gradient,
        so the screen always has a deliberate backdrop.
        """
        return {
            "image": _absolute_media_url(
                obj.background_image, self.context.get("request"), images.BACKGROUND
            ),
            "fit": obj.background_fit,
            "overlay": obj.background_overlay,
        }

    def get_theme(self, obj: DisplayProfile) -> dict[str, Any]:
        """Colours, applied by the frontend as CSS custom properties."""
        return {
            "preset": obj.theme_preset,
            "bg_start": obj.color_bg_start,
            "bg_end": obj.color_bg_end,
            "accent": obj.color_accent,
            "price": obj.color_price,
            "text": obj.color_text,
            "text_dim": obj.color_text_dim,
            "in_stock": obj.color_in_stock,
            "out_of_stock": obj.color_out_of_stock,
            "card_opacity": obj.card_opacity,
        }

    def get_voice(self, obj: DisplayProfile) -> dict[str, Any]:
        return {
            "enabled": obj.enable_voice_search,
            "wake_word_enabled": obj.enable_wake_word,
            "wake_phrases": obj.wake_phrase_list,
            # "auto" or a specific BCP-47 tag. Pinning it makes the first
            # attempt succeed for a showroom with one main language.
            "language": obj.voice_input_language,
        }

    def get_multi(self, obj: DisplayProfile) -> dict[str, Any]:
        return {"enabled": obj.enable_multi_scan, "max_items": obj.max_multi_items}

    def get_speech(self, obj: DisplayProfile) -> dict[str, Any]:
        """Settings for reading the price aloud."""
        return {
            "enabled": obj.enable_price_speech,
            "language": obj.speech_language,
            "template": obj.speech_template,
            # Sent as multipliers because that is what the browser wants.
            "rate": round(obj.speech_rate / 100, 2),
            "pitch": round(obj.speech_pitch / 100, 2),
            "voice": obj.speech_voice_preference,
        }


def build_display_block(
    request=None,
    profile: DisplayProfile | None = None,
    company=None,
) -> dict[str, Any]:
    """Return the ``display`` block for a station.

    Small helper so the lookup, settings and not-found paths cannot drift.
    ``company`` is the scope actually in force, which may be narrower than the
    station's own setting when the signed-in user is restricted.
    """
    profile = profile or DisplayProfile.resolve()
    return DisplayProfileSerializer(
        profile, context={"request": request, "company": company}
    ).data


class StationSerializer(serializers.ModelSerializer):
    """Lightweight listing used by the on-screen station picker."""

    company = serializers.SerializerMethodField()

    class Meta:
        model = DisplayProfile
        fields = ["slug", "name", "store_name", "company", "is_default"]

    def get_company(self, obj: DisplayProfile) -> str:
        return obj.company.name if obj.company else "All companies"


# ---------------------------------------------------------------------------
# Inbound: Tally sync
# ---------------------------------------------------------------------------


class TallyProductSyncSerializer(serializers.Serializer):
    """Validates one incoming ``tally_product`` record.

    Accepts the same shape the lookup endpoint emits so a script can round-trip
    the payload. Computed fields (``availability``, the final prices) are
    accepted and ignored rather than rejected — that keeps the contract
    forgiving for whatever Tally exports.

    Note: ``image`` is **not** synced. Transferring binaries would mean
    fetching remote URLs inside a request, so product images stay an
    admin-managed concern. Sending an image key is harmless; it is ignored.

    ``wholesale_price`` is optional — omit it and the sync falls back to
    ``retail_price``, the same "partial data never leaves a blank screen"
    behaviour the old per-price-list overrides had.
    """

    barcode = serializers.CharField(max_length=64)
    # Company slug. Omitted means the default company, so a single-company
    # Tally export keeps working unchanged.
    company = serializers.CharField(
        max_length=120, required=False, allow_null=True, allow_blank=True
    )
    alias = serializers.CharField(
        max_length=64, required=False, allow_null=True, allow_blank=True
    )
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    category = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    retail_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    given_disc = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False
    )
    wholesale_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, allow_null=True
    )
    gst = serializers.DecimalField(
        max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False
    )
    hsn = serializers.CharField(
        max_length=8, required=False, allow_blank=True, allow_null=True
    )
    closing_stock = serializers.DecimalField(
        max_digits=14, decimal_places=3, required=False, allow_null=True
    )
    unit = serializers.CharField(max_length=20, required=False, allow_blank=True)
    tally_guid = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    is_active = serializers.BooleanField(required=False)

    def validate_barcode(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Barcode cannot be blank.")
        return value

    def validate_company(self, value: str | None) -> Company:
        """Resolve a company by slug or name, falling back to the default."""
        value = (value or "").strip()
        if not value:
            return Company.get_default()

        company = Company.objects.filter(slug__iexact=value).first() or (
            Company.objects.filter(name__iexact=value).first()
        )
        if company is None:
            known = ", ".join(Company.objects.values_list("slug", flat=True)) or "none"
            raise serializers.ValidationError(
                f"Unknown company '{value}'. Create it in the admin first. "
                f"Known: {known}."
            )
        return company

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # Empty strings on the unique-if-set columns must become NULL, or the
        # second record without an alias collides with the first.
        for key in ("alias", "tally_guid"):
            if key in attrs:
                cleaned = (attrs[key] or "").strip()
                attrs[key] = cleaned or None

        # No wholesale rate supplied: quote the retail price, same as an
        # unpriced product falling back to its base price under the old
        # per-price-list system.
        if not attrs.get("wholesale_price"):
            attrs["wholesale_price"] = attrs.get("retail_price")

        return attrs
