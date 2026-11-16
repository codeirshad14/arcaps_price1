"""
REST API views.

Endpoints:

* ``GET  /api/lookup/?code=&station=``  — called by the kiosk on every scan
* ``POST /api/tally-product/``          — upsert products pushed from Tally
* ``GET  /api/settings/?station=``      — the display block, for initial load
* ``GET  /api/stations/``               — station list, for the picker
* ``GET  /api/products/``               — read-only paginated list, debugging
* ``POST /api/login/``                  — JSON session login
* ``POST /api/logout/``                 — end the current session

Every read endpoint accepts ``?station=<slug>``. That one parameter decides
both which prices are quoted and how the screen looks, so several systems can
share one server with different settings.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    Value,
    When,
)
from django.utils import timezone
from rest_framework import filters, generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from kiosk.views import REMEMBERED_SESSION_AGE

from products.models import (
    Category,
    Company,
    CustomerSession,
    DisplayProfile,
    Product,
    ScanLog,
    resolve_company,
)
from products.serializers import (
    CatalogProductSerializer,
    ProductCrudSerializer,
    StationSerializer,
    TallyProductSerializer,
    TallyProductSyncSerializer,
    build_display_block,
)

from .permissions import HasTallyAPIKey, ReadOnlyOrAPIKey

# Query-param value -> ScanLog.source. Anything unrecognised is treated as a
# scanner hit, which is the overwhelmingly common case.
_VALID_SOURCES = {choice for choice, _ in ScanLog.SOURCE_CHOICES}

# A single scan must never turn into an unbounded batch of lookups.
MAX_CODES_PER_REQUEST = 8


def _resolve_station(request: Request) -> DisplayProfile:
    """Find the profile for this request's station, always returning one."""
    return DisplayProfile.resolve(request.query_params.get("station"))


def _resolve_scope(request: Request) -> tuple[DisplayProfile, Company | None]:
    """Station plus the company whose catalogue may be shown.

    ``None`` for the company means every company. The signed-in user can
    narrow the station's setting but never widen it — see
    ``products.models.resolve_company``.
    """
    profile = _resolve_station(request)
    company = resolve_company(getattr(request, "user", None), profile)
    return profile, company


def _resolve_customer_session(request: Request) -> CustomerSession | None:
    """The active customer-portal session for this browser, if any.

    Reads the same Django session key the customer portal sets at login
    (``CustomerSession.SESSION_KEY``) — present only when this call came from
    the self-service page, not from a staff kiosk. Used to tag ScanLog rows so
    the admin can report per mobile number, and to ride the remaining seconds
    back to the customer page's countdown on every lookup.
    """
    session_id = request.session.get(CustomerSession.SESSION_KEY)
    if not session_id:
        return None
    session = CustomerSession.objects.filter(pk=session_id).first()
    if session is None or session.is_expired:
        return None
    return session


# ---------------------------------------------------------------------------
# 1. Lookup
# ---------------------------------------------------------------------------


def _find_products(code: str, company: Company | None) -> list[Product]:
    """Active products matching a code, scoped to a company.

    Returns a list rather than one row because codes are unique only *within*
    a company: on an all-companies screen "1345" can legitimately be two
    different products, and showing both is the honest answer. A screen locked
    to one company can never see more than one.
    """
    queryset = (
        Product.objects.filter(is_active=True)
        .filter(Q(barcode__iexact=code) | Q(alias__iexact=code))
        .select_related("category", "company")
    )
    if company is not None:
        queryset = queryset.filter(company=company)

    # Deterministic order so the same scan always renders the same way.
    return list(queryset.order_by("company__name", "-barcode"))


def _find_by_name(name: str, company: Company | None) -> list[Product]:
    """Active products whose name is exactly ``name``, ignoring case.

    Exact on purpose, and never "contains" or "similar". This is a price
    checker: a near match puts a different product's price on screen, and a
    customer reading it has no way to know it is not the one they asked
    about. Typing "blue cap" must find the product called "Blue Cap" and
    nothing else — not "Blue Cap XL", not "Navy Blue Cap".

    The only leniency is case and the spacing between words, which never
    change which product is meant — on BOTH sides. Tally names can carry a
    stray double space ("BUCKET R  CAP"), and nobody types that; comparing
    the words with any run of whitespace between them finds it. The pattern
    is still anchored at both ends, so it matches the whole name or nothing.
    """
    words = name.split()
    if not words:
        return []
    pattern = r"^\s*" + r"\s+".join(re.escape(word) for word in words) + r"\s*$"
    queryset = (
        Product.objects.filter(is_active=True, name__iregex=pattern)
        .select_related("category", "company")
    )
    if company is not None:
        queryset = queryset.filter(company=company)
    # Same order as a code lookup, so the same search always renders the same.
    return list(queryset.order_by("company__name", "-barcode"))


def _requested_codes(request: Request) -> list[str]:
    """Collect codes from ``?code=`` — repeated, comma-separated, or both.

    Supports the multi-product screen: ``?code=1345,1346`` or
    ``?code=1345&code=1346`` both work.
    """
    raw: list[str] = []
    for value in request.query_params.getlist("code"):
        raw.extend(value.split(","))

    codes, seen = [], set()
    for item in raw:
        code = item.strip()
        # De-duplicate: scanning the same item twice should not show it twice.
        if code and code.lower() not in seen:
            seen.add(code.lower())
            codes.append(code)
    return codes[:MAX_CODES_PER_REQUEST]


@api_view(["GET"])
def lookup(request: Request) -> Response:
    """Look up one or more scanned codes and return products plus settings.

    The ``display`` block rides along on every response — including misses —
    so admin changes take effect on the very next scan.

    Query params:
        code         required; repeat or comma-separate for several products.
                     An exact product name works too — see below
        station      which display profile (and price list) to use
        source       scanner (default) | voice | manual, recorded in ScanLog
        omit_hidden  "1" to drop fields switched off in the admin entirely

    Each code is matched against the part number (barcode) and alias first,
    then against the product name. Every match is exact, ignoring only case:
    a partial or similar match would show another product's price, which on
    a price checker is worse than "not found". See ``_find_by_name``.

    Response shape is unchanged for the single-code case: ``tally_product`` is
    the object. A multi-code request additionally returns ``tally_products``.
    """
    profile, company = _resolve_scope(request)
    display = build_display_block(request, profile, company)
    customer_session = _resolve_customer_session(request)

    source = request.query_params.get("source", ScanLog.SOURCE_SCANNER)
    if source not in _VALID_SOURCES:
        source = ScanLog.SOURCE_SCANNER

    codes = _requested_codes(request)

    # An empty scan is a frontend hiccup, not a product miss — don't pollute
    # the scan log with it.
    if not codes:
        return Response(
            {
                "found": False,
                "code": "",
                "tally_product": None,
                "detail": "No code supplied.",
                "display": display,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    context = {"request": request}
    visible = None
    if request.query_params.get("omit_hidden") == "1":
        visible = _visible_field_names(display["fields"])

    found_items: list[dict[str, Any]] = []
    missing: list[str] = []
    logs: list[ScanLog] = []

    for code in codes:
        # The part number wins: a product whose *name* happens to read like
        # another product's code must not take its place.
        matches = _find_products(code, company) or _find_by_name(code, company)

        logs.append(
            ScanLog(
                code=code[:128],
                found=bool(matches),
                # A code matching several companies has no single product to
                # point at, so the log records the first and the scope.
                product=matches[0] if matches else None,
                station=profile,
                company=company,
                customer_session=customer_session,
                source=source,
            )
        )

        if not matches:
            missing.append(code)
            continue

        for product in matches:
            found_items.append(
                TallyProductSerializer(product, context=context, fields=visible).data
            )

    # One round trip rather than one per code.
    ScanLog.objects.bulk_create(logs)

    payload: dict[str, Any] = {
        "found": bool(found_items),
        "display": display,
        # Kept for the single-code contract; the first hit, or null.
        "tally_product": found_items[0] if found_items else None,
        "not_found": missing,
    }

    # Several codes, or one code matching several companies: either way the
    # frontend needs the full list.
    if len(codes) > 1 or len(found_items) > 1:
        payload["tally_products"] = found_items

    if not found_items:
        payload["code"] = codes[0]

    # Rides along the same way `display` does, so the customer page's
    # countdown resyncs to the real remaining time on every lookup rather
    # than drifting from a background-throttled JS timer.
    if customer_session is not None:
        payload["customer_session"] = {"seconds_remaining": customer_session.seconds_remaining}

    return Response(payload)


def _visible_field_names(flags: dict[str, Any]) -> set[str]:
    """Map display flags onto ``tally_product`` field names.

    ``barcode`` and ``unit`` are always kept: the frontend echoes the barcode
    on the not-found screen and needs the unit to render a stock quantity.
    """
    # Company is always kept: on an all-companies screen it is the only thing
    # telling two same-barcode products apart.
    keep = {"barcode", "unit", "company", "company_code"}
    for flag, field in (
        ("name", "name"),
        ("image", "image"),
        ("description", "description"),
        ("price", "price"),
        ("mrp", "mrp"),
        ("discount", "discount_percent"),
    ):
        if flags.get(flag):
            keep.add(field)
    if flags.get("price"):
        keep.add("prices")
    if flags.get("availability"):
        keep.update({"availability", "closing_stock"})
    return keep


# ---------------------------------------------------------------------------
# 1b. Catalogue (internal)
# ---------------------------------------------------------------------------

CATALOG_PAGE_SIZE = 24

# The retail price a customer pays — discount off, GST on — worked out in the
# database, so the catalogue can filter and sort by it. Product computes the
# same figure in Python (retail_final_price), rounding at each step; this
# skips the rounding, which only matters at the last paisa of a range.
_FINAL_PRICE = ExpressionWrapper(
    F("retail_price") * (Value(100) - F("given_disc")) * (Value(100) + F("gst")) / Value(10000),
    output_field=DecimalField(max_digits=20, decimal_places=4),
)

_CATALOG_SORTS = {
    "name": ["name"],
    "-name": ["-name"],
    "price": ["final_price", "name"],
    "-price": ["-final_price", "name"],
    "stock": ["closing_stock", "name"],
    "-stock": ["-closing_stock", "name"],
    "new": ["-created_at", "name"],
}


def _decimal_param(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value not in (None, "") else None
    except InvalidOperation:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def catalog(request: Request) -> Response:
    """Browse the catalogue: the internal team's catalogue screen.

    Signed-in users only. Customers never have an account — the QR portal
    keeps its visits in its own session — so this is closed to them however
    they arrive.

    Unlike the price lookup, search here is partial on purpose: this is for
    browsing ("show me every cap"), by staff who can see what they are
    looking at, not a customer reading a single price off a screen. An exact
    match on code or name is still listed first.

    Query params (all optional):
        q          words matched against name, part number and alias
        category   category id, or "none" for products without one
        company    company slug — only on an all-companies station
        stock      "in" | "out"
        discount   "1" for discounted products only
        min_price  final retail price, incl. discount and GST
        max_price
        sort       name | -name | price | -price | stock | -stock | new
        page       1-based; CATALOG_PAGE_SIZE per page
        station    as everywhere: decides the company scope

    Page 1 also carries ``facets`` — the categories (and companies) with how
    many products each has under the current search — to draw the filters.
    """
    profile, company = _resolve_scope(request)
    params = request.query_params

    products = Product.objects.filter(is_active=True).select_related("category", "company")
    if company is not None:
        products = products.filter(company=company)
    products = products.annotate(final_price=_FINAL_PRICE)

    query = " ".join(params.get("q", "").split())
    for word in query.split():
        products = products.filter(
            Q(name__icontains=word) | Q(barcode__icontains=word) | Q(alias__icontains=word)
        )

    # A station locked to one company already decided this; only an
    # all-companies screen may narrow it.
    company_slug = params.get("company")
    if company is None and company_slug:
        products = products.filter(company__slug=company_slug)

    stock = params.get("stock")
    if stock == "in":
        products = products.filter(closing_stock__gt=0)
    elif stock == "out":
        products = products.filter(closing_stock__lte=0)

    if params.get("discount") == "1":
        products = products.filter(given_disc__gt=0)

    low, high = _decimal_param(params.get("min_price")), _decimal_param(params.get("max_price"))
    if low is not None:
        products = products.filter(final_price__gte=low)
    if high is not None:
        products = products.filter(final_price__lte=high)

    # Counted before the category filter, so the list keeps offering every
    # category the search could switch to, not just the one already picked.
    before_category = products

    category = params.get("category")
    if category == "none":
        products = products.filter(category__isnull=True)
    elif category and category.isdigit():
        products = products.filter(category_id=int(category))

    ordering = list(_CATALOG_SORTS.get(params.get("sort", "name"), _CATALOG_SORTS["name"]))
    if query:
        products = products.annotate(
            _exact=Case(
                When(
                    Q(barcode__iexact=query) | Q(alias__iexact=query) | Q(name__iexact=query),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        ordering.insert(0, "_exact")
    products = products.order_by(*ordering, "pk")

    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1
    total = products.count()
    start = (page - 1) * CATALOG_PAGE_SIZE
    items = products[start:start + CATALOG_PAGE_SIZE]

    payload: dict[str, Any] = {
        "count": total,
        "page": page,
        "has_more": start + CATALOG_PAGE_SIZE < total,
        "currency_symbol": profile.currency_symbol,
        "results": CatalogProductSerializer(items, many=True, context={"request": request}).data,
    }

    if page == 1:
        categories = (
            before_category.values("category_id", "category__name")
            .annotate(n=Count("id"))
            .order_by("category__name")
        )
        facets: dict[str, Any] = {
            "categories": [
                {
                    "id": row["category_id"] if row["category_id"] is not None else "none",
                    "name": row["category__name"] or "Uncategorised",
                    "count": row["n"],
                }
                for row in categories
            ],
        }
        if company is None:
            companies = (
                Product.objects.filter(is_active=True)
                .values("company__slug", "company__name")
                .annotate(n=Count("id"))
                .order_by("company__name")
            )
            facets["companies"] = [
                {"slug": row["company__slug"], "name": row["company__name"], "count": row["n"]}
                for row in companies
            ]
        payload["facets"] = facets

    return Response(payload)


# ---------------------------------------------------------------------------
# 2. Tally sync
# ---------------------------------------------------------------------------


class TallyProductSyncView(APIView):
    """Upsert one or many products pushed in from Tally.

    Accepts either shape::

        {"tally_product": {...}}
        {"tally_product": [{...}, {...}]}

    Records are processed independently: one bad row reports an error and the
    rest still apply. A partial failure returns 207 so the caller can tell it
    apart from a clean run.
    """

    permission_classes = [HasTallyAPIKey]

    def post(self, request: Request) -> Response:
        payload = request.data.get("tally_product")

        if payload is None:
            return Response(
                {
                    "error": True,
                    "detail": 'Request body must contain a "tally_product" key.',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        records = payload if isinstance(payload, list) else [payload]
        if not records:
            return Response(
                {"error": True, "detail": '"tally_product" was an empty list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = [self._sync_one(index, raw) for index, raw in enumerate(records)]

        summary = {
            "created": sum(r["status"] == "created" for r in results),
            "updated": sum(r["status"] == "updated" for r in results),
            "errors": sum(r["status"] == "error" for r in results),
            "total": len(results),
        }

        if summary["errors"] == 0:
            http_status = status.HTTP_200_OK
        elif summary["errors"] == summary["total"]:
            http_status = status.HTTP_400_BAD_REQUEST
        else:
            http_status = status.HTTP_207_MULTI_STATUS

        return Response({"summary": summary, "results": results}, status=http_status)

    def _sync_one(self, index: int, raw: Any) -> dict[str, Any]:
        """Validate and upsert a single record, never raising."""
        if not isinstance(raw, dict):
            return {
                "index": index,
                "barcode": None,
                "status": "error",
                "errors": {"non_field_errors": ["Each record must be an object."]},
            }

        serializer = TallyProductSyncSerializer(data=raw)
        if not serializer.is_valid():
            return {
                "index": index,
                "barcode": raw.get("barcode"),
                "status": "error",
                "errors": serializer.errors,
            }

        data = dict(serializer.validated_data)
        category_name = data.pop("category", None)
        # validate_company() always returns a Company, defaulting when absent.
        company = data.pop("company", None) or Company.get_default()
        data["company"] = company

        # The part number is the identity, and it arrives in `barcode`.
        #
        # This used to prefer `tally_guid` when one was supplied, on the theory
        # that GUIDs are stable while barcodes get relabelled. That was wrong
        # for this Tally export: the same GUID turns up on several stock items,
        # so matching on it merged unrelated products into one row and the
        # uniqueness constraint failed the rest of the batch. The part number
        # is what actually identifies a product here.
        #
        # The company stays part of the key: part numbers are unique only
        # within a company, so matching on the code alone would let one
        # company's sync overwrite another's product.
        match_key = {"company": company, "barcode": data["barcode"]}

        defaults = dict(data)
        defaults["last_synced_at"] = timezone.now()
        if category_name:
            category, _ = Category.objects.get_or_create(name=category_name.strip())
            defaults["category"] = category

        # Remove the match key from defaults so update_or_create does not try
        # to write the column it is filtering on back onto itself.
        for key in match_key:
            defaults.pop(key, None)

        try:
            product, created = Product.objects.update_or_create(
                **match_key, defaults=defaults
            )
        except Exception as exc:  # noqa: BLE001 - report, never 500 the batch
            return {
                "index": index,
                "barcode": data.get("barcode"),
                "status": "error",
                "errors": {"non_field_errors": [str(exc)]},
            }

        return {
            "index": index,
            "barcode": product.barcode,
            "company": company.slug,
            "status": "created" if created else "updated",
            "id": product.id,
        }


# ---------------------------------------------------------------------------
# 3. Settings and stations
# ---------------------------------------------------------------------------


@api_view(["GET"])
def display_settings(request: Request) -> Response:
    """Return the display block on its own, for the kiosk's initial paint."""
    profile, company = _resolve_scope(request)
    return Response({"display": build_display_block(request, profile, company)})


@api_view(["GET"])
def stations(request: Request) -> Response:
    """List the stations this user may switch to.

    A login restricted to one company must not be offered another company's
    station, or the picker would look like a way around the restriction.
    """
    profiles = DisplayProfile.objects.filter(is_active=True).select_related("company")

    access = getattr(getattr(request, "user", None), "company_access", None)
    if access is not None and access.company_id:
        profiles = profiles.filter(
            Q(company__isnull=True) | Q(company_id=access.company_id)
        )

    return Response({"stations": StationSerializer(profiles, many=True).data})


# ---------------------------------------------------------------------------
# 4. Product list
# ---------------------------------------------------------------------------


class ProductListCreateView(generics.ListCreateAPIView):
    """``GET`` the product list, ``POST`` one or many products.

    GET is open — the kiosk and any LAN device may read. POST requires the
    ``X-API-Key`` header (or a logged-in staff session).

    POST accepts three shapes::

        {"barcode": "...", ...}                  one product, flat
        {"product": {...}}                       one product, wrapped
        {"product": [{...}, {...}]}              many products

    The flat form returns the created object, as REST expects. The wrapped
    forms return a per-record summary instead, because with a batch there is
    no single "created object" to return and one bad row must not discard the
    good ones.

    Query params on GET:
        search    matches name, barcode or alias
        ordering  name | retail_price | closing_stock | updated_at (prefix - to reverse)
        all=1     include deactivated products (default: active only)
    """

    serializer_class = ProductCrudSerializer
    permission_classes = [ReadOnlyOrAPIKey]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "barcode", "alias"]
    ordering_fields = ["name", "retail_price", "closing_stock", "updated_at"]
    ordering = ["name"]

    def get_queryset(self):
        queryset = Product.objects.select_related("category", "company")
        # Default to active only; ?all=1 includes deactivated rows.
        if self.request.query_params.get("all") != "1":
            queryset = queryset.filter(is_active=True)

        # A login restricted to one company only ever lists its own products.
        access = getattr(getattr(self.request, "user", None), "company_access", None)
        if access is not None and access.company_id:
            queryset = queryset.filter(company_id=access.company_id)

        # ?company=<slug> narrows further, for scripts managing one catalogue.
        company_slug = self.request.query_params.get("company")
        if company_slug:
            queryset = queryset.filter(company__slug=company_slug)

        return queryset

    def create(self, request: Request, *args, **kwargs) -> Response:
        payload = request.data

        # Not the wrapped form: fall through to DRF's normal single create.
        if not isinstance(payload, dict) or "product" not in payload:
            return super().create(request, *args, **kwargs)

        records = payload["product"]
        if isinstance(records, dict):
            records = [records]

        if not isinstance(records, list) or not records:
            return Response(
                {
                    "error": True,
                    "detail": '"product" must be an object, or a non-empty list '
                    "of objects.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = [self._create_one(i, raw) for i, raw in enumerate(records)]

        summary = {
            "created": sum(r["status"] == "created" for r in results),
            "errors": sum(r["status"] == "error" for r in results),
            "total": len(results),
        }

        if summary["errors"] == 0:
            http_status = status.HTTP_201_CREATED
        elif summary["errors"] == summary["total"]:
            http_status = status.HTTP_400_BAD_REQUEST
        else:
            # Some worked, some did not — the caller needs to be able to tell.
            http_status = status.HTTP_207_MULTI_STATUS

        return Response({"summary": summary, "results": results}, status=http_status)

    def _create_one(self, index: int, raw: Any) -> dict[str, Any]:
        """Validate and create a single product, never raising."""
        if not isinstance(raw, dict):
            return {
                "index": index,
                "barcode": None,
                "status": "error",
                "errors": {"non_field_errors": ["Each record must be an object."]},
            }

        serializer = self.get_serializer(data=raw)
        if not serializer.is_valid():
            return {
                "index": index,
                "barcode": raw.get("barcode"),
                "status": "error",
                "errors": serializer.errors,
            }

        try:
            product = serializer.save()
        except Exception as exc:  # noqa: BLE001 - report, never 500 the batch
            return {
                "index": index,
                "barcode": raw.get("barcode"),
                "status": "error",
                "errors": {"non_field_errors": [str(exc)]},
            }

        return {
            "index": index,
            "barcode": product.barcode,
            "status": "created",
            "id": product.id,
            "product": serializer.data,
        }


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    """``GET`` / ``PUT`` / ``PATCH`` / ``DELETE`` a single product by id.

    The detail route is keyed on the numeric id rather than the barcode
    deliberately: barcodes here are themselves numeric ("1345"), so a shared
    route would be ambiguous about which one it meant. To address a product by
    its barcode use ``?search=`` on the list endpoint, or the upsert-by-barcode
    behaviour of ``POST /api/tally-product/``.

    ``DELETE`` removes the row for good (scan-log history is kept, with the
    product reference nulled). To take a product off the screen without
    losing it, ``PATCH {"is_active": false}`` instead.
    """

    serializer_class = ProductCrudSerializer
    permission_classes = [ReadOnlyOrAPIKey]
    # Deactivated products must stay reachable here, or you could never
    # re-activate one through the API.
    queryset = Product.objects.select_related("category", "company")


# ---------------------------------------------------------------------------
# 5. Session login/logout
# ---------------------------------------------------------------------------


@api_view(["POST"])
def api_login(request: Request) -> Response:
    """Sign in over JSON and start the same session the HTML login page uses.

    For clients that cannot submit the kiosk's HTML form — a JS fetch from a
    custom screen, a companion app on the same LAN. Same "remember this
    device" semantics as ``kiosk.views.KioskLoginView``: ticking it keeps the
    session for a year, leaving it off forgets it when the browser closes.
    """
    username = request.data.get("username", "")
    password = request.data.get("password", "")
    if not username or not password:
        return Response(
            {"detail": "username and password are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response(
            {"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED
        )

    login(request, user)
    if request.data.get("remember_device", True):
        request.session.set_expiry(REMEMBERED_SESSION_AGE)
    else:
        request.session.set_expiry(0)  # until the browser closes

    return Response(
        {"detail": "Logged in.", "user": {"username": user.username, "is_staff": user.is_staff}}
    )


@api_view(["POST"])
def api_logout(request: Request) -> Response:
    """End the current session, if any.

    Unlike ``api_login``, this runs with a session already attached, so DRF's
    SessionAuthentication enforces CSRF here: the caller must send the
    ``csrftoken`` cookie's value back as ``X-CSRFToken``, same as any other
    authenticated POST to this API.
    """
    logout(request)
    return Response({"detail": "Logged out."})


# ---------------------------------------------------------------------------
# API root
# ---------------------------------------------------------------------------


@api_view(["GET"])
def api_root(request: Request) -> Response:
    """Index of available endpoints, handy in the browsable API."""
    return Response(
        {
            "lookup": reverse("api:lookup", request=request) + "?code=1345",
            "lookup_multiple": reverse("api:lookup", request=request) + "?code=1345,1346",
            "settings": reverse("api:settings", request=request),
            "stations": reverse("api:stations", request=request),
            "products": reverse("api:product-list", request=request),
            "product_detail": reverse("api:product-list", request=request) + "<id>/",
            "tally_sync": reverse("api:tally-sync", request=request),
            "login": reverse("api:login", request=request),
            "logout": reverse("api:logout", request=request),
        }
    )
