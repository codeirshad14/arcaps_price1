"""
Management command: create realistic demo data for the price checker.

    python manage.py seed_products
    python manage.py seed_products --reset

The fixture deliberately includes edge cases you will want to see on screen:

* products with **no image** (the "No Image Available" placeholder path)
* products with **zero stock** (the red "Out of Stock" badge)
* products with **no discount** (no strikethrough, no discount badge)
* one product carrying a separate ``alias`` (QR value differing from barcode)
* wholesale priced meaningfully below retail on every product, so the two
  tiers always read as genuinely different on screen
"""

from __future__ import annotations

import io
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFont

from products.models import Category, Company, DisplayProfile, Product

# Palette used for the generated placeholder images — muted, showroom-friendly.
_SWATCHES = [
    ((30, 58, 95), (59, 130, 246)),
    ((49, 46, 82), (139, 92, 246)),
    ((19, 78, 74), (20, 184, 166)),
    ((69, 26, 26), (239, 68, 68)),
    ((66, 50, 12), (245, 158, 11)),
    ((30, 41, 59), (100, 116, 139)),
]

# HSN code + GST% per category — standard Indian slabs for apparel/goods.
_CATEGORY_TAX = {
    "Caps": ("6505", "12"),
    "Hats": ("6505", "12"),
    "Winter Wear": ("6117", "12"),
    "Bags": ("4202", "18"),
    "Accessories": ("6217", "12"),
}

# barcode, alias, name, category, retail_price, given_disc, stock, unit, description, has_image
_PRODUCTS = [
    (
        "1345", None, "Blue Cotton Cap", "Caps",
        "699.00", "28.6", "42", "PCS",
        "Premium cotton cap with an adjustable strap and breathable eyelets.", True,
    ),
    (
        "1346", None, "Black Snapback Cap", "Caps",
        "899.00", "27.8", "18", "PCS",
        "Flat-brim snapback with a structured six-panel crown.", True,
    ),
    (
        "1347", "QR-1347-AR", "Red Baseball Cap", "Caps",
        "549.00", "27.3", "7", "PCS",
        "Classic curved-brim baseball cap in brushed twill.", True,
    ),
    (
        "1348", None, "Beige Bucket Hat", "Hats",
        "999.00", "25.0", "0", "PCS",
        "Wide-brim bucket hat in washed cotton. Packable and machine washable.", True,
    ),
    (
        "1349", None, "Olive Trucker Cap", "Caps",
        "549.00", "0", "25", "PCS",
        "Mesh-back trucker cap that stays cool through long summer days.", False,
    ),
    (
        "1350", None, "Navy Wool Beanie", "Winter Wear",
        "499.00", "30.1", "63", "PCS",
        "Soft merino-blend beanie with a folded cuff.", True,
    ),
    (
        "1351", None, "Grey Fleece Beanie", "Winter Wear",
        "399.00", "25.1", "0", "PCS",
        "Fleece-lined beanie for genuinely cold mornings.", False,
    ),
    (
        "1352", None, "Canvas Tote Bag", "Bags",
        "1299.00", "30.8", "31", "PCS",
        "Heavyweight 12oz canvas tote with reinforced handles.", True,
    ),
    (
        "1353", None, "Leather Belt — Brown", "Accessories",
        "1699.00", "29.4", "14", "PCS",
        "Full-grain leather belt with a brushed nickel buckle.", True,
    ),
    (
        "1354", None, "Cotton Wristband Pair", "Accessories",
        "199.00", "0", "120", "PCS",
        "Absorbent terry-cloth wristbands, sold as a pair.", False,
    ),
    (
        "1355", None, "Sports Visor — White", "Caps",
        "449.00", "26.7", "48", "PCS",
        "Lightweight visor with a moisture-wicking sweatband.", True,
    ),
    (
        "1356", None, "Denim Cap — Washed", "Caps",
        "799.00", "27.5", "9", "PCS",
        "Stonewashed denim cap that softens with wear.", False,
    ),
    (
        "1357", None, "Kids Cartoon Cap", "Caps",
        "349.00", "28.7", "0", "PCS",
        "Sized for ages 4–8, with an embroidered character patch.", True,
    ),
    (
        "1358", None, "Nylon Sling Bag", "Bags",
        "1499.00", "26.7", "22", "PCS",
        "Water-resistant sling bag with a hidden rear zip pocket.", True,
    ),
    (
        "1359", None, "Silk Neck Scarf", "Accessories",
        "2199.00", "31.8", "5.5", "PCS",
        "Hand-rolled silk scarf, 65cm square.", False,
    ),
    (
        "1360", None, "Corduroy Dad Cap", "Caps",
        "699.00", "0", "16", "PCS",
        "Unstructured six-panel cap in fine-wale corduroy.", True,
    ),
]


class Command(BaseCommand):
    help = "Create ~15 realistic demo products, categories and display settings."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing products and categories before seeding.",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            deleted, _ = Product.objects.all().delete()
            Category.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Deleted {deleted} existing rows."))

        # Make sure at least one station exists so the kiosk has settings.
        DisplayProfile.resolve()
        # Every product needs an owning company.
        company = Company.get_default()

        categories: dict[str, Category] = {}
        created_count = 0
        updated_count = 0

        for index, row in enumerate(_PRODUCTS):
            (
                barcode, alias, name, category_name,
                retail_price, given_disc, stock, unit, description, has_image,
            ) = row

            if category_name not in categories:
                categories[category_name], _ = Category.objects.get_or_create(
                    name=category_name
                )
            hsn, gst = _CATEGORY_TAX.get(category_name, ("", "0"))

            # Wholesale is priced 15% below what a retail customer actually
            # pays *after* their discount — not off the list price — so it
            # always reads as a genuinely better rate, not a worse one.
            retail_price_dec = Decimal(retail_price)
            given_disc_dec = Decimal(given_disc)
            retail_selling = (
                retail_price_dec
                if not given_disc_dec
                else (retail_price_dec * (Decimal("100") - given_disc_dec) / Decimal("100"))
            )
            wholesale_price = (retail_selling * Decimal("0.85")).quantize(Decimal("1"))

            product, created = Product.objects.update_or_create(
                company=company,
                barcode=barcode,
                defaults={
                    "alias": alias,
                    "name": name,
                    "description": description,
                    "category": categories[category_name],
                    "retail_price": retail_price_dec,
                    "given_disc": given_disc_dec,
                    "wholesale_price": wholesale_price,
                    "gst": Decimal(gst),
                    "hsn": hsn,
                    "closing_stock": Decimal(stock),
                    "unit": unit,
                    "is_active": True,
                },
            )

            # Only generate an image when the row asks for one and the product
            # does not already have one — re-running the command stays cheap.
            if has_image and not product.image:
                image_bytes = _build_placeholder_image(name, _SWATCHES[index % len(_SWATCHES)])
                product.image.save(
                    f"{barcode}-{_slug(name)}.jpg",
                    ContentFile(image_bytes),
                    save=True,
                )

            created_count += int(created)
            updated_count += int(not created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created_count} new and {updated_count} existing products "
                f"across {len(categories)} categories."
            )
        )
        self.stdout.write(
            "Edge cases included: no-image products, zero-stock products, "
            "products without a discount, and one product with a separate "
            "alias (1347). Every product has a distinct wholesale price."
        )

        self._seed_stations()

    # -- Stations -------------------------------------------------------------

    def _seed_stations(self) -> None:
        """Create two stations that differ in timing and theme.

        Both always show retail and wholesale together now — there is no
        per-station price list to pick any more.
        """
        DisplayProfile.objects.update_or_create(
            slug="entrance",
            defaults={
                "name": "Entrance Screen",
                "is_default": True,
                "display_duration_seconds": 8,
                "theme_preset": "midnight",
                "show_image": True,
                "show_description": True,
                "show_availability": False,
            },
        )
        DisplayProfile.objects.update_or_create(
            slug="counter",
            defaults={
                "name": "Billing Counter",
                "is_default": False,
                "display_duration_seconds": 4,
                "theme_preset": "ocean",
                "show_image": False,
                "show_description": False,
                "show_availability": True,
                "availability_mode": DisplayProfile.AVAILABILITY_QUANTITY,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Stations: /?station=entrance (8s, midnight theme) and "
                "/?station=counter (4s, ocean theme, no image, shows stock qty)."
            )
        )


# ---------------------------------------------------------------------------
# Placeholder image generation
#
# Generated locally with Pillow so seeding needs no network access and no
# binary assets in the repository.
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Lowercase filename-safe slug — good enough for demo filenames."""
    keep = [c.lower() if c.isalnum() else "-" for c in text]
    return "".join(keep).strip("-").replace("--", "-")


def _build_placeholder_image(
    label: str, swatch: tuple[tuple[int, int, int], tuple[int, int, int]]
) -> bytes:
    """Render a 800x800 JPEG: vertical gradient with the product name on it."""
    size = 800
    top, bottom = swatch
    image = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(image)

    # Simple vertical gradient, one horizontal line at a time.
    for y in range(size):
        ratio = y / (size - 1)
        colour = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=colour)

    font = _load_font(46)
    lines = _wrap(label, width=18)
    line_height = 62
    block_height = line_height * len(lines)
    y = (size - block_height) // 2

    for line in lines:
        # textbbox gives accurate centring for the loaded font.
        left, top_edge, right, bottom_edge = draw.textbbox((0, 0), line, font=font)
        x = (size - (right - left)) // 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _load_font(size: int):
    """Load a readable TrueType font, falling back to Pillow's bitmap default."""
    for candidate in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap to at most `width` characters per line."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
