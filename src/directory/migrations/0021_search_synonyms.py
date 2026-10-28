"""Search synonyms, edited in the admin, starting with theater/theatre (#20).

After this the list belongs to whoever runs the directory. It is not in the
taxonomy fixture, so a deploy never puts back a group they deleted.
"""

from django.db import migrations, models

STARTING_GROUPS = ["theater, theatre"]


def seed(apps, schema_editor):
    SearchSynonym = apps.get_model("directory", "SearchSynonym")
    for words in STARTING_GROUPS:
        SearchSynonym.objects.get_or_create(words=words)


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0020_retire_part_of_the_day"),
    ]

    operations = [
        migrations.CreateModel(
            name="SearchSynonym",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "words",
                    models.CharField(
                        help_text="Two or more single words that mean the same thing, separated by commas — e.g. 'theater, theatre'. Capitals do not matter.",
                        max_length=300,
                    ),
                ),
            ],
            options={
                "verbose_name": "search synonym",
                "verbose_name_plural": "search synonyms",
                "ordering": ["words"],
            },
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
