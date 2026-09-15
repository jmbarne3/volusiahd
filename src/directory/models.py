"""Data model for the directory.

The shape of these models determines the admin experience, which is the thing
that has to still be working in eighteen months. Every field that is ambiguous
to a non-technical editor carries `help_text`; that text is the documentation.

Note the absence of any faith-based or secular flag. This is deliberate and
settled — programs describe their own affiliation in their own description.
"""

from django.db import models
from django.urls import reverse
from django.utils import timezone
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
    email = models.EmailField(blank=True, help_text="The program's general contact address.")
    phone = models.CharField(max_length=32, blank=True)

    street = models.CharField(max_length=160, blank=True)
    city = models.CharField(max_length=80, blank=True)
    zip_code = models.CharField("ZIP code", max_length=10, blank=True)

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


class Submission(models.Model):
    """A public "suggest a program" record, approved into a Program in one action.

    This is the feature that determines whether the directory grows without the
    content manager doing all of the typing.
    """

    class Status(models.TextChoices):
        NEW = "new", "New — needs review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    program_name = models.CharField(max_length=160)
    website = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    city = models.CharField(max_length=80, blank=True)
    categories = models.ManyToManyField(Category, blank=True)
    description = models.TextField(
        blank=True,
        help_text="What the submitter told us about the program.",
    )

    submitter_name = models.CharField(max_length=120, blank=True)
    submitter_email = models.EmailField(blank=True)

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
