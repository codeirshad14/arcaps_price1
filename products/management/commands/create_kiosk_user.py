"""
Management command: create a login for showroom staff.

    python manage.py create_kiosk_user rakesh
    python manage.py create_kiosk_user rakesh --password "some pass"
    python manage.py create_kiosk_user manager --staff        # + admin access

A plain kiosk user can open the price-checker screen and nothing else. They
cannot reach /admin/, so there is no risk of a counter login being used to
edit prices. Pass --staff only for someone who should also manage the
catalogue.
"""

from __future__ import annotations

import getpass
import secrets
import string

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.password_validation import validate_password


def _readable_password(length: int = 12) -> str:
    """Generate a password that is easy to read off a screen and type once.

    Ambiguous characters (0/O, 1/l/I) are excluded — these get written on a
    sticky note by the till, and a misread character is a support call.
    """
    alphabet = "".join(
        c for c in string.ascii_letters + string.digits if c not in "0O1lI"
    )
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Command(BaseCommand):
    help = "Create a login for showroom staff to open the price-checker screen."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Login name, e.g. counter1")
        parser.add_argument(
            "--password",
            help="Set the password directly. Omit to be prompted, or use "
            "--generate for a random one.",
        )
        parser.add_argument(
            "--generate",
            action="store_true",
            help="Generate a random password and print it once.",
        )
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Also allow access to the admin site (manage products).",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Full admin rights. Implies --staff.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"].strip()

        if not username:
            raise CommandError("Username cannot be blank.")
        if User.objects.filter(username__iexact=username).exists():
            raise CommandError(f"A user named '{username}' already exists.")

        password = options.get("password")
        generated = False

        if options["generate"]:
            password = _readable_password()
            generated = True
        elif not password:
            password = getpass.getpass("Password: ")
            confirm = getpass.getpass("Password (again): ")
            if password != confirm:
                raise CommandError("The two passwords did not match.")

        if not password:
            raise CommandError("Password cannot be blank.")

        # Run Django's own password rules so a weak counter password is caught
        # here rather than becoming the showroom's weakest link.
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("Password rejected:\n  " + "\n  ".join(exc.messages))

        is_superuser = options["superuser"]
        is_staff = options["staff"] or is_superuser

        user = User.objects.create_user(
            username=username,
            password=password,
            is_staff=is_staff,
            is_superuser=is_superuser,
        )

        role = (
            "superuser (full admin)"
            if is_superuser
            else "staff (kiosk + admin)"
            if is_staff
            else "kiosk only (no admin access)"
        )
        self.stdout.write(self.style.SUCCESS(f"Created '{user.username}' — {role}."))

        if generated:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"  Password: {password}"))
            self.stdout.write("  Shown once. Write it down now.")

        self.stdout.write("")
        self.stdout.write("Sign in on the showroom PC at /login/ and tick")
        self.stdout.write('"Remember this system" — it will not ask again.')
