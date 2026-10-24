"""Customer QR portal routes — all public except /poster/ (staff-only).

The entry point carries a random 32-character token: a customer arrives by
scanning the shop's QR code, and the address is deliberately not something a
passer-by could guess or type. Everything after sign-in is guarded by the
session cookie instead, so the token only has to appear on the one URL the
poster encodes.

Route order matters. The literal paths are declared first so that `check/`,
`logout/`, `status/`, `finished/`, `qr.png` and `poster/` can never be
swallowed by the token pattern — which is additionally constrained to 32 hex
characters, a shape none of those names can take.
"""

from django.urls import path, re_path

from . import views

app_name = "customer"

urlpatterns = [
    path("check/", views.check, name="check"),
    path("logout/", views.logout_view, name="logout"),
    path("finished/", views.finished, name="finished"),
    path("status/", views.status, name="status"),
    path("qr.png", views.qr_code, name="qr"),
    path("poster/", views.poster, name="poster"),
    re_path(r"^(?P<token>[0-9a-f]{32})/$", views.landing, name="landing"),
    # A bare /customer/ used to be the entry point. It now explains itself
    # rather than 404ing, because staff will have the old address bookmarked.
    path("", views.entry_moved, name="entry-moved"),
    # Catch-all, and it must stay last: everything above claims its own path
    # first. A customer who mistypes the address, or whose saved link is from
    # a poster that has since been reprinted, gets the closing page telling
    # them to scan again instead of a 404 they can do nothing with.
    re_path(r"^.*$", views.wrong_address, name="wrong-address"),
]
