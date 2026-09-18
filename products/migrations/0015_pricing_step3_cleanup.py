"""
STEP 3 of 3: every product now has real retail_price/wholesale_price
values (step 2), so make them required, and remove what they replaced —
the old price/mrp columns, the per-station price_list, and the PriceList/
ProductPrice models entirely.
"""

from decimal import Decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0014_pricing_step2_backfill"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="retail_price",
            field=models.DecimalField(
                max_digits=12, decimal_places=2,
                help_text="List price, before discount and GST.",
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="wholesale_price",
            field=models.DecimalField(
                max_digits=12, decimal_places=2,
                help_text="Bulk-buyer rate, before GST.",
                validators=[django.core.validators.MinValueValidator(Decimal("0"))],
            ),
        ),
        migrations.RemoveField(model_name="product", name="price"),
        migrations.RemoveField(model_name="product", name="mrp"),
        migrations.RemoveField(model_name="displayprofile", name="price_list"),
        migrations.DeleteModel(name="ProductPrice"),
        migrations.DeleteModel(name="PriceList"),
    ]
