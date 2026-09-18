"""
Kiosk view — renders the one and only customer-facing page.

Everything dynamic (lookups, and the display settings on each scan) goes
through the REST API from JavaScript. This view supplies only what is needed to
paint the first frame correctly before any fetch completes, so the idle screen
never flashes unstyled or with stale branding.
"""

from functools import lru_cache

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from products.models import DisplayProfile, ScreenHeaderSettings
from products.serializers import build_display_block

# A year, in seconds — how long a "remembered" showroom PC stays signed in.
REMEMBERED_SESSION_AGE = 60 * 60 * 24 * 365

# Files whose changes must reach an already-open browser. One stamp covers
# every page: the customer portal renders the kiosk's markup against the same
# stylesheet, so a change to either set has to bust both.
_VERSIONED_ASSETS = (
    "static/css/kiosk.css",
    "static/js/kiosk.js",
    "static/js/price-fit.js",
    "static/js/price-speech.js",
    "static/css/customer.css",
    "static/js/customer.js",
)


def _asset_version(debug: bool) -> str:
    """A cache-busting stamp derived from the CSS/JS modification times.

    Browsers cache static files aggressively, and a showroom screen is often
    left open for days — without this, an admin who changes the theme sees the
    old stylesheet until they clear the cache by hand.

    Computed fresh on every request while DEBUG is on so edits show up on a
    plain reload; cached in production, where the files do not change.
    """
    stamps = []
    for relative in _VERSIONED_ASSETS:
        try:
            stamps.append(int((settings.BASE_DIR / relative).stat().st_mtime))
        except OSError:
            continue
    return str(max(stamps)) if stamps else "1"


@lru_cache(maxsize=1)
def _cached_asset_version() -> str:
    return _asset_version(debug=False)


def asset_version() -> str:
    """The stamp to put on asset URLs, honouring DEBUG. Shared with the
    customer portal so both pages version their assets identically."""
    return _asset_version(True) if settings.DEBUG else _cached_asset_version()


class KioskLoginForm(AuthenticationForm):
    """Sign-in form with a per-machine "remember me"."""

    remember_device = forms.BooleanField(
        required=False,
        initial=True,
        label="Remember this system",
        help_text="Stay signed in on this computer. Untick on a shared or "
        "public machine.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"autofocus": True, "autocomplete": "username", "placeholder": "Username"}
        )
        self.fields["password"].widget.attrs.update(
            {"autocomplete": "current-password", "placeholder": "Password"}
        )


class KioskLoginView(LoginView):
    """Login page, themed to match the kiosk.

    The session length is decided here rather than by a global setting so the
    "remember this system" tick means something: a showroom PC stays signed in
    for a year, while a borrowed laptop is forgotten when the browser closes.
    """

    template_name = "kiosk/login.html"
    authentication_form = KioskLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)

        if form.cleaned_data.get("remember_device"):
            self.request.session.set_expiry(REMEMBERED_SESSION_AGE)
        else:
            # 0 means "until the browser closes".
            self.request.session.set_expiry(0)

        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = DisplayProfile.resolve(self.request.GET.get("station"))
        initial_display = build_display_block(self.request, profile)
        context.update(
            {
                "display": profile,
                "logo_url": initial_display.get("logo"),
                "navbar_title": ScreenHeaderSettings.get_solo().name_for(profile),
                "theme": initial_display.get("theme") or {},
                "asset_version": (
                    _asset_version(True) if settings.DEBUG else _cached_asset_version()
                ),
            }
        )
        return context


def screen(request: HttpRequest) -> HttpResponse:
    """Render the kiosk page for one station.

    The station comes from ``?station=<slug>``; the frontend then remembers it
    in localStorage, so each showroom system is set up once by URL and keeps
    its own profile from then on.

    Login is enforced here rather than with a decorator so it can be switched
    off per install via KIOSK_REQUIRE_LOGIN.
    """
    if settings.KIOSK_REQUIRE_LOGIN and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

    profile = DisplayProfile.resolve(request.GET.get("station"))

    # The same display block the API returns, embedded via json_script so the
    # frontend has correct settings and colours on first paint. Reusing the
    # serializer keeps the two paths from drifting apart.
    initial_display = build_display_block(request, profile)
    background = initial_display.get("background") or {}
    theme = initial_display.get("theme") or {}
    header = ScreenHeaderSettings.get_solo()

    response = render(
        request,
        "kiosk/screen.html",
        {
            "display": profile,
            "initial_display": initial_display,
            "logo_url": initial_display.get("logo"),
            # The top bar is configured once for every screen — the same
            # settings drive the customer portal's bar. Falls back to this
            # station's store name when left blank.
            "navbar_title": header.name_for(profile),
            "navbar_subtitle": header.label("counter_label"),
            "show_station_label": header.show_station_on_counter,
            "background_url": background.get("image"),
            "background_fit": background.get("fit"),
            "background_overlay": background.get("overlay"),
            # Server-rendered so the very first paint is already themed —
            # otherwise the default palette flashes before the JS runs.
            "theme": theme,
            "asset_version": (
                _asset_version(True) if settings.DEBUG else _cached_asset_version()
            ),
        },
    )

    # The page itself must never be cached: it carries the station's branding
    # and the version stamps for the stylesheet and script. A stale copy would
    # keep pointing at old assets no matter how they are versioned.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response
