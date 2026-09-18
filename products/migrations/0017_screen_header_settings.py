"""One home for the top bar shared by the counter screen and the customer page.

The name used to be configured twice: `DisplayProfile.store_name` drove the
kiosk, and `CustomerPortalSettings.navbar_title` drove the customer page. The
two could disagree, and changing the shop name meant editing both. The new
singleton owns it for both screens, and the two customer-only fields go away.

Anything already typed into the customer-only fields is carried across before
they are dropped, so nobody loses wording they had set.
"""

from django.db import migrations, models


def carry_navbar_wording_across(apps, schema_editor):
    """Move any configured customer navbar wording to the shared settings."""
    Portal = apps.get_model("products", "CustomerPortalSettings")
    Header = apps.get_model("products", "ScreenHeaderSettings")

    portal = Portal.objects.first()
    if portal is None:
        return

    title = (portal.navbar_title or "").strip()
    subtitle = (portal.navbar_subtitle or "").strip()
    if not (title or subtitle):
        return

    header = Header.objects.first() or Header()
    if title and not (header.navbar_name or "").strip():
        header.navbar_name = title
    # The old subtitle was customer-only, so it lands on the customer label.
    if subtitle and not (header.customer_label or "").strip():
        header.customer_label = subtitle
    header.save()


def restore_navbar_wording(apps, schema_editor):
    """Reverse: put the shared wording back on the customer settings row."""
    Portal = apps.get_model("products", "CustomerPortalSettings")
    Header = apps.get_model("products", "ScreenHeaderSettings")

    portal = Portal.objects.first()
    header = Header.objects.first()
    if portal is None or header is None:
        return
    portal.navbar_title = header.navbar_name or ""
    portal.navbar_subtitle = header.customer_label or ""
    portal.save()


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0016_customer_portal_token_and_labels"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScreenHeaderSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "navbar_name",
                    models.CharField(
                        blank=True,
                        help_text="Name shown in the top bar of BOTH the "
                        "counter screen and the customer page. Leave blank to "
                        "use the station's store name.",
                        max_length=120,
                    ),
                ),
                (
                    "counter_label",
                    models.CharField(
                        blank=True,
                        help_text="Word under the name on the staff counter "
                        "screen. Leave blank for 'Counter'.",
                        max_length=60,
                    ),
                ),
                (
                    "customer_label",
                    models.CharField(
                        blank=True,
                        help_text="Word under the name on the customer's own "
                        "phone. Leave blank for 'Customer'.",
                        max_length=60,
                    ),
                ),
                (
                    "show_station_on_counter",
                    models.BooleanField(
                        default=True,
                        help_text="Also show which station and price list the "
                        "counter screen is using. Useful while setting up, "
                        "noise once it is running.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "screen header settings",
                "verbose_name_plural": "screen header settings",
            },
        ),
        # Carry the values over BEFORE the old columns are dropped.
        migrations.RunPython(carry_navbar_wording_across, restore_navbar_wording),
        migrations.RemoveField(
            model_name="customerportalsettings",
            name="navbar_title",
        ),
        migrations.RemoveField(
            model_name="customerportalsettings",
            name="navbar_subtitle",
        ),
    ]
