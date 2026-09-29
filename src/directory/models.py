"""Data model for the directory.

The shape of these models determines the admin experience, which is the thing
that has to still be working in eighteen months. Every field that is ambiguous
to a non-technical editor carries `help_text`; that text is the documentation.

Faith affiliation is a field here, and for most of this project's life it
deliberately was not. The argument against it was that a program describes its
own character better in prose than a checkbox can. The argument that won is that
eight of the thirteen kinds of program we list asked for it by name, and that for
a great many families it is the first thing they want to know and the last thing
they should have to go hunting through paragraphs for. It stays optional, and
"not stated" is a perfectly good answer to publish.
"""

import re
import time

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.text import capfirst
from django_prose_editor.fields import ProseEditorField

from . import geocoding

# A deliberately narrow toolbar. A full word-processor toolbar invites
# inconsistent typography that then has to be cleaned up by hand across
# hundreds of records. Sanitization is derived from this same dict, so the
# editor and the allowlist cannot drift apart.
RICH_TEXT_CONFIG = {
    "extensions": {
        "Bold": True,
        "Italic": True,
        "Link": {"enableTarget": False, "protocols": ["http", "https", "mailto"]},
        "BulletList": True,
        "ListItem": True,
        "OrderedList": True,
        "Heading": {"levels": [3]},
        "History": True,
    }
}


WEEKDAYS = [
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
]
WEEKDAY_ORDER = [code for code, _ in WEEKDAYS]
WEEKDAY_NAMES = dict(WEEKDAYS)


def split_weekdays(value):
    """Whatever a weekday value holds, as an ordered list of valid codes.

    Accepts the stored string, a list from a form, or None, and always returns
    the days in week order rather than the order somebody happened to tick them.
    Anything unrecognised is dropped rather than raising: a garbled value should
    mean "we do not know which days" and not a 500 on a program page.
    """
    if not value:
        return []
    codes = value.split(",") if isinstance(value, str) else list(value)
    codes = {str(code).strip().lower() for code in codes}
    return [code for code in WEEKDAY_ORDER if code in codes]


def join_weekdays(value):
    """The canonical stored form: week order, comma separated, no spaces."""
    return ",".join(split_weekdays(value))


def validate_weekdays(value):
    codes = value.split(",") if isinstance(value, str) else list(value or [])
    unknown = [code for code in codes if code and code not in WEEKDAY_NAMES]
    if unknown:
        raise ValidationError(f"Not days of the week: {', '.join(unknown)}.")


class WeekdayChoiceField(forms.MultipleChoiceField):
    """Seven checkboxes in, one stored string out."""

    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs):
        kwargs.setdefault("choices", WEEKDAYS)
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def prepare_value(self, value):
        # The model hands back "mon,thu"; the checkboxes need ["mon", "thu"].
        return split_weekdays(value) if isinstance(value, str) else value

    def clean(self, value):
        return join_weekdays(super().clean(value))

    def has_changed(self, initial, data):
        return join_weekdays(initial) != join_weekdays(data)


class WeekdaysField(models.CharField):
    """The days of the week something meets, as a comma separated list of codes.

    Text rather than seven boolean columns, a join table, or a bitmask, and the
    reason is one convenient accident: no day's three-letter code is a substring
    of another's. That makes `filter(meeting_days__contains="thu")` an exact
    test rather than a near-miss waiting to happen, so the one query this field
    exists to answer — "what meets on Thursdays" — needs no cleverness at all.

    It is a scan rather than an index seek. Over a directory of a few hundred
    programs that is not worth a column per day; if this ever becomes the slow
    part of a page, the honest fix is seven generated columns and not a rewrite
    of everything that reads it.
    """

    def __init__(self, *args, **kwargs):
        # Seven codes and six commas. Set here rather than at each use so the two
        # concrete models cannot disagree about it.
        kwargs["max_length"] = 27
        kwargs.setdefault("blank", True)
        kwargs.setdefault("validators", [validate_weekdays])
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("max_length", None)
        return name, path, args, kwargs

    def get_prep_value(self, value):
        # A list assigned straight onto the instance is normalised on the way to
        # the database, so a shell script and the form store the same thing.
        if not isinstance(value, str) and value is not None:
            value = join_weekdays(value)
        return super().get_prep_value(value)

    def formfield(self, **kwargs):
        # Built directly rather than through `super()`, which would hand a
        # `max_length` and an `empty_value` to a field that takes neither.
        defaults = {
            "required": not self.blank,
            "label": capfirst(self.verbose_name),
            "help_text": self.help_text,
        }
        defaults.update(kwargs)
        for unusable in ["max_length", "empty_value", "widget"]:
            defaults.pop(unusable, None)
        return WeekdayChoiceField(**defaults)


class SanitizedRichTextMixin(models.Model):
    """Sanitize every rich text field on save, not just on form validation.

    `ProseEditorField.clean()` only runs during `full_clean()`, which means the
    admin is covered but a spreadsheet import or a shell script is not. Since
    the templates render this HTML with `|safe`, the sanitization has to happen
    at the database boundary rather than at the form boundary.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        for field in self._meta.get_fields():
            if isinstance(field, ProseEditorField):
                value = getattr(self, field.attname, None)
                if value:
                    setattr(self, field.attname, field.sanitize(value))
        return super().save(*args, **kwargs)


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True, help_text="Used in the web address.")
    description = models.TextField(
        blank=True,
        help_text="One or two sentences shown at the top of the category page.",
    )
    sort_order = models.PositiveIntegerField(
        default=100,
        help_text="Lower numbers appear first. Use 10, 20, 30 so you can insert between later.",
    )
    icon = models.CharField(
        max_length=40,
        blank=True,
        help_text="Optional Material Symbols icon name, e.g. 'groups' or 'sports_soccer'.",
    )
    # Which tags a registrant may pick once they have chosen this heading.
    #
    # The relation is declared here rather than on Tag so that the editing
    # screen follows the model: the question we actually ask is "which tags
    # belong under Co-ops", which is a question about the heading, not one to
    # answer a hundred times over on each tag in turn. A tag can sit under
    # several headings — "Lego" is a class and a club — so this is many to many
    # and not a parent.
    tags = models.ManyToManyField(
        "Tag",
        related_name="categories",
        blank=True,
        help_text="The tags a registrant may choose after picking this heading. "
        "A tag can appear under more than one heading. A tag under no heading is "
        "ours to apply here in the admin and is never offered on the public form.",
    )

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("directory:category", kwargs={"slug": self.slug})

    def question_groups(self):
        """The tag questions this heading asks, each with only its own answers.

        Derived rather than stored. A heading offers a tag through `tags`, so it
        asks a question exactly when it offers one of that question's answers —
        which means there is one screen to maintain instead of two that can
        disagree about whether Sports asks about ability level.
        """
        grouped = {}
        for tag in self.tags.all():
            if tag.group_id:
                grouped.setdefault(tag.group, []).append(tag)
        return sorted(grouped.items(), key=lambda pair: (pair[0].sort_order, pair[0].name))

    def plain_tags(self):
        """The tags that are not an answer to anything — ordinary subject tags."""
        return [tag for tag in self.tags.all() if not tag.group_id]


class TagGroup(models.Model):
    """A question whose answers are tags. "Ability level". "Parent involvement".

    Most of what looks like a field belonging to one kind of program turns out to
    be a question with a fixed set of answers — beginner or advanced, drop-off or
    stay and help — where the only thing that varies is who gets asked. Those are
    tags in a named group rather than columns, and that buys three things: a new
    question costs a row here instead of a migration, the answers are hers to
    reword without a deploy, and filtering by one goes through the same code path
    and the same index as filtering by any other tag.

    Which headings ask a question is deliberately not stored here. See
    `Category.question_groups`.
    """

    name = models.CharField(
        max_length=60,
        unique=True,
        help_text='The question, worded as a label: "Ability level", "Parent involvement".',
    )
    slug = models.SlugField(
        max_length=60,
        unique=True,
        help_text="Used in the web address when someone filters by this. Leave blank "
        "and it will be filled in from the name.",
    )
    prompt = models.CharField(
        max_length=200,
        blank=True,
        help_text="Optional. A sentence under the question on the registration form.",
    )
    allows_several = models.BooleanField(
        "more than one answer",
        default=True,
        help_text="Tick when a program can honestly pick several — a class may suit "
        "beginners and intermediates both. Untick when the answers are alternatives.",
    )
    sort_order = models.PositiveSmallIntegerField(
        default=100,
        help_text="Lower numbers appear first on the form.",
    )

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "tag question"
        verbose_name_plural = "tag questions"

    def __str__(self):
        return self.name


class Tag(models.Model):
    """The finer of the two taxonomies. Many more of these than categories.

    A category answers "what kind of thing is this" and there are fewer than
    ten, so every one can be listed on a filter bar. A tag answers anything
    else worth searching for — "Lego", "dual enrollment", "wheelchair
    accessible" — and there will be far too many to list, so tags are found
    rather than browsed: on a program's own page, on a tag page of their own,
    and through the search box.

    Created here and nowhere else. The registration form lets people pick from
    the list but never add to it, because the entire value of a tag is that the
    same idea always carries the same word, and free entry guarantees the
    opposite. Suggestions from registrants are a later problem, and a different
    one.

    Which tags a registrant is shown depends on the heading they chose; that
    pairing lives on `Category.tags`.
    """

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(
        max_length=60,
        unique=True,
        help_text="Used in the web address. Leave blank and it will be filled in from the name.",
    )
    description = models.TextField(
        blank=True,
        help_text="Optional. One or two sentences shown at the top of the tag's page.",
    )
    # SET_NULL rather than CASCADE: deleting a question should stop asking it,
    # not delete the answers and unfile every program that gave one.
    group = models.ForeignKey(
        TagGroup,
        related_name="tags",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Leave blank for an ordinary subject tag. Set it and this tag becomes "
        "one of the answers to that question, asked of any heading that offers it.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        """Qualified by its question, where it has one.

        This is what the admin's tag pickers show, and "Beginner" on its own in a
        list of two hundred tags tells nobody whether it is an answer about ability
        or a subject somebody invented. Public templates render `name`, so nothing
        a family reads changes.
        """
        return f"{self.group.name}: {self.name}" if self.group_id else self.name

    def get_absolute_url(self):
        return reverse("directory:tag", kwargs={"slug": self.slug})


def _clock(value):
    """A time of day as somebody would say it: 9 a.m., 9:30 a.m., noon.

    Written out rather than handed to `strftime`, because stripping the leading
    zero off an hour needs the GNU "%-I" extension and this has to give the same
    answer on a developer's Mac as it does on the server.
    """
    if value.minute == 0 and value.hour == 12:
        return "noon"
    if value.minute == 0 and value.hour == 0:
        return "midnight"
    hour = value.hour % 12 or 12
    clock = f"{hour}:{value.minute:02d}" if value.minute else str(hour)
    return f"{clock} {'a.m.' if value.hour < 12 else 'p.m.'}"


def _date(value, *, year=False):
    """September 2, or September 2, 2026 when the year is doing work."""
    text = f"{value.strftime('%B')} {value.day}"
    return f"{text}, {value.year}" if year else text


class SharedProgramFields(models.Model):
    """Every question a registration and a published program answer identically.

    The two models mirror each other on purpose — approving a registration is
    meant to be one click and no retyping — and a mirror maintained by hand
    across two class bodies is a mirror that drifts. Anything worded the same on
    both sides is declared here once, and `shared_values()` is what copies it
    across, so a field added here cannot be forgotten on the way to publication.

    What is deliberately not here is what genuinely differs: the name of the
    thing (`name` against `program_name`), the description (a rich text editor
    for us, plain text from a stranger), what deleting a category does, and where
    a logo is uploaded to.

    Nearly all of it is optional at the database level, and that is not
    laziness. The thirteen kinds of program in this directory ask overlapping but
    different sets of these questions — a testing service has no meeting days
    and a parent support group serves no grades — so which answers are required
    is a property of the heading, held in `questions.py`, and not something a
    column can know.
    """

    class Faith(models.TextChoices):
        FAITH_BASED = "faith", "Faith-based"
        SECULAR = "secular", "Not faith-based"

    class Cost(models.TextChoices):
        FREE = "free", "Free"
        PAID = "paid", "There is a cost"
        VARIES = "varies", "Varies"

    class Delivery(models.TextChoices):
        IN_PERSON = "in_person", "In person"
        ONLINE = "online", "Online"
        HYBRID = "hybrid", "In person or online"

    # --- Who is behind it ---------------------------------------------------

    host_name = models.CharField(
        "host or group name",
        max_length=120,
        blank=True,
        help_text="The larger group this runs under, when the listing is a class or a "
        "team rather than the group itself. Leave blank when they are the same thing.",
    )
    class_names = models.CharField(
        "classes offered",
        max_length=240,
        blank=True,
        help_text="The individual classes, separated by commas — "
        "e.g. 'Algebra I, Geometry, Chemistry'.",
    )
    instructor_info = models.TextField(
        "teachers and qualifications",
        blank=True,
        help_text="Who teaches, and what qualifies them. Families ask this before they "
        "ask almost anything else.",
    )
    highlights = models.TextField(
        "what makes it special",
        blank=True,
        help_text="Plain text. What a family gets here that they would not get "
        "elsewhere. Blank lines start new paragraphs.",
    )
    faith_basis = models.CharField(
        "faith affiliation",
        max_length=8,
        choices=Faith.choices,
        blank=True,
        help_text="Leave blank if they have not told us. 'Not faith-based' is a "
        "different answer from no answer, and both are fine to publish.",
    )

    # --- How families reach them -------------------------------------------

    website = models.URLField(blank=True)
    facebook = models.URLField(
        "Facebook page",
        blank=True,
        help_text="The full address of their Facebook page or group, "
        "e.g. 'https://facebook.com/groups/example'.",
    )
    email = models.EmailField(blank=True, help_text="The program's general contact address.")
    phone = models.CharField(max_length=32, blank=True)

    # --- Who it serves ------------------------------------------------------

    serves_grades = models.CharField(
        max_length=80,
        blank=True,
        help_text="Free text, e.g. 'K-8' or 'high school only'. Leave blank if it varies.",
    )
    age_min = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- When ---------------------------------------------------------------

    # Days of the week and a run of dates, asked of every kind of program. A
    # co-op meeting Tuesdays and Thursdays from September to May is the ordinary
    # case, and the three fields below say exactly that in a form a filter can
    # read. `meeting_schedule` survives underneath them for everything a
    # structure cannot hold: alternate weeks, times that vary by class, a season
    # that skips December.
    meeting_days = WeekdaysField(
        "days they meet",
        help_text="Tick every day they normally meet.",
    )
    meeting_time_start = models.TimeField(
        "starts at",
        null=True,
        blank=True,
        help_text="Leave blank if it varies.",
    )
    meeting_time_end = models.TimeField("ends at", null=True, blank=True)
    season_start = models.DateField(
        "first meeting",
        null=True,
        blank=True,
        help_text="The date this season or term starts. Leave blank if they run "
        "year round or have not set one.",
    )
    season_end = models.DateField("last meeting", null=True, blank=True)
    meeting_schedule = models.TextField(
        "anything else about when",
        blank=True,
        help_text="Free text, for what the boxes above cannot say — "
        "e.g. 'alternate Tuesdays' or 'times vary by class'.",
    )

    enrollment_opens = models.DateField(null=True, blank=True)
    enrollment_closes = models.DateField(null=True, blank=True)
    enrollment_notes = models.CharField(
        "enrollment, in words",
        max_length=160,
        blank=True,
        help_text="e.g. 'rolling admission' or 'waiting list only'.",
    )

    # --- What it costs ------------------------------------------------------

    cost_basis = models.CharField(
        "cost",
        max_length=8,
        choices=Cost.choices,
        blank=True,
        help_text="The headline answer. The detail goes in the box below.",
    )
    cost_notes = models.TextField(
        "cost, in detail",
        blank=True,
        help_text="Free text — fee structures never fit a single number. "
        "e.g. '$45/semester per family, plus a $20 materials fee'.",
    )

    # Step Up For Students runs Florida's scholarships through a marketplace
    # called EMA. A direct pay provider is one the family can pay from their
    # scholarship account without laying out the money first, which for many
    # families decides whether a program is affordable at all. The two
    # scholarships are separate approvals, so a provider can be one and not
    # the other.
    step_up_direct_pay = models.BooleanField(
        "Step Up direct pay provider",
        default=False,
        help_text="Tick if families can pay this program directly through "
        "Step Up For Students' EMA marketplace.",
    )
    step_up_pep = models.BooleanField(
        "direct pay for PEP",
        default=False,
        help_text="Personalized Education Program.",
    )
    step_up_fes_ua = models.BooleanField(
        "direct pay for FES-UA",
        default=False,
        help_text="Family Empowerment Scholarship for Students with Unique Abilities.",
    )

    # --- Where, and how far -------------------------------------------------

    delivery = models.CharField(
        "in person or online",
        max_length=10,
        choices=Delivery.choices,
        blank=True,
        help_text="Leave blank if they have not told us.",
    )
    travels_to_student_home = models.BooleanField(
        "will travel to the student",
        default=False,
        help_text="Tick if they come to the family rather than the other way round.",
    )
    service_area = models.CharField(
        "how far they travel",
        max_length=160,
        blank=True,
        help_text="e.g. 'west Volusia only' or 'up to 30 miles'. For programs with no "
        "one fixed address.",
    )

    class Meta:
        abstract = True

    @classmethod
    def shared_field_names(cls):
        """Every field declared on this base, in declaration order.

        Read off the class rather than typed out, because the whole point of the
        base is that the list cannot fall out of date. `build_program_from` and
        the test that guards it both work from this.
        """
        return [field.name for field in SharedProgramFields._meta.local_fields]

    def shared_values(self):
        """This record's shared answers, as keyword arguments for the other model."""
        return {name: getattr(self, name) for name in self.shared_field_names()}

    @property
    def age_range_display(self):
        if self.age_min and self.age_max:
            return f"Ages {self.age_min}-{self.age_max}"
        if self.age_min:
            return f"Ages {self.age_min} and up"
        if self.age_max:
            return f"Through age {self.age_max}"
        return ""

    @property
    def step_up_display(self):
        """The sentence a program page shows, empty if they are not a provider.

        A provider who is direct pay but has told us nothing about which
        scholarships still gets the headline, because that alone answers the
        question most families are asking.
        """
        if not self.step_up_direct_pay:
            return ""
        scholarships = [
            name
            for name, ticked in [("PEP", self.step_up_pep), ("FES-UA", self.step_up_fes_ua)]
            if ticked
        ]
        if not scholarships:
            return "Step Up direct pay"
        return f"Step Up direct pay ({', '.join(scholarships)})"

    @property
    def weekday_list(self):
        return split_weekdays(self.meeting_days)

    @property
    def meeting_days_display(self):
        """The days as a family reads them: Tuesdays and Thursdays."""
        names = [f"{WEEKDAY_NAMES[code]}s" for code in self.weekday_list]
        if len(names) > 2:
            return ", ".join(names[:-1]) + f" and {names[-1]}"
        return " and ".join(names)

    @property
    def meeting_time_display(self):
        if self.meeting_time_start and self.meeting_time_end:
            return f"{_clock(self.meeting_time_start)} to {_clock(self.meeting_time_end)}"
        if self.meeting_time_start:
            return f"from {_clock(self.meeting_time_start)}"
        if self.meeting_time_end:
            return f"until {_clock(self.meeting_time_end)}"
        return ""

    @property
    def season_display(self):
        if self.season_start and self.season_end:
            return f"{self.season_start.strftime('%B')} through {self.season_end.strftime('%B')}"
        if self.season_start:
            return f"from {_date(self.season_start)}"
        if self.season_end:
            return f"until {_date(self.season_end)}"
        return ""

    @property
    def when_display(self):
        """One line answering "when does this meet", however much we know."""
        parts = [self.meeting_days_display, self.meeting_time_display, self.season_display]
        return ", ".join(part for part in parts if part)

    @property
    def enrollment_display(self):
        """When you can sign up, in words, or the notes if there are no dates."""
        if self.enrollment_opens and self.enrollment_closes:
            return f"{_date(self.enrollment_opens)} to {_date(self.enrollment_closes, year=True)}"
        if self.enrollment_closes:
            return f"closes {_date(self.enrollment_closes, year=True)}"
        if self.enrollment_opens:
            return f"opens {_date(self.enrollment_opens, year=True)}"
        return self.enrollment_notes

    @property
    def cost_display(self):
        """The headline and the detail, in whichever combination exists."""
        headline = self.get_cost_basis_display() if self.cost_basis else ""
        detail = self.cost_notes.strip()
        if headline and detail:
            # "Free" beside a note saying what the note says would be noise; a
            # note that is only a number reads better with the headline on it.
            return detail if headline.lower() in detail.lower() else f"{headline} — {detail}"
        return detail or headline

    @property
    def is_online_option(self):
        return self.delivery in {self.Delivery.ONLINE, self.Delivery.HYBRID}


class ProgramQuerySet(models.QuerySet):
    """The filters the listing page is built from.

    Every one of them is a no-op when handed nothing, so the view can apply the
    whole set unconditionally and let an absent query parameter mean "do not
    narrow by this" rather than growing an `if` per filter.
    """

    def published(self):
        return self.filter(status=Program.Status.PUBLISHED)

    def meeting_on(self, day):
        """Programs that meet on a given weekday.

        A substring test, which is exact here and only here: see `WeekdaysField`
        for why no day's code can match another's.
        """
        if day not in WEEKDAY_NAMES:
            return self
        return self.filter(meeting_days__contains=day)

    def with_faith_basis(self, basis):
        if basis not in SharedProgramFields.Faith.values:
            return self
        return self.filter(faith_basis=basis)

    def online(self):
        return self.filter(
            delivery__in=[SharedProgramFields.Delivery.ONLINE, SharedProgramFields.Delivery.HYBRID]
        )

    def direct_pay(self):
        return self.filter(step_up_direct_pay=True)

    def serving_age(self, age):
        """Programs that could take a child of this age.

        A blank age bound means "we do not know", and an unknown bound has to
        widen the answer rather than narrow it — dropping every program that
        never filled in an age range would hide most of the directory from the
        one filter most likely to be used.
        """
        try:
            age = int(age)
        except (TypeError, ValueError):
            return self
        return self.filter(
            Q(age_min__lte=age) | Q(age_min__isnull=True),
            Q(age_max__gte=age) | Q(age_max__isnull=True),
        )

    def tagged(self, slugs):
        """Programs carrying every one of these tags, not any of them.

        Each tag a family adds should narrow the list. Chaining a filter per tag
        is what makes that true: a single `tags__slug__in` would widen it, and
        "beginner and Saturdays" would return every beginner class in the county.
        """
        programs = self
        for slug in dict.fromkeys(slug for slug in slugs if slug):
            programs = programs.filter(tags__slug=slug)
        return programs


class Program(SanitizedRichTextMixin, SharedProgramFields):
    """A single homeschool program. The core record of the directory.

    Most of its fields are on `SharedProgramFields`, alongside `Submission`.
    What is declared here is what a published record has and a registration does
    not: a slug, a status, a rich text description, and our own record keeping.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft — not visible on the site"
        PUBLISHED = "published", "Published — visible to everyone"
        ARCHIVED = "archived", "Archived — no longer operating"

    name = models.CharField(max_length=160)
    slug = models.SlugField(
        max_length=160,
        unique=True,
        help_text="Used in the web address. Leave blank and it will be filled in from the name.",
    )
    short_description = models.CharField(
        max_length=240,
        help_text="One sentence, plain text. This is what shows in listings and search results.",
    )
    description = ProseEditorField(
        blank=True,
        config=RICH_TEXT_CONFIG,
        sanitize=True,
        help_text="The full description shown on the program's own page.",
    )
    # One category, not several. A program that is three things at once is a
    # program nobody can file, and the filter bar only makes sense if every
    # program appears under exactly one heading. Tags carry everything else.
    #
    # PROTECT rather than SET_NULL: deleting a category that programs still
    # point at should stop and make someone re-file them, not quietly unfile
    # a dozen listings. The admin shows what is in the way.
    category = models.ForeignKey(
        Category,
        related_name="programs",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        help_text="The one heading this program is filed under.",
    )
    tags = models.ManyToManyField(
        Tag,
        related_name="programs",
        blank=True,
        help_text="The finer taxonomy. Add as many as genuinely apply — tags are "
        "how someone finds a program they could not have guessed the category of.",
    )

    # Where a program meets is `ProgramLocation`, edited inline in the admin. It
    # is a table rather than a text field because the next question families ask
    # is "how far is that from me", and no amount of free text answers it.

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Featured programs appear at the top of the home page.",
    )
    logo = models.ImageField(upload_to="logos/", blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_verified_on = models.DateField(
        null=True,
        blank=True,
        help_text="The last date someone confirmed this information is still correct. "
        "A directory dies of staleness, not of missing features.",
    )

    objects = ProgramQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("directory:program", kwargs={"slug": self.slug})

    @property
    def is_published(self):
        return self.status == self.Status.PUBLISHED

    @property
    def location_list(self):
        """The addresses, as a family should read them.

        A program that meets in three places is the normal case, not the odd
        one, so every template that shows a location loops over this rather than
        assembling a single address out of parts. Callers that render this for
        more than one program want `prefetch_related("locations")`.
        """
        return [location.display_name for location in self.locations.all()]

    @property
    def primary_location(self):
        """The first address, for listings that only have room for one."""
        locations = self.location_list
        return locations[0] if locations else ""

    @property
    def answered_questions(self):
        """The tag questions this program answered, as (question, answers) pairs.

        A program page shows "Ability level: beginner, intermediate" rather than
        loose chips, because an answer to a question read out of context is not
        obviously an answer to anything. Callers rendering more than one program
        want `prefetch_related("tags__group")`.
        """
        answers = {}
        for tag in self.tags.all():
            if tag.group_id:
                answers.setdefault(tag.group, []).append(tag)
        return sorted(answers.items(), key=lambda pair: (pair[0].sort_order, pair[0].name))

    @property
    def plain_tag_list(self):
        """The ordinary subject tags, which are chips and links like always."""
        return [tag for tag in self.tags.all() if not tag.group_id]

    def mark_verified(self, on=None):
        self.last_verified_on = on or timezone.localdate()


class LocationQuerySet(models.QuerySet):
    def pending(self):
        """The ones nothing has looked up yet — what a retry works through."""
        return self.filter(status=self.model.Status.PENDING)

    def mappable(self):
        """The ones a distance search can actually use."""
        return self.exclude(latitude=None).exclude(longitude=None)

    def look_up(self, *, pause=0.0):
        """Geocode these locations. Returns (resolved, missed, gave_up).

        Never raises. A geocoder that is not answering ends the run early and
        leaves the rest of the rows pending, because pending is exactly the
        state the next attempt looks for — and because working through two
        hundred addresses to collect two hundred identical failures is a way of
        being rude to a free service.

        `pause` is for batches. An editor saving three addresses is not bulk
        use and should not be made to wait; `manage.py geocode` passes the
        courtesy delay.
        """
        resolved = missed = 0
        for index, location in enumerate(self):
            if index and pause:
                time.sleep(pause)
            try:
                place = geocoding.resolve(location.query)
            except geocoding.GeocoderUnavailable:
                return resolved, missed, True
            location.apply(place)
            location.save()
            if place:
                resolved += 1
            else:
                missed += 1
        return resolved, missed, False


class BaseLocation(models.Model):
    """One place, as somebody wrote it and as it turned out to be.

    Two names, deliberately. `query` is what was typed — "the Presbyterian
    church on Ocean Ave" — and `label` is what the geocoder matched it to.
    Keeping both is what lets a lookup be run again later without losing the
    original wording, and what lets an editor see at a glance when a resolved
    address is not the place anyone meant.

    The coordinates are nullable and have to stay that way. An address that no
    geocoder recognises is still an address worth publishing, and a program that
    meets "at members' homes, term by term" has no point on a map at all.
    Anything that searches by distance is therefore searching a subset of the
    directory, and has to say so rather than quietly drop the rest.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Not looked up yet"
        RESOLVED = "resolved", "Resolved to a point on the map"
        FAILED = "failed", "No match found"

    query = models.CharField(
        "address as entered",
        max_length=255,
        help_text="One place. A street address resolves best, but a landmark or a "
        "city works. Change this and the lookup runs again on save.",
    )
    label = models.CharField(
        "resolved address",
        max_length=255,
        blank=True,
        help_text="What the lookup matched, and what the site shows. Overwrite it if "
        "the match is right but the wording is wrong.",
    )

    # FloatField rather than Decimal: what these feed is arithmetic — a haversine
    # distance, computed in the database — and mixing Decimal with float inside a
    # query expression is a reliable source of bugs that buys no accuracy. A
    # double carries a degree to eleven decimal places, which is millimetres,
    # against a street address that is ambiguous by several metres anyway.
    latitude = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        help_text="Filled in by the lookup. Type one in yourself if you know better.",
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    # Kept separately from the label because "programs in Deltona" is a question
    # answerable with an index, and pulling a city back out of a formatted
    # address string is not.
    city = models.CharField(max_length=80, blank=True)
    postcode = models.CharField(max_length=16, blank=True)

    # OpenStreetMap's own identifiers for the thing that matched. Not used yet;
    # they are what would let two programs meeting at the same church be
    # recognised as meeting at the same church.
    osm_type = models.CharField(max_length=1, blank=True)
    osm_id = models.BigIntegerField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING)
    resolved_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Lower numbers first. The first one is what listings show.",
    )

    objects = LocationQuerySet.as_manager()

    # Everything `apply()` and `forget_lookup()` write. Named once so that a
    # save with `update_fields` cannot quietly drop half of a reset.
    LOOKUP_FIELDS = [
        "label",
        "latitude",
        "longitude",
        "city",
        "postcode",
        "osm_type",
        "osm_id",
        "status",
        "resolved_at",
    ]

    # Everything except which record it hangs off, for copying a place from a
    # submission onto the program it becomes.
    COPY_FIELDS = ["query", "sort_order", *LOOKUP_FIELDS]

    class Meta:
        abstract = True
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        changed_query = self.query != getattr(self, "_loaded_query", self.query)
        changed_point = (self.latitude, self.longitude) != getattr(
            self, "_loaded_point", (self.latitude, self.longitude)
        )
        # A corrected address is a different address, and keeping the old
        # coordinates against it would publish a program at a place it does not
        # meet. Unless the same edit supplied new coordinates by hand, in which
        # case the editor has already answered the question.
        if changed_query and not changed_point:
            self.forget_lookup()
        # Coordinates typed in by hand resolve an address as surely as the
        # geocoder would, and a row left pending would be overwritten by the
        # next lookup.
        elif self.status == self.Status.PENDING and self.has_point:
            self.status = self.Status.RESOLVED

        if (update_fields := kwargs.get("update_fields")) is not None:
            kwargs["update_fields"] = {*update_fields, *self.LOOKUP_FIELDS}
        result = super().save(*args, **kwargs)
        self._loaded_query = self.query
        self._loaded_point = (self.latitude, self.longitude)
        return result

    @classmethod
    def from_db(cls, db, field_names, values):
        """Remember what was loaded, so `save()` can tell what an editor changed."""
        instance = super().from_db(db, field_names, values)
        instance._loaded_query = instance.query
        instance._loaded_point = (instance.latitude, instance.longitude)
        return instance

    @property
    def display_name(self):
        """What a page shows: the resolved address, or the typed one if nothing resolved."""
        return self.label or self.query

    @property
    def has_point(self):
        return self.latitude is not None and self.longitude is not None

    def apply(self, place):
        """Record the outcome of a lookup. The caller saves.

        A miss is recorded rather than ignored, because "we looked and found
        nothing" and "nobody has looked" need different handling and look
        identical in an empty coordinate column.
        """
        self.resolved_at = timezone.now()
        if place is None:
            self.status = self.Status.FAILED
            return
        self.label = place.label
        self.latitude = place.latitude
        self.longitude = place.longitude
        self.city = place.city
        self.postcode = place.postcode
        self.osm_type = place.osm_type
        self.osm_id = place.osm_id
        self.status = self.Status.RESOLVED

    def copied_values(self):
        """This place as keyword arguments for the other kind of location row."""
        return {field: getattr(self, field) for field in self.COPY_FIELDS}

    def forget_lookup(self):
        """Throw away what a previous lookup decided, without touching `query`."""
        self.label = ""
        self.latitude = self.longitude = None
        self.city = self.postcode = self.osm_type = ""
        self.osm_id = self.resolved_at = None
        self.status = self.Status.PENDING


class ProgramLocation(BaseLocation):
    """Where a published program meets."""

    program = models.ForeignKey(Program, related_name="locations", on_delete=models.CASCADE)

    class Meta(BaseLocation.Meta):
        verbose_name = "meeting place"
        verbose_name_plural = "meeting places"
        # For the distance search: a haversine is too expensive to run over every
        # row, so the query will cut the field down to a box of latitudes and
        # longitudes first and do the real arithmetic on what survives. This is
        # the index that step reads.
        indexes = [models.Index(fields=["latitude", "longitude"])]


class ContactPerson(models.Model):
    """Who to call about this program. Never published.

    Edited inline on the program, never navigated to separately, and rendered
    by no public template. Families reach a program through its website,
    Facebook page, email or phone; this is the roster of people we talk to
    when a listing needs checking.
    """

    program = models.ForeignKey(Program, related_name="contacts", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    role = models.CharField(
        max_length=80,
        blank=True,
        help_text="e.g. 'Coordinator', 'Registration'.",
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "contact"
        verbose_name_plural = "contacts"

    def __str__(self):
        return f"{self.name} ({self.role})" if self.role else self.name


class Page(SanitizedRichTextMixin, models.Model):
    """A handful of flat pages: About, FAQ, Resources. Deliberately not more."""

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True, help_text="Used in the web address.")
    body = ProseEditorField(config=RICH_TEXT_CONFIG, sanitize=True)
    is_published = models.BooleanField(default=False)
    show_in_nav = models.BooleanField(
        "show in navigation",
        default=False,
        help_text="Tick to add a link to this page in the site header.",
    )
    sort_order = models.PositiveIntegerField(default=100)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("directory:page", kwargs={"slug": self.slug})


def validate_logo_size(value):
    """Public uploads need a ceiling. 2 MB is generous for a logo."""
    limit = 2 * 1024 * 1024
    if value.size > limit:
        raise ValidationError("That image is larger than 2 MB. Please upload a smaller file.")


class Submission(SharedProgramFields):
    """A record waiting on approval, from one of two doors.

    A **registration** is a provider describing their own program. It mirrors
    every published field on `Program`, because the whole point is that
    approving it is the only action left — nobody retypes anything.

    What it deliberately does not collect is a named person to publish.
    Families reach a program through its website, Facebook page, email or
    phone; the people behind it are our records, not the directory's content.

    A **referral** is someone pointing us at a program they do not run. It is
    deliberately thin: a name and a way to reach them, so we can invite the
    people who run it to register it properly themselves.

    Both land in one queue. She should not have to remember to check two.

    Everything a registration answers the same way a published program does is on
    `SharedProgramFields`, which is what makes approval a copy rather than a
    transcription.
    """

    class Kind(models.TextChoices):
        REGISTRATION = "registration", "Registration — the provider's own program"
        REFERRAL = "referral", "Referral — someone else suggested it"

    class Status(models.TextChoices):
        NEW = "new", "New — needs review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.REGISTRATION,
        db_index=True,
    )

    # --- The program itself. These mirror `Program` field for field. --------

    program_name = models.CharField(max_length=160)
    short_description = models.CharField(
        max_length=240,
        blank=True,
        help_text="One sentence, plain text. Shown in listings and search results.",
    )
    description = models.TextField(
        blank=True,
        help_text="The full description, as plain text. Blank lines start new paragraphs.",
    )
    # SET_NULL here, unlike on `Program`: a submission is a record of what
    # someone sent us, and an old one should never be the reason a category
    # cannot be tidied up.
    category = models.ForeignKey(
        Category,
        related_name="submissions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    tags = models.ManyToManyField(Tag, blank=True)

    # Where they meet is `SubmissionLocation`. The registration form resolves
    # each address as it is typed, so what arrives is already a point on a map
    # rather than a paragraph somebody has to interpret later.

    logo = models.ImageField(
        upload_to="submissions/logos/",
        blank=True,
        validators=[validate_logo_size],
    )

    # --- Who sent it, and our handling of it -------------------------------

    submitter_name = models.CharField(max_length=120, blank=True)
    submitter_email = models.EmailField(blank=True)
    submitter_role = models.CharField(
        max_length=80,
        blank=True,
        help_text="Their role at the program, for registrations.",
    )
    is_authorized = models.BooleanField(
        default=False,
        help_text="The registrant confirmed they are authorized to list this program.",
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    review_notes = models.TextField(blank=True, help_text="For our records. Never shown publicly.")
    created_program = models.ForeignKey(
        Program,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="from_submissions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.program_name

    @property
    def is_registration(self):
        return self.kind == self.Kind.REGISTRATION

    @property
    def is_complete_enough_to_publish(self):
        """A record with no one-line description has nothing to show in a listing."""
        return bool(self.short_description.strip())

    @property
    def location_lines(self):
        """The addresses, as the submitter left them."""
        return [location.display_name for location in self.locations.all()]

    def description_as_html(self):
        """Plain text in, paragraphs out.

        The public forms take plain text rather than handing strangers a rich
        text editor. `Program.description` is sanitized again on save, so this
        only has to produce the paragraphs.
        """
        blocks = [escape(block.strip()) for block in re.split(r"\n\s*\n", self.description or "")]
        return "".join(f"<p>{block}</p>" for block in blocks if block)


class SubmissionLocation(BaseLocation):
    """A place somebody named on one of the public forms.

    The same shape as `ProgramLocation`, and usually already resolved: the form
    is a search box, so what arrives is generally a place the submitter picked
    off a list rather than a line of prose. That is the point of building it
    that way — the person who knows where they meet is the person choosing which
    match is right, and approving their submission copies the answer across
    instead of guessing at it.
    """

    submission = models.ForeignKey(Submission, related_name="locations", on_delete=models.CASCADE)

    class Meta(BaseLocation.Meta):
        verbose_name = "place"
        verbose_name_plural = "places they named"
