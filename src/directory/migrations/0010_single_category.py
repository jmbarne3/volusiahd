"""One category per program, not several.

A program filed under three headings is a program nobody can find, and the
filter bar only reads correctly if every program sits under exactly one. Tags
carry everything else, which is what they are for.

Order matters: add the column, carry a value across, then drop the join tables.
Where a record had more than one category the lowest `sort_order` wins — the
heading the editor ranked first — and the rest are dropped. Nothing preserves
them, so this is lossy by intent rather than by accident; at the time of writing
no record in either table had more than one.

Reversing restores the join table and puts the single category back into it.
That round-trips whatever survived, not whatever was dropped.
"""

import django.db.models.deletion
from django.db import migrations, models

MODELS = ["Program", "Submission"]


def keep_the_first_category(apps, schema_editor):
    for model_name in MODELS:
        model = apps.get_model("directory", model_name)
        for record in model.objects.prefetch_related("categories"):
            first = min(
                record.categories.all(),
                key=lambda c: (c.sort_order, c.name),
                default=None,
            )
            if first is not None:
                record.category = first
                record.save(update_fields=["category"])


def back_into_the_join_table(apps, schema_editor):
    for model_name in MODELS:
        model = apps.get_model("directory", model_name)
        for record in model.objects.exclude(category=None):
            record.categories.add(record.category)


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0009_submission_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="category",
            field=models.ForeignKey(
                blank=True,
                help_text="The one heading this program is filed under.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="programs",
                to="directory.category",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submissions",
                to="directory.category",
            ),
        ),
        migrations.RunPython(keep_the_first_category, back_into_the_join_table),
        migrations.RemoveField(model_name="program", name="categories"),
        migrations.RemoveField(model_name="submission", name="categories"),
    ]
