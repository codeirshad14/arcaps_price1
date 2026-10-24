"""Stop treating Tally's GUID as an identity.

This export reuses a GUID across several stock items, so the per-company
uniqueness constraint rejected rows Tally considers perfectly normal — and
because the sync preferred the GUID as its match key, it also merged unrelated
products into one row. Both problems go away by keying on the part number
(held in `barcode`) and demoting the GUID to reference data.

Dropping a constraint is safe to re-apply in reverse only while no duplicate
GUIDs exist; once Tally has synced, reversing this migration will fail until
the duplicates are cleared. That is the expected trade-off, not a bug.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0017_screen_header_settings'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='product',
            name='unique_tally_guid_per_company',
        ),
        migrations.AlterField(
            model_name='product',
            name='tally_guid',
            field=models.CharField(blank=True, db_index=True, help_text="Tally's own identifier, kept for reference only. Tally reuses these across stock items, so it is NOT unique and is not used to match records — the part number (barcode) is.", max_length=100, null=True),
        ),
    ]
