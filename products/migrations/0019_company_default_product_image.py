"""A per-company fallback picture for products that arrive without one.

A Tally sync brings across prices and stock but never images, so a freshly
synced catalogue was a wall of "No Image Available" cards. The company owns
this rather than a shop-wide setting because every product already belongs to
one, so there is always a fallback to reach for without adding a settings
model to hold a single field.

Applied by `Product.save()` at creation only. Re-applying it on every save
would make removing a product's picture impossible — the next save would put
the default straight back.

Nothing is backfilled: existing products keep whatever they have, including
nothing.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0018_drop_tally_guid_uniqueness'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='default_product_image',
            field=models.ImageField(blank=True, help_text="Picture given to a <strong>newly created</strong> product of this company that arrives without one — whether it is added here or pushed in from Tally. Changing it later does not touch products that already exist, and a product whose picture is removed by hand stays without one. Leave empty to keep showing the 'No Image Available' placeholder instead.", null=True, upload_to='branding/'),
        ),
    ]
