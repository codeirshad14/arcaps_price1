"""
Smaller copies of uploaded pictures, for the screens.

Uploads arrive at whatever size the camera or design tool produced: the
catalogue has product photos of 6 MB and a 1.8 MB logo that every page
loads. A phone on mobile data downloads each one in full before the tab
stops loading, and a few customers doing that at once is enough to clog the
server's connection for everybody — which is what "the tab keeps loading"
was.

So the screens are given a resized WebP copy instead, usually a few dozen
KB. Copies are made on first use and kept under MEDIA_ROOT/resized/<size>/,
beside the originals and served the same way. Nothing is stored in the
database and the original upload is never touched. A copy is rebuilt
whenever its original is newer, so replacing a picture shows straight away.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)

RESIZED_DIR = "resized"
QUALITY = 80

# Longest side, in pixels, for each place a picture is shown. Generous enough
# for a 1080p showroom screen; anything larger is bytes nobody sees.
PRODUCT = 900
CARD = 480
LOGO = 480
BACKGROUND = 1920
THUMBNAIL = 240
PREVIEW = 800

# Pillow can read these but a WebP copy would lose something that matters:
# an animated GIF's frames, an SVG's scalability.
_KEEP_AS_IS = {".gif", ".svg"}


def resized_name(name: str, size: int) -> str | None:
    """Storage name of a copy of ``name`` no larger than ``size`` pixels.

    Made on first use. Returns ``None`` when no copy can be made — a storage
    that is not on local disk, a format left alone on purpose, a file Pillow
    cannot read — and the caller then serves the original, so a picture
    problem never breaks a page.
    """
    if not name or Path(name).suffix.lower() in _KEEP_AS_IS:
        return None
    try:
        source = Path(default_storage.path(name))
    except NotImplementedError:
        return None
    if not source.is_file():
        return None

    # The original extension stays in the name, so x.png and x.jpg cannot
    # collide on one x.webp.
    target_name = f"{RESIZED_DIR}/{size}/{Path(name).as_posix()}.webp"
    target = Path(default_storage.path(target_name))
    try:
        if not target.is_file() or target.stat().st_mtime < source.stat().st_mtime:
            _write_resized(source, target, size)
    except Exception:  # noqa: BLE001 - serve the original rather than fail the page
        logger.warning("Could not make a resized copy of %s", name, exc_info=True)
        return None
    return target_name


def resized_url(image_field, size: int) -> str:
    """URL of a resized copy of an ImageField's file, or of the original."""
    name = resized_name(image_field.name, size)
    return default_storage.url(name) if name else image_field.url


def _write_resized(source: Path, target: Path, size: int) -> None:
    from PIL import Image, ImageOps

    with Image.open(source) as original:
        # Phone photos store their rotation in EXIF rather than the pixels;
        # without this they come out sideways once the EXIF is dropped.
        image = ImageOps.exif_transpose(original)
        image.thumbnail((size, size), Image.Resampling.LANCZOS)  # only shrinks
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")

        target.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and swapped in, so a second worker resizing the same
        # picture at the same moment can never serve a half-written file.
        partial = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        image.save(partial, "WEBP", quality=QUALITY, method=4)
        os.replace(partial, target)
