"""
Local development settings — run on your own computer, never on the server.

    python manage.py runserver 127.0.0.1:8001 --settings=config.settings_local

settings.py is the live server's configuration (DEBUG off, PostgreSQL,
HTTPS-only cookies and redirects). This file takes all of it and then
switches back to what a laptop needs, reading the values from the .env of
the ar_price_taller_2 checkout so both copies run with the same secrets.

settings.py itself is left alone, so the server keeps exactly the
configuration it had.
"""

from pathlib import Path

from decouple import Config, Csv, RepositoryEnv

from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR

ENV_FILE = Path(r"D:\MPA\Python\Chat_gpt_cloude\Projects\ar_price_taller_2\.env")
env = Config(RepositoryEnv(str(ENV_FILE)))

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = env("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
TALLY_API_KEY = env("TALLY_API_KEY", default=TALLY_API_KEY)  # noqa: F405
TIME_ZONE = env("TIME_ZONE", default="Asia/Kolkata")
KIOSK_REQUIRE_LOGIN = env("KIOSK_REQUIRE_LOGIN", default=True, cast=bool)
SESSION_COOKIE_AGE = env("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 365, cast=int)

# settings.py switched these on because its DEBUG is False. Plain http on
# 127.0.0.1 has no certificate, so they would redirect every request to
# https and drop the session cookie.
SECURE_PROXY_SSL_HEADER = None
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = env("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = False

# The .env names SQLite. The file is a copy of ar_price_taller_2's database
# placed next to manage.py, so nothing done here can touch that project's data.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Product pictures and logos live with the other checkout. Uploads made from
# this copy's admin will be written there too.
MEDIA_ROOT = ENV_FILE.parent / "media"
