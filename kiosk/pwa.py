"""
The installable app (PWA): manifest, service worker, icons, install page.

Installing is offered from one place only — the "Install the app" page in the
admin, open to staff and nobody else. That page is the only one linking the
manifest, so browsers do not offer to install from the counter screen or the
customer portal, and a customer's phone never meets any of this.

What gets installed is the counter screen (start_url "/"), running in its
own window without browser bars. It still signs in like the page does, and
every price still comes live from the server: the service worker caches the
CSS, JS and pictures so the app opens fast, never a price.

    /manifest.webmanifest       the app's name, colours and icons
    /sw.js                      service worker; served from the root so that
                                it may look after every page under "/"
    /pwa/icon-<size>.png        icon drawn from the station's logo and theme
    /admin/install-app/         the install page (staff only)
"""

from __future__ import annotations

import io

from django.contrib import admin
from django.core.files.storage import default_storage
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from products import images
from products.models import DisplayProfile, ScreenHeaderSettings

from .views import asset_version

ICON_SIZES = {180, 192, 512}


def _app_name(station: DisplayProfile) -> str:
    return ScreenHeaderSettings.get_solo().name_for(station) or "Price Checker"


def _icon_url(size: int, station: DisplayProfile, maskable: bool = False) -> str:
    name = "kiosk:pwa-icon-maskable" if maskable else "kiosk:pwa-icon"
    # The station's last save in the URL, so a new logo or theme reaches an
    # icon the browser has already cached.
    return f"{reverse(name, args=[size])}?v={int(station.updated_at.timestamp())}"


def manifest(request: HttpRequest) -> JsonResponse:
    station = DisplayProfile.resolve()
    name = _app_name(station)
    data = {
        "id": "/",
        "name": f"{name} Price Checker",
        "short_name": name[:12],
        "description": "Scan or type a code or product name to see today's price.",
        "start_url": "/",
        "scope": "/",
        # Full screen where the device allows it — a counter tablet then has
        # no status bar either — and an ordinary app window where it does not.
        "display": "standalone",
        "display_override": ["fullscreen", "standalone"],
        "orientation": "any",
        "background_color": station.color_bg_start,
        "theme_color": station.color_bg_start,
        "icons": [
            {"src": _icon_url(192, station), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": _icon_url(512, station), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {
                "src": _icon_url(512, station, maskable=True),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Content-Type"] = "application/manifest+json"
    response["Cache-Control"] = "no-cache"
    return response


def service_worker(request: HttpRequest) -> HttpResponse:
    response = render(
        request,
        "pwa/sw.js",
        # The asset stamp changes whenever the CSS/JS does, which makes this
        # file different, which is what tells browsers to update the worker.
        {"version": asset_version()},
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    return response


def icon(request: HttpRequest, size: int, maskable: bool = False) -> HttpResponse:
    if size not in ICON_SIZES:
        raise Http404("No icon at that size.")
    response = HttpResponse(_render_icon(size, maskable), content_type="image/png")
    response["Cache-Control"] = "public, max-age=86400"
    return response


def install_app(request: HttpRequest) -> HttpResponse:
    """The admin page that installs the app. Wrapped in admin_view in urls.py."""
    station = DisplayProfile.resolve()
    context = {
        **admin.site.each_context(request),
        "title": "Install the app",
        "app_name": _app_name(station),
        "theme_color": station.color_bg_start,
        "icon_url": _icon_url(192, station),
        "apple_icon_url": _icon_url(180, station),
    }
    return render(request, "admin/install_app.html", context)


# ---------------------------------------------------------------------------
# Drawing the icon
# ---------------------------------------------------------------------------


def _render_icon(size: int, maskable: bool) -> bytes:
    """The station's logo on its own theme gradient.

    Drawn on request rather than shipped as a file, so a shop that changes
    its logo or colours gets a matching app icon without anyone making one.

    A maskable icon is cropped by the phone into a circle or squircle, so it
    is full-bleed with the logo kept inside the middle "safe zone"; the
    ordinary one gets its own rounded corners.
    """
    from PIL import Image, ImageDraw, ImageFont

    station = DisplayProfile.resolve()
    top = Image.new("RGB", (size, size), _rgb(station.color_bg_start))
    bottom = Image.new("RGB", (size, size), _rgb(station.color_bg_end))
    gradient = Image.linear_gradient("L").resize((size, size))
    canvas = Image.composite(bottom, top, gradient).convert("RGBA")

    inner = int(size * (0.5 if maskable else 0.66))
    logo = _load_logo(station, inner)
    if logo is not None:
        canvas.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
    else:
        # No logo uploaded: the shop's initial, like the top bar's monogram.
        letter = (_app_name(station)[:1] or "P").upper()
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default(size=int(inner * 0.8))
        except TypeError:  # Pillow < 10.1 has only a tiny bitmap font
            font = ImageFont.load_default()
        draw.text((size / 2, size / 2), letter, fill=_rgb(station.color_accent),
                  font=font, anchor="mm")

    if not maskable:
        corners = Image.new("L", (size, size), 0)
        ImageDraw.Draw(corners).rounded_rectangle(
            (0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=255
        )
        canvas.putalpha(corners)

    buffer = io.BytesIO()
    canvas.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _load_logo(station: DisplayProfile, box: int):
    """The station's logo fitted inside ``box`` pixels, or ``None``."""
    from PIL import Image, ImageOps

    if not station.logo or not default_storage.exists(station.logo.name):
        return None
    # The small copy decodes in a blink; the upload can be megabytes.
    name = images.resized_name(station.logo.name, images.LOGO) or station.logo.name
    try:
        with default_storage.open(name, "rb") as handle, Image.open(handle) as original:
            logo = ImageOps.exif_transpose(original).convert("RGBA")
    except Exception:  # noqa: BLE001 - a broken logo falls back to the monogram
        return None
    logo.thumbnail((box, box), Image.Resampling.LANCZOS)
    return logo


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = (hex_colour or "#000000").lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (0, 0, 0)
