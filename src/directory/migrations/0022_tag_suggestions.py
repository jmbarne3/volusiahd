"""Tag suggestions from the registration form, for the owner to decide (#25)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0021_search_synonyms"),
    ]

    operations = [
        migrations.CreateModel(
            name="TagSuggestion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=60, verbose_name="suggested tag")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Waiting for a decision"),
                            ("approved", "Approved as a new tag"),
                            ("mapped", "Used an existing tag instead"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tag_suggestions",
                        to="directory.submission",
                    ),
                ),
                (
                    "tag",
                    models.ForeignKey(
                        blank=True,
                        help_text="The tag this suggestion became, or the existing tag used instead.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="suggestions",
                        to="directory.tag",
                    ),
                ),
            ],
            options={
                "verbose_name": "tag suggestion",
                "verbose_name_plural": "tag suggestions",
                "ordering": ["-created_at"],
            },
        ),
    ]
