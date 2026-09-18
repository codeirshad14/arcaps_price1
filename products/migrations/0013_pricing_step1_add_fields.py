"""
STEP 1 of 3: simplify pricing from the PriceList/ProductPrice system to
plain fields on Product (retail_price, given_disc, wholesale_price, gst,
hsn), and rename alt_code to alias (Tally's own term for the same idea).

New price fields are added nullable for now — step 2 backfills every
existing product from its current price/mrp/wholesale ProductPrice, and
step 3 makes them required once every row has a value.
"""

from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0012_customerportalsettings_customersession_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="product",
            old_name="alt_code",
            new_name="alias",
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="unique_alt_code_per_company",
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                fields=("company", "alias"), name="unique_alias_per_company"
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="retail_price",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, null=True,
                help_text="List price, before discount and GST.",
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="wholesale_price",
            field=models.DecimalField(
                max_digits=12, decimal_places=2, null=True,
                help_text="Bulk-buyer rate, before GST.",
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="given_disc",
            field=models.DecimalField(
                verbose_name="discount %",
                max_digits=5, decimal_places=2, default=Decimal("0"),
                help_text="Percentage off the retail price. 0 means no discount.",
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="gst",
            field=models.DecimalField(
                verbose_name="GST %",
                max_digits=5, decimal_places=2, default=Decimal("0"),
                help_text="Tax percentage. Added on top of both prices below.",
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0")),
                    django.core.validators.MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="hsn",
            field=models.CharField(
                verbose_name="HSN/SAC code",
                max_length=8, blank=True,
                help_text="For GST filing. Not shown on the customer screen.",
            ),
        ),
    ]
