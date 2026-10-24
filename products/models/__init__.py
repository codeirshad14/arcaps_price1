"""
Data models for the showroom price checker, split by concern:

    catalog.py           — Category, Product (the sellable catalogue)
    company.py           — Company, UserCompanyAccess, resolve_company()
    station.py            — DisplayProfile (showroom station config/theme)
    customer_portal.py   — CustomerPortalSettings, CustomerSession
    screen_header.py     — ScreenHeaderSettings (top bar, shared by both screens)
    scanlog.py            — ScanLog (audit trail of every lookup)

Everything is re-exported here so ``from products.models import X`` keeps
working exactly as before — this split is a readability change only, not an
API change. It has no effect on migrations either: Django ties a model to
its app_label ("products"), never to which file it happens to be written in.
"""

from .catalog import Category, Product
from .company import Company, UserCompanyAccess, resolve_company
from .customer_portal import (
    CustomerPortalSettings,
    CustomerSession,
    mobile_number_validator,
)
from .scanlog import ScanLog
from .screen_header import ScreenHeaderSettings
from .station import THEME_PRESETS, DisplayProfile, hex_colour_validator

__all__ = [
    "Category",
    "Product",
    "Company",
    "UserCompanyAccess",
    "resolve_company",
    "CustomerPortalSettings",
    "CustomerSession",
    "mobile_number_validator",
    "ScanLog",
    "ScreenHeaderSettings",
    "THEME_PRESETS",
    "DisplayProfile",
    "hex_colour_validator",
]
