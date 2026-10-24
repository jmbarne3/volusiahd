"""Grade levels replace free-text grades and an age range.

Programs and registrations now tick the levels they take — Pre-K, elementary,
middle, high — and the old `serves_grades`, `age_min` and `age_max` columns go.
Before they go, each row's levels are worked out from what it said:

1. The grades text, when it reads as grades: "K-5", "grades 6-12", "3rd–8th",
   "high school only", "preschool", "all ages".
2. Otherwise the age range, with boundaries drawn so that a K–5 program listed
   as ages 5–11 does not also land in middle school. An eleven-year-old can be
   in fifth grade or sixth; the text, where there is any, is the better witness.
3. Otherwise nothing. A program with no levels shows under every level, so it
   is never hidden — it just stops being precise until somebody ticks boxes.

Every row that fell through to 2 or 3 is printed, so whoever runs this on
production can see which listings to check. The old values cannot be restored by
reversing this migration; the backup is the way back.
"""

import re

from django.db import migrations

from directory.models import GradeLevelsField

LEVELS = ["prek", "elementary", "middle", "high"]

# A grade as a number: Pre-K is -1, kindergarten 0.
_GRADE = r"(pre-?k|k|\d{1,2})(?:st|nd|rd|th)?"
_RANGE = re.compile(rf"\b{_GRADE}\s*(?:-|–|—|to|through|thru)\s*{_GRADE}\b")
_SINGLE = re.compile(r"\b(?:grade\s+(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+grade)\b")


def _grade(token):
    token = token.replace("-", "")
    return {"prek": -1, "k": 0}.get(token, None) if not token.isdigit() else int(token)


def _levels_for_grades(low, high):
    found = set()
    if low <= -1:
        found.add("prek")
    if low <= 5 and high >= 0:
        found.add("elementary")
    if low <= 8 and high >= 6:
        found.add("middle")
    if high >= 9:
        found.add("high")
    return found


def levels_from_text(text):
    """The levels a free-text grades answer names, or an empty set if it names none."""
    text = (text or "").lower()
    found = set()
    for low, high in _RANGE.findall(text):
        low, high = _grade(low), _grade(high)
        if low is not None and high is not None and low <= high <= 12:
            found |= _levels_for_grades(low, high)
    for one, other in _SINGLE.findall(text):
        grade = int(one or other)
        if grade <= 12:
            found |= _levels_for_grades(grade, grade)
    if re.search(r"\b(pre-?k|preschool|pre-school|vpk)\b", text):
        found.add("prek")
    if "elementary" in text:
        found.add("elementary")
    if "middle" in text:
        found.add("middle")
    if "high school" in text:
        found.add("high")
    if re.search(r"\ball (ages|grades|levels)\b", text):
        found |= set(LEVELS)
    return found


def levels_from_ages(youngest, oldest):
    """The levels an age range covers, cautiously at the edges."""
    if youngest is None and oldest is None:
        return set()
    youngest = 0 if youngest is None else youngest
    oldest = 18 if oldest is None else oldest
    found = set()
    if youngest <= 4:
        found.add("prek")
    if youngest <= 10 and oldest >= 6:
        found.add("elementary")
    if youngest <= 13 and oldest >= 12:
        found.add("middle")
    if oldest >= 15:
        found.add("high")
    return found


def convert(apps, schema_editor):
    for model_name in ["Program", "Submission"]:
        model = apps.get_model("directory", model_name)
        unsure = []
        for row in model.objects.all():
            levels = levels_from_text(row.serves_grades)
            if not levels:
                levels = levels_from_ages(row.age_min, row.age_max)
                # Said something, but not in words we could read as grades.
                if row.serves_grades or row.age_min is not None or row.age_max is not None:
                    unsure.append((row, levels))
            row.grade_levels = ",".join(level for level in LEVELS if level in levels)
            row.save(update_fields=["grade_levels"])
        if unsure:
            print(f"\n  {model_name}: grade levels to check by hand —")
            for row, levels in unsure:
                ages = f"ages {row.age_min}–{row.age_max}"
                print(
                    f"    #{row.pk} {row}: grades {row.serves_grades!r}, {ages} "
                    f"→ {', '.join(sorted(levels)) or 'none'}"
                )


class Migration(migrations.Migration):
    dependencies = [
        ("directory", "0014_program_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="program",
            name="grade_levels",
            field=GradeLevelsField(
                blank=True,
                help_text="Tick every level they take. Families filter by this, and a "
                "program with none ticked shows up under every level.",
                verbose_name="grade levels",
            ),
        ),
        migrations.AddField(
            model_name="submission",
            name="grade_levels",
            field=GradeLevelsField(
                blank=True,
                help_text="Tick every level they take. Families filter by this, and a "
                "program with none ticked shows up under every level.",
                verbose_name="grade levels",
            ),
        ),
        migrations.RunPython(convert, migrations.RunPython.noop),
        migrations.RemoveField(model_name="program", name="serves_grades"),
        migrations.RemoveField(model_name="program", name="age_min"),
        migrations.RemoveField(model_name="program", name="age_max"),
        migrations.RemoveField(model_name="submission", name="serves_grades"),
        migrations.RemoveField(model_name="submission", name="age_min"),
        migrations.RemoveField(model_name="submission", name="age_max"),
    ]
