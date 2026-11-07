"""Suggested edits to a listed program (#49): what changed, and applying it.

A suggested edit is a `Submission` of kind EDIT. The public form is the
registration form pre-filled from the program, so a submission holds the whole
listing, not just the parts somebody touched. Working out which parts those are
is this module's job, and it compares against `edit_baseline` — the program as
it stood when the edit was sent — rather than against the program now. The
difference matters the day an editor fixes a phone number in the admin while a
family's edit about the meeting days is waiting: applying the family's edit
should change the days and leave her phone number alone.

Only what changed is copied. A program's description is rich text and the
public form takes plain text, so an untouched description keeps its formatting;
only one somebody actually rewrote replaces it.
"""

import difflib
import html
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils.html import escape, strip_tags
from django.utils.safestring import mark_safe
from django.utils.text import capfirst

from .models import Program, ProgramLocation, SharedProgramFields, Submission

# Where the two models name a field differently, the submission's name is used
# as the key and this maps it to the program's.
PROGRAM_ATTR = {"program_name": "name"}


def field_keys():
    """Every comparable field, in the order the registration form asks them."""
    return [
        "program_name",
        "short_description",
        "description",
        "category",
        "tags",
        *SharedProgramFields.shared_field_names(),
        "locations",
    ]


def description_text(rich):
    """A program's rich-text description as the plain text the public form takes.

    Paragraphs, headings and list items each become a paragraph, separated by a
    blank line — which is exactly what `Submission.description_as_html` splits
    on, so an untouched description round-trips to the same paragraphs.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", rich or "")
    text = re.sub(r"(?i)</(p|h[1-6]|li|blockquote|div)>", "\n\n", text)
    return normalise_text(html.unescape(strip_tags(text)))


def normalise_text(text):
    """Paragraphs with tidy whitespace, so a browser's \\r\\n is not a change."""
    paragraphs = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    return "\n\n".join(" ".join(p.split()) for p in paragraphs if p.strip())


def _shared_value(obj, name):
    # Django's own serialisation, so a time, a month and a weekday list all
    # compare as the same kind of thing whichever model they came from.
    return SharedProgramFields._meta.get_field(name).value_to_string(obj)


def program_values(program):
    """The program's answers, in a form that survives being stored as JSON."""
    values = {
        name: _shared_value(program, name) for name in SharedProgramFields.shared_field_names()
    }
    values.update(
        program_name=program.name,
        short_description=normalise_text(program.short_description),
        description=description_text(program.description),
        category=program.category_id,
        tags=sorted(program.tags.values_list("pk", flat=True)),
        locations=[location.query for location in program.locations.all()],
    )
    return values


def submission_values(submission):
    """The same answers, as the person who suggested the edit left them."""
    values = {
        name: _shared_value(submission, name) for name in SharedProgramFields.shared_field_names()
    }
    values.update(
        program_name=submission.program_name,
        short_description=normalise_text(submission.short_description),
        description=normalise_text(submission.description),
        category=submission.category_id,
        tags=sorted(submission.tags.values_list("pk", flat=True)),
        locations=[location.query for location in submission.locations.all()],
    )
    return values


@dataclass(frozen=True)
class Change:
    key: str
    label: str
    current: str
    suggested: str
    # The program has changed this same field since the edit was sent, so
    # applying it would overwrite somebody else's correction.
    changed_since: bool = False


def changes(submission):
    """What this edit would change, as `Change` rows. Empty if nothing."""
    program = submission.edits_program
    if program is None:
        return []
    baseline = submission.edit_baseline or {}
    suggested = submission_values(submission)
    current = program_values(program)

    rows = [
        Change(
            key=key,
            label=label_for(key),
            current=display(key, program),
            suggested=display(key, submission),
            changed_since=key in baseline and current[key] != baseline[key],
        )
        for key in field_keys()
        if key in baseline and suggested[key] != baseline[key]
    ]
    if submission.logo:
        rows.append(
            Change(
                key="logo",
                label="Logo",
                current="Current logo" if program.logo else "—",
                suggested="A new logo was uploaded",
            )
        )
    return rows


def is_stale(submission):
    """True if the program was saved after this edit was suggested."""
    program = submission.edits_program
    return bool(program and program.updated_at > submission.created_at)


def label_for(key):
    from .forms import ProgramRegistrationForm

    labels = ProgramRegistrationForm._meta.labels
    if key in labels:
        return labels[key]
    if key in ("category", "tags", "locations"):
        return {"category": "Category", "tags": "Tags", "locations": "Where they meet"}[key]
    return capfirst(Submission._meta.get_field(key).verbose_name)


def display(key, obj):
    """One field of a program or a submission, as an editor reads it."""
    if key == "tags":
        names = sorted(tag.name for tag in obj.tags.all())
        return ", ".join(names) or "—"
    if key == "locations":
        places = [location.display_name for location in obj.locations.all()]
        return "; ".join(places) or "—"
    if key == "category":
        return obj.category.name if obj.category else "—"
    if key == "description":
        text = description_text(obj.description) if isinstance(obj, Program) else obj.description
        return normalise_text(text) or "—"
    if key == "grade_levels":
        return obj.grade_levels_display or "—"
    if key == "meeting_days":
        return obj.meeting_days_display or "—"

    attr = PROGRAM_ATTR.get(key, key) if isinstance(obj, Program) else key
    value = getattr(obj, attr)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    field = type(obj)._meta.get_field(attr)
    if field.choices and value not in (None, ""):
        return str(dict(field.flatchoices).get(value, value))
    if value in (None, ""):
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%-I:%M %p")
    return str(value)


@transaction.atomic
def apply(submission):
    """Copy what this edit changed onto its program. Returns the changes applied.

    The program keeps its address (slug), its status and everything the
    person did not touch. Applying is a confirmation that the listing is right,
    so it also counts as verifying it today.
    """
    program = submission.edits_program
    applied = changes(submission)
    keys = {change.key for change in applied}

    for name in SharedProgramFields.shared_field_names():
        if name in keys:
            setattr(program, name, getattr(submission, name))
    if "program_name" in keys:
        program.name = submission.program_name
    if "short_description" in keys:
        program.short_description = submission.short_description
    if "description" in keys:
        program.description = submission.description_as_html()
    if "category" in keys:
        program.category = submission.category
    if "logo" in keys:
        submission.copy_logo_to(program)

    program.mark_verified()
    program.save()

    if "tags" in keys:
        program.tags.set(submission.tags.all())
    if "locations" in keys:
        program.locations.all().delete()
        ProgramLocation.objects.bulk_create(
            [
                ProgramLocation(program=program, **place.copied_values())
                for place in submission.locations.all()
            ]
        )

    submission.status = Submission.Status.APPROVED
    submission.save(update_fields=["status"])
    return applied


# Inline, because the admin's stylesheet is Unfold's and a class of ours would
# need a stylesheet of ours to mean anything. Translucent, so they read on the
# light theme and the dark one alike.
ADDED = '<mark style="background:rgba(34,197,94,.3);color:inherit;border-radius:2px">{}</mark>'
REMOVED = (
    '<del style="background:rgba(239,68,68,.25);color:inherit;border-radius:2px;'
    'text-decoration-thickness:1px">{}</del>'
)


def highlight(current, suggested):
    """Both values as HTML, with the words that differ marked.

    Word by word rather than letter by letter: "Thursdays" against "Tuesdays"
    should read as one word swapped, not as three scattered letters. Spaces and
    line breaks are kept as they are and never marked, so the paragraphs of a
    description still line up.
    """
    if current == "—" or suggested == "—":
        # Nothing on one side: the whole of the other side is the change.
        return (
            mark_safe(REMOVED.format(escape(current)) if current != "—" else current),
            mark_safe(ADDED.format(escape(suggested)) if suggested != "—" else suggested),
        )

    before, after = re.split(r"(\s+)", current), re.split(r"(\s+)", suggested)
    old, new = [], []
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        old.append(_marked(before[a1:a2], None if op == "equal" else REMOVED))
        new.append(_marked(after[b1:b2], None if op == "equal" else ADDED))
    return mark_safe("".join(old)), mark_safe("".join(new))


def _marked(tokens, template):
    """A run of tokens, marked as one block if it changed.

    One mark around the whole run, so three new words read as one addition,
    with any space at its edges left outside it.
    """
    text = "".join(tokens)
    core = text.strip()
    if template is None or not core:
        return escape(text)
    lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]
    return escape(lead) + template.format(escape(core)) + escape(trail)
