"""Remove the seven parent-involvement answers that the new five replace (#10).

The deploy step loads `taxonomy.json` after migrating, and loading a fixture adds
and updates but never deletes. Without this, the old answers would stay on the
registration form next to the new ones. The order works out: this runs first and
removes the old tags, then the fixture adds the new ones.

Any program or registration carrying an old answer loses it. When this was
written, production had no programs, so nothing was attached to them. The new
answers are combinations rather than one-for-one renames (see #10), so there is
no mechanical way to translate old answers into new ones.
"""

from django.db import migrations

RETIRED = [
    "drop-off-allowed",
    "drop-off-only",
    "families-stay-all-day",
    "families-stay-part-of-the-day",
    "parents-may-attend",
    "parents-must-attend",
    "parents-must-help",
]


def retire(apps, schema_editor):
    apps.get_model("directory", "Tag").objects.filter(slug__in=RETIRED).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0017_instructor_info_wording"),
    ]

    operations = [
        migrations.RunPython(retire, migrations.RunPython.noop),
    ]
