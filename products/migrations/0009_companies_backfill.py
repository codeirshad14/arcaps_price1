"""
STEP 2 of 3: give every existing row a company.

Everything in the database before this point belonged to a single business,
so it all moves to one default company. The company FK can only be made
non-nullable (step 3) once no product is left without one.
"""

from django.db import migrations
from django.utils.text import slugify


def create_default_company(apps, schema_editor):
    Company = apps.get_model("products", "Company")
    Product = apps.get_model("products", "Product")
    DisplayProfile = apps.get_model("products", "DisplayProfile")

    # Name it after the store if a station already carries one, so the admin
    # shows something recognisable rather than "Default Company".
    station = DisplayProfile.objects.filter(is_default=True).first() or (
        DisplayProfile.objects.order_by("pk").first()
    )
    name = (getattr(station, "store_name", "") or "").strip() or "Default Company"

    company, _ = Company.objects.get_or_create(
        slug=slugify(name)[:120] or "default",
        defaults={"name": name[:120], "is_default": True, "is_active": True},
    )

    # Existing stations keep showing everything (company left blank), which
    # preserves current behaviour exactly. Only products need an owner.
    updated = Product.objects.filter(company__isnull=True).update(company=company)
    if updated:
        print(f"\n    assigned {updated} product(s) to '{company.name}'")


def unassign(apps, schema_editor):
    """Reverse: detach products so the column can be dropped again."""
    Product = apps.get_model("products", "Product")
    Product.objects.update(company=None)


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0008_companies_step1"),
    ]

    operations = [
        migrations.RunPython(create_default_company, unassign),
    ]
