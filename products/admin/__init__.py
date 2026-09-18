"""
Admin site configuration, split by concern to match ``products/models/``:

    product.py            — Category, Product
    company.py             — Company, the User-form company-access inline
    station.py              — DisplayProfile (showroom station)
    customer_portal.py     — CustomerPortalSettings, CustomerSession
    scanlog.py               — ScanLog

Each module registers its own models via ``@admin.register`` on import, so
this file only needs to make sure every module actually gets imported.
"""

from . import company, customer_portal, product, scanlog, station  # noqa: F401
