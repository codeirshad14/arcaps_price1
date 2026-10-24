"""
API authentication for the write endpoints.

The Tally sync endpoint is machine-to-machine on a trusted LAN, so a shared
secret in a header is the right weight of protection — a full user/token system
would be ceremony without benefit here. The kiosk read endpoints stay open.
"""

from __future__ import annotations

import hmac

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import SAFE_METHODS, BasePermission

API_KEY_HEADER = "X-API-Key"


class InvalidAPIKey(APIException):
    """401 rather than DRF's default 403.

    DRF only produces 401 when an authentication class advertises a
    ``WWW-Authenticate`` scheme. We authenticate with a bare header instead, so
    the status code is set explicitly here — the spec calls for 401 and a
    client script needs to tell "wrong key" apart from "not allowed".
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    default_detail = f"Missing or invalid {API_KEY_HEADER} header."
    default_code = "invalid_api_key"


def _check_api_key(request) -> bool:
    """Validate the ``X-API-Key`` header, raising 401 when it does not match."""
    expected = settings.TALLY_API_KEY

    # An unset key would otherwise make the endpoint world-writable the moment
    # someone forgets to fill in .env. Fail closed instead.
    if not expected:
        raise InvalidAPIKey(
            "TALLY_API_KEY is not configured on the server, so write access "
            "is disabled."
        )

    provided = request.headers.get(API_KEY_HEADER, "")

    # Constant-time compare keeps the check from leaking the key one character
    # at a time through response timing.
    if not provided or not hmac.compare_digest(provided, expected):
        raise InvalidAPIKey()
    return True


class HasTallyAPIKey(BasePermission):
    """Require a correct ``X-API-Key`` header for every request."""

    def has_permission(self, request, view) -> bool:
        return _check_api_key(request)


class ReadOnlyOrAPIKey(BasePermission):
    """Open for reads; writes need an API key **or** a logged-in staff session.

    The kiosk screen fetches product data anonymously from any machine on the
    showroom LAN, so GET must stay open. Anything that changes the catalogue —
    POST, PUT, PATCH, DELETE — needs the same shared secret the Tally sync
    endpoint uses; a second scheme for the same trust boundary would only be
    one more thing to get wrong.

    The staff-session exemption exists for one concrete reason: DRF's browsable
    API decides whether to render the POST/PUT form by running this permission
    check first, and hides the form when it fails. Without the exemption,
    opening /api/products/ in a browser shows no way to create anything — a
    browser cannot send a custom header from that page. Anyone with an admin
    session can already edit products through the admin, so this grants no new
    authority.
    """

    def has_permission(self, request, view) -> bool:
        if request.method in SAFE_METHODS:
            return True

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return True

        return _check_api_key(request)
