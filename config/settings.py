"""
Django settings for the Showroom Price Checker.

All environment-specific values (secret key, debug flag, database, Tally API
key) are read from a `.env` file via python-decouple so the same codebase runs
unchanged on a developer laptop and on the showroom PC. See `.env.example`.
"""

import mimetypes
from pathlib import Path

from decouple import Csv, config

# Windows has no registry entry for .woff2, so Django's static server hands the
# vendored fonts out as application/octet-stream. Browsers cope, but the
# correct type avoids surprises behind a strict proxy or CSP.
mimetypes.add_type("font/woff2", ".woff2", True)
mimetypes.add_type("font/woff", ".woff", True)

# Project folder — the one containing manage.py. Worked out automatically, so
# it is correct wherever the project is copied to and needs no editing.
BASE_DIR = Path(__file__).resolve().parent.parent


# ===========================================================================
#  SITE SETTINGS
#
#  This is the only block you normally need to edit. Everything below it
#  is worked out from these few values.
# ===========================================================================

# Your website address. No "http://" and no "www." — both are added for you.
DOMAIN = "arcapworks.in"

# True while working on your own computer.
# MUST be False on the live server, or visitors can see error pages with
# your settings in them.
DEBUG = False

# --- Database --------------------------------------------------------------
# False -> the db.sqlite3 file next to manage.py (your own computer)
# True  -> PostgreSQL, using the details below (the live server)
USE_POSTGRES = True

DB_NAME = "arcapsprice"
DB_USER = "arcapsprice"
DB_PASSWORD = "arcapspriceA@#$"
DB_HOST = "127.0.0.1"
DB_PORT = "5432"

# ===========================================================================
#  Nothing below here normally needs changing.
# ===========================================================================


# ---------------------------------------------------------------------------
# Core security settings
# ---------------------------------------------------------------------------

SECRET_KEY = config("SECRET_KEY", default="dev-only-insecure-key-change-me")

# Which addresses the site will answer to. The domain is listed with and
# without "www." because people type both, and the two local addresses stay
# so the showroom PC can always reach it directly.
ALLOWED_HOSTS = [DOMAIN, f"www.{DOMAIN}", "127.0.0.1", "localhost"]

# Logging into the admin over HTTPS fails without this: Django checks that a
# form was posted from an address it trusts, and on a real domain that check
# is done against the full https:// origin, not just the host name.
CSRF_TRUSTED_ORIGINS = [f"https://{DOMAIN}", f"https://www.{DOMAIN}"]


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    # Jazzmin must precede django.contrib.admin — it overrides admin templates.
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "corsheaders",
    "import_export",
    # Local
    "products",
    "api",
    "kiosk",
    "customer_portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # CorsMiddleware must sit above CommonMiddleware.
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-wide templates live in `templates/`; each app may still ship
        # its own under `<app>/templates/`.
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ---------------------------------------------------------------------------
# Database
#
# Switched by USE_POSTGRES at the top of this file. Both descriptions live
# here together so it is obvious what the switch actually changes.
# ---------------------------------------------------------------------------

if USE_POSTGRES:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DB_NAME,
            "USER": DB_USER,
            "PASSWORD": DB_PASSWORD,
            "HOST": DB_HOST,
            "PORT": DB_PORT,
            # Hold each connection open for reuse rather than building a new
            # one per request. PostgreSQL connections are costly enough that
            # this is the difference between a snappy screen and a visible
            # pause on every scan.
            #
            # Must stay below PostgreSQL's own idle timeout, or Django will
            # hand out a connection the server has already dropped.
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# Should the kiosk screen itself require a login? On a showroom PC the answer
# is usually yes — staff sign in once and the machine stays signed in.
KIOSK_REQUIRE_LOGIN = config("KIOSK_REQUIRE_LOGIN", default=True, cast=bool)

# ---------------------------------------------------------------------------
# Sessions
#
# A showroom PC must not be asked to log in again every fortnight, so the
# cookie lasts a year and every request pushes the expiry further out. Ticking
# "remember this device" off at login shortens it to the browser session
# instead — see kiosk.views.KioskLoginView.
# ---------------------------------------------------------------------------

SESSION_COOKIE_AGE = config("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 365, cast=int)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = True          # rolling expiry
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_HTTPONLY = True


# ---------------------------------------------------------------------------
# HTTPS
#
# Switched on automatically whenever DEBUG is False, so the live site is
# protected without a second thing to remember, and your own computer — which
# has no certificate — is left alone.
# ---------------------------------------------------------------------------

if not DEBUG:
    # Nginx terminates HTTPS and talks to Django over plain HTTP, so Django
    # sees an insecure request and would otherwise redirect forever. This
    # header is how Nginx tells it the visitor's connection really was secure.
    # It is only safe because Nginx sets the header itself on every request,
    # overwriting anything a visitor tries to send.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

    # Cookies must never travel over plain HTTP: the session cookie is what
    # keeps staff signed in, and the CSRF cookie protects every admin form.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SECURE_SSL_REDIRECT = True

    # HSTS tells browsers to refuse plain HTTP for this domain in future.
    # Deliberately left off until HTTPS is confirmed working: browsers
    # remember it for as long as it says, so switching it on before the
    # certificate is right can lock people out of the site with no way to
    # undo it from the server. Raise it to 31536000 (one year) once the
    # padlock has been showing for a few days.
    SECURE_HSTS_SECONDS = 0


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
# Hand-written CSS/JS lives here and is tracked in git.
STATICFILES_DIRS = [BASE_DIR / "static"]
# `collectstatic` target for production; not used by runserver.
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded product images and the store logo.
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# Tally integration
# ---------------------------------------------------------------------------

# Shared secret required in the X-API-Key header on POST /api/tally-product/.
TALLY_API_KEY = "MwKTgK4rwcdIF6eGKZJ5eK8vKZpFjSwLaNuH8mBBKbk"


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    # The kiosk page is public and read-only; write endpoints enforce their own
    # API-key permission class, so the global default stays open.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        # Browsable API is genuinely useful for debugging the Tally contract.
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
}


# ---------------------------------------------------------------------------
# CORS
#
# Same-origin in the showroom, so this stays closed by default. Open it only if
# you later serve the kiosk page from a different host than the API.
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------

# Wrap each import in a transaction so a half-valid spreadsheet cannot leave
# the catalogue in a partial state.
IMPORT_EXPORT_USE_TRANSACTIONS = True
IMPORT_EXPORT_SKIP_ADMIN_LOG = True


# ---------------------------------------------------------------------------
# Jazzmin admin theming
# ---------------------------------------------------------------------------

JAZZMIN_SETTINGS = {
    "site_title": "Price Checker Admin",
    "site_header": "AR Caps",
    "site_brand": "Price Checker",
    "site_logo": None,
    "login_logo": None,
    "welcome_sign": "Sign in to manage products and the showroom display",
    "copyright": "AR Caps",
    # Global admin search box searches products by barcode/name.
    "search_model": ["products.Product"],
    "topmenu_links": [
        {"name": "Home", "url": "admin:index", "permissions": ["auth.view_user"]},
        # Opens the customer-facing screen in a new tab so the admin session
        # stays put in the current one.
        {"name": "View Price Checker Screen", "url": "kiosk:screen", "new_window": True},
        {"name": "Customer QR Poster", "url": "customer:poster", "new_window": True},
        {"name": "API Root", "url": "/api/", "new_window": True},
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "products.Category": "fas fa-tags",
        "products.Product": "fas fa-box-open",
        "products.DisplayProfile": "fas fa-desktop",
        "products.ScanLog": "fas fa-barcode",
        "products.CustomerSession": "fas fa-user-clock",
        "products.CustomerPortalSettings": "fas fa-qrcode",
    },
    # Jazzmin hides logout inside the username dropdown, where people do not
    # find it. This script adds a visible one to the top bar; see the file for
    # why it is JS rather than a template override.
    "custom_js": "admin_extra/admin_logout.js",
    "custom_css": "admin_extra/admin_extra.css",
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {
        # The station form has many grouped fieldsets; tabs down the side keep
        # it navigable.
        "products.DisplayProfile": "vertical_tabs",
    },
}

JAZZMIN_UI_TWEAKS = {
    # Jazzmin's template already puts `bg-body` on the navbar, which is WHITE
    # in the light theme. Pairing that with "navbar-dark" — which only sets
    # the *text* scheme to light — painted every navbar item white on white:
    # the account icon vanished entirely and the logout link was barely
    # legible. The text scheme has to match the background it sits on.
    "navbar": "navbar-light",
    "navbar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_fixed": True,
    "theme": "flatly",
    # Jazzmin 3.x drives light/dark via data-bs-theme; "dark_mode_theme" is gone.
    "default_theme_mode": "light",
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "warning": "btn-warning",
        "danger": "btn-danger",
        "success": "btn-success",
    },
}
