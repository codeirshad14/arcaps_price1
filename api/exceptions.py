"""
Custom DRF exception handling.

The kiosk JavaScript only ever needs to know two things from an error: that it
failed, and a sentence it can put on screen. This handler normalises every DRF
error into a predictable shape so the frontend never has to guess:

    {"error": true, "detail": "...", "status_code": 400}
"""

from __future__ import annotations

import logging
from typing import Any

from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc: Exception, context: dict[str, Any]):
    """Wrap DRF's default handler in a consistent error envelope.

    Returns ``None`` for exceptions DRF does not recognise, which lets Django's
    own 500 handling take over — we do not want to swallow real bugs.
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API exception in %s", context.get("view"))
        return None

    detail = response.data
    # DRF uses {"detail": "..."} for simple errors and a field->messages dict
    # for validation errors. Keep field errors intact under "errors".
    if isinstance(detail, dict) and "detail" in detail:
        payload = {"error": True, "detail": str(detail["detail"])}
    else:
        payload = {
            "error": True,
            "detail": "Validation failed.",
            "errors": detail,
        }

    payload["status_code"] = response.status_code
    response.data = payload
    return response
