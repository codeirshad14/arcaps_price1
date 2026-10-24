"""
API tests.

Covers the contract the kiosk and any Tally-side script depend on: the
``tally_product`` shape (now always carrying both Retail and Wholesale),
multi-code lookup, sync upsert rules, permissions, and full product CRUD.
"""

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from products.models import (
    Category,
    Company,
    DisplayProfile,
    Product,
    ScanLog,
    UserCompanyAccess,
)

TEST_KEY = "test-api-key-1234567890"


@override_settings(TALLY_API_KEY=TEST_KEY)
class ApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Migration 0009 already seeds a company named after the store, so
        # reuse it rather than colliding on the unique slug.
        cls.company, _ = Company.objects.update_or_create(
            slug="ar-caps",
            defaults={"name": "AR Caps", "short_code": "AR", "is_default": True},
        )
        cls.other_company, _ = Company.objects.update_or_create(
            slug="mpa-traders",
            defaults={"name": "MPA Traders", "short_code": "MPA"},
        )
        cls.category = Category.objects.create(name="Caps")

        # Chosen so the discount/GST maths lands on clean numbers: 1000 with
        # 20% off is exactly 800.
        cls.product = Product.objects.create(
            company=cls.company,
            barcode="1345",
            alias="QR-1345-AR",
            name="Blue Cotton Cap",
            description="Premium cotton cap.",
            category=cls.category,
            retail_price=Decimal("1000.00"),
            given_disc=Decimal("20.00"),
            wholesale_price=Decimal("700.00"),
            closing_stock=Decimal("42.000"),
            unit="PCS",
        )
        cls.other = Product.objects.create(
            company=cls.company,
            barcode="1346", name="Black Snapback",
            retail_price=Decimal("650.00"), wholesale_price=Decimal("650.00"),
        )
        cls.inactive = Product.objects.create(
            company=cls.company,
            barcode="1999", name="Retired",
            retail_price=Decimal("10.00"), wholesale_price=Decimal("10.00"),
            is_active=False,
        )
        # Same barcode as cls.product but a different company — the clash the
        # company feature exists to solve.
        cls.rival = Product.objects.create(
            company=cls.other_company,
            barcode="1345", name="MPA Steel Bolt",
            retail_price=Decimal("75.00"), wholesale_price=Decimal("65.00"),
        )

        cls.entrance = DisplayProfile.objects.create(
            name="Entrance", slug="entrance", is_default=True, company=cls.company,
        )
        cls.counter = DisplayProfile.objects.create(
            name="Counter", slug="counter", company=cls.company,
        )
        # A screen that shows every company, for the clash tests.
        cls.all_screen = DisplayProfile.objects.create(
            name="All", slug="all", company=None,
        )

    def auth(self):
        return {"HTTP_X_API_KEY": TEST_KEY}


class LookupTests(ApiTestBase):
    def url(self, **params):
        base = reverse("api:lookup")
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base}?{query}"

    def test_found_payload_matches_contract(self):
        r = self.client.get(self.url(code="1345"))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["found"])

        product = data["tally_product"]
        self.assertEqual(product["name"], "Blue Cotton Cap")
        self.assertEqual(product["price"], "800.00")
        self.assertEqual(product["mrp"], "1000.00")
        self.assertEqual(product["discount_percent"], 20.0)
        self.assertEqual(product["closing_stock"], "42.000")
        self.assertEqual(product["unit"], "PCS")
        self.assertEqual(product["availability"], "In Stock")
        self.assertEqual(product["barcode"], "1345")
        self.assertIsNone(product["image"])       # no file uploaded in tests

        # Both tiers ride along on every lookup — this is the whole point.
        prices = {tier["slug"]: tier for tier in product["prices"]}
        self.assertEqual(prices["retail"]["price"], "800.00")
        self.assertEqual(prices["retail"]["mrp"], "1000.00")
        self.assertEqual(prices["wholesale"]["price"], "700.00")
        self.assertIsNone(prices["wholesale"]["mrp"])

    def test_display_block_present_on_every_response(self):
        for code in ["1345", "does-not-exist"]:
            with self.subTest(code=code):
                display = self.client.get(self.url(code=code)).json()["display"]
                self.assertIn("duration_seconds", display)
                self.assertIn("currency_symbol", display)
                self.assertIn("fields", display)
                self.assertIn("theme", display)
                self.assertIn("speech", display)

    def test_lookup_by_alias_case_insensitive_and_trimmed(self):
        r = self.client.get(self.url(code="%20qr-1345-ar%20"))
        self.assertTrue(r.json()["found"])

    def test_not_found_shape(self):
        data = self.client.get(self.url(code="9999")).json()
        self.assertFalse(data["found"])
        self.assertIsNone(data["tally_product"])
        self.assertEqual(data["code"], "9999")

    def test_empty_code_is_400_and_not_logged(self):
        before = ScanLog.objects.count()
        r = self.client.get(self.url(code="%20%20"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ScanLog.objects.count(), before)

    def test_inactive_product_is_invisible(self):
        self.assertFalse(self.client.get(self.url(code="1999")).json()["found"])

    def test_scan_is_logged_with_station_and_source(self):
        self.client.get(self.url(code="1345", station="counter", source="voice"))
        log = ScanLog.objects.latest("created_at")
        self.assertTrue(log.found)
        self.assertEqual(log.source, "voice")
        self.assertEqual(log.station, self.counter)

    def test_unknown_source_falls_back_to_scanner(self):
        self.client.get(self.url(code="1345", source="telepathy"))
        self.assertEqual(ScanLog.objects.latest("created_at").source, "scanner")

    def test_prices_are_the_same_regardless_of_station(self):
        """Pricing lives on the product now, not on a per-station price list."""
        entrance = self.client.get(self.url(code="1345", station="entrance")).json()
        counter = self.client.get(self.url(code="1345", station="counter")).json()
        self.assertEqual(entrance["tally_product"]["price"], "800.00")
        self.assertEqual(counter["tally_product"]["price"], "800.00")
        self.assertEqual(
            entrance["tally_product"]["prices"], counter["tally_product"]["prices"]
        )

    def test_unknown_station_uses_default(self):
        data = self.client.get(self.url(code="1345", station="nope")).json()
        self.assertEqual(data["display"]["station"]["slug"], "entrance")

    def test_multi_code_lookup(self):
        data = self.client.get(self.url(code="1345,1346,9999")).json()
        self.assertTrue(data["found"])
        self.assertEqual(len(data["tally_products"]), 2)
        self.assertEqual(data["not_found"], ["9999"])

    def test_multi_code_deduplicates(self):
        """Scanning the same item twice must not show it twice.

        After de-duplication only one code remains, so the response uses the
        single-code shape — `tally_product`, no `tally_products` list.
        """
        data = self.client.get(self.url(code="1345,1345")).json()
        self.assertNotIn("tally_products", data)
        self.assertEqual(data["tally_product"]["barcode"], "1345")
        self.assertEqual(ScanLog.objects.filter(code="1345").count(), 1)

    def test_multi_code_is_capped(self):
        codes = ",".join(str(i) for i in range(20))
        data = self.client.get(self.url(code=codes)).json()
        self.assertLessEqual(ScanLog.objects.count(), 8)
        self.assertIn("tally_products", data)

    def test_omit_hidden_drops_switched_off_fields(self):
        self.entrance.show_mrp = False
        self.entrance.show_discount = False
        self.entrance.show_description = False
        self.entrance.show_availability = False
        self.entrance.save()

        data = self.client.get(
            self.url(code="1345", station="entrance", omit_hidden="1")
        ).json()
        keys = set(data["tally_product"])
        self.assertNotIn("mrp", keys)
        self.assertNotIn("discount_percent", keys)
        self.assertNotIn("description", keys)
        self.assertIn("price", keys)
        self.assertIn("prices", keys)
        self.assertIn("barcode", keys)


class MultiCompanyTests(ApiTestBase):
    """The clash this feature exists to solve.

    Company A and company B both sell barcode 1345. A screen locked to one
    company must show only its own; an all-companies screen must show both,
    labelled, rather than silently picking one.
    """

    def url(self, **params):
        base = reverse("api:lookup")
        return base + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    def test_same_barcode_allowed_in_two_companies(self):
        self.assertEqual(Product.objects.filter(barcode="1345").count(), 2)

    def test_duplicate_barcode_within_one_company_is_rejected(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(
                    company=self.company, barcode="1345",
                    name="Dup", retail_price=Decimal("1.00"), wholesale_price=Decimal("1.00"),
                )

    def test_station_locked_to_company_sees_only_its_own(self):
        data = self.client.get(self.url(code="1345", station="entrance")).json()
        self.assertNotIn("tally_products", data)
        self.assertEqual(data["tally_product"]["name"], "Blue Cotton Cap")
        self.assertEqual(data["tally_product"]["company"], "AR Caps")

    def test_all_companies_station_shows_every_match(self):
        data = self.client.get(self.url(code="1345", station="all")).json()
        self.assertTrue(data["found"])
        names = sorted(p["name"] for p in data["tally_products"])
        self.assertEqual(names, ["Blue Cotton Cap", "MPA Steel Bolt"])
        # Each card must say whose product it is, or the screen is ambiguous.
        self.assertEqual(
            sorted(p["company"] for p in data["tally_products"]),
            ["AR Caps", "MPA Traders"],
        )

    def test_station_block_reports_the_scope(self):
        locked = self.client.get(self.url(code="1345", station="entrance")).json()
        self.assertEqual(locked["display"]["station"]["company"], "AR Caps")
        self.assertFalse(locked["display"]["station"]["all_companies"])

        every = self.client.get(self.url(code="1345", station="all")).json()
        self.assertIsNone(every["display"]["station"]["company"])
        self.assertTrue(every["display"]["station"]["all_companies"])

    def test_company_recorded_on_the_scan_log(self):
        self.client.get(self.url(code="1345", station="entrance"))
        self.assertEqual(ScanLog.objects.latest("created_at").company, self.company)

    # --- user-level restriction -------------------------------------------

    def _user_locked_to(self, company, username="locked"):
        user = get_user_model().objects.create_user(username, f"{username}@x.com", "pw12345678")
        UserCompanyAccess.objects.create(user=user, company=company)
        return user

    def test_user_company_narrows_an_all_companies_station(self):
        """A restricted login must not see other companies, whatever station."""
        self.client.force_login(self._user_locked_to(self.other_company))
        data = self.client.get(self.url(code="1345", station="all")).json()
        self.assertNotIn("tally_products", data)
        self.assertEqual(data["tally_product"]["company"], "MPA Traders")

    def test_user_company_beats_the_station_company(self):
        self.client.force_login(self._user_locked_to(self.other_company))
        # The station says AR Caps; the user says MPA. The user wins.
        data = self.client.get(self.url(code="1345", station="entrance")).json()
        self.assertEqual(data["tally_product"]["company"], "MPA Traders")

    def test_unrestricted_user_sees_the_station_scope(self):
        user = get_user_model().objects.create_user("free", "f@x.com", "pw12345678")
        self.client.force_login(user)
        data = self.client.get(self.url(code="1345", station="all")).json()
        self.assertEqual(len(data["tally_products"]), 2)

    def test_station_picker_hides_other_companies(self):
        self.client.force_login(self._user_locked_to(self.other_company))
        slugs = [s["slug"] for s in self.client.get(reverse("api:stations")).json()["stations"]]
        self.assertIn("all", slugs)            # shared screens stay offered
        self.assertNotIn("entrance", slugs)    # AR Caps screen must not

    def test_product_list_is_scoped_for_a_restricted_user(self):
        self.client.force_login(self._user_locked_to(self.other_company))
        data = self.client.get(reverse("api:product-list")).json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["company_name"], "MPA Traders")

    # --- sync --------------------------------------------------------------

    def test_sync_targets_the_named_company(self):
        payload = {
            "tally_product": {
                "barcode": "1345", "name": "MPA Renamed",
                "retail_price": "80.00", "company": "mpa-traders",
            }
        }
        r = self.client.post(
            reverse("api:tally-sync"), data=json.dumps(payload),
            content_type="application/json", **self.auth(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"][0]["status"], "updated")

        self.rival.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.rival.name, "MPA Renamed")
        # Crucially, the other company's product with the same code is intact.
        self.assertEqual(self.product.name, "Blue Cotton Cap")

    def test_sync_defaults_to_the_default_company(self):
        payload = {"tally_product": {"barcode": "7777", "name": "New", "retail_price": "5.00"}}
        self.client.post(
            reverse("api:tally-sync"), data=json.dumps(payload),
            content_type="application/json", **self.auth(),
        )
        self.assertEqual(Product.objects.get(barcode="7777").company, self.company)

    def test_sync_rejects_unknown_company(self):
        payload = {
            "tally_product": {
                "barcode": "7778", "name": "X", "retail_price": "1.00", "company": "ghost-co",
            }
        }
        r = self.client.post(
            reverse("api:tally-sync"), data=json.dumps(payload),
            content_type="application/json", **self.auth(),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("company", r.json()["results"][0]["errors"])

    # --- CRUD --------------------------------------------------------------

    def test_crud_create_accepts_a_company_slug(self):
        r = self.client.post(
            reverse("api:product-list"),
            data=json.dumps({
                "company": "mpa-traders", "barcode": "1346",
                "name": "MPA Bolt 1346", "retail_price": "12.00", "wholesale_price": "12.00",
            }),
            content_type="application/json", **self.auth(),
        )
        # 1346 already exists for AR Caps; for MPA it is free.
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["company_name"], "MPA Traders")

    def test_crud_duplicate_within_company_reports_on_the_field(self):
        r = self.client.post(
            reverse("api:product-list"),
            data=json.dumps({
                "company": "ar-caps", "barcode": "1345",
                "name": "Dup", "retail_price": "1.00", "wholesale_price": "1.00",
            }),
            content_type="application/json", **self.auth(),
        )
        self.assertEqual(r.status_code, 400)
        # Reported against `barcode`, not as a vague non_field_error.
        self.assertIn("barcode", r.json()["errors"])


class SettingsAndStationsTests(ApiTestBase):
    def test_settings_endpoint(self):
        data = self.client.get(reverse("api:settings")).json()
        self.assertIn("display", data)
        self.assertEqual(data["display"]["station"]["slug"], "entrance")

    def test_settings_respects_station_param(self):
        url = reverse("api:settings") + "?station=counter"
        self.assertEqual(
            self.client.get(url).json()["display"]["station"]["slug"], "counter"
        )

    def test_stations_endpoint_lists_active_only(self):
        DisplayProfile.objects.create(name="Off", slug="off", is_active=False)
        slugs = [s["slug"] for s in self.client.get(reverse("api:stations")).json()["stations"]]
        self.assertIn("entrance", slugs)
        self.assertNotIn("off", slugs)

    def test_duration_zero_survives_serialisation(self):
        """A 0 must reach the frontend as 0, not be dropped or defaulted.

        This is the exact shape of bug that a truthiness check causes: 0 is
        falsy, so a careless `value or default` would turn "hold forever"
        into "10 seconds".
        """
        self.entrance.display_duration_seconds = 0
        self.entrance.save()

        display = self.client.get(reverse("api:settings")).json()["display"]
        self.assertEqual(display["duration_seconds"], 0)
        self.assertIsInstance(display["duration_seconds"], int)

        # And on every lookup, since that is what the kiosk actually reads.
        lookup = self.client.get(reverse("api:lookup") + "?code=1345").json()
        self.assertEqual(lookup["display"]["duration_seconds"], 0)

    def test_speech_block_shape(self):
        speech = self.client.get(reverse("api:settings")).json()["display"]["speech"]
        self.assertIn("enabled", speech)
        self.assertIn("language", speech)
        self.assertIn("template", speech)
        self.assertEqual(speech["rate"], 0.95)
        self.assertEqual(speech["pitch"], 1.05)
        # A soft female voice is the default for the price announcement.
        self.assertEqual(speech["voice"], "female")

    def test_speech_voice_preference_is_sent(self):
        self.entrance.speech_voice_preference = "male"
        self.entrance.speech_pitch = 90
        self.entrance.speech_rate = 120
        self.entrance.save()

        speech = self.client.get(
            reverse("api:settings") + "?station=entrance"
        ).json()["display"]["speech"]
        self.assertEqual(speech["voice"], "male")
        self.assertEqual(speech["pitch"], 0.9)
        self.assertEqual(speech["rate"], 1.2)


@override_settings(TALLY_API_KEY=TEST_KEY)
class TallySyncTests(ApiTestBase):
    def post(self, payload, **extra):
        return self.client.post(
            reverse("api:tally-sync"),
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    def test_requires_api_key(self):
        r = self.post({"tally_product": {"barcode": "1", "name": "x", "retail_price": "1"}})
        self.assertEqual(r.status_code, 401)

    def test_wrong_api_key(self):
        r = self.post(
            {"tally_product": {"barcode": "1", "name": "x", "retail_price": "1"}},
            HTTP_X_API_KEY="wrong",
        )
        self.assertEqual(r.status_code, 401)

    @override_settings(TALLY_API_KEY="")
    def test_unset_key_fails_closed(self):
        r = self.post({"tally_product": {"barcode": "1", "name": "x", "retail_price": "1"}})
        self.assertEqual(r.status_code, 401)

    def test_create_single(self):
        r = self.post(
            {"tally_product": {"barcode": "7001", "name": "New", "retail_price": "10.00"}},
            **self.auth(),
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["summary"]["created"], 1)
        self.assertTrue(Product.objects.filter(barcode="7001").exists())

    def test_upsert_on_barcode(self):
        payload = {"tally_product": {"barcode": "1346", "name": "Renamed", "retail_price": "1.00"}}
        r = self.post(payload, **self.auth())
        self.assertEqual(r.json()["summary"]["updated"], 1)
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Renamed")

    def test_matches_on_part_number_not_guid(self):
        """The part number is the identity; a GUID never redirects the match.

        This is the opposite of how the sync first behaved. Tally reuses a
        GUID across stock items, so preferring it merged unrelated products
        into a single row.
        """
        self.other.tally_guid = "guid-1"
        self.other.save()

        payload = {
            "tally_product": {
                "barcode": "9100",           # a part number nothing uses yet
                "name": "Brand New Item",
                "retail_price": "5.00",
                "tally_guid": "guid-1",      # already on self.other
            }
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["summary"]["created"], 1)

        # The existing row is untouched: the GUID did not pull the update onto it.
        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "Black Snapback")
        self.assertEqual(self.other.barcode, "1346")

        created = Product.objects.get(company=self.company, barcode="9100")
        self.assertEqual(created.name, "Brand New Item")

    def test_duplicate_tally_guid_is_allowed(self):
        """Tally hands the same GUID to several stock items; that must sync."""
        payload = {
            "tally_product": [
                {"barcode": "9201", "name": "One", "retail_price": "1.00",
                 "tally_guid": "shared-guid"},
                {"barcode": "9202", "name": "Two", "retail_price": "2.00",
                 "tally_guid": "shared-guid"},
                {"barcode": "9203", "name": "Three", "retail_price": "3.00",
                 "tally_guid": "shared-guid"},
            ]
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(r.json()["summary"]["errors"], 0)
        self.assertEqual(r.json()["summary"]["created"], 3)
        self.assertEqual(
            Product.objects.filter(tally_guid="shared-guid").count(), 3
        )

    def test_stock_is_updated_on_an_existing_part(self):
        payload = {
            "tally_product": {
                "barcode": "1345",
                "name": "Blue Cotton Cap",
                "retail_price": "1000.00",
                "closing_stock": "7.500",
            }
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.json()["summary"]["updated"], 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.closing_stock, Decimal("7.500"))

    def test_sync_gives_a_new_product_the_company_default_image(self):
        """Tally sends no images, so a synced-in product would otherwise be blank."""
        self.company.default_product_image = "branding/ar-default.jpg"
        self.company.save(update_fields=["default_product_image"])

        payload = {"tally_product": {"barcode": "8100", "name": "Fresh", "retail_price": "9.00"}}
        self.assertEqual(self.post(payload, **self.auth()).status_code, 200)
        self.assertEqual(
            Product.objects.get(barcode="8100").image.name, "branding/ar-default.jpg"
        )

    def test_sync_does_not_apply_the_default_to_an_existing_product(self):
        """An update is not a creation — a product left without a picture stays so."""
        self.company.default_product_image = "branding/ar-default.jpg"
        self.company.save(update_fields=["default_product_image"])

        payload = {"tally_product": {"barcode": "1346", "name": "Renamed", "retail_price": "1.00"}}
        self.post(payload, **self.auth())

        self.other.refresh_from_db()
        self.assertFalse(self.other.image)

    def test_crud_create_gets_the_company_default_image(self):
        self.company.default_product_image = "branding/ar-default.jpg"
        self.company.save(update_fields=["default_product_image"])

        r = self.client.post(
            reverse("api:product-list"),
            data=json.dumps({
                "barcode": "8101", "name": "Via CRUD",
                "retail_price": "5.00", "wholesale_price": "5.00",
            }),
            content_type="application/json", **self.auth(),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(
            Product.objects.get(barcode="8101").image.name, "branding/ar-default.jpg"
        )

    def test_sync_never_overwrites_the_image(self):
        """A sync updates stock and prices but must leave the photo alone.

        Images are uploaded in the admin and have no equivalent in Tally, so
        a sync that cleared them would wipe the catalogue's photos on the
        first run.
        """
        self.product.image = "products/blue-cap.jpg"
        self.product.save(update_fields=["image"])

        payload = {
            "tally_product": {
                "barcode": "1345",
                "name": "Blue Cotton Cap",
                "retail_price": "1100.00",
                "closing_stock": "3.000",
                # Even when Tally sends something here, it is ignored.
                "image": "",
            }
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.json()["summary"]["updated"], 1)

        self.product.refresh_from_db()
        self.assertEqual(self.product.image, "products/blue-cap.jpg")
        self.assertEqual(self.product.closing_stock, Decimal("3.000"))
        self.assertEqual(self.product.retail_price, Decimal("1100.00"))

    def test_same_part_number_in_two_companies_stays_separate(self):
        """Part numbers are unique per company, not globally."""
        payload = {
            "tally_product": {
                "barcode": "1345",
                "company": "mpa-traders",
                "name": "MPA Steel Bolt Updated",
                "retail_price": "99.00",
            }
        }
        self.post(payload, **self.auth())
        self.rival.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.rival.name, "MPA Steel Bolt Updated")
        self.assertEqual(self.product.name, "Blue Cotton Cap")

    def test_last_synced_at_is_stamped(self):
        self.post(
            {"tally_product": {"barcode": "1346", "name": "x", "retail_price": "1.00"}},
            **self.auth(),
        )
        self.other.refresh_from_db()
        self.assertIsNotNone(self.other.last_synced_at)

    def test_partial_failure_returns_207(self):
        payload = {
            "tally_product": [
                {"barcode": "8001", "name": "Good", "retail_price": "10.00"},
                {"barcode": "8002", "name": "No price"},
            ]
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.status_code, 207)
        summary = r.json()["summary"]
        self.assertEqual(summary["created"], 1)
        self.assertEqual(summary["errors"], 1)

    def test_all_failures_return_400(self):
        payload = {"tally_product": [{"barcode": "8003", "name": "No price"}]}
        self.assertEqual(self.post(payload, **self.auth()).status_code, 400)

    def test_given_disc_out_of_range_rejected(self):
        payload = {
            "tally_product": {
                "barcode": "8004", "name": "Bad", "retail_price": "500.00", "given_disc": "150",
            }
        }
        r = self.post(payload, **self.auth())
        self.assertEqual(r.status_code, 400)
        self.assertIn("given_disc", r.json()["results"][0]["errors"])

    def test_missing_root_key(self):
        self.assertEqual(self.post({}, **self.auth()).status_code, 400)

    def test_empty_list(self):
        self.assertEqual(self.post({"tally_product": []}, **self.auth()).status_code, 400)

    def test_non_object_record(self):
        r = self.post({"tally_product": ["nope"]}, **self.auth())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["results"][0]["status"], "error")

    def test_category_created_on_demand(self):
        payload = {
            "tally_product": {
                "barcode": "8005", "name": "X", "retail_price": "1.00", "category": "Brand New"
            }
        }
        self.post(payload, **self.auth())
        self.assertTrue(Category.objects.filter(name="Brand New").exists())

    def test_wholesale_price_defaults_to_retail_when_omitted(self):
        """No wholesale rate supplied: quote retail, same as the old
        per-price-list fallback for an unpriced tier."""
        payload = {"tally_product": {"barcode": "8007", "name": "X", "retail_price": "200.00"}}
        self.post(payload, **self.auth())
        self.assertEqual(Product.objects.get(barcode="8007").wholesale_price, Decimal("200.00"))

    def test_wholesale_price_synced_explicitly(self):
        payload = {
            "tally_product": {
                "barcode": "8008", "name": "X", "retail_price": "200.00",
                "wholesale_price": "150.00",
            }
        }
        self.post(payload, **self.auth())
        self.assertEqual(Product.objects.get(barcode="8008").wholesale_price, Decimal("150.00"))


@override_settings(TALLY_API_KEY=TEST_KEY)
class ProductCrudTests(ApiTestBase):
    def list_url(self):
        return reverse("api:product-list")

    def detail_url(self, pk):
        return reverse("api:product-detail", args=[pk])

    # --- read -------------------------------------------------------------
    def test_list_is_open(self):
        r = self.client.get(self.list_url())
        self.assertEqual(r.status_code, 200)
        # 1345 + 1346 + the rival company's 1345; the inactive one is excluded.
        self.assertEqual(r.json()["count"], 3)

    def test_list_all_includes_inactive(self):
        r = self.client.get(self.list_url() + "?all=1")
        self.assertEqual(r.json()["count"], 4)

    def test_search(self):
        r = self.client.get(self.list_url() + "?search=snapback")
        self.assertEqual(r.json()["count"], 1)

    def test_detail_is_open(self):
        self.assertEqual(self.client.get(self.detail_url(self.product.pk)).status_code, 200)

    def test_detail_reachable_for_inactive(self):
        """Otherwise a deactivated product could never be re-activated."""
        self.assertEqual(self.client.get(self.detail_url(self.inactive.pk)).status_code, 200)

    # --- write permissions -------------------------------------------------
    def test_write_requires_key(self):
        cases = [
            ("post", self.list_url(), {"barcode": "x", "name": "x", "retail_price": "1"}),
            ("put", self.detail_url(self.product.pk),
             {"barcode": "1345", "name": "x", "retail_price": "1"}),
            ("patch", self.detail_url(self.product.pk), {"retail_price": "1"}),
            ("delete", self.detail_url(self.product.pk), None),
        ]
        for method, url, payload in cases:
            with self.subTest(method=method):
                fn = getattr(self.client, method)
                r = fn(url, data=json.dumps(payload) if payload else None,
                       content_type="application/json")
                self.assertEqual(r.status_code, 401)

    def test_staff_session_may_write(self):
        """The browsable API needs this, or it renders no create form."""
        user = get_user_model().objects.create_superuser("s", "s@x.com", "pw")
        self.client.force_login(user)
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "barcode": "7100", "name": "Session",
                "retail_price": "5.00", "wholesale_price": "5.00",
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)

    # --- create ------------------------------------------------------------
    def test_create(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "barcode": "7001", "name": "New Cap",
                "retail_price": "400.00", "given_disc": "37.5", "wholesale_price": "300.00",
                "category": self.category.pk,
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["given_disc"], "37.50")
        self.assertEqual(body["category_name"], "Caps")

    # --- batch create ------------------------------------------------------
    def test_create_batch_wrapped_in_product_key(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "product": [
                    {"barcode": "7201", "name": "Batch One",
                     "retail_price": "10.00", "wholesale_price": "10.00"},
                    {"barcode": "7202", "name": "Batch Two",
                     "retail_price": "20.00", "wholesale_price": "18.00",
                     "given_disc": "10", "unit": "PCS"},
                ]
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual(body["summary"], {"created": 2, "errors": 0, "total": 2})
        self.assertEqual(body["results"][0]["barcode"], "7201")
        self.assertTrue(Product.objects.filter(barcode="7202").exists())

    def test_create_single_wrapped_in_product_key(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "product": {
                    "barcode": "7203", "name": "One",
                    "retail_price": "5.00", "wholesale_price": "5.00",
                }
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["summary"]["created"], 1)

    def test_batch_partial_failure_returns_207(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "product": [
                    {"barcode": "7204", "name": "Good",
                     "retail_price": "10.00", "wholesale_price": "10.00"},
                    {"barcode": "1345", "name": "Duplicate",
                     "retail_price": "10.00", "wholesale_price": "10.00"},
                    {"name": "No barcode", "retail_price": "10.00", "wholesale_price": "10.00"},
                ]
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 207)
        body = r.json()
        self.assertEqual(body["summary"]["created"], 1)
        self.assertEqual(body["summary"]["errors"], 2)
        # The good row still landed despite its neighbours failing.
        self.assertTrue(Product.objects.filter(barcode="7204").exists())
        self.assertIn("barcode", body["results"][1]["errors"])

    def test_batch_all_failed_returns_400(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({"product": [{"name": "No barcode", "retail_price": "1.00"}]}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)

    def test_batch_empty_list_rejected(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({"product": []}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)

    def test_batch_non_object_record(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({"product": ["nope"]}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["results"][0]["status"], "error")

    def test_batch_still_requires_auth(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "product": [{"barcode": "x", "name": "x",
                             "retail_price": "1", "wholesale_price": "1"}]
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    def test_flat_create_still_works(self):
        """The wrapper must not break the plain single-object form."""
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "barcode": "7205", "name": "Flat",
                "retail_price": "9.00", "wholesale_price": "9.00",
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["barcode"], "7205")

    def test_create_duplicate_barcode_rejected(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "barcode": "1345", "name": "Dup",
                "retail_price": "1.00", "wholesale_price": "1.00",
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)

    def test_create_missing_barcode_rejected(self):
        r = self.client.post(
            self.list_url(),
            data=json.dumps({
                "name": "No barcode", "retail_price": "1.00", "wholesale_price": "1.00",
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)

    # --- update ------------------------------------------------------------
    def test_put_replaces_omitted_fields(self):
        r = self.client.put(
            self.detail_url(self.product.pk),
            data=json.dumps({
                "barcode": "1345", "name": "Only Name",
                "retail_price": "300.00", "wholesale_price": "300.00",
            }),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # PUT describes the whole resource: anything omitted resets.
        self.assertIsNone(body["category"])
        self.assertEqual(body["given_disc"], "0.00")
        self.assertIsNone(body["alias"])
        self.assertEqual(body["description"], "")
        self.assertEqual(body["unit"], "PCS")
        self.assertEqual(body["hsn"], "")

    def test_patch_merges(self):
        r = self.client.patch(
            self.detail_url(self.product.pk),
            data=json.dumps({"retail_price": "1200.00"}),
            content_type="application/json",
            **self.auth(),
        )
        body = r.json()
        self.assertEqual(body["retail_price"], "1200.00")
        self.assertEqual(body["category_name"], "Caps")   # untouched
        self.assertEqual(body["given_disc"], "20.00")      # untouched

    def test_patch_given_disc_out_of_range_rejected(self):
        r = self.client.patch(
            self.detail_url(self.product.pk),
            data=json.dumps({"given_disc": "150"}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(r.status_code, 400)

    # --- delete ------------------------------------------------------------
    def test_delete(self):
        r = self.client.delete(self.detail_url(self.other.pk), **self.auth())
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=self.other.pk).exists())

    def test_delete_missing_is_404(self):
        r = self.client.delete(self.detail_url(999999), **self.auth())
        self.assertEqual(r.status_code, 404)

    def test_delete_keeps_scan_history(self):
        ScanLog.objects.create(code="1346", found=True, product=self.other)
        self.client.delete(self.detail_url(self.other.pk), **self.auth())
        log = ScanLog.objects.get(code="1346")
        self.assertIsNone(log.product)      # SET_NULL, history preserved


@override_settings(KIOSK_REQUIRE_LOGIN=False)
class KioskPageTests(ApiTestBase):
    def test_page_renders(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_page_renders_per_station(self):
        r = self.client.get("/?station=counter")
        self.assertEqual(r.status_code, 200)
        # The counter station's theme must be inlined for the first paint.
        self.assertIn("--accent:", r.content.decode())

    def test_no_template_comment_leaks_into_html(self):
        """A multi-line {# #} comment renders as visible text — guard against it."""
        body = self.client.get("/").content.decode()
        self.assertNotIn("#}", body)
        self.assertNotIn("Live speech transcript", body)
        self.assertNotIn("scanner sink", body)

    def test_initial_display_is_embedded(self):
        body = self.client.get("/").content.decode()
        self.assertIn('id="initial-display"', body)

    def test_assets_are_cache_busted(self):
        """A showroom screen left open must pick up new CSS on reload."""
        body = self.client.get("/").content.decode()
        self.assertRegex(body, r"kiosk\.css\?v=\d+")
        self.assertRegex(body, r"kiosk\.js\?v=\d+")

    def test_page_is_not_cached(self):
        response = self.client.get("/")
        self.assertIn("no-store", response["Cache-Control"])


class LoginTests(ApiTestBase):
    """Signing in once per showroom PC, and staying signed in."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = get_user_model().objects.create_user(
            "counter_staff", "s@example.com", "kiosk-pass-123"
        )

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_kiosk_redirects_to_login_when_required(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_kiosk_preserves_station_through_login_redirect(self):
        r = self.client.get("/?station=counter")
        self.assertIn("station%3Dcounter", r["Location"])

    @override_settings(KIOSK_REQUIRE_LOGIN=False)
    def test_kiosk_open_when_login_not_required(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_login_page_renders_with_station_theme(self):
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("--accent:", body)
        self.assertIn("Remember this system", body)

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_login_grants_access(self):
        r = self.client.post(
            "/login/",
            {"username": "counter_staff", "password": "kiosk-pass-123",
             "remember_device": "on"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_remember_device_gives_a_long_session(self):
        """The whole point: a showroom PC must not be asked again next week."""
        self.client.post(
            "/login/",
            {"username": "counter_staff", "password": "kiosk-pass-123",
             "remember_device": "on"},
        )
        # A year, allowing for the second or two the request took.
        self.assertGreater(self.client.session.get_expiry_age(), 60 * 60 * 24 * 364)

    def test_without_remember_session_ends_with_browser(self):
        self.client.post(
            "/login/",
            {"username": "counter_staff", "password": "kiosk-pass-123"},
        )
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_bad_password_is_rejected(self):
        r = self.client.post(
            "/login/", {"username": "counter_staff", "password": "wrong"}
        )
        self.assertEqual(r.status_code, 200)          # re-rendered with errors
        self.assertFalse(r.context["form"].is_valid())

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_logout_ends_access(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.client.post("/logout/")
        self.assertEqual(self.client.get("/").status_code, 302)

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_logout_control_is_on_the_kiosk_page(self):
        """Without it there is no way to sign out or switch user."""
        self.client.force_login(self.staff)
        body = self.client.get("/").content.decode()
        self.assertIn('action="/logout/"', body)
        # Django 5 removed GET logout, so it must be a POST form.
        self.assertIn('method="post"', body)
        self.assertIn("csrfmiddlewaretoken", body)
        # And it should say who is signed in.
        self.assertIn("counter_staff", body)

    def test_logout_must_be_post(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertEqual(self.client.post("/logout/").status_code, 302)

    def test_kiosk_only_user_cannot_reach_admin(self):
        """A counter login must not be able to edit prices."""
        kiosk_user = get_user_model().objects.create_user(
            "counter1", "c@example.com", "kiosk-pass-123"
        )
        self.assertFalse(kiosk_user.is_staff)
        self.client.force_login(kiosk_user)

        with override_settings(KIOSK_REQUIRE_LOGIN=True):
            self.assertEqual(self.client.get("/").status_code, 200)

        admin = self.client.get("/admin/")
        self.assertIn(admin.status_code, (302, 403))

    def test_kiosk_only_user_cannot_write_through_the_api(self):
        kiosk_user = get_user_model().objects.create_user(
            "counter2", "c2@example.com", "kiosk-pass-123"
        )
        self.client.force_login(kiosk_user)
        r = self.client.post(
            reverse("api:product-list"),
            data=json.dumps({
                "barcode": "9999", "name": "Nope",
                "retail_price": "1.00", "wholesale_price": "1.00",
            }),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    @override_settings(KIOSK_REQUIRE_LOGIN=True)
    def test_api_stays_open_for_the_kiosk_javascript(self):
        """The page may need a login, but its own fetches must still work."""
        self.assertEqual(self.client.get(reverse("api:settings")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("api:lookup") + "?code=1345").status_code, 200
        )
