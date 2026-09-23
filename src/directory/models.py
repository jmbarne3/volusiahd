"""Data model for the directory.

The shape of these models determines the admin experience, which is the thing
that has to still be working in eighteen months. Every field that is ambiguous
to a non-technical editor carries `help_text`; that text is the documentation.

Note the absence of any faith-based or secular flag. This is deliberate and
settled — programs describe their own affiliation in their own description.
"""

import re

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django_prose_editor.fields import ProseEditorField

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

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("directory:category", kwargs={"slug": self.slug})


class ProgramQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Program.Status.PUBLISHED)


class Program(SanitizedRichTextMixin, models.Model):
    """A single homeschool program. The core record of the directory."""

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
    categories = models.ManyToManyField(Category, related_name="programs", blank=True)

    website = models.URLField(blank=True)
    facebook = models.URLField(
        "Facebook page",
        blank=True,
        help_text="The full address of their Facebook page or group, "
        "e.g. 'https://facebook.com/groups/example'.",
    )
    email = models.EmailField(blank=True, help_text="The program's general contact address.")
    phone = models.CharField(max_length=32, blank=True)

    locations = models.TextField(
        "where they meet",
        blank=True,
        help_text="One address per line. A program that meets in several places gets "
        "a line for each; a program that moves around can say so in words instead.",
    )

    serves_grades = models.CharField(
        max_length=80,
        blank=True,
        help_text="Free text, e.g. 'K–8' or 'high school only'. Leave blank if it varies.",
    )
    age_min = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max = models.PositiveSmallIntegerField(null=True, blank=True)

    cost_notes = models.TextField(
        blank=True,
        help_text="Free text — fee structures never fit a single number. "
        "e.g. '$45/semester per family, plus a $20 materials fee'.",
    )
    meeting_schedule = models.TextField(
        blank=True,
        help_text="Free text, e.g. 'Tuesdays 9–noon, September through May'.",
    )

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
    def age_range_display(self):
        if self.age_min and self.age_max:
            return f"Ages {self.age_min}–{self.age_max}"
        if self.age_min:
            return f"Ages {self.age_min} and up"
        if self.age_max:
            return f"Through age {self.age_max}"
        return ""

    @property
    def location_list(self):
        """The addresses, one per line, with the blanks dropped.

        A program that meets in three places is the normal case, not the odd
        one, so every template that shows a location loops over this rather
        than assembling a single address out of parts.
        """
        return [line.strip() for line in (self.locations or "").splitlines() if line.strip()]

    @property
    def primary_location(self):
        """The first address, for listings that only have room for one."""
        locations = self.location_list
        return locations[0] if locations else ""

    def mark_verified(self, on=None):
        self.last_verified_on = on or timezone.localdate()


class ContactPerson(models.Model):
    """Edited inline on the program, never navigated to separately."""

    program = models.ForeignKey(Program, related_name="contacts", on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    role = models.CharField(
        max_length=80,
        blank=True,
        help_text="e.g. 'Coordinator', 'Registration'.",
    )
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    is_public = models.BooleanField(
        default=False,
        help_text="Untick to keep this person's details for our records only. "
        "Only ticked contacts appear on the public site.",
    )

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


class Submission(models.Model):
    """A record waiting on approval, from one of two doors.

    A **registration** is a provider describing their own program. It mirrors
    every field on `Program`, because the whole point is that approving it is
    the only action left — nobody retypes anything.

    A **referral** is someone pointing us at a program they do not run. It is
    deliberately thin: a name and a way to reach them, so we can invite the
    people who run it to register it properly themselves.

    Both land in one queue. She should not have to remember to check two.
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
    categories = models.ManyToManyField(Category, blank=True)

    website = models.URLField(blank=True)
    facebook = models.URLField("Facebook page", blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    locations = models.TextField("where they meet", blank=True)

    serves_grades = models.CharField(max_length=80, blank=True)
    age_min = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max = models.PositiveSmallIntegerField(null=True, blank=True)

    cost_notes = models.TextField(blank=True)
    meeting_schedule = models.TextField(blank=True)

    logo = models.ImageField(
        upload_to="submissions/logos/",
        blank=True,
        validators=[validate_logo_size],
    )

    # --- The contact to publish alongside the program ----------------------

    contact_name = models.CharField(max_length=120, blank=True)
    contact_role = models.CharField(max_length=80, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_is_public = models.BooleanField(
        default=True,
        help_text="Whether this contact's details may appear on the public page.",
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

    def description_as_html(self):
        """Plain text in, paragraphs out.

        The public forms take plain text rather than handing strangers a rich
        text editor. `Program.description` is sanitized again on save, so this
        only has to produce the paragraphs.
        """
        blocks = [escape(block.strip()) for block in re.split(r"\n\s*\n", self.description or "")]
        return "".join(f"<p>{block}</p>" for block in blocks if block)
