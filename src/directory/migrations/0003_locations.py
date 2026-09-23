"""Fold street, city and ZIP into a single free-text `locations` field.

A program that meets in three churches on three different days was never
expressible in one street/city/ZIP row, and that is the normal case here, not
the exception. One address per line handles it, and handles "we rotate between
members' homes" too, which no structured address field ever could.

The order matters: add the new column, carry the old values across, and only
then drop the old columns. Reversing this is deliberately lossy — an address
that came back would land in `street` whole, because we cannot reliably split
a line a human typed back into its parts.
"""

from django.db import migrations, models


def combine(apps, schema_editor):
    for model_name in ["Program", "Submission"]:
        model = apps.get_model("directory", model_name)
        for record in model.objects.exclude(street="", city="", zip_code=""):
            town = " ".join(part for part in [record.city, record.zip_code] if part)
            record.locations = ", ".join(part for part in [record.street, town] if part)
            record.save(update_fields=["locations"])


def split_back(apps, schema_editor):
    for model_name in ["Program", "Submission"]:
        model = apps.get_model("directory", model_name)
        for record in model.objects.exclude(locations=""):
            record.street = record.locations.splitlines()[0][:160]
            record.save(update_fields=["street"])


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0002_submission_age_max_submission_age_min_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="locations",
            field=models.TextField(
                blank=True,
                help_text="One address per line. A program that meets in several places "
                "gets a line for each; a program that moves around can say so in words "
                "instead.",
                verbose_name="where they meet",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="locations",
            field=models.TextField(blank=True, verbose_name="where they meet"),
        ),
        migrations.RunPython(combine, split_back),
        migrations.RemoveField(model_name="program", name="city"),
        migrations.RemoveField(model_name="program", name="street"),
        migrations.RemoveField(model_name="program", name="zip_code"),
        migrations.RemoveField(model_name="submission", name="city"),
        migrations.RemoveField(model_name="submission", name="street"),
        migrations.RemoveField(model_name="submission", name="zip_code"),
    ]
