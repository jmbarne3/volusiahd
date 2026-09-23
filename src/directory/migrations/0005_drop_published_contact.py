"""Stop collecting a named person to publish alongside a program.

Families reach a program through its website, Facebook page, email or phone.
The people behind it are our records, not the directory's content, and the
"About you" fields on a registration already tell us who to call.

Dropping five columns from a live table throws away whatever registrants
already typed into them, so anything present is folded into `review_notes`
first — that field exists for exactly this, is never shown publicly, and is
where she would look for it anyway. `ContactPerson` rows already attached to a
published program are untouched.
"""

from django.db import migrations

FIELDS = [
    ("contact_name", "Name"),
    ("contact_role", "Role"),
    ("contact_email", "Email"),
    ("contact_phone", "Phone"),
]


def preserve_in_review_notes(apps, schema_editor):
    Submission = apps.get_model("directory", "Submission")
    for submission in Submission.objects.exclude(
        contact_name="", contact_role="", contact_email="", contact_phone=""
    ):
        lines = [
            f"{label}: {value}"
            for field, label in FIELDS
            if (value := getattr(submission, field))
        ]
        note = "Contact given at registration — " + "; ".join(lines)
        submission.review_notes = f"{submission.review_notes}\n\n{note}".strip()
        submission.save(update_fields=["review_notes"])


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0004_facebook"),
    ]

    operations = [
        # Irreversible by design: once the columns are gone the note in
        # `review_notes` is the record, and parsing it back would be guesswork.
        migrations.RunPython(preserve_in_review_notes, migrations.RunPython.noop),
        migrations.RemoveField(model_name="submission", name="contact_email"),
        migrations.RemoveField(model_name="submission", name="contact_is_public"),
        migrations.RemoveField(model_name="submission", name="contact_name"),
        migrations.RemoveField(model_name="submission", name="contact_phone"),
        migrations.RemoveField(model_name="submission", name="contact_role"),
    ]
