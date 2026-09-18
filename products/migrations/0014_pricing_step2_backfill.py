"""
STEP 2 of 3: give every existing product real values for the new pricing
fields, computed from what is already there — nothing is guessed:

    retail_price = old mrp if it was set (and genuinely higher), else old price
    given_disc   = whatever % turns retail_price back into the old price,
                   so today's real selling prices carry over unchanged
    wholesale_price = the existing Wholesale ProductPrice, else old price

gst/hsn have no prior source data — they stay at their step-1 defaults
(0 / blank) and are meant to be filled in via the admin after this ships.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations


def backfill_pricing(apps, schema_editor):
    Product = apps.get_model("products", "Product")
    ProductPrice = apps.get_model("products", "ProductPrice")

    wholesale_prices = {
        row["product_id"]: row["price"]
        for row in ProductPrice.objects.filter(
            price_list__slug="wholesale"
        ).values("product_id", "price")
    }

    updated = 0
    for product in Product.objects.all():
        old_price = product.price
        old_mrp = product.mrp

        if old_mrp and old_mrp > old_price:
            retail_price = old_mrp
            given_disc = ((old_mrp - old_price) / old_mrp * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            retail_price = old_price
            given_disc = Decimal("0")

        product.retail_price = retail_price
        product.given_disc = given_disc
        product.wholesale_price = wholesale_prices.get(product.pk, old_price)
        product.save(update_fields=["retail_price", "given_disc", "wholesale_price"])
        updated += 1

    print(f"\n    backfilled pricing for {updated} product(s)")


def clear_pricing(apps, schema_editor):
    """Reverse: blank the new fields back out."""
    Product = apps.get_model("products", "Product")
    Product.objects.update(retail_price=None, wholesale_price=None, given_disc=Decimal("0"))


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0013_pricing_step1_add_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_pricing, clear_pricing),
    ]
