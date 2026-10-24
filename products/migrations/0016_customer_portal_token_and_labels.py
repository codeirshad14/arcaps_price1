"""Customer portal: an unguessable entry URL, and admin-editable wording.

`access_token` is added in three steps rather than one because it is unique
and non-nullable: a single AddField would either need a callable default
(which would give every existing row the SAME value, breaking uniqueness) or
fail outright on a table that already has a row.
"""

import uuid

from django.db import migrations, models


def fill_tokens(apps, schema_editor):
    """Give every existing settings row its own token."""
    Settings = apps.get_model("products", "CustomerPortalSettings")
    for row in Settings.objects.filter(access_token=""):
        row.access_token = uuid.uuid4().hex
        row.save(update_fields=["access_token"])


def clear_tokens(apps, schema_editor):
    """Reverse of fill_tokens — the column is about to be dropped anyway."""
    Settings = apps.get_model("products", "CustomerPortalSettings")
    Settings.objects.update(access_token="")


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0015_pricing_step3_cleanup"),
    ]

    operations = [
        # --- Step 1: the column, not yet unique -----------------------------
        migrations.AddField(
            model_name="customerportalsettings",
            name="access_token",
            field=models.CharField(
                default="",
                editable=False,
                help_text="Random part of the customer entry URL.",
                max_length=32,
            ),
            preserve_default=False,
        ),
        # --- Step 2: one distinct value per row -----------------------------
        migrations.RunPython(fill_tokens, clear_tokens),
        # --- Step 3: now uniqueness can hold --------------------------------
        migrations.AlterField(
            model_name="customerportalsettings",
            name="access_token",
            field=models.CharField(
                editable=False,
                help_text="Random part of the customer entry URL.",
                max_length=32,
                unique=True,
            ),
        ),
        # --- Wording, all optional with built-in fallbacks ------------------
        migrations.AddField(
            model_name="customerportalsettings",
            name="navbar_title",
            field=models.CharField(
                blank=True,
                help_text="Name shown in the top bar of the customer pages. "
                "Leave blank to use the station's store name.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="navbar_subtitle",
            field=models.CharField(
                blank=True,
                help_text="Small line under the top-bar name. Leave blank for "
                "'Price check'.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="welcome_heading",
            field=models.CharField(
                blank=True,
                help_text="Heading above the code box on the price-check page. "
                "Leave blank to use the station's welcome message.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="prompt_title",
            field=models.CharField(
                blank=True,
                help_text="Big prompt inside the code window. Leave blank for "
                "'Enter the product code'.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="prompt_subtitle",
            field=models.CharField(
                blank=True,
                help_text="Small line under the prompt. Leave blank for "
                "'It is printed on the tag'.",
                max_length=160,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="submit_label",
            field=models.CharField(
                blank=True,
                help_text="Text on the button that looks up the price. Leave "
                "blank for 'Show Price'.",
                max_length=60,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="finish_label",
            field=models.CharField(
                blank=True,
                help_text="Text on the button that ends the visit. Leave blank "
                "for 'Done'.",
                max_length=60,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="idle_heading",
            field=models.CharField(
                blank=True,
                help_text="Heading in the empty panel before a price is looked "
                "up. Leave blank for 'Ready when you are'.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="customerportalsettings",
            name="poster_instructions",
            field=models.TextField(
                blank=True,
                help_text="Paragraph printed under the QR code on the poster. "
                "Leave blank for the standard wording.",
            ),
        ),
    ]
