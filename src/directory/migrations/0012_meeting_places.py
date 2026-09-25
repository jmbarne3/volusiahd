"""Where a program meets stops being a paragraph and becomes rows on a map.

Hand-edited from the generated version so that the table exists before the text
field is dropped: each non-empty line of the old `Program.locations` becomes one
`ProgramLocation`, waiting to be looked up. Nothing is geocoded here — a
migration that makes a few hundred network calls is a migration that fails
halfway through somebody else's outage.

The relation is created as `locations_from_text` and renamed to `locations` at
the end. For the length of this migration both would be called `locations` —
the old text field and the new accessor — and the accessor wins, which makes the
text field unreadable exactly when it needs to be read. The rename is a state
change only and touches no data.

Reversing this puts the lines back into a text field, one per line, and throws
away every coordinate. That is the honest reverse: there is nowhere in a text
field to put a latitude.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def lines_into_rows(apps, schema_editor):
    """One row per line, in the order they were written."""
    Program = apps.get_model("directory", "Program")
    ProgramLocation = apps.get_model("directory", "ProgramLocation")
    rows = []
    for pk, locations in Program.objects.exclude(locations="").values_list("pk", "locations"):
        lines = [line.strip() for line in locations.splitlines() if line.strip()]
        rows += [
            ProgramLocation(program_id=pk, query=line[:255], sort_order=order)
            for order, line in enumerate(lines)
        ]
    ProgramLocation.objects.bulk_create(rows)


def rows_back_into_lines(apps, schema_editor):
    Program = apps.get_model("directory", "Program")
    ProgramLocation = apps.get_model("directory", "ProgramLocation")
    written = {}
    for row in ProgramLocation.objects.order_by("sort_order", "pk"):
        written.setdefault(row.program_id, []).append(row.label or row.query)
    for pk, lines in written.items():
        Program.objects.filter(pk=pk).update(locations="\n".join(lines))


class Migration(migrations.Migration):

    dependencies = [
        ('directory', '0011_category_tags'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProgramLocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('query', models.CharField(help_text='One place. A street address resolves best, but a landmark or a city works. Change this and the lookup runs again on save.', max_length=255, verbose_name='address as entered')),
                ('label', models.CharField(blank=True, help_text='What the lookup matched, and what the site shows. Overwrite it if the match is right but the wording is wrong.', max_length=255, verbose_name='resolved address')),
                ('latitude', models.FloatField(blank=True, help_text='Filled in by the lookup. Type one in yourself if you know better.', null=True, validators=[django.core.validators.MinValueValidator(-90), django.core.validators.MaxValueValidator(90)])),
                ('longitude', models.FloatField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(-180), django.core.validators.MaxValueValidator(180)])),
                ('city', models.CharField(blank=True, max_length=80)),
                ('postcode', models.CharField(blank=True, max_length=16)),
                ('osm_type', models.CharField(blank=True, max_length=1)),
                ('osm_id', models.BigIntegerField(blank=True, null=True)),
                ('status', models.CharField(choices=[('pending', 'Not looked up yet'), ('resolved', 'Resolved to a point on the map'), ('failed', 'No match found')], default='pending', max_length=10)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0, help_text='Lower numbers first. The first one is what listings show.')),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='locations_from_text', to='directory.program')),
            ],
            options={
                'verbose_name': 'meeting place',
                'verbose_name_plural': 'meeting places',
                'ordering': ['sort_order', 'pk'],
                'indexes': [models.Index(fields=['latitude', 'longitude'], name='directory_p_latitud_9f4660_idx')],
            },
        ),
        migrations.RunPython(lines_into_rows, rows_back_into_lines),
        migrations.RemoveField(
            model_name="program",
            name="locations",
        ),
        migrations.AlterField(
            model_name="programlocation",
            name="program",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="locations",
                to="directory.program",
            ),
        ),
    ]
