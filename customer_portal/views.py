"""
Customer self-service QR portal — views.

Everything here is deliberately public: a customer reaches it by scanning
the shop's QR code with their own phone, with no staff login involved.
Identity is a name + mobile number captured for the shop's records, not an
authentication credential — a ``CustomerSession`` row backs a plain Django
session key, so no separate token system is needed.

The entry address carries a random 32-character token so it cannot be reached
by guessing. That token is obscurity, not authentication: it keeps the portal
off the open web for passers-by, and anyone who has the poster can use it.
Nothing behind it is secret to a customer in the shop.

    GET/POST /customer/<token>/ — name/mobile capture, or straight through to
                                   /customer/check/ if already signed in.
    GET      /customer/check/   — the price-check page.
    GET      /customer/finished/— the visit is over; scan the poster to return.
    POST     /customer/logout/  — end the visit early.
    GET      /customer/status/  — tiny JSON poll for the countdown.
    GET      /customer/qr.png   — QR code image, encodes the tokenised entry.
    GET      /customer/poster/  — printable branded poster. Staff-only.
    GET      /customer/         — 404, or a redirect to the poster for staff.
"""

from __future__ import annotations

import io
import secrets
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve
from django.utils import timezone
from django.views.decorators.http import require_POST

from kiosk.views import asset_version as kiosk_asset_version
from products.models import (
    CustomerPortalSettings,
    CustomerSession,
    ScreenHeaderSettings,
    resolve_company,
)
from products.serializers import build_display_block


class CustomerEntryForm(forms.ModelForm):
    """Name + mobile number, captured fresh on every visit."""

    class Meta:
        model = CustomerSession
        fields = ["name", "mobile_number"]
        labels = {"name": "Your name", "mobile_number": "Mobile number"}
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "manual-input",
                    "placeholder": "Your name",
                    "autocomplete": "name",
                    "autofocus": True,
                }
            ),
            "mobile_number": forms.TextInput(
                attrs={
                    "class": "manual-input",
                    "placeholder": "e.g. 9876543210",
                    "autocomplete": "tel",
                    "inputmode": "tel",
                }
            ),
        }


def settings_row_entry_url() -> str:
    """The current tokenised entry path.

    Looked up fresh each time rather than captured, so rotating the token in
    the admin takes effect immediately for every redirect.
    """
    return CustomerPortalSettings.get_solo().entry_path


# Marks a browser that has already completed a visit here. The sign-in form
# reads it to tell a fresh scan apart from the Back button — see landing().
VISIT_ENDED_KEY = "customer_visit_ended"


def _active_session(request: HttpRequest) -> CustomerSession | None:
    """The signed-in customer for this browser, or ``None``.

    Clears a stale session id from the Django session the moment it is found
    to be expired, so a later request does not keep re-checking a dead row,
    and records that a visit ended on this browser.
    """
    session_id = request.session.get(CustomerSession.SESSION_KEY)
    if not session_id:
        return None
    session = CustomerSession.objects.filter(pk=session_id).first()
    if session is None or session.is_expired:
        request.session.pop(CustomerSession.SESSION_KEY, None)
        request.session[VISIT_ENDED_KEY] = True
        return None
    return session


def _branding_context(request: HttpRequest, settings_row: CustomerPortalSettings) -> dict:
    """Logo/theme/background borrowed from the branding station.

    Deliberately the same keys the kiosk view passes to its template: these
    pages now render the kiosk's own markup against the kiosk stylesheet, so
    anything the template reads has to be here under the same name.
    """
    branding_station = settings_row.resolve_branding_station()
    display = build_display_block(request, branding_station)
    background = display.get("background") or {}
    header = ScreenHeaderSettings.get_solo()
    return {
        "branding_station": branding_station,
        "display": display,
        # Wording, resolved here so no template has to know a fallback. The
        # ones that fall back to the station rather than to a constant are
        # resolved here for the same reason.
        #
        # The top bar comes from ScreenHeaderSettings, which drives the staff
        # counter screen too — the shop name is one setting, not one per page.
        "navbar_title": header.name_for(branding_station),
        "navbar_subtitle": header.label("customer_label"),
        "welcome_heading": (
            settings_row.welcome_heading.strip() or display.get("welcome_message", "")
        ),
        "prompt_title": settings_row.label("prompt_title"),
        "prompt_subtitle": settings_row.label("prompt_subtitle"),
        "submit_label": settings_row.label("submit_label"),
        "finish_label": settings_row.label("finish_label"),
        "idle_heading": settings_row.label("idle_heading"),
        "expired_heading": settings_row.label("expired_heading"),
        "expired_message": settings_row.label("expired_message"),
        "poster_instructions": settings_row.label("poster_instructions"),
        "logo_url": display.get("logo"),
        "background_url": background.get("image"),
        "background_fit": background.get("fit"),
        "background_overlay": background.get("overlay"),
        "theme": display.get("theme") or {},
        "asset_version": kiosk_asset_version(),
    }


def entry_moved(request: HttpRequest) -> HttpResponse:
    """A bare /customer/ is no longer the way in.

    Staff get sent to the poster, which is what they were almost certainly
    after. A customer gets the closing page: it still refuses to be a way in
    without the token, but it says so in words they can act on instead of
    showing them a 404.
    """
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("customer:poster")
    return redirect("customer:finished")


def wrong_address(request: HttpRequest, *args, **kwargs) -> HttpResponse:
    """Anything else under /customer/ — a mistyped or stale address.

    A customer holding a phone should never meet a 404. They get the closing
    page, which tells them to scan the poster, and that is a real instruction
    rather than a dead end.

    The slash handling is not optional. This pattern matches every unclaimed
    path under /customer/, missing trailing slash included, so Django's own
    APPEND_SLASH never gets the chance to run. Without doing its job here, the
    entry address typed without its final slash would send a customer to the
    closing page instead of letting them in.
    """
    if not request.path.endswith("/"):
        candidate = request.path + "/"
        try:
            match = resolve(candidate)
        except Resolver404:
            pass
        else:
            # Guard against pointing at this very view, which would just add a
            # pointless hop before landing on the closing page anyway.
            if match.url_name != "wrong-address":
                return redirect(candidate)

    return redirect("customer:finished")


def landing(request: HttpRequest, token: str) -> HttpResponse:
    settings_row = CustomerPortalSettings.get_solo()

    # Constant-time compare: the token is the only thing standing between a
    # stranger and the portal, so do not leak its prefix through timing.
    #
    # A wrong code goes to the closing page rather than a 404 — most often it
    # is a customer whose poster has been reprinted since they saved the link,
    # and "scan the code at the counter" is exactly what they need to be told.
    # It gives nothing away either: a mistyped path lands in the same place,
    # so there is no way to tell a wrong code from an address that was never
    # a portal at all.
    if not secrets.compare_digest(token, settings_row.access_token):
        return redirect("customer:finished")

    if not settings_row.is_enabled:
        return redirect("customer:finished")

    if _active_session(request) is not None:
        return redirect("customer:check")

    if request.method == "POST":
        form = CustomerEntryForm(request.POST)
        if form.is_valid():
            branding_station = settings_row.resolve_branding_station()
            session = form.save(commit=False)
            session.company = resolve_company(None, branding_station)
            session.expires_at = timezone.now() + timedelta(
                minutes=settings_row.session_minutes
            )
            session.save()
            request.session[CustomerSession.SESSION_KEY] = session.pk
            # A new visit has begun, so the previous one stops being a reason
            # to turn this browser away.
            request.session.pop(VISIT_ENDED_KEY, None)
            return redirect("customer:check")
    else:
        form = CustomerEntryForm()

    context = {
        "form": form,
        "settings": settings_row,
        # Switches on the Back-button guard in the template. Only a browser
        # that has already finished a visit here is watched, so someone
        # arriving for the first time is never caught by it.
        "visit_ended": bool(request.session.get(VISIT_ENDED_KEY)),
    }
    context.update(_branding_context(request, settings_row))

    response = render(request, "customer/landing.html", context)
    # Keeps the form out of the back/forward cache, so returning to it is a
    # real request that runs the guard rather than a silent replay of the
    # page as it was.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def check(request: HttpRequest) -> HttpResponse:
    session = _active_session(request)
    if session is None:
        # Deliberately not the entry URL. Sending an expired visit straight
        # back to the sign-in form handed the customer another session without
        # the poster, which made the token pointless — a phone left on a
        # counter could go round that loop all day.
        return redirect("customer:finished")

    settings_row = CustomerPortalSettings.get_solo()
    branding = _branding_context(request, settings_row)
    context = {
        "session": session,
        "settings": settings_row,
        "seconds_remaining": session.seconds_remaining,
        "initial_display": branding["display"],
        "session_payload": {
            "seconds_remaining": session.seconds_remaining,
            "station": branding["branding_station"].slug,
        },
    }
    context.update(branding)

    response = render(request, "customer/check.html", context)
    # A shared phone/tablet must never see the previous customer's session
    # replayed from a cached page.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    session = _active_session(request)
    if session is not None:
        session.ended_at = timezone.now()
        session.save(update_fields=["ended_at"])
    request.session.pop(CustomerSession.SESSION_KEY, None)
    request.session[VISIT_ENDED_KEY] = True
    # Finishing early and timing out are the same thing from here on: the
    # visit is over and the poster is the way back.
    return redirect("customer:finished")


def finished(request: HttpRequest) -> HttpResponse:
    """The end of a visit — timed out, or ended with the finish button.

    Deliberately a dead end. It carries no link onward and never renders the
    tokenised entry URL, so the only way back in is scanning the poster. The
    token is the whole reason the portal is not reachable by guessing, and a
    page that handed it back would give that away.
    """
    settings_row = CustomerPortalSettings.get_solo()
    context = {
        "settings": settings_row,
        # Arms the Back-button trap. Only a browser that actually finished a
        # visit is held here; someone who merely mistyped an address is free
        # to go back wherever they came from.
        "visit_ended": bool(request.session.get(VISIT_ENDED_KEY)),
    }
    context.update(_branding_context(request, settings_row))

    response = render(request, "customer/finished.html", context)
    # A shared phone must not be able to go back to this page and find the
    # previous customer's visit still described on it.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


def status(request: HttpRequest) -> JsonResponse:
    """Tiny poll the check page uses as a belt-and-braces timer resync."""
    session = _active_session(request)
    if session is None:
        return JsonResponse({"expired": True, "seconds_remaining": 0})
    return JsonResponse({"expired": False, "seconds_remaining": session.seconds_remaining})


def qr_code(request: HttpRequest) -> HttpResponse:
    """The shop-wide QR image, encoding the absolute landing-page URL."""
    import qrcode

    url = request.build_absolute_uri(settings_row_entry_url())
    image = qrcode.make(url, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    response["Cache-Control"] = "no-store"
    return response


def poster(request: HttpRequest) -> HttpResponse:
    """Printable branded poster for the counter. Staff-only — not a customer page."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

    settings_row = CustomerPortalSettings.get_solo()
    context = {"settings": settings_row}
    context.update(_branding_context(request, settings_row))
    return render(request, "customer/poster.html", context)
