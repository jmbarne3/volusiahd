"""Remove the "Part of the day" parent-involvement answer (#19).

Loading the fixture on deploy never deletes, so taking the answer out of
`seed_taxonomy` alone would leave it on the co-ops form in production. Any
program or registration carrying it loses that answer.
"""

from django.db import migrations

RETIRED = ["part-of-the-day"]


def retire(apps, schema_editor):
    apps.get_model("directory", "Tag").objects.filter(slug__in=RETIRED).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0019_fold_highlights_into_description"),
    ]

    operations = [
        migrations.RunPython(retire, migrations.RunPython.noop),
    ]
