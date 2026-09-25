"""Data model for the directory.

The shape of these models determines the admin experience, which is the thing
that has to still be working in eighteen months. Every field that is ambiguous
to a non-technical editor carries `help_text`; that text is the documentation.

Note the absence of any faith-based or secular flag. This is deliberate and
settled — programs describe their own affiliation in their own description.
"""

import re
import time

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("directory:tag", kwargs={"slug": self.slug})


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

    website = models.URLField(blank=True)
    facebook = models.URLField(
        "Facebook page",
        blank=True,
        help_text="The full address of their Facebook page or group, "
        "e.g. 'https://facebook.com/groups/example'.",
    )
    email = models.EmailField(blank=True, help_text="The program's general contact address.")
    phone = models.CharField(max_length=32, blank=True)

    # Where a program meets is `ProgramLocation`, edited inline below. It is a
    # table rather than a text field because the next question families ask is
    # "how far is that from me", and no amount of free text answers it.

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


class Submission(models.Model):
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

    website = models.URLField(blank=True)
    facebook = models.URLField("Facebook page", blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    # Where they meet is `SubmissionLocation`. The registration form resolves
    # each address as it is typed, so what arrives is already a point on a map
    # rather than a paragraph somebody has to interpret later.

    serves_grades = models.CharField(max_length=80, blank=True)
    age_min = models.PositiveSmallIntegerField(null=True, blank=True)
    age_max = models.PositiveSmallIntegerField(null=True, blank=True)

    cost_notes = models.TextField(blank=True)
    meeting_schedule = models.TextField(blank=True)

    step_up_direct_pay = models.BooleanField("Step Up direct pay provider", default=False)
    step_up_pep = models.BooleanField("direct pay for PEP", default=False)
    step_up_fes_ua = models.BooleanField("direct pay for FES-UA", default=False)

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
