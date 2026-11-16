"""
Kiosk tests: the installable app (PWA) and the resized pictures it serves.

The app can be installed only from a staff page in the admin, so these check
both that its pieces work and that nobody else is offered it.
"""

import io
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from products import images
from products.models import DisplayProfile


class PwaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.station = DisplayProfile.objects.create(
            name="Entrance", slug="entrance", is_default=True
        )
        user_model = get_user_model()
        cls.staff = user_model.objects.create_user("staff", password="x", is_staff=True)
        cls.plain = user_model.objects.create_user("plain", password="x")

    def test_manifest_describes_the_counter_screen(self):
        response = self.client.get(reverse("kiosk:pwa-manifest"))
        self.assertEqual(response["Content-Type"], "application/manifest+json")
        data = response.json()
        self.assertEqual(data["start_url"], "/")
        self.assertEqual(data["scope"], "/")
        self.assertEqual(data["theme_color"], self.station.color_bg_start)
        purposes = {icon["purpose"] for icon in data["icons"]}
        self.assertEqual(purposes, {"any", "maskable"})

    def test_service_worker_is_javascript_and_versioned(self):
        response = self.client.get(reverse("kiosk:pwa-service-worker"))
        self.assertEqual(response["Content-Type"], "application/javascript")
        body = response.content.decode()
        self.assertNotIn("{{", body)          # the version was filled in
        # Prices must never come from a cache.
        self.assertIn("/api/", body)
        self.assertNotIn('pathname.indexOf("/api/") === 0', body)

    def test_icons_are_pngs_of_the_requested_size(self):
        for name, size in [("kiosk:pwa-icon", 192), ("kiosk:pwa-icon", 512),
                           ("kiosk:pwa-icon-maskable", 512), ("kiosk:pwa-icon", 180)]:
            with self.subTest(name=name, size=size):
                response = self.client.get(reverse(name, args=[size]))
                self.assertEqual(response["Content-Type"], "image/png")
                self.assertEqual(Image.open(io.BytesIO(response.content)).size, (size, size))

    def test_unknown_icon_size_is_404(self):
        self.assertEqual(self.client.get(reverse("kiosk:pwa-icon", args=[64])).status_code, 404)

    def test_install_page_is_staff_only(self):
        url = reverse("install-app")
        self.assertEqual(self.client.get(url).status_code, 302)       # to admin login

        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("kiosk:pwa-manifest"))

    def test_only_the_install_page_links_the_manifest(self):
        """The sign-in page and the counter screen never offer the install."""
        # Signed out first: the sign-in page redirects anyone already in.
        self.assertNotContains(self.client.get(reverse("kiosk:login")), 'rel="manifest"')
        self.client.force_login(self.staff)
        self.assertNotContains(self.client.get(reverse("kiosk:screen")), 'rel="manifest"')


class CatalogPageTests(TestCase):
    """The internal catalogue page: staff only, reached from the counter."""

    @classmethod
    def setUpTestData(cls):
        DisplayProfile.objects.create(name="Entrance", slug="entrance", is_default=True)
        cls.user = get_user_model().objects.create_user("counter", password="x")

    def test_signed_out_is_sent_to_sign_in(self):
        response = self.client.get(reverse("kiosk:catalog"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_signed_in_sees_the_catalogue(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("kiosk:catalog") + "?station=entrance")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="catalog-config"')
        self.assertContains(response, "css/catalog.css")

    def test_counter_screen_shows_the_catalogue_icon_when_signed_in(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("kiosk:screen")), 'id="catalog-link"')

    def test_customer_page_never_shows_it(self):
        from datetime import timedelta

        from django.utils import timezone

        from products.models import CustomerSession

        visit = CustomerSession.objects.create(
            name="Walk-in", mobile_number="9876543210",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        session = self.client.session
        session[CustomerSession.SESSION_KEY] = visit.pk
        session.save()
        response = self.client.get(reverse("customer:check"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "catalog")


class ResizedImageTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)

    def _upload(self, name, size=(3000, 1500), fmt="PNG"):
        buffer = io.BytesIO()
        Image.new("RGB", size, (200, 30, 30)).save(buffer, fmt)
        path = Path(self.media, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(buffer.getvalue())
        return name

    def test_copy_is_webp_and_fits_the_size(self):
        name = images.resized_name(self._upload("products/big.png"), images.PRODUCT)
        self.assertTrue(name.endswith(".webp"))
        with Image.open(Path(self.media, name)) as copy:
            self.assertEqual(copy.format, "WEBP")
            self.assertEqual(max(copy.size), images.PRODUCT)

    def test_small_pictures_are_never_enlarged(self):
        name = images.resized_name(self._upload("products/small.png", (120, 80)), images.PRODUCT)
        with Image.open(Path(self.media, name)) as copy:
            self.assertEqual(copy.size, (120, 80))

    def test_original_is_untouched(self):
        original = self._upload("products/keep.png")
        before = Path(self.media, original).read_bytes()
        images.resized_name(original, images.THUMBNAIL)
        self.assertEqual(Path(self.media, original).read_bytes(), before)

    def test_unreadable_or_missing_files_fall_back_to_the_original(self):
        Path(self.media, "products").mkdir(parents=True, exist_ok=True)
        Path(self.media, "products/broken.png").write_bytes(b"not an image")
        self.assertIsNone(images.resized_name("products/broken.png", images.PRODUCT))
        self.assertIsNone(images.resized_name("products/missing.png", images.PRODUCT))
        self.assertIsNone(images.resized_name("products/anim.gif", images.PRODUCT))

    def test_lookup_serves_the_small_copy(self):
        from decimal import Decimal

        from products.models import Company, Product

        DisplayProfile.objects.create(name="E", slug="e", is_default=True)
        buffer = io.BytesIO()
        Image.new("RGB", (2400, 2400), (10, 120, 200)).save(buffer, "PNG")
        Product.objects.create(
            company=Company.get_default(), barcode="777", name="Photo cap",
            retail_price=Decimal("10"), wholesale_price=Decimal("9"),
            image=SimpleUploadedFile("photo.png", buffer.getvalue(), "image/png"),
        )
        data = self.client.get(reverse("api:lookup") + "?code=777").json()
        self.assertIn(f"/{images.RESIZED_DIR}/{images.PRODUCT}/", data["tally_product"]["image"])
        self.assertTrue(data["tally_product"]["image"].endswith(".webp"))
