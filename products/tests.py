"""
Model and admin tests.

Covers the rules that are easy to break without noticing: discount/GST maths,
unique-if-set columns, the singleton-ish default station, and that every
admin page and filter renders.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    Category,
    Company,
    DisplayProfile,
    Product,
    ScanLog,
    UserCompanyAccess,
    resolve_company,
)


class ProductModelTests(TestCase):
    def setUp(self):
        self.company, _ = Company.objects.update_or_create(
            slug="test-co", defaults={"name": "Test Co", "is_default": True}
        )
        self.category = Category.objects.create(name="Caps")
        self.product = Product.objects.create(
            company=self.company,
            barcode="1345",
            name="Blue Cotton Cap",
            category=self.category,
            retail_price=Decimal("699.00"),
            given_disc=Decimal("28.6"),
            wholesale_price=Decimal("424.00"),
            closing_stock=Decimal("42.000"),
        )

    def test_str_uses_barcode_and_name(self):
        self.assertEqual(str(self.product), "1345 — Blue Cotton Cap")

    def test_retail_selling_price_applies_discount(self):
        # 699 with 28.6% off is 499.09.
        self.assertEqual(self.product.retail_selling_price, Decimal("499.09"))

    def test_retail_selling_price_without_discount_is_retail_price(self):
        self.product.given_disc = Decimal("0")
        self.assertEqual(self.product.retail_selling_price, self.product.retail_price)

    def test_final_prices_without_gst_equal_selling_prices(self):
        self.assertEqual(self.product.retail_final_price, self.product.retail_selling_price)
        self.assertEqual(self.product.wholesale_final_price, self.product.wholesale_price)

    def test_gst_is_added_on_top_of_both_tiers(self):
        self.product.gst = Decimal("18")
        # 499.09 (selling price) * 1.18 = 588.9262 -> 588.93
        self.assertEqual(self.product.retail_final_price, Decimal("588.93"))
        # 424.00 * 1.18 = 500.32
        self.assertEqual(self.product.wholesale_final_price, Decimal("500.32"))

    def test_retail_price_with_gst_ignores_discount(self):
        self.product.gst = Decimal("18")
        # 699.00 * 1.18 = 824.82 -- the struck-through "was" price.
        self.assertEqual(self.product.retail_price_with_gst, Decimal("824.82"))

    def test_availability_label(self):
        self.assertEqual(self.product.availability_label, "In Stock")
        self.product.closing_stock = Decimal("0")
        self.assertEqual(self.product.availability_label, "Out of Stock")

    def test_stock_quantity_label_trims_trailing_zeros(self):
        self.assertEqual(self.product.stock_quantity_label, "42 PCS")
        self.product.closing_stock = Decimal("5.500")
        self.assertEqual(self.product.stock_quantity_label, "5.5 PCS")

    def test_blank_alias_is_stored_as_null(self):
        """Two products with no alias must not collide on the unique index."""
        a = Product.objects.create(
            company=self.company, barcode="A1", name="A",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"), alias="",
        )
        b = Product.objects.create(
            company=self.company, barcode="B1", name="B",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"), alias="",
        )
        self.assertIsNone(a.alias)
        self.assertIsNone(b.alias)

    def test_blank_tally_guid_is_stored_as_null(self):
        a = Product.objects.create(
            company=self.company, barcode="A2", name="A",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"), tally_guid="",
        )
        b = Product.objects.create(
            company=self.company, barcode="B2", name="B",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"), tally_guid="",
        )
        self.assertIsNone(a.tally_guid)
        self.assertIsNone(b.tally_guid)

    def test_barcode_is_trimmed_on_save(self):
        p = Product.objects.create(
            company=self.company, barcode="  9001  ", name="X",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"),
        )
        self.assertEqual(p.barcode, "9001")


class DefaultProductImageTests(TestCase):
    """A company's fallback picture, applied at creation and never after."""

    def setUp(self):
        self.company = Company.objects.create(
            name="Imaged Co", slug="imaged-co",
            default_product_image="branding/company-default.jpg",
        )
        self.bare = Company.objects.create(name="Bare Co", slug="bare-co")

    def make(self, company, barcode, **extra):
        return Product.objects.create(
            company=company, barcode=barcode, name="X",
            retail_price=Decimal("1"), wholesale_price=Decimal("1"), **extra
        )

    def test_new_product_without_an_image_gets_the_company_default(self):
        self.assertEqual(
            self.make(self.company, "D1").image.name, "branding/company-default.jpg"
        )

    def test_new_product_keeps_its_own_image(self):
        product = self.make(self.company, "D2", image="products/real-photo.jpg")
        self.assertEqual(product.image.name, "products/real-photo.jpg")

    def test_company_without_a_default_leaves_the_image_empty(self):
        self.assertFalse(self.make(self.bare, "D3").image)

    def test_clearing_an_image_sticks(self):
        """The whole reason this is creation-only: a removal must not be undone."""
        product = self.make(self.company, "D4")
        product.image = ""
        product.save()

        product.refresh_from_db()
        self.assertFalse(product.image)

    def test_changing_the_default_does_not_touch_existing_products(self):
        product = self.make(self.company, "D5")
        self.company.default_product_image = "branding/new-default.jpg"
        self.company.save()

        product.refresh_from_db()
        self.assertEqual(product.image.name, "branding/company-default.jpg")

    def test_an_ordinary_edit_does_not_reapply_the_default(self):
        product = self.make(self.company, "D6")
        product.image = ""
        product.save()

        product.name = "Renamed"
        product.save()

        product.refresh_from_db()
        self.assertFalse(product.image)

    def test_each_company_uses_its_own_default(self):
        other = Company.objects.create(
            name="Other Co", slug="other-co",
            default_product_image="branding/other-default.jpg",
        )
        self.assertEqual(
            self.make(other, "D7").image.name, "branding/other-default.jpg"
        )


class DisplayProfileTests(TestCase):
    def test_resolve_creates_a_profile_when_none_exists(self):
        # Migration 0003 seeds a "default" station so a fresh install has one;
        # clear it to exercise the truly-empty path.
        DisplayProfile.objects.all().delete()
        profile = DisplayProfile.resolve()
        self.assertTrue(profile.pk)
        self.assertTrue(profile.is_default)
        self.assertEqual(DisplayProfile.objects.count(), 1)

    def test_migration_seeds_a_default_station(self):
        """A fresh database must never leave the kiosk without a station."""
        self.assertTrue(DisplayProfile.objects.filter(is_default=True).exists())

    def test_resolve_prefers_requested_slug(self):
        DisplayProfile.objects.create(name="A", slug="a", is_default=True)
        counter = DisplayProfile.objects.create(name="Counter", slug="counter")
        self.assertEqual(DisplayProfile.resolve("counter"), counter)

    def test_resolve_falls_back_to_default_for_unknown_slug(self):
        default = DisplayProfile.objects.create(name="A", slug="a", is_default=True)
        self.assertEqual(DisplayProfile.resolve("nope"), default)

    def test_resolve_skips_inactive_station(self):
        default = DisplayProfile.objects.create(name="A", slug="a", is_default=True)
        DisplayProfile.objects.create(name="Off", slug="off", is_active=False)
        self.assertEqual(DisplayProfile.resolve("off"), default)

    def test_only_one_default_station(self):
        a = DisplayProfile.objects.create(name="A", slug="a", is_default=True)
        DisplayProfile.objects.create(name="B", slug="b", is_default=True)
        a.refresh_from_db()
        self.assertFalse(a.is_default)

    def test_theme_preset_overwrites_colours_on_save(self):
        profile = DisplayProfile.objects.create(
            name="A", slug="a", theme_preset="ocean", color_accent="#000000"
        )
        self.assertEqual(profile.color_accent, "#38e1d0")

    def test_custom_preset_keeps_chosen_colours(self):
        profile = DisplayProfile.objects.create(
            name="A", slug="a", theme_preset="custom", color_accent="#123456"
        )
        self.assertEqual(profile.color_accent, "#123456")

    def test_duration_zero_is_allowed(self):
        """0 means 'hold until the next scan', so it must pass validation."""
        profile = DisplayProfile(
            name="Hold", slug="hold", display_duration_seconds=0
        )
        profile.full_clean()          # raises ValidationError if 0 is rejected
        profile.save()
        self.assertEqual(
            DisplayProfile.objects.get(slug="hold").display_duration_seconds, 0
        )

    def test_negative_duration_is_rejected(self):
        from django.core.exceptions import ValidationError

        profile = DisplayProfile(
            name="Bad", slug="bad", display_duration_seconds=-1
        )
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_wake_phrase_list_normalises(self):
        profile = DisplayProfile.objects.create(
            name="A", slug="a", wake_phrases="  Hi Ahmad \n\nMR CAPS\nhi ahmad\n"
        )
        self.assertEqual(profile.wake_phrase_list, ["hi ahmad", "mr caps"])


class ResolveCompanyTests(TestCase):
    def test_user_company_wins_over_station_company(self):
        user_company = Company.objects.create(name="User Co", slug="user-co")
        station_company = Company.objects.create(name="Station Co", slug="station-co")
        user = get_user_model().objects.create_user("staffer", password="pw")
        UserCompanyAccess.objects.create(user=user, company=user_company)
        station = DisplayProfile.objects.create(
            name="A", slug="a-station", company=station_company
        )
        self.assertEqual(resolve_company(user, station), user_company)

    def test_falls_back_to_station_company(self):
        station_company = Company.objects.create(name="Station Co 2", slug="station-co-2")
        station = DisplayProfile.objects.create(
            name="B", slug="b-station", company=station_company
        )
        self.assertEqual(resolve_company(None, station), station_company)

    def test_none_means_every_company(self):
        station = DisplayProfile.objects.create(name="C", slug="c-station")
        self.assertIsNone(resolve_company(None, station))


class AdminTests(TestCase):
    """Every admin page and filter must render — a broken one blocks staff."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            "admin", "a@example.com", "pw"
        )
        cls.company, _ = Company.objects.update_or_create(
            slug="test-co", defaults={"name": "Test Co", "is_default": True}
        )
        category = Category.objects.create(name="Caps")
        for i in range(3):
            Product.objects.create(
                company=cls.company,
                barcode=f"100{i}",
                name=f"Cap {i}",
                category=category,
                retail_price=Decimal("100") * (i + 1),
                given_disc=Decimal("10") if i == 0 else Decimal("0"),
                wholesale_price=Decimal("90") * (i + 1),
                gst=Decimal("12"),
                hsn="6505",
                closing_stock=Decimal(i),
            )
        cls.profile = DisplayProfile.objects.create(name="Main", slug="main", is_default=True)
        ScanLog.objects.create(code="1345", found=False, station=cls.profile)

    def setUp(self):
        self.client.force_login(self.user)

    def test_changelists_render(self):
        for model in [
            "product", "category", "company", "displayprofile", "scanlog",
            "customersession", "customerportalsettings",
        ]:
            with self.subTest(model=model):
                url = reverse(f"admin:products_{model}_changelist")
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_change_forms_render(self):
        targets = [
            ("product", Product.objects.first().pk),
            ("company", self.company.pk),
            ("displayprofile", self.profile.pk),
        ]
        for model, pk in targets:
            with self.subTest(model=model):
                url = reverse(f"admin:products_{model}_change", args=[pk])
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_product_filters_render(self):
        base = reverse("admin:products_product_changelist")
        filters = [
            "stock_status=in", "stock_status=out", "stock_status=low",
            "has_image=yes", "has_image=no",
            "discounted=yes", "discounted=no",
            "price_band=0-299", "price_band=1500+",
        ]
        for f in filters:
            with self.subTest(filter=f):
                self.assertEqual(self.client.get(f"{base}?{f}").status_code, 200)

    def test_stock_filter_returns_correct_rows(self):
        base = reverse("admin:products_product_changelist")
        out = self.client.get(f"{base}?stock_status=out")
        self.assertEqual(out.context["cl"].result_count, 1)   # only stock 0
        in_stock = self.client.get(f"{base}?stock_status=in")
        self.assertEqual(in_stock.context["cl"].result_count, 2)

    def test_no_image_filter_matches_null_and_empty(self):
        base = reverse("admin:products_product_changelist")
        r = self.client.get(f"{base}?has_image=no")
        self.assertEqual(r.context["cl"].result_count, 3)

    def test_discount_filter_returns_correct_rows(self):
        base = reverse("admin:products_product_changelist")
        discounted = self.client.get(f"{base}?discounted=yes")
        self.assertEqual(discounted.context["cl"].result_count, 1)
        plain = self.client.get(f"{base}?discounted=no")
        self.assertEqual(plain.context["cl"].result_count, 2)

    def test_export_returns_csv(self):
        url = reverse("admin:products_product_export")
        data = {"format": "0"}
        for f in [
            "barcode", "alias", "name", "description", "category",
            "retail_price", "given_disc", "wholesale_price", "gst", "hsn",
            "closing_stock", "unit", "is_active", "tally_guid",
        ]:
            data[f"productresource_{f}"] = "on"
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["Content-Type"])
        body = r.content.decode()
        self.assertIn("barcode", body.splitlines()[0])
        self.assertEqual(len(body.strip().splitlines()) - 1, 3)

    def test_scanlog_is_read_only(self):
        url = reverse("admin:products_scanlog_add")
        self.assertIn(self.client.get(url).status_code, (302, 403))
