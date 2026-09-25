"""A submitted address becomes a row too, with the point the submitter chose.

Same shape as 0012, and the same hand edit for the same reason: the new relation
wants the accessor name `locations`, the old text field already has it, and the
accessor wins — so the copy has to happen while the relation is still called
something else. Every line somebody typed becomes a row waiting to be looked up.

Reversing this writes the lines back into a text field and drops the
coordinates, which is the only honest reverse available.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models



def lines_into_rows(apps, schema_editor):
    """One row per line, in the order they were written."""
    Submission = apps.get_model("directory", "Submission")
    SubmissionLocation = apps.get_model("directory", "SubmissionLocation")
    rows = []
    for pk, locations in Submission.objects.exclude(locations="").values_list("pk", "locations"):
        lines = [line.strip() for line in locations.splitlines() if line.strip()]
        rows += [
            SubmissionLocation(submission_id=pk, query=line[:255], sort_order=order)
            for order, line in enumerate(lines)
        ]
    SubmissionLocation.objects.bulk_create(rows)


def rows_back_into_lines(apps, schema_editor):
    Submission = apps.get_model("directory", "Submission")
    SubmissionLocation = apps.get_model("directory", "SubmissionLocation")
    written = {}
    for row in SubmissionLocation.objects.order_by("sort_order", "pk"):
        written.setdefault(row.submission_id, []).append(row.label or row.query)
    for pk, lines in written.items():
        Submission.objects.filter(pk=pk).update(locations="\n".join(lines))


class Migration(migrations.Migration):

    dependencies = [
        ('directory', '0012_meeting_places'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubmissionLocation',
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
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='locations_from_text', to='directory.submission')),
            ],
            options={
                'verbose_name': 'place',
                'verbose_name_plural': 'places they named',
                'ordering': ['sort_order', 'pk'],
                'abstract': False,
            },
        ),
        migrations.RunPython(lines_into_rows, rows_back_into_lines),
        migrations.RemoveField(
            model_name="submission",
            name="locations",
        ),
        migrations.AlterField(
            model_name="submissionlocation",
            name="submission",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="locations",
                to="directory.submission",
            ),
        ),
    ]
