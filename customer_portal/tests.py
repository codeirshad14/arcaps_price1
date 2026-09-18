"""
Customer QR portal tests.

Concentrated on the two things that are easy to get wrong here: who may reach
each page, and the rule that ending a visit must never hand the entry token
back. The token is the only thing keeping the portal off the open web, so a
page that leaks it quietly undoes the whole design.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from products.models import CustomerPortalSettings, CustomerSession, DisplayProfile


class PortalTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        DisplayProfile.objects.update_or_create(
            slug="portal", defaults={"name": "Portal", "is_default": True}
        )
        cls.settings_row = CustomerPortalSettings.get_solo()

    def entry_url(self) -> str:
        return reverse("customer:landing", args=[self.settings_row.access_token])

    def sign_in(self):
        return self.client.post(
            self.entry_url(), {"name": "Asha", "mobile_number": "9876543210"}
        )

    def expire_current_visit(self) -> CustomerSession:
        session = CustomerSession.objects.latest("started_at")
        session.expires_at = timezone.now() - timedelta(seconds=1)
        session.save(update_fields=["expires_at"])
        return session


class EntryTests(PortalTestBase):
    def test_signing_in_starts_a_visit_and_reaches_the_check_page(self):
        self.assertRedirects(self.sign_in(), reverse("customer:check"))
        self.assertEqual(self.client.get(reverse("customer:check")).status_code, 200)
        self.assertEqual(CustomerSession.objects.count(), 1)

    def test_bare_customer_url_sends_the_public_to_the_closing_page(self):
        self.assertRedirects(
            self.client.get("/customer/"), reverse("customer:finished")
        )

    def test_bare_customer_url_sends_staff_to_the_poster(self):
        staff = get_user_model().objects.create_superuser("s", "s@example.com", "pw")
        self.client.force_login(staff)
        self.assertRedirects(
            self.client.get("/customer/"), reverse("customer:poster")
        )

    def test_check_page_is_not_cached(self):
        self.sign_in()
        response = self.client.get(reverse("customer:check"))
        self.assertIn("no-store", response["Cache-Control"])


class WrongAddressTests(PortalTestBase):
    """A customer holding a phone should never meet a 404."""

    def assertGoesToClosingPage(self, url):
        self.assertRedirects(self.client.get(url), reverse("customer:finished"))

    def test_mistyped_path(self):
        self.assertGoesToClosingPage("/customer/chekc/")

    def test_nonsense_path(self):
        self.assertGoesToClosingPage("/customer/anything/at/all/")

    def test_well_formed_but_wrong_code(self):
        """A reprinted poster leaves old saved links looking exactly like this."""
        self.assertGoesToClosingPage(reverse("customer:landing", args=["0" * 32]))

    def test_code_of_the_wrong_shape(self):
        self.assertGoesToClosingPage("/customer/not-a-real-token/")

    def test_switched_off_portal(self):
        self.settings_row.is_enabled = False
        self.settings_row.save()
        self.assertGoesToClosingPage(self.entry_url())

    def test_a_wrong_code_is_indistinguishable_from_a_wrong_path(self):
        """Otherwise the difference would confirm which codes are real."""
        wrong_code = self.client.get(reverse("customer:landing", args=["0" * 32]))
        wrong_path = self.client.get("/customer/nope/")
        self.assertEqual(wrong_code.status_code, wrong_path.status_code)
        self.assertEqual(wrong_code["Location"], wrong_path["Location"])

    # --- the real pages must still be reachable ----------------------------

    def test_the_real_entry_address_still_works(self):
        self.assertEqual(self.client.get(self.entry_url()).status_code, 200)

    def test_missing_trailing_slash_still_reaches_the_sign_in_form(self):
        """The catch-all pre-empts Django's APPEND_SLASH, so it does that job.

        Without it, the shop's own address typed without its final slash would
        send a customer to the closing page instead of letting them in.
        """
        r = self.client.get(self.entry_url().rstrip("/"))
        self.assertRedirects(r, self.entry_url())

    def test_missing_trailing_slash_on_a_real_page(self):
        self.assertRedirects(
            self.client.get("/customer/check"), reverse("customer:check"),
            target_status_code=302,
        )

    def test_named_routes_are_not_swallowed(self):
        self.sign_in()
        for name in ["check", "finished", "status", "qr"]:
            with self.subTest(route=name):
                self.assertEqual(
                    self.client.get(reverse(f"customer:{name}")).status_code, 200
                )


class VisitEndsTests(PortalTestBase):
    """Timing out and finishing early both land on the same dead end."""

    def test_expired_visit_is_sent_to_the_finished_page(self):
        self.sign_in()
        self.expire_current_visit()
        self.assertRedirects(
            self.client.get(reverse("customer:check")), reverse("customer:finished")
        )

    def test_finishing_early_is_sent_to_the_finished_page(self):
        self.sign_in()
        self.assertRedirects(
            self.client.post(reverse("customer:logout")), reverse("customer:finished")
        )

    def test_finishing_early_stamps_the_visit_as_ended(self):
        self.sign_in()
        self.client.post(reverse("customer:logout"))
        self.assertIsNotNone(CustomerSession.objects.latest("started_at").ended_at)

    def test_finished_page_renders_for_anyone(self):
        """It is reached with no session at all, so it cannot require one."""
        r = self.client.get(reverse("customer:finished"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Scan to log in again", r.content.decode())

    def test_finished_page_never_leaks_the_entry_token(self):
        """The whole point: the poster is the only way back in."""
        self.sign_in()
        self.expire_current_visit()

        body = self.client.get(reverse("customer:finished")).content.decode()
        self.assertNotIn(self.settings_row.access_token, body)
        self.assertNotIn(self.settings_row.entry_path, body)

    def test_finished_page_is_not_cached(self):
        response = self.client.get(reverse("customer:finished"))
        self.assertIn("no-store", response["Cache-Control"])

    def test_expired_visit_cannot_look_up_a_price_as_that_customer(self):
        """The session row must stop tagging scans once it has expired."""
        self.sign_in()
        session = self.expire_current_visit()
        self.client.get(reverse("api:lookup") + "?code=nothing-here")
        self.assertEqual(session.scan_logs.count(), 0)

    def test_scanning_the_poster_again_starts_a_fresh_visit(self):
        self.sign_in()
        self.expire_current_visit()

        self.assertRedirects(self.sign_in(), reverse("customer:check"))
        self.assertEqual(CustomerSession.objects.count(), 2)
        self.assertEqual(self.client.get(reverse("customer:check")).status_code, 200)


class RotateAccessTokenTests(PortalTestBase):
    """Changing the QR code, which is the shop's answer to a copied address."""

    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            "rotator", "r@example.com", "pw"
        )

    def rotate_url(self) -> str:
        return reverse(
            "admin:products_customerportalsettings_rotate_token",
            args=[self.settings_row.pk],
        )

    def test_rotation_issues_a_different_address(self):
        before = self.settings_row.access_token
        self.settings_row.rotate_access_token()
        self.assertNotEqual(self.settings_row.access_token, before)

    def test_the_old_address_stops_working(self):
        """Anyone holding the old link is told to scan, not let in."""
        old_url = self.entry_url()
        self.settings_row.rotate_access_token()
        self.assertRedirects(
            self.client.get(old_url), reverse("customer:finished")
        )

    def test_the_new_address_works(self):
        self.settings_row.rotate_access_token()
        self.assertEqual(self.client.get(self.entry_url()).status_code, 200)

    def test_rotation_signs_out_a_visit_in_progress(self):
        self.sign_in()
        self.assertEqual(self.client.get(reverse("customer:check")).status_code, 200)

        ended = self.settings_row.rotate_access_token()

        self.assertEqual(ended, 1)
        self.assertRedirects(
            self.client.get(reverse("customer:check")), reverse("customer:finished")
        )

    def test_rotation_leaves_everything_else_alone(self):
        """Only the address is suspect, so only the address changes."""
        self.settings_row.title = "Check Our Prices"
        self.settings_row.session_minutes = 25
        self.settings_row.submit_label = "Go"
        self.settings_row.save()

        self.settings_row.rotate_access_token()
        self.settings_row.refresh_from_db()

        self.assertEqual(self.settings_row.title, "Check Our Prices")
        self.assertEqual(self.settings_row.session_minutes, 25)
        self.assertEqual(self.settings_row.submit_label, "Go")

    def test_rotation_is_recorded(self):
        self.assertIsNone(self.settings_row.access_token_rotated_at)
        self.settings_row.rotate_access_token()
        self.assertIsNotNone(self.settings_row.access_token_rotated_at)

    def test_already_ended_visits_are_not_recounted(self):
        self.sign_in()
        self.client.post(reverse("customer:logout"))
        self.assertEqual(self.settings_row.rotate_access_token(), 0)

    # --- through the admin -------------------------------------------------

    def test_admin_get_confirms_and_changes_nothing(self):
        """A link being followed must never rotate the address on its own."""
        self.client.force_login(self.admin)
        before = self.settings_row.access_token

        r = self.client.get(self.rotate_url())

        self.assertEqual(r.status_code, 200)
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.access_token, before)

    def test_admin_post_rotates(self):
        self.client.force_login(self.admin)
        before = self.settings_row.access_token

        r = self.client.post(self.rotate_url())

        self.assertEqual(r.status_code, 302)
        self.settings_row.refresh_from_db()
        self.assertNotEqual(self.settings_row.access_token, before)

    def test_rotation_requires_a_staff_login(self):
        before = self.settings_row.access_token
        r = self.client.post(self.rotate_url())

        self.assertIn(r.status_code, (302, 403))
        self.settings_row.refresh_from_db()
        self.assertEqual(self.settings_row.access_token, before)

    def test_change_form_offers_the_button(self):
        self.client.force_login(self.admin)
        url = reverse(
            "admin:products_customerportalsettings_change", args=[self.settings_row.pk]
        )
        body = self.client.get(url).content.decode()

        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn(self.rotate_url(), body)
        self.assertIn("Change the QR code now", body)

    def test_poster_shows_the_new_address(self):
        """The poster is generated live, so reprinting is all that is needed."""
        self.client.force_login(self.admin)
        self.settings_row.rotate_access_token()

        r = self.client.get(reverse("customer:poster"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse("customer:qr")).status_code, 200)


class BackButtonTests(PortalTestBase):
    """A finished visit must be restarted by scanning, not by pressing Back.

    The guard itself lives in the browser, because a scan and a history move
    are the same request here. What is testable server-side is the half that
    decides whether the guard is armed at all.
    """

    GUARD = "back_forward"

    def test_first_arrival_is_not_guarded(self):
        """Someone who has never visited must not be bounced off the form."""
        body = self.client.get(self.entry_url()).content.decode()
        self.assertNotIn(self.GUARD, body)

    def test_guard_is_armed_after_a_visit_times_out(self):
        self.sign_in()
        self.expire_current_visit()
        self.client.get(reverse("customer:check"))

        body = self.client.get(self.entry_url()).content.decode()
        self.assertIn(self.GUARD, body)
        self.assertIn(reverse("customer:finished"), body)

    def test_guard_is_armed_after_finishing_early(self):
        self.sign_in()
        self.client.post(reverse("customer:logout"))

        self.assertIn(self.GUARD, self.client.get(self.entry_url()).content.decode())

    def test_expiry_records_that_a_visit_ended(self):
        self.sign_in()
        self.expire_current_visit()
        self.client.get(reverse("customer:check"))

        self.assertTrue(self.client.session.get("customer_visit_ended"))

    def test_signing_in_again_disarms_the_guard(self):
        """After a genuine re-scan the browser is no longer under suspicion."""
        self.sign_in()
        self.client.post(reverse("customer:logout"))
        self.sign_in()

        self.assertFalse(self.client.session.get("customer_visit_ended"))
        self.assertNotIn(self.GUARD, self.client.get(self.entry_url()).content.decode())

    def test_a_fresh_scan_still_lets_the_customer_back_in(self):
        """The point of the whole feature: scanning must always work."""
        self.sign_in()
        self.expire_current_visit()
        self.client.get(reverse("customer:check"))

        self.assertEqual(self.client.get(self.entry_url()).status_code, 200)
        self.assertRedirects(self.sign_in(), reverse("customer:check"))
        self.assertEqual(self.client.get(reverse("customer:check")).status_code, 200)

    def test_sign_in_form_is_not_cached(self):
        """Otherwise Back replays the page and the guard never runs."""
        response = self.client.get(self.entry_url())
        self.assertIn("no-store", response["Cache-Control"])

    # --- the closing page holds them there --------------------------------

    TRAP = "popstate"

    def test_closing_page_traps_back_after_a_visit_ends(self):
        self.sign_in()
        self.client.post(reverse("customer:logout"))

        body = self.client.get(reverse("customer:finished")).content.decode()
        self.assertIn(self.TRAP, body)

    def test_closing_page_traps_back_after_a_timeout(self):
        self.sign_in()
        self.expire_current_visit()
        self.client.get(reverse("customer:check"))

        body = self.client.get(reverse("customer:finished")).content.decode()
        self.assertIn(self.TRAP, body)

    def test_a_stranger_who_mistyped_an_address_is_not_trapped(self):
        """They never had a visit, so there is nothing to hold them here for."""
        body = self.client.get("/customer/typo/").content.decode()
        self.assertNotIn(self.TRAP, self.client.get(
            reverse("customer:finished")
        ).content.decode())
        self.assertNotIn(self.TRAP, body)

    def test_trap_is_released_once_a_new_visit_starts(self):
        self.sign_in()
        self.client.post(reverse("customer:logout"))
        self.sign_in()

        body = self.client.get(reverse("customer:finished")).content.decode()
        self.assertNotIn(self.TRAP, body)


class WordingTests(PortalTestBase):
    def test_admin_wording_replaces_the_default(self):
        self.settings_row.expired_heading = "Please scan again"
        self.settings_row.expired_message = "Ask at the counter for help."
        self.settings_row.save()

        body = self.client.get(reverse("customer:finished")).content.decode()
        self.assertIn("Please scan again", body)
        self.assertIn("Ask at the counter for help.", body)
        self.assertNotIn("Scan to log in again", body)

    def test_blank_wording_falls_back_to_the_default(self):
        self.settings_row.expired_heading = ""
        self.settings_row.save()
        self.assertIn(
            "Scan to log in again",
            self.client.get(reverse("customer:finished")).content.decode(),
        )
