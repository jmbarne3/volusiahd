"""Remove "What makes it special" and keep what people wrote in it (#18).

The full description now asks for what makes a program special, so the
separate field only repeated it. Before the column goes, anything in it is
added to the end of the description, so nothing a provider wrote is lost.

`Program.description` is rich text, so highlights go in as escaped paragraphs.
`Submission.description` is plain text, so they go in after a blank line.
"""

import re

from django.db import migrations
from django.utils.html import escape


def _paragraphs(text):
    blocks = [escape(block.strip()) for block in re.split(r"\n\s*\n", text)]
    return "".join(f"<p>{block}</p>" for block in blocks if block)


def fold_highlights(apps, schema_editor):
    Program = apps.get_model("directory", "Program")
    Submission = apps.get_model("directory", "Submission")

    for program in Program.objects.exclude(highlights=""):
        program.description = (program.description or "") + _paragraphs(program.highlights)
        program.save(update_fields=["description"])

    for submission in Submission.objects.exclude(highlights=""):
        parts = [submission.description.strip(), submission.highlights.strip()]
        submission.description = "\n\n".join(part for part in parts if part)
        submission.save(update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0018_retire_parent_involvement_tags"),
    ]

    operations = [
        # Not reversible in any useful way: once merged, the two texts can't be
        # told apart again. Going back restores an empty column.
        migrations.RunPython(fold_highlights, migrations.RunPython.noop),
        migrations.RemoveField(model_name="program", name="highlights"),
        migrations.RemoveField(model_name="submission", name="highlights"),
    ]
