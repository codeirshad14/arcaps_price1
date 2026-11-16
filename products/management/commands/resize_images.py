"""
Make the resized copies of every uploaded picture ahead of time.

    python manage.py resize_images

The screens would make them anyway, one at a time on first view (see
products/images.py). Running this after a deploy, or after a bulk upload of
photos, means no customer is the one who waits while a 6 MB photo is shrunk.
Safe to run as often as you like: a copy already up to date is skipped.
"""

from django.core.management.base import BaseCommand

from products import images
from products.models import Company, DisplayProfile, Product


class Command(BaseCommand):
    help = "Create the small copies of product photos, logos and backgrounds."

    def handle(self, *args, **options):
        jobs = []
        for name in Product.objects.exclude(image="").exclude(image__isnull=True).values_list(
            "image", flat=True
        ):
            jobs += [(name, images.PRODUCT), (name, images.THUMBNAIL), (name, images.PREVIEW)]
        for logo, background in DisplayProfile.objects.values_list("logo", "background_image"):
            if logo:
                jobs += [(logo, images.LOGO), (logo, images.THUMBNAIL), (logo, images.PREVIEW)]
            if background:
                jobs += [(background, images.BACKGROUND), (background, images.PREVIEW)]
        for name in Company.objects.exclude(default_product_image="").exclude(
            default_product_image__isnull=True
        ).values_list("default_product_image", flat=True):
            jobs.append((name, images.PREVIEW))

        # Many products share one company default picture; do each file once.
        jobs = sorted(set(jobs))
        made = sum(1 for name, size in jobs if images.resized_name(name, size))
        skipped = len(jobs) - made
        self.stdout.write(
            self.style.SUCCESS(f"{made} resized copies ready.")
            + (f" {skipped} left as they are (missing, GIF/SVG, or unreadable)." if skipped else "")
        )
