"""Contacts are never published, so the flag that published them goes.

A boolean that decides whether a person's name and phone number appear on a
public page is a mistake waiting for a mis-click. Removing it makes the rule
structural rather than a matter of remembering: there is no longer any value
of any field that puts a contact on the site.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0005_drop_published_contact"),
    ]

    operations = [
        migrations.RemoveField(model_name="contactperson", name="is_public"),
    ]
